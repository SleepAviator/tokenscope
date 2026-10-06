#!/usr/bin/env python3
"""Collect CC-Switch usage from configured hosts and render standalone figures."""
import argparse
import base64
import configparser
import csv
import hashlib
import colorsys
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import zlib

from collect import collect

ROOT = Path(__file__).resolve().parent
SESSION = {'session_log', 'codex_session', 'gemini_session', 'opencode_session'}


def canonical_model(row):
    """Preserve the source model label without aliases or per-request corrections."""
    return row.get('model') or 'unknown'


def fresh(r):
    value = int(r['input_tokens'])
    semantics = int(r.get('input_token_semantics', 0))
    if semantics not in (0, 1, 2):
        raise ValueError('Unsupported input_token_semantics: ' + str(semantics))
    if semantics != 2 and r['app_type'] in ('codex', 'gemini', 'grokbuild'):
        subtract = r['cache_read_tokens'] + (r['cache_creation_tokens'] if semantics == 1 else 0)
        if value >= subtract:
            value -= subtract
    return value


def effective(rows):
    """Mirror CC-Switch effective_usage_log_filter, including Desktop proxy matching."""
    proxies = defaultdict(list)
    for r in rows:
        if (r.get('data_source') or 'proxy') == 'proxy' and 200 <= r['status_code'] < 300:
            proxies[(r['input_tokens'], r['output_tokens'], r['cache_read_tokens'])].append(r)
    kept = []
    for r in rows:
        duplicate = False
        if r.get('data_source') in SESSION:
            for p in proxies[(r['input_tokens'], r['output_tokens'], r['cache_read_tokens'])]:
                app_match = p['app_type'] == r['app_type'] or (r['app_type'] == 'claude' and p['app_type'] == 'claude-desktop')
                cache_match = p['cache_creation_tokens'] == r['cache_creation_tokens'] or (r['cache_creation_tokens'] == 0 and r['data_source'] != 'session_log')
                model_match = p['model'].lower() == r['model'].lower() or 'unknown' in (p['model'].lower(), r['model'].lower())
                if app_match and cache_match and model_match and abs(p['created_at'] - r['created_at']) <= 600:
                    duplicate = True
                    break
        if not duplicate:
            kept.append(r)
    return kept


def fetch(item):
    name, cfg = item
    database = cfg.get('database', '~/.cc-switch/cc-switch.db')
    roots = [p.strip() for p in cfg.get('codex_session_roots', '~/.codex/sessions;~/.codex/archived_sessions').split(';') if p.strip()]
    codex_home = cfg.get('codex_home', '~/.codex')
    claude_projects = cfg.get('claude_projects', '~/.claude/projects')
    native_option = cfg.get('codex_native_tps', 'true').lower()
    if native_option not in configparser.ConfigParser.BOOLEAN_STATES:
        raise ValueError('codex_native_tps must be true or false')
    native_tps = configparser.ConfigParser.BOOLEAN_STATES[native_option]
    if cfg['transport'] == 'local':
        data = collect(database, roots, codex_home, claude_projects, native_tps)
    elif cfg['transport'] == 'ssh':
        # Execute allowlisted reader in memory; no remote installation or database copy.
        code = (ROOT / 'collect.py').read_text() + '\n'
        code = code.replace("if __name__ == '__main__':", 'if False:')
        code += 'import zlib,base64\nprint(base64.b64encode(zlib.compress(json.dumps(collect(' + repr(database) + ', ' + repr(roots) + ', ' + repr(codex_home) + ', ' + repr(claude_projects) + ', ' + repr(native_tps) + ')).encode(), 9)).decode())\n'
        encoded = base64.b64encode(zlib.compress(code.encode(), 9)).decode()
        python = cfg['python']
        if any(c in python for c in '\"\r\n'):
            raise ValueError('Invalid interpreter path')
        command = '"' + python + '" -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\'' + encoded + '\')))"'
        try:
            result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', cfg['host'], command], capture_output=True, text=True, timeout=int(cfg.get('timeout_seconds', '900')))
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(name + ': SSH usage collection exceeded configured timeout') from None
        if result.returncode:
            raise RuntimeError(name + ': SSH collector failed: ' + result.stderr.strip())
        data = json.loads(zlib.decompress(base64.b64decode(result.stdout.strip())))
    else:
        raise ValueError('Unsupported transport')
    print(name + ': ' + str(len(data['proxy_request_logs'])) + ' requests, ' + str(len(data['usage_daily_rollups'])) + ' rollups', flush=True)
    return name, data


def write_csv(path, rows):
    if not rows:
        path.unlink(missing_ok=True)
        return
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def response_duration_ms(row):
    """Recorded per-request elapsed time only; never session wall-clock time."""
    for field in ('duration_ms', 'latency_ms'):
        value = row.get(field)
        if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
            return value
    return 0


def build(sources, out, render_figures=True):
    out.mkdir(parents=True, exist_ok=True)
    totals = defaultdict(lambda: defaultdict(int))
    hourly_totals = defaultdict(lambda: defaultdict(int))
    session_totals = defaultdict(lambda: defaultdict(int))
    samples = []
    responses = []
    audit = {}
    seen = {}
    duplicates = 0
    rollup_seen = {}
    matching_rollups = 0
    for host, data in sources:
        names = {(p['id'], p['app_type']): p['name'] for p in data['providers']}
        raw = data['proxy_request_logs']
        details = effective(raw)
        audit[host] = {'collected_at': data['collected_at'], 'timezone': data['host_timezone'], 'raw_requests': len(raw), 'proxy_session_duplicates': len(raw)-len(details), 'rollup_rows': len(data['usage_daily_rollups']), 'cross_host_duplicates': 0, 'timed_requests': 0}
        audit[host]['latest_request_utc'] = datetime.fromtimestamp(max(r['created_at'] for r in raw), timezone.utc).isoformat() if raw else None
        audit[host]['rollup_dates'] = [min(r['date'] for r in data['usage_daily_rollups']), max(r['date'] for r in data['usage_daily_rollups'])] if data['usage_daily_rollups'] else []
        audit[host]['request_status_counts'] = dict(Counter(str(r['status_code']) for r in raw))
        audit[host]['zero_token_requests'] = sum(not any(r[k] for k in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens')) for r in raw)
        audit[host]['session_metadata'] = data.get('session_metadata', {})
        rows = []
        for r in details:
            identity = (r['app_type'], r['request_id'])
            signature = tuple(r[k] for k in ('created_at', 'model', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
            if identity in seen:
                if seen[identity] != signature:
                    raise ValueError('Conflicting cross-host request ID; merge stopped for review')
                duplicates += 1
                audit[host]['cross_host_duplicates'] += 1
                continue
            seen[identity] = signature
            rows.append((r, 1, 'request'))
        rows.extend((r, r['request_count'], 'rollup') for r in data['usage_daily_rollups'])
        for r in data['usage_daily_rollups']:
            signature = tuple(r.get(k) for k in ('date', 'app_type', 'model', 'request_count', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens', 'input_token_semantics'))
            if signature in rollup_seen and rollup_seen[signature] != host:
                matching_rollups += 1
            rollup_seen[signature] = host
        for r, count, grain in rows:
            if grain == 'request':
                local_time = datetime.fromtimestamp(r['created_at'])
                r = dict(r, date=local_time.date().isoformat(),
                         local_datetime=local_time.strftime('%Y-%m-%d %H:%M:%S'))
            app = 'Claude Code' if r['app_type'] in ('claude', 'claude-desktop') else r['app_type'].title()
            provider = names.get((r['provider_id'], r['app_type']), 'Session / provider unknown' if r['provider_id'].startswith('_') else 'Unresolved provider')
            if r.get('session_provider'):
                provider = r['session_provider']
            model = canonical_model(r)
            key = (r['date'], host, app, provider, model, grain)
            hour = r['local_datetime'][11:13] if grain == 'request' else 'Unknown hour'
            for t in (totals[key], hourly_totals[(r['date'], hour, host, app, provider, model, grain)]):
                t['requests'] += count
                t['fresh_input_tokens'] += fresh(r)
                for field in ('output_tokens', 'cache_read_tokens', 'cache_creation_tokens'):
                    t[field] += r[field]
                t['cost_usd'] = t.get('cost_usd', Decimal(0)) + Decimal(str(r['total_cost_usd']))
            if grain == 'request' and r.get('session_key'):
                st = session_totals[(r['date'], host, app, r['session_key'], model)]
                st['requests'] += count
                for field in ('tps_count', 'tps_sum', 'tps_max', 'native_tps_count', 'native_tps_sum', 'native_tps_max'):
                    st.setdefault(field, 0)
                st['session_title'] = r.get('session_title') or ''
                local_time = r.get('local_datetime', '')
                hour = local_time[11:13]
                if len(local_time) >= 13 and hour.isdigit() and 0 <= int(hour) < 24:
                    hours = st.setdefault('hours', {})
                    hours[hour] = hours.get(hour, 0) + fresh(r) + sum(r[k] for k in ('output_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
                st['fresh_input_tokens'] += fresh(r)
                for field in ('output_tokens', 'cache_read_tokens', 'cache_creation_tokens'):
                    st[field] += r[field]
                st['cost_usd'] = st.get('cost_usd', Decimal(0)) + Decimal(str(r['total_cost_usd']))
            duration = response_duration_ms(r)
            native_duration = r.get('native_response_ms', 0)
            if grain == 'request' and r['output_tokens'] > 0 and 200 <= r['status_code'] < 300:
                elapsed = duration
                timing_source = ('duration_ms' if r.get('duration_ms') == duration else 'latency_ms') if duration else ''
                if not elapsed and isinstance(native_duration, (int, float)) and math.isfinite(native_duration) and native_duration > 0:
                    elapsed, timing_source = native_duration, 'native_log'
                first_token = r.get('first_token_ms')
                # Zero is the upstream default for unavailable first-token timing.
                if not isinstance(first_token, (int, float)) or not math.isfinite(first_token) or first_token <= 0 or (duration and first_token > duration):
                    first_token = None
                responses.append(dict(date=r['date'], hour=r['local_datetime'][11:13],
                                      host=host, app=app, model=model, output_tokens=r['output_tokens'],
                                      duration_ms=elapsed or None, duration_source=timing_source,
                                      tps=r['output_tokens'] * 1000 / elapsed if elapsed else None,
                                      first_token_ms=first_token))
            if (grain == 'request' and r.get('session_key') and duration == 0 and
                    native_duration > 0 and math.isfinite(native_duration) and
                    r['output_tokens'] > 0 and 200 <= r['status_code'] < 300):
                rate = r['output_tokens'] * 1000 / native_duration
                st['native_tps_count'] += 1
                st['native_tps_sum'] += rate
                st['native_tps_max'] = max(st['native_tps_max'], rate)
            if grain == 'request' and duration > 0 and r['output_tokens'] > 0 and 200 <= r['status_code'] < 300:
                samples.append({'date': r['date'], 'local_datetime': r.get('local_datetime', r['date']), 'host': host, 'app': app, 'provider': provider, 'model': model, 'created_at': r['created_at'], 'output_tokens': r['output_tokens'], 'duration_ms': duration, 'duration_source': 'duration_ms' if r.get('duration_ms') == duration else 'latency_ms', 'tps': r['output_tokens'] / (duration / 1000)})
                audit[host]['timed_requests'] += 1
                if r.get('session_key'):
                    st['tps_count'] += 1
                    st['tps_sum'] += r['output_tokens'] * 1000 / duration
                    st['tps_max'] = max(st['tps_max'], r['output_tokens'] * 1000 / duration)
    daily = []
    for (date, host, app, provider, model, grain), t in sorted(totals.items()):
        row = dict(date=date, host=host, app=app, provider=provider, model=model, grain=grain, **t)
        row['total_tokens'] = sum(t[k] for k in ('fresh_input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
        row['cost_usd'] = str(row['cost_usd'])
        daily.append(row)
    summary = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'sources': audit, 'cross_host_duplicates_removed': duplicates,
               'total_tokens': sum(r['total_tokens'] for r in daily), 'requests': sum(r['requests'] for r in daily),
               'timed_requests': len(samples), 'by_host_app': [], 'matching_cross_host_rollups_retained': matching_rollups,
               'caveats': ['Timestamped requests use the dashboard host local date and hour across all machines. Historical rollups lack timestamps, retain their source-local date, and appear under Unknown hour in the hourly chart.',
                           'Rollups and remaining requests are additive as in CC-Switch. Rollup cross-host overlap cannot be verified without original IDs.',
                           'Same request IDs across hosts are deduplicated; copied sessions with rewritten IDs may remain.',
                           'TPS is output tokens divided by recorded request duration (latency fallback), NOT the previous user-turn metric. Missing timings are excluded.',
                           'Claude Desktop gateway rows are included in Claude Code, matching CC-Switch; this does not capture all Desktop chat usage.',
                           'Source databases are read as stored; this tool does not trigger CC-Switch session import or repair upstream accounting. Costs are recorded estimates.']}
    summary['total_cost_usd'] = str(sum((Decimal(r['cost_usd']) for r in daily), Decimal(0)))
    summary['by_model'] = []
    for model in sorted({r['model'] for r in daily}):
        selected = [r for r in daily if r['model'] == model]
        summary['by_model'].append(dict(model=model, tokens=sum(r['total_tokens'] for r in selected),
            requests=sum(r['requests'] for r in selected),
            cost_usd=str(sum((Decimal(r['cost_usd']) for r in selected), Decimal(0)))))
    summary['caveats'].append('Provider metadata is joined from Codex session headers by session ID. Rollups lack IDs; unmatched providers remain explicitly unknown. Model labels come from CC-Switch per-record usage, not one session-wide model.')
    summary['caveats'].append('USD values are CC-Switch recorded estimates, not invoices or subscription charges. Zero recorded cost does not establish free usage; no current price list is applied retroactively.')
    summary['caveats'].append('Model labels are preserved as recorded; no model aliases or per-request corrections are applied.')
    for host in audit:
        for app in sorted({r['app'] for r in daily if r['host'] == host}):
            selected = [r for r in daily if r['host'] == host and r['app'] == app]
            summary['by_host_app'].append(dict(host=host, app=app, tokens=sum(r['total_tokens'] for r in selected), requests=sum(r['requests'] for r in selected), start=min(r['date'] for r in selected), end=max(r['date'] for r in selected)))
    write_csv(out / 'daily_usage.csv', daily)
    hourly = []
    for (date, hour, host, app, provider, model, grain), t in sorted(hourly_totals.items()):
        item = dict(date=date, hour=hour, host=host, app=app, provider=provider, model=model, grain=grain, **t)
        item['total_tokens'] = sum(t[k] for k in ('fresh_input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
        item['cost_usd'] = str(item['cost_usd'])
        hourly.append(item)
    write_csv(out / 'hourly_usage.csv', hourly)
    session_daily = []
    for (date, host, app, session_key, model), st in sorted(session_totals.items()):
        item = dict(date=date, host=host, app=app, session_key=session_key, model=model, **st)
        item['hours'] = json.dumps(item.get('hours', {}), sort_keys=True)
        item['total_tokens'] = sum(st[k] for k in ('fresh_input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'))
        item['cost_usd'] = str(item['cost_usd'])
        session_daily.append(item)
    write_csv(out / 'session_daily_usage.csv', session_daily)
    summary['session_detail_available'] = True
    summary['caveats'].append('Per-session detail uses recorded session IDs, hashed before export, grouped per machine and application. Some sources use request-scoped IDs. Rollups and requests without usable IDs are excluded from session detail, not from daily totals.')
    write_csv(out / 'request_tps.csv', samples)
    write_csv(out / 'response_speed.csv', responses)
    summary['response_speed_available'] = True
    summary['host_date'] = datetime.now().date().isoformat()
    summary['host_timezone'] = datetime.now().astimezone().tzname()
    if render_figures:
        render(daily, samples, out, summary)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    lines = ['# CC-Switch merged usage', '', 'Generated: ' + summary['generated_at_utc'], '', '| Machine | App | Tokens | Requests | First day | Last day |', '|---|---|---:|---:|---|---|']
    for r in summary['by_host_app']:
        lines.append(f"| {r['host']} | {r['app']} | {r['tokens']:,} | {r['requests']:,} | {r['start']} | {r['end']} |")
    lines += ['', '## Usage and recorded estimated cost by model', '', '| Model | Tokens | Requests | Estimated USD |', '|---|---:|---:|---:|']
    for r in summary['by_model']:
        lines.append(f"| {r['model']} | {r['tokens']:,} | {r['requests']:,} | ${Decimal(r['cost_usd']):,.2f} |")
    lines += ['', f"Total recorded estimated cost: ${Decimal(summary['total_cost_usd']):,.2f} (not a bill)."]
    lines += ['', 'Total tokens: ' + f"{summary['total_tokens']:,}", '', '## Collection quality', '']
    for host, a in audit.items():
        lines.append(f"- {host}: collected {a['collected_at']}; latest request {a['latest_request_utc']}; {a['timed_requests']} timed requests; {a['zero_token_requests']} zero-token requests; HTTP statuses {a['request_status_counts']}.")
    lines += ['', f'Cross-host identical request IDs removed: {duplicates}. Matching rollup rows retained for review: {matching_rollups}.', '', '## Coverage and definitions', ''] + ['- ' + s for s in summary['caveats']]
    (out / 'summary.md').write_text('\n'.join(lines) + '\n')
    return summary


def monthly_leaders(daily):
    totals = defaultdict(Counter)
    costs = defaultdict(lambda: defaultdict(Decimal))
    for row in daily:
        totals[row['date'][:7]][row['model']] += int(row['total_tokens'])
        costs[row['date'][:7]][row['model']] += Decimal(row['cost_usd'])
    result = []
    for month, models in sorted(totals.items()):
        top = max(models.values())
        if top <= 0:
            continue
        leaders = sorted(m for m, n in models.items() if n == top)
        result.append(dict(month=month, models=leaders,
                           cost_usd_by_model={m: str(costs[month][m]) for m in leaders},
                           tokens=top, share=top / sum(models.values())))
    return result


def render(daily, samples, out, summary):
    os.environ.setdefault('MPLCONFIGDIR', str(out / '.mpl'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import numpy as np
    from matplotlib.ticker import FuncFormatter
    models = sorted({r['model'] for r in daily})
    palette = ['#37749e', '#d88632', '#737d45', '#ac6687', '#695ba2', '#328e86', '#777777']
    # Model identity controls color, independent of host, date range or sort order.
    colors = {m: colorsys.hls_to_rgb(int(hashlib.sha256(m.encode()).hexdigest()[:8], 16) / 2**32,
                                   .43, .58) for m in models}
    summary['model_colors'] = {m: matplotlib.colors.to_hex(c) for m, c in colors.items()}
    dates = sorted({r['date'] for r in daily})
    if not dates:
        raise ValueError('No retained usage records available to plot')
    xs = [datetime.fromisoformat(d) for d in dates]
    fig, ax = plt.subplots(figsize=(20, max(8, len(models) * .23 + 2)))
    bottom = np.zeros(len(dates))
    for p in models:
        byday = defaultdict(int)
        for r in daily:
            if r['model'] == p:
                byday[r['date']] += r['total_tokens']
        values = np.array([byday[d] for d in dates])
        ax.bar(xs, values, bottom=bottom, width=.82, color=colors[p], alpha=.65, label=p)
        bottom += values
    ax.set_ylim(0, max(float(max(bottom)) * 1.45, 1))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x/1e9:.1f}B' if x >= 1e9 else f'{x/1e6:.0f}M'))
    ax.set_ylabel('Daily tokens: fresh input + cache read + cache write + output')
    ax.set_xlabel('Source-local calendar date (historical rollups retain their recorded day)')
    ax.grid(axis='y', alpha=.15)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
    start, end = mdates.date2num(xs[0]), mdates.date2num(xs[-1])
    interior = [t for t in mdates.MonthLocator().tick_values(xs[0], xs[-1]) if start + 12 < t < end - 12]
    ax.set_xticks([start] + interior + ([end] if end != start else []))
    right = ax.twinx()
    costs = defaultdict(Decimal)
    for r in daily:
        costs[r['date']] += Decimal(r['cost_usd'])
    daily_costs = [float(costs[d]) for d in dates]
    right.plot(xs, daily_costs, color='#242424', linewidth=1.7,
               label='Daily total cost (USD)', zorder=5)
    right.set_ylim(0, max(max(daily_costs) * 1.45, 1))
    right.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'${x:,.0f}'))
    right.set_ylabel('Daily recorded estimated cost (USD)')
    right.legend(loc='upper left', bbox_to_anchor=(0, 1.07), fontsize=10)
    summary['monthly_top_models'] = monthly_leaders(daily)
    for i, leader in enumerate(summary['monthly_top_models']):
        month_dates = [x for d, x in zip(dates, xs) if d.startswith(leader['month'])]
        center = month_dates[0] + (month_dates[-1] - month_dates[0]) / 2
        model = leader['models'][0]
        name = '\n'.join(f"{m}\n${Decimal(leader['cost_usd_by_model'][m]):,.2f} est." for m in leader['models'])
        label = f"{center:%b %Y}\n{name}\n{leader['share']:.0%} of month tokens"
        right.text(center, .96 if i % 2 == 0 else .83, label,
                   transform=right.get_xaxis_transform(), ha='center', va='top',
                   fontsize=9, color='#242424', zorder=10,
                   bbox=dict(boxstyle='round,pad=.35', facecolor='white',
                             edgecolor=colors[model], linewidth=1.6, alpha=.97))
    for key in ('tps_p1_p99', 'tps_visible', 'tps_clipped'):
        summary.pop(key, None)
    ax.legend(loc='upper left', bbox_to_anchor=(1.09, 1), fontsize=9, title='Model')
    fig.suptitle('CC-Switch usage: ' + ', '.join(summary['sources']), fontsize=19)
    ax.set_title(f"{dates[0]} to {dates[-1]} · {summary['total_tokens']:,} tokens · ${Decimal(summary['total_cost_usd']):,.2f} recorded estimated cost", fontsize=11, pad=40)
    fig.text(.07, .025, 'Bars: tokens by model (left). Line: daily total estimated USD (right), not invoices. Zero cost may mean unpriced usage. Latest day may be partial.', fontsize=9)
    fig.tight_layout(rect=(0,.05,1,.96))
    fig.savefig(out / 'usage.png', dpi=160)
    fig.savefig(out / 'usage.svg')
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(20, max(7, len(models) * .23 + 2)))
    bottom = np.zeros(len(dates))
    for model in models:
        byday = defaultdict(Decimal)
        for r in daily:
            if r['model'] == model:
                byday[r['date']] += Decimal(r['cost_usd'])
        values = np.array([float(byday[d]) for d in dates])
        ax.bar(xs, values, bottom=bottom, width=.82, color=colors[model], label=model)
        bottom += values
    ax.set_ylim(0, max(float(max(bottom)) * 1.08, 1))
    ax.set_ylabel('Recorded estimated cost (USD)')
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'${x:,.0f}'))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
    ax.set_xticks([start] + interior + ([end] if end != start else []))
    ax.set_xlabel('Source-local calendar date')
    ax.grid(axis='y', alpha=.15)
    ax.legend(loc='upper left', bbox_to_anchor=(1.01, 1), fontsize=9, title='Model')
    ax.set_title(f"CC-Switch recorded estimated cost by model · ${Decimal(summary['total_cost_usd']):,.2f} total")
    fig.text(.07, .025, 'Not paid invoices or subscription charges. Zero recorded cost can mean unpriced usage. No price estimates have been invented.', fontsize=10)
    fig.tight_layout(rect=(0,.05,1,1))
    fig.savefig(out / 'cost.png', dpi=160)
    fig.savefig(out / 'cost.svg')
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 5))
    hosts = list(summary['sources'])
    bottom = np.zeros(len(hosts))
    for i, app in enumerate(sorted({r['app'] for r in daily})):
        values = np.array([sum(r['total_tokens'] for r in daily if r['host'] == h and r['app'] == app) for h in hosts])
        ax.bar(hosts, values, bottom=bottom, label=app, color=palette[i % len(palette)])
        bottom += values
    for h, value in zip(hosts, bottom):
        ax.annotate(f'{value/1e9:.3f}B', (h, value), xytext=(0, 5), textcoords='offset points', ha='center')
    ax.set_ylim(0, max(bottom) * 1.15)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x/1e9:.1f}B'))
    ax.set_ylabel('Total tokens (including cache)')
    ax.set_title('Retained CC-Switch usage by machine and application')
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / 'machines.png', dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.ini')
    parser.add_argument('--hosts', nargs='+', help='Subset of configured source names')
    args = parser.parse_args()
    cfg = configparser.ConfigParser(interpolation=None)
    if not cfg.read(args.config):
        parser.error('Missing config.ini; copy config.example.ini and configure sources')
    items = [(s[7:], dict(cfg[s])) for s in cfg.sections() if s.startswith('source:') and (not args.hosts or s[7:] in args.hosts)]
    if not items or (args.hosts and set(args.hosts) - {n for n, _ in items}):
        parser.error('Unknown or empty host selection')
    out = ROOT / 'output' / ('local' if len(items) == 1 else 'overall')
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        sources = list(pool.map(fetch, items))  # transient memory only; fail if any host fails
    result = build(sources, out)
    print(json.dumps(result['by_host_app'], indent=2))
    print('Output: ' + str(out))


if __name__ == '__main__':
    main()
