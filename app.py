#!/usr/bin/env python3
"""Foreground LAN dashboard. Run python app.py; Ctrl+C stops all local workers."""
import argparse
import configparser
from contextlib import nullcontext
import csv
from datetime import datetime, timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from urllib.parse import urlsplit

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
APP_SUPPORT = Path.home() / 'Library' / 'Application Support' / 'TokenScope'
ASSETS = {'/': ('web.html', 'text/html'), '/web.js': ('web.js', 'text/javascript'),
          '/session_usage.js': ('session_usage.js', 'text/javascript'),
          '/i18n.js': ('i18n.js', 'text/javascript'),
          '/web.css': ('web.css', 'text/css')}


def next_boundary(now, interval):
    """Align to host-local midnight, not the previous collection completion."""
    local = datetime.fromtimestamp(now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = (local - midnight).total_seconds()
    target = midnight + timedelta(seconds=(int(seconds // interval) + 1) * interval)
    return min(target, midnight + timedelta(days=1)).timestamp()


def interval_value(value):
    if type(value) is not int or not 5 <= value <= 600:
        raise ValueError('Refresh interval must be an integer from 5 to 600 seconds')
    return value


def public_data(folder):
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    path = folder / 'daily_usage.csv'
    rows = []
    if path.exists():
        with path.open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                rows.append({k: row[k] for k in ('date', 'host', 'app', 'model', 'cost_usd')} |
                            {'tokens': int(row['total_tokens']), 'requests': int(row['requests'])})
    hourly_rows = []
    path = folder / 'hourly_usage.csv'
    if path.exists():
        with path.open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                hourly_rows.append({k: row[k] for k in ('date', 'hour', 'host', 'app', 'model', 'cost_usd')} |
                                   {'tokens': int(row['total_tokens']), 'requests': int(row['requests'])})
    session_rows = None
    if summary.get('session_detail_available'):
        session_rows = []
        path = folder / 'session_daily_usage.csv'
        if path.exists():
            with path.open(encoding='utf-8', newline='') as stream:
                for row in csv.DictReader(stream):
                    session_rows.append({k: row[k] for k in ('date', 'host', 'app', 'session_key', 'model', 'cost_usd')} |
                                        {'session_title': row.get('session_title', ''), 'hours': json.loads(row.get('hours') or '{}')} |
                                        {k: int(row[k]) for k in ('requests', 'fresh_input_tokens', 'cache_read_tokens', 'cache_creation_tokens', 'output_tokens')} |
                                        {'tokens': int(row['total_tokens'])} |
                                        {k: float(row.get(k) or 0) for k in ('tps_count', 'tps_sum', 'tps_max', 'native_tps_count', 'native_tps_sum', 'native_tps_max')})
    return {'generated_at': summary['generated_at_utc'], 'rows': rows, 'hourly_rows': hourly_rows, 'session_rows': session_rows,
            'sources': {name: {'collected_at': a['collected_at'], 'timezone': a['timezone']}
                        for name, a in summary['sources'].items()},
            'caveats': [s for s in summary['caveats'] if not s.startswith('TPS')]}


def collect_worker(config, destination, fingerprint, workdir=None):
    # Separate process makes blocking SSH and local parsing cancellable on Ctrl+C.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import sqlite3
    from update import fetch, build
    cfg = configparser.ConfigParser(interpolation=None)
    if not cfg.read(config, encoding='utf-8'):
        raise ValueError('Cannot read source configuration')
    items = [(s[7:], dict(cfg[s])) for s in cfg.sections() if s.startswith('source:')]
    if not items:
        raise ValueError('No source sections configured')
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    if destination.exists():
        saved = json.loads(destination.read_text(encoding='utf-8'))
        if saved.get('config_hash') == fingerprint:
            previous = saved
    snapshots = previous.get('source_snapshots', {})
    snapshots = {name: snapshots[name] for name, _ in items if name in snapshots}
    failures = []
    context = nullcontext(str(workdir)) if workdir else tempfile.TemporaryDirectory(prefix='usage-web-refresh-')
    with context as tmp:
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            jobs = {pool.submit(fetch, item): item[0] for item in items}
            for future in as_completed(jobs):
                name = jobs[future]
                try:
                    _, snapshots[name] = future.result()
                except (RuntimeError, OSError, ValueError, sqlite3.Error) as error:
                    failures.append(name)
                    print(f'{name}: refresh unavailable; keeping last successful data. {error}', file=sys.stderr, flush=True)
        sources = [(name, snapshots[name]) for name, _ in items if name in snapshots]
        build(sources, Path(tmp), render_figures=False)
        data = public_data(Path(tmp))
        old = previous.get('data') or {}
        legacy = []
        for name, _ in items:
            if name not in failures:
                data['sources'][name]['status'] = 'fresh'
                continue
            if name not in snapshots and name in old.get('sources', {}):
                # Upgrade an existing aggregate-only cache without dropping offline hosts.
                legacy.append(name)
                retained = [r for r in old.get('rows', []) if r['host'] == name]
                data['rows'].extend(retained)
                prior_hours = [r for r in old.get('hourly_rows', []) if r['host'] == name]
                data['hourly_rows'].extend(prior_hours or [dict(r, hour='Unknown hour') for r in retained])
                data['session_rows'].extend(r for r in (old.get('session_rows') or []) if r['host'] == name)
                data['sources'][name] = dict(old['sources'][name])
            source = data['sources'].setdefault(name, {'collected_at': None, 'timezone': None})
            source['status'] = 'stale' if source['collected_at'] else 'unavailable'
        data['warnings'] = [f'{name}: disconnected or collection failed. ' +
                            ('Showing last successful data.' if data['sources'][name]['status'] == 'stale'
                             else 'No cached data available; totals are incomplete.')
                            for name, _ in items if name in failures]
        if legacy:
            data['warnings'].append('Older cached data retained for offline sources; cross-machine deduplication cannot be rechecked until they reconnect.')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.web-', suffix='.tmp', delete=False) as stream:
            staged = Path(stream.name)
            json.dump({'config_hash': fingerprint, 'data': data, 'source_snapshots': snapshots}, stream)
        try:
            os.replace(staged, destination)  # atomic on the cache's filesystem
        finally:
            staged.unlink(missing_ok=True)


def stop_process(process):
    if process is None:
        return
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


class Collector:
    def __init__(self, config, interval, cache):
        self.config, self.cache = config, cache
        self.fingerprint = hashlib.sha256(config.read_bytes()).hexdigest()
        self.interval = interval_value(interval)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.process = None
        self.workdir = None
        self.data = None
        self.version = 0
        self.error = None
        self.started_at = None
        self.next_run = next_boundary(time.time(), self.interval)
        self.csrf = secrets.token_urlsafe(32)
        if cache.exists():
            saved = json.loads(cache.read_text(encoding='utf-8'))
            if saved.get('config_hash') == self.fingerprint:
                self.data = saved['data']
                self.version = 1
        elif config == ROOT / 'config.ini' and (ROOT / 'output/overall/summary.json').exists():
            self.data = public_data(ROOT / 'output/overall')
            self.version = 1
        self.thread = threading.Thread(target=self.loop, name='usage-scheduler')

    def refresh(self):
        with self.lock:
            if self.process is not None or self.stop.is_set():
                return False
            self.started_at = time.time()
            self.error = None
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.workdir = tempfile.TemporaryDirectory(prefix='usage-web-refresh-')
            command = [sys.executable]
            if not getattr(sys, 'frozen', False):
                command.append(str(ROOT / 'app.py'))
            command += ['--worker', '--config', str(self.config), '--cache', str(self.cache),
                        '--workdir', self.workdir.name]
            self.process = subprocess.Popen(command, start_new_session=os.name == 'posix')
            return True

    def set_interval(self, value):
        with self.lock:
            self.interval = interval_value(value)
            self.next_run = next_boundary(time.time(), self.interval)

    def status(self):
        with self.lock:
            return {'refreshing': self.process is not None, 'interval': self.interval,
                    'next_run': self.next_run, 'started_at': self.started_at,
                    'error': self.error, 'version': self.version, 'csrf': self.csrf,
                    'server_time': time.time(), 'timezone': str(datetime.now().astimezone().tzinfo)}

    def loop(self):
        self.refresh()
        while not self.stop.wait(.25):
            with self.lock:
                if self.process is not None and self.process.poll() is not None:
                    code = self.process.returncode
                    stop_process(self.process)
                    self.process = None
                    self.workdir.cleanup()
                    self.workdir = None
                    if code == 0:
                        try:
                            saved = json.loads(self.cache.read_text(encoding='utf-8'))
                            if saved['config_hash'] != self.fingerprint:
                                raise ValueError('Configuration changed; restart the server')
                            self.data = saved['data']
                            self.version += 1
                        except (OSError, ValueError, KeyError) as error:
                            print(f'Cache validation failed: {error}', file=sys.stderr, flush=True)
                            self.error = 'Result validation failed. Previous data retained; see terminal.'
                    else:
                        self.error = 'Collection failed. Previous data retained; see terminal for details.'
                now = time.time()
                if now >= self.next_run:
                    self.next_run = next_boundary(now, self.interval)
                    self.refresh()  # busy runs skip boundaries; no accumulated queue

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
        with self.lock:
            stop_process(self.process)
            self.process = None
            if self.workdir:
                self.workdir.cleanup()
                self.workdir = None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        pass  # collection progress/errors remain visible in the terminal

    def respond(self, status, body, kind='application/json'):
        payload = json.dumps(body).encode() if kind == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        host = urlsplit('http://' + self.headers.get('Host', '')).hostname
        if host == 'localhost':
            return True
        try:
            address = ipaddress.ip_address(host)
            # Tailscale uses CGNAT, which ipaddress intentionally does not mark private.
            return (address.is_private or address.is_loopback or
                    (address.version == 4 and address in ipaddress.ip_network('100.64.0.0/10')))
        except (ValueError, TypeError):
            return False

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {'error': 'Use a LAN or Tailscale IP address, or localhost'})
        path = urlsplit(self.path).path
        if path in ASSETS:
            filename, kind = ASSETS[path]
            return self.respond(200, (ROOT / filename).read_bytes(), kind)
        if path == '/api/status':
            return self.respond(200, self.server.collector.status())
        if path == '/api/data':
            with self.server.collector.lock:
                return self.respond(200, self.server.collector.data)
        self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.valid_host() or self.headers.get('X-Usage-CSRF') != self.server.collector.csrf
                or (origin and origin != 'http://' + self.headers.get('Host', ''))):
            return self.respond(403, {'error': 'Same-origin refresh control required'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024:
                raise ValueError('Invalid request size')
            body = json.loads(self.rfile.read(size))
            if self.path == '/api/interval':
                self.server.collector.set_interval(body['seconds'])
            elif self.path == '/api/refresh':
                self.server.collector.refresh()
            else:
                return self.respond(404, {'error': 'Not found'})
        except (ValueError, KeyError, TypeError) as error:
            return self.respond(400, {'error': str(error)})
        self.respond(200, self.server.collector.status())


def main():
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        default_config = APP_SUPPORT / 'config.ini'
        if not default_config.exists():
            shutil.copy2(ROOT / 'config.example.ini', default_config)
        default_cache = APP_SUPPORT / 'output' / 'web.json'
    else:
        default_config = ROOT / 'config.ini'
        default_cache = ROOT / 'output' / 'web.json'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=default_config)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--cache', type=Path, default=default_cache)
    parser.add_argument('--open-browser', action='store_true',
                        help='Open the local dashboard in the default browser after startup')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--workdir', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = args.config.expanduser().resolve()
    cache = args.cache.expanduser().resolve()
    if args.worker:
        collect_worker(config, cache, hashlib.sha256(config.read_bytes()).hexdigest(), args.workdir)
        return
    collector = Collector(config, args.interval, cache)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    server.collector = collector
    print(f'Usage dashboard: http://127.0.0.1:{server.server_port}/', flush=True)
    print(f'LAN / Tailscale: http://<this-machine-LAN-or-Tailscale-IP>:{server.server_port}/ (trusted networks only; no authentication).', flush=True)
    print('Ctrl+C stops the server and local collection/SSH processes.', flush=True)
    collector.thread.start()
    if args.open_browser:
        url = f'http://127.0.0.1:{server.server_port}/'
        timer = threading.Timer(1, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        print('\nStopping…', flush=True)
    finally:
        collector.close()
        server.server_close()
        print('Stopped.', flush=True)


if __name__ == '__main__':
    main()
