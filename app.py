#!/usr/bin/env python3
"""Foreground LAN dashboard. Run python app.py; Ctrl+C stops all local workers."""
import argparse
import configparser
from contextlib import redirect_stdout
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
import threading
import time
import webbrowser
from urllib.parse import urlsplit

from live_meter import LiveMeter

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
APP_SUPPORT = Path.home() / 'Library' / 'Application Support' / 'TokenScope'
ASSETS = {'/': ('web.html', 'text/html'), '/web.js': ('web.js', 'text/javascript'),
          '/session_usage.js': ('session_usage.js', 'text/javascript'),
          '/response_speed.js': ('response_speed.js', 'text/javascript'),
          '/i18n.js': ('i18n.js', 'text/javascript'),
          '/web.css': ('web.css', 'text/css')}


def disabled_live_snapshot():
    return {'enabled': False, 'status': 'disabled', 'window_seconds': 5,
            'total_tps': None, 'average_tps': None, 'contributing_sessions': 0,
            'sources': [], 'sessions': [], 'pending_sessions': [], 'issues': []}


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
    responses = None
    if summary.get('response_speed_available'):
        responses = []
        path = folder / 'response_speed.csv'
        if path.exists():
            with path.open(encoding='utf-8', newline='') as stream:
                for row in csv.DictReader(stream):
                    responses.append({k: row[k] for k in ('date', 'hour', 'host', 'app', 'model', 'duration_source')} |
                                     {'output_tokens': int(row['output_tokens'])} |
                                     {k: float(row[k]) if row.get(k) else None for k in ('duration_ms', 'tps', 'first_token_ms')})
    return {'generated_at': summary['generated_at_utc'], 'rows': rows, 'hourly_rows': hourly_rows, 'session_rows': session_rows,
            'response_rows': responses, 'host_date': summary.get('host_date'), 'host_timezone': summary.get('host_timezone'),
            'sources': {name: {'collected_at': a['collected_at'], 'timezone': a['timezone']}
                        for name, a in summary['sources'].items()},
            'caveats': [s for s in summary['caveats'] if not s.startswith('TPS')]}


def public_snapshot(result):
    """Project the shared aggregation directly; no CSV round trip or disk cache."""
    summary = result['summary']
    def totals(rows, hourly=False):
        fields = ('date', 'hour', 'host', 'app', 'model', 'cost_usd') if hourly else ('date', 'host', 'app', 'model', 'cost_usd')
        return [{k: row[k] for k in fields} |
                {'tokens': int(row['total_tokens']), 'requests': int(row['requests'])} for row in rows]
    sessions = [{k: row[k] for k in ('date', 'host', 'app', 'session_key', 'model', 'cost_usd')} |
                {'session_title': row.get('session_title', ''), 'hours': row.get('hours', {})} |
                {k: int(row[k]) for k in ('requests', 'fresh_input_tokens', 'cache_read_tokens', 'cache_creation_tokens', 'output_tokens')} |
                {'tokens': int(row['total_tokens'])} |
                {k: float(row.get(k) or 0) for k in ('tps_count', 'tps_sum', 'tps_max', 'native_tps_count', 'native_tps_sum', 'native_tps_max')}
                for row in result['session_daily']]
    responses = [{k: row[k] for k in ('date', 'hour', 'host', 'app', 'model', 'duration_source')} |
                 {'output_tokens': int(row['output_tokens'])} |
                 {k: None if row.get(k) in (None, '') else float(row[k]) for k in ('duration_ms', 'tps', 'first_token_ms')}
                 for row in result['responses']]
    return {'generated_at': summary['generated_at_utc'], 'rows': totals(result['daily']),
            'hourly_rows': totals(result['hourly'], hourly=True), 'session_rows': sessions,
            'response_rows': responses, 'host_date': summary['host_date'], 'host_timezone': summary['host_timezone'],
            'sources': {name: {'collected_at': item['collected_at'], 'timezone': item['timezone']}
                        for name, item in summary['sources'].items()},
            'caveats': [s for s in summary['caveats'] if not s.startswith('TPS')]}


def collect_snapshot(config, fingerprint, previous=None):
    """Keep one successful source snapshot per machine in this worker's RAM."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import sqlite3
    from update import fetch, aggregate
    cfg = configparser.ConfigParser(interpolation=None)
    if not cfg.read(config, encoding='utf-8'):
        raise ValueError('Cannot read source configuration')
    items = [(s[7:], dict(cfg[s])) for s in cfg.sections() if s.startswith('source:')]
    if not items:
        raise ValueError('No source sections configured')
    previous = previous if previous and previous.get('config_hash') == fingerprint else {}
    snapshots = previous.get('source_snapshots', {})
    snapshots = {name: snapshots[name] for name, _ in items if name in snapshots}
    failures = []
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
    data = public_snapshot(aggregate(sources))
    old = previous.get('data') or {}
    legacy = []
    for name, _ in items:
        if name not in failures:
            data['sources'][name]['status'] = 'fresh'
            continue
        if name not in snapshots and name in old.get('sources', {}):
            legacy.append(name)
            data['rows'].extend(r for r in old.get('rows', []) if r['host'] == name)
            prior_hours = [r for r in old.get('hourly_rows', []) if r['host'] == name]
            data['hourly_rows'].extend(prior_hours or [dict(r, hour='Unknown hour') for r in old.get('rows', []) if r['host'] == name])
            data['session_rows'].extend(r for r in (old.get('session_rows') or []) if r['host'] == name)
            data['response_rows'].extend(r for r in (old.get('response_rows') or []) if r['host'] == name)
            data['sources'][name] = dict(old['sources'][name])
        source = data['sources'].setdefault(name, {'collected_at': None, 'timezone': None})
        source['status'] = 'stale' if source['collected_at'] else 'unavailable'
    data['warnings'] = [f'{name}: disconnected or collection failed. ' +
                        ('Showing last successful data.' if data['sources'][name]['status'] == 'stale'
                         else 'No cached data available; totals are incomplete.')
                        for name, _ in items if name in failures]
    if legacy:
        data['warnings'].append('Older cached data retained for offline sources; cross-machine deduplication cannot be rechecked until they reconnect.')
    return {'config_hash': fingerprint, 'data': data, 'source_snapshots': snapshots}


MAX_WORKER_FRAME = 256 * 1024 * 1024


def write_worker_frame(stream, value):
    payload = json.dumps(value, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_WORKER_FRAME:
        raise ValueError('Dashboard result exceeds the RAM transport limit')
    stream.write(str(len(payload)).encode('ascii') + b'\n')
    stream.write(payload)
    stream.flush()


def read_worker_frame(stream):
    header = stream.readline(32)
    if not header:
        return None
    if not isinstance(header, bytes) or not header.endswith(b'\n') or not header[:-1].isdigit():
        raise ValueError('Invalid dashboard worker frame')
    size = int(header)
    if not 0 < size <= MAX_WORKER_FRAME:
        raise ValueError('Invalid dashboard worker frame size')
    payload = bytearray()
    while len(payload) < size:
        chunk = stream.read(min(65536, size - len(payload)))
        if not chunk:
            raise ValueError('Truncated dashboard worker frame')
        payload.extend(chunk)
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('Invalid dashboard worker result')
    return value


def memory_worker(config, cache=None):
    """Persistent cancellable producer; retained history and results never hit disk."""
    fingerprint = hashlib.sha256(config.read_bytes()).hexdigest()
    previous = json.loads(cache.read_text(encoding='utf-8')) if cache and cache.exists() else None
    output = sys.stdout.buffer
    for line in sys.stdin.buffer:
        if line != b'refresh\n':
            raise ValueError('Unknown dashboard worker command')
        try:
            if hashlib.sha256(config.read_bytes()).hexdigest() != fingerprint:
                raise ValueError('Configuration changed; restart the server')
            with redirect_stdout(sys.stderr):
                state = collect_snapshot(config, fingerprint, previous)
            write_worker_frame(output, {'config_hash': fingerprint, 'data': state['data']})
            previous = state
        except (OSError, RuntimeError, ValueError) as error:
            print(f'Historical refresh failed: {error}', file=sys.stderr, flush=True)
            write_worker_frame(output, {'error': 'Collection failed. Previous data retained; see diagnostics.'})


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
    def __init__(self, config, interval, cache=None):
        self.config, self.cache = config, cache
        self.fingerprint = hashlib.sha256(config.read_bytes()).hexdigest()
        self.interval = interval_value(interval)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.process = None
        self.readers = []
        self.collecting = False
        self.worker_failed = False
        self.data = None
        self.version = 0
        self.error = None
        self.started_at = None
        self.next_run = next_boundary(time.time(), self.interval)
        self.csrf = secrets.token_urlsafe(32)
        if cache and cache.exists():
            saved = json.loads(cache.read_text(encoding='utf-8'))
            if saved.get('config_hash') == self.fingerprint:
                self.data = saved['data']
                self.version = 1
        self.thread = threading.Thread(target=self.loop, name='usage-scheduler')

    def refresh(self):
        with self.lock:
            if self.collecting or self.stop.is_set():
                return False
            self.started_at = time.time()
            self.error = None
            if self.process and (self.worker_failed or self.process.poll() is not None):
                stop_process(self.process)
                self._close_pipes(self.process)
                self.process = None
            try:
                if self.process is None:
                    command = [sys.executable]
                    if not getattr(sys, 'frozen', False):
                        command.append(str(ROOT / 'app.py'))
                    command += ['--worker', '--config', str(self.config)]
                    if self.cache:
                        command += ['--cache', str(self.cache)]
                    self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                    start_new_session=os.name == 'posix')
                    self.worker_failed = False
                    reader = threading.Thread(target=self._receive, args=(self.process,), name='usage-ram-reader')
                    self.readers = [r for r in self.readers if r.is_alive()]
                    self.readers.append(reader)
                    reader.start()
                self.collecting = True
                self.process.stdin.write(b'refresh\n')
                self.process.stdin.flush()
                return True
            except OSError as error:
                self.collecting = False
                self.worker_failed = True
                self.error = 'Collection failed. Previous data retained; see diagnostics.'
                print(f'Cannot request historical refresh: {error}', file=sys.stderr, flush=True)
                return False

    @staticmethod
    def _close_pipes(process):
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass  # The exited worker may already have closed its pipe.

    def _receive(self, process):
        try:
            while not self.stop.is_set():
                result = read_worker_frame(process.stdout)
                if result is None:
                    raise RuntimeError('Dashboard worker stopped')
                if 'error' not in result and (result.get('config_hash') != self.fingerprint or
                                             not isinstance(result.get('data'), dict)):
                    raise ValueError('Invalid dashboard worker result')
                with self.lock:
                    if process is not self.process or self.stop.is_set():
                        return
                    if not self.collecting:
                        raise ValueError('Unrequested dashboard worker result')
                    self.collecting = False
                    if 'error' in result:
                        self.error = 'Collection failed. Previous data retained; see diagnostics.'
                    else:
                        self.data = result['data']
                        self.version += 1
                        self.error = None
        except (OSError, RuntimeError, ValueError) as error:
            if not self.stop.is_set():
                with self.lock:
                    if process is self.process:
                        self.worker_failed = True
                        self.collecting = False
                        self.error = 'Collection failed. Previous data retained; see diagnostics.'
                print(f'Dashboard RAM worker unavailable: {error}', file=sys.stderr, flush=True)

    def set_interval(self, value):
        with self.lock:
            self.interval = interval_value(value)
            self.next_run = next_boundary(time.time(), self.interval)

    def status(self):
        with self.lock:
            return {'refreshing': self.collecting, 'interval': self.interval, 'storage': 'memory',
                    'next_run': self.next_run, 'started_at': self.started_at,
                    'error': self.error, 'version': self.version, 'csrf': self.csrf,
                    'server_time': time.time(), 'timezone': str(datetime.now().astimezone().tzinfo)}

    def loop(self):
        self.refresh()
        while not self.stop.wait(.25):
            with self.lock:
                now = time.time()
                if now >= self.next_run:
                    self.next_run = next_boundary(now, self.interval)
                    self.refresh()  # busy runs skip boundaries; no accumulated queue

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Historical scheduler did not stop')
        with self.lock:
            process, readers = self.process, list(self.readers)
            self.process = None
            self.collecting = False
        if process:
            stop_process(process)
            self._close_pipes(process)
        for reader in readers:
            reader.join(timeout=4)
            if reader.is_alive():
                raise RuntimeError('Historical RAM reader did not stop')


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

    def loopback_peer(self):
        # Host is supplied by the client; the connection address establishes locality.
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback

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
        if path == '/api/live':
            if not self.loopback_peer():
                return self.respond(403, {'error': 'Live meter is available only over loopback'})
            meter = getattr(self.server, 'live_meter', None)
            return self.respond(200, meter.snapshot() if meter else disabled_live_snapshot())
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
    else:
        default_config = ROOT / 'config.ini'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=default_config)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--cache', type=Path,
                        help='Optional read-only startup snapshot; refreshes stay in RAM')
    parser.add_argument('--open-browser', action='store_true',
                        help='Open the local dashboard in the default browser after startup')
    parser.add_argument('--live-meter', action='store_true',
                        help='Monitor live output TPS for the native menu bar (loopback API only)')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = args.config.expanduser().resolve()
    cache = args.cache.expanduser().resolve() if args.cache else None
    if args.worker:
        memory_worker(config, cache)
        return
    collector = Collector(config, args.interval, cache)
    server = None
    live_meter = None
    collector_started = False
    timer = None
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.daemon_threads = True
        server.collector = collector
        live_meter = LiveMeter(config) if args.live_meter else None
        server.live_meter = live_meter
        collector.thread.start()
        collector_started = True
        if live_meter:
            live_meter.start()
        print(f'Usage dashboard: http://127.0.0.1:{server.server_port}/', flush=True)
        print(f'LAN / Tailscale: http://<this-machine-LAN-or-Tailscale-IP>:{server.server_port}/ (trusted networks only; no authentication).', flush=True)
        print('Ctrl+C stops the server and local collection/SSH processes.', flush=True)
        if args.open_browser:
            url = f'http://127.0.0.1:{server.server_port}/'
            timer = threading.Timer(1, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        print('\nStopping…', flush=True)
    finally:
        if timer:
            timer.cancel()
        try:
            if live_meter:
                live_meter.close()
        finally:
            try:
                if collector_started:
                    collector.close()
            finally:
                if server:
                    server.server_close()
        print('Stopped.', flush=True)


if __name__ == '__main__':
    main()
