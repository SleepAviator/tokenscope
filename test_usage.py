import unittest
import tempfile
import json
import csv
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from update import fresh, effective, build, canonical_model, monthly_leaders
from collect import enrich_session_providers


def row(**kw):
    r = dict(app_type='codex', input_tokens=1000, output_tokens=20,
             cache_read_tokens=600, cache_creation_tokens=100,
             input_token_semantics=0, status_code=200, created_at=1000,
             model='model', data_source='codex_session')
    r.update(kw)
    if 'date' in kw and 'created_at' not in kw:
        r['created_at'] = int(datetime.fromisoformat(kw['date'] + 'T12:00:00').timestamp())
    return r


class Accounting(unittest.TestCase):
    def test_monthly_leaders_use_summed_costs_and_preserve_ties(self):
        rows = [dict(date='2026-01-01', model='a', total_tokens=4, cost_usd='0.10'),
                dict(date='2026-01-02', model='a', total_tokens=4, cost_usd='0.10'),
                dict(date='2026-01-02', model='b', total_tokens=6, cost_usd='0.90'),
                dict(date='2026-02-01', model='a', total_tokens=2, cost_usd='0.30'),
                dict(date='2026-02-01', model='b', total_tokens=20, cost_usd='0.30')]
        leaders = monthly_leaders(rows)
        self.assertEqual(leaders[0]['cost_usd_by_model'], {'b': '0.90'})
        self.assertEqual(leaders[1]['cost_usd_by_model'], {'a': '0.30', 'b': '0.30'})
        self.assertEqual(leaders[0]['models'], ['b'])
        self.assertEqual(leaders[0]['tokens_by_model'], {'b': 6})
        self.assertAlmostEqual(leaders[0]['share'], 9 / 11)
        self.assertEqual(leaders[0]['ranking'], 'cost_usd')
        self.assertEqual(leaders[0]['share_basis'], 'cost_usd')
        self.assertEqual(leaders[1]['models'], ['a', 'b'])
        self.assertEqual(leaders[1]['tokens_by_model'], {'a': 2, 'b': 20})
        self.assertEqual(leaders[1]['share'], .5)

    def test_monthly_leaders_skip_zero_recorded_cost(self):
        rows = [dict(date='2026-01-01', model='unpriced', total_tokens=1000, cost_usd='0')]
        self.assertEqual(monthly_leaders(rows), [])
        self.assertEqual(monthly_leaders([]), [])

    def test_model_labels_are_preserved(self):
        for model in ('example-model', 'example-model:cloud', 'vendor/example-model', 'unknown'):
            self.assertEqual(canonical_model(row(model=model)), model)
        self.assertEqual(canonical_model(row(model=None)), 'unknown')

    def test_header_provider_join(self):
        sid = '00000000-0000-0000-0000-000000000001'
        rows = [row(session_id=sid), row(session_id='missing')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('rollout-' + sid + '.jsonl')
            path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': sid, 'model_provider': 'ollama'}}) + '\nnot parsed session content\n')
            audit = enrich_session_providers(rows, [directory])
        self.assertEqual(rows[0]['session_provider'], 'ollama')
        self.assertNotIn('session_provider', rows[1])
        self.assertNotIn('session_id', rows[0])
        self.assertEqual(len(rows[0]['session_key']), 64)
        self.assertNotEqual(rows[0]['session_key'], sid)
        self.assertEqual(audit['sessions_unmatched'], 1)

    def test_session_keys_missing_and_source_ids(self):
        rows = [row(session_id=s, data_source='proxy') for s in ('same-id', 'same-id', '', None, 'unknown')]
        enrich_session_providers(rows, [])
        self.assertEqual(rows[0]['session_key'], rows[1]['session_key'])
        self.assertTrue(all('session_key' not in r for r in rows[2:]))
        self.assertTrue(all('session_id' not in r for r in rows))

    def test_session_detail_grain_dedup_and_public_projection(self):
        from app import public_data
        requests = [row(request_id=str(i), provider_id='_session', date=date, model=model,
                        session_key='hashed-id', total_cost_usd='0.10')
                    for i, (date, model) in enumerate([('2026-01-31', 'a'), ('2026-02-01', 'a'), ('2026-02-01', 'b')])]
        requests.append(row(request_id='no-session', provider_id='_session', date='2026-02-01', total_cost_usd='0.20'))
        rollup = row(provider_id='_session', date='2026-01-01', request_count=10, total_cost_usd='1')
        data = dict(collected_at='2026-02-02', host_timezone='UTC', providers=[], proxy_request_logs=requests, usage_daily_rollups=[rollup])
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            result = build([('one', data), ('two', data)], out, render_figures=False)
            public = public_data(out)
        self.assertEqual(len(public['session_rows']), 3)
        self.assertEqual(sum(r['requests'] for r in public['session_rows']), 3)
        self.assertEqual({r['host'] for r in public['session_rows']}, {'one'})
        self.assertEqual({r['date'] for r in public['session_rows']}, {'2026-01-31', '2026-02-01'})
        self.assertEqual(sum(r['tokens'] for r in public['session_rows']), 3*1120)
        self.assertEqual(result['cross_host_duplicates_removed'], 4)
        self.assertEqual(result['total_cost_usd'], '2.50')
        self.assertTrue(all('request_id' not in r and 'session_id' not in r for r in public['session_rows']))
        self.assertEqual(sum(r['tokens'] for r in public['rows'])-sum(r['tokens'] for r in public['session_rows']), 3*1120)

    def test_empty_session_detail_is_available(self):
        from app import public_data
        data = dict(collected_at='2026-02-02', host_timezone='UTC', providers=[], proxy_request_logs=[], usage_daily_rollups=[])
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            build([('one', data)], out, render_figures=False)
            self.assertEqual(public_data(out)['session_rows'], [])

    def test_models_and_costs_remain_separate(self):
        requests = [row(request_id=str(i), provider_id='_session', date='2026-09-01',
                        model=model, total_cost_usd=cost, session_provider='ollama')
                    for i, (model, cost) in enumerate([('a', '0.10'), ('b', '0.20')])]
        data = dict(collected_at='2026-09-02', host_timezone='UTC', providers=[], proxy_request_logs=requests, usage_daily_rollups=[])
        with tempfile.TemporaryDirectory() as directory, patch('update.render'):
            result = build([('one', data)], Path(directory))
        self.assertEqual(result['total_cost_usd'], '0.30')
        self.assertEqual([r['model'] for r in result['by_model']], ['a', 'b'])

    def test_cache_semantics(self):
        self.assertEqual(fresh(row()), 400)
        self.assertEqual(fresh(row(input_token_semantics=1)), 300)
        self.assertEqual(fresh(row(input_token_semantics=2)), 1000)
        self.assertEqual(fresh(row(app_type='claude')), 1000)

    def test_proxy_duplicate_and_window(self):
        p = row(data_source='proxy')
        self.assertEqual(len(effective([p, row()])), 1)
        self.assertEqual(len(effective([p, row(created_at=1601)])), 2)
        self.assertEqual(len(effective([row(data_source='proxy', status_code=500), row()])), 2)

    def test_claude_desktop_dedup(self):
        p = row(app_type='claude-desktop', data_source='proxy')
        s = row(app_type='claude', data_source='session_log')
        self.assertEqual(effective([p, s]), [p])

    def test_codex_unknown_cache_write(self):
        p = row(data_source='proxy')
        self.assertEqual(effective([p, row(cache_creation_tokens=0)]), [p])

    def test_cross_host_identity_and_rollup_accounting(self):
        request = row(request_id='same', provider_id='_session', date='2026-09-01', total_cost_usd='0')
        rollup = row(provider_id='_session', date='2026-08-01', request_count=3, total_cost_usd='0')
        data = dict(collected_at='2026-09-02', host_timezone='UTC', providers=[], proxy_request_logs=[request], usage_daily_rollups=[rollup])
        with tempfile.TemporaryDirectory() as directory, patch('update.render'):
            result = build([('one', data), ('two', data)], Path(directory))
        # One unique request plus two retained rollups; identical aggregates are flagged.
        self.assertEqual(result['cross_host_duplicates_removed'], 1)
        self.assertEqual(result['matching_cross_host_rollups_retained'], 1)
        self.assertEqual(result['requests'], 7)
        self.assertEqual(result['total_tokens'], 3 * (400 + 600 + 100 + 20))

    def test_request_dates_and_hours_follow_dashboard_host(self):
        instant = int(datetime(2026, 1, 1, 23, 30).timestamp())
        request = row(request_id='remote-request', provider_id='_session',
                      created_at=instant, date='2026-01-02', local_datetime='2026-01-02 08:30:00',
                      total_cost_usd='0.25', session_key='session')
        rollup = row(provider_id='_session', date='2026-01-02', request_count=2,
                     total_cost_usd='0.50')
        data = dict(collected_at='2026-01-03', host_timezone='Asia/Shanghai', providers=[],
                    proxy_request_logs=[request], usage_daily_rollups=[rollup])
        with tempfile.TemporaryDirectory() as directory, patch('update.render'):
            build([('remote', data)], Path(directory))
            with (Path(directory) / 'hourly_usage.csv').open(newline='') as source:
                hourly = list(csv.DictReader(source))
            with (Path(directory) / 'session_daily_usage.csv').open(newline='') as source:
                sessions = list(csv.DictReader(source))
        self.assertEqual({(r['date'], r['hour'], r['grain']) for r in hourly},
                         {('2026-01-01', '23', 'request'), ('2026-01-02', 'Unknown hour', 'rollup')})
        self.assertEqual(sum(int(r['total_tokens']) for r in hourly), 2 * 1120)
        self.assertEqual(sum(float(r['cost_usd']) for r in hourly), 0.75)
        self.assertEqual(sessions[0]['date'], '2026-01-01')
        self.assertEqual(json.loads(sessions[0]['hours']), {'23': 1120})


if __name__ == '__main__':
    unittest.main()
