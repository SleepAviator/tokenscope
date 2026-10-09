"""Live, passive response-window estimates; independent of historical accounting."""
import configparser
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import zlib

WINDOW_SECONDS = 5
HEARTBEAT_TIMEOUT = 3
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
PROBE_OPTIONS = ('database', 'codex_session_roots', 'codex_home', 'claude_projects',
                 'codex_native_tps', 'codex_otel_port')
BOOTSTRAP = "import sys,zlib;exec(compile(zlib.decompress(sys.stdin.buffer.read(int(sys.stdin.buffer.readline()))), '<tokenscope-live>', 'exec'), {'__name__':'tokenscope_live_probe'})"


def probe_payload(cfg):
    """Ship both readers in memory; source machines need no installed package."""
    telemetry = (ROOT / 'live_telemetry.py').read_text(encoding='utf-8')
    source = (ROOT / 'live_probe.py').read_text(encoding='utf-8')
    options = {key: cfg[key] for key in PROBE_OPTIONS if key in cfg}
    prefix = ("import sys,types\n"
              "_telemetry = types.ModuleType('live_telemetry')\n"
              "sys.modules['live_telemetry'] = _telemetry\n"
              "exec(compile(" + repr(telemetry) + ", '<tokenscope-telemetry>', 'exec'), _telemetry.__dict__)\n")
    return zlib.compress((prefix + source + '\nrun_probe(' + repr(options) + ')\n').encode('utf-8'), 9)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid live telemetry number')
    return float(value)


def _label(value, default='Unknown'):
    if not isinstance(value, str):
        return default
    return ''.join(c for c in value if c.isprintable())[:200] or default


def validate_packet(packet):
    """Allowlist the remote projection rather than forwarding arbitrary JSON."""
    if not isinstance(packet, dict) or packet.get('version') != 1:
        raise ValueError('Unsupported live telemetry protocol')
    sampled = _number(packet.get('sampled_at'))
    samples = packet.get('samples')
    pending = packet.get('pending_sessions', [])
    coverage = packet.get('coverage')
    if not isinstance(samples, list) or not isinstance(pending, list) or not isinstance(coverage, dict):
        raise ValueError('Invalid live telemetry snapshot')
    status = coverage.get('status')
    issues = coverage.get('issues', [])
    if status not in ('complete', 'partial') or not isinstance(issues, list) or any(not isinstance(s, str) for s in issues):
        raise ValueError('Invalid live telemetry coverage')
    clean = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError('Invalid response sample')
        start, end = _number(sample.get('start')), _number(sample.get('end'))
        tokens = sample.get('output_tokens')
        key, session = sample.get('event_key'), sample.get('session_key')
        if (not isinstance(key, str) or not key or len(key) > 256 or
                (session is not None and (not isinstance(session, str) or not session or len(session) > 256)) or
                isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0 or
                not end > start or end > sampled + 1):
            raise ValueError('Invalid response sample fields')
        clean.append({'event_key': key, 'session_key': session,
                      'runtime': _label(sample.get('runtime')),
                      'provider': _label(sample.get('provider')),
                      'model': _label(sample.get('model')),
                      'output_tokens': tokens, 'start': start, 'end': end,
                      'basis': _label(sample.get('basis'))})
    clean_pending = []
    for item in pending:
        if not isinstance(item, dict) or item.get('status') not in ('awaiting_usage', 'missing_timing', 'ambiguous_identity'):
            raise ValueError('Invalid pending session')
        session = item.get('session_key')
        if session is not None and (not isinstance(session, str) or not session or len(session) > 256):
            raise ValueError('Invalid pending session identity')
        clean_pending.append({'session_key': session, 'runtime': _label(item.get('runtime')),
                              'provider': _label(item.get('provider')), 'model': _label(item.get('model')),
                              'status': item['status']})
    return {'version': 1, 'sampled_at': sampled, 'samples': clean, 'pending_sessions': clean_pending,
            'coverage': {'status': status, 'issues': [_label(s) for s in issues]}}


def _canonical_responses(candidates, source_order, telemetry_sessions=()):
    """Share response-copy and session-authority rules between rate and arrival views."""
    selected, conflicts = [], []
    telemetry_sessions = set(telemetry_sessions) | {
        sample['session_key'] for copies in candidates.values() for _, sample, _ in copies
        if sample['runtime'] == 'codex' and sample['basis'] == 'codex_otel'
        and sample['session_key'] is not None}
    for key, copies in candidates.items():
        copies = [(name, sample, value) for name, sample, value in copies
                  if sample['runtime'] != 'codex' or sample['basis'] == 'codex_otel'
                  or sample['session_key'] not in telemetry_sessions]
        if not copies:
            continue
        identities = {sample['session_key'] for _, sample, _ in copies if sample['session_key'] is not None}
        if (len({(sample['runtime'], sample['output_tokens']) for _, sample, _ in copies}) != 1 or
                len(identities) > 1):
            conflicts.append((key, [name for name, _, _ in copies]))
            continue
        selected.append(min(copies, key=lambda c: (
            0 if c[1]['basis'] == 'codex_otel' else 2 if 'proxy' in c[1]['basis'].lower() else 1,
            not bool(c[1]['session_key']), source_order[c[0]])))
    return selected, conflicts


def project_sources(states, now):
    """Project one consistent window using source-relative ages, not host clock sync."""
    sources, pending, issues, candidates = [], [], [], {}
    fresh = 0
    partial = False
    for name, state in states.items():
        packet, received, error = state.get('packet'), state.get('received_at'), state.get('error')
        age = max(0.0, now - received) if received is not None else None
        source = {'name': name, 'status': 'initializing', 'age_seconds': age,
                  'total_tps': None, 'issues': []}
        sources.append(source)
        if packet is None or age >= HEARTBEAT_TIMEOUT:
            source['status'] = 'stale' if packet is not None else 'error' if error else 'initializing'
            source['issues'] = [error or ('Live reader heartbeat expired' if packet is not None else 'Waiting for live reader')]
            partial = True
            continue
        fresh += 1
        source['status'] = 'error' if error else 'fresh'
        source['total_tps'] = 0.0
        source['issues'] = list(packet['coverage']['issues']) + ([error] if error else [])
        partial |= bool(error or packet['coverage']['status'] == 'partial')
        frame_time = packet['sampled_at']
        for item in packet['pending_sessions']:
            pending.append(dict(item, source=name))
            partial = True
        for sample in packet['samples']:
            # Source clock offsets cancel; queue/transmission age only ages the frame.
            start = received + sample['start'] - frame_time
            end = received + sample['end'] - frame_time
            overlap = max(0.0, min(end, now) - max(start, now - WINDOW_SECONDS))
            if overlap <= 0:
                continue
            tps = sample['output_tokens'] * overlap / (end - start) / WINDOW_SECONDS
            candidates.setdefault(sample['event_key'], []).append((name, sample, tps))
    session_groups = {}
    unattributed = False
    source_by_name = {s['name']: s for s in sources}
    selected, conflicts = _canonical_responses(candidates, {name: i for i, name in enumerate(states)})
    for _, names in conflicts:
        partial = True
        for name in names:
            source_by_name[name]['issues'].append('Conflicting copies of a response excluded')
    for name, sample, tps in selected:
        source_by_name[name]['total_tps'] += tps
        session = sample['session_key']
        if session is None:
            unattributed = True
            partial = True
            source_by_name[name]['issues'].append('Output without a reliable session identity; average unavailable')
        group_key = (sample['runtime'], session) if session else (sample['event_key'], None)
        group = session_groups.setdefault(group_key, {'session_key': session, 'runtime': sample['runtime'],
                                                      'tps': 0.0, 'sources': set(), 'providers': set(), 'models': set()})
        group['tps'] += tps
        group['sources'].add(name)
        group['providers'].add(sample['provider'])
        group['models'].add(sample['model'])
    sessions = [{'session_key': g['session_key'], 'runtime': g['runtime'], 'tps': g['tps'],
                 'source': ', '.join(sorted(g['sources'])), 'provider': ', '.join(sorted(g['providers'])),
                 'model': ', '.join(sorted(g['models'])), 'status': 'measured'} for g in session_groups.values()]
    sessions.sort(key=lambda s: (-s['tps'], s['source'], s['runtime']))
    count = sum(s['session_key'] is not None for s in sessions)
    total = sum(s['tps'] for s in sessions) if fresh and (sessions or not partial) else None
    average = total / count if count and not unattributed else None
    for source in sources:
        source['issues'] = list(dict.fromkeys(source['issues']))
        has_pending = any(p['source'] == source['name'] for p in pending)
        if source['total_tps'] == 0 and (source['issues'] or has_pending):
            source['total_tps'] = None
        issues.extend(f"{source['name']}: {issue}" for issue in source['issues'])
    status = 'partial' if partial else 'complete'
    if not fresh:
        status = 'initializing' if all(s['status'] == 'initializing' for s in sources) else 'unavailable'
    return {'enabled': True, 'status': status,
            'sampled_at': datetime.now(timezone.utc).isoformat(), 'window_seconds': WINDOW_SECONDS,
            'total_tps': total, 'average_tps': average, 'contributing_sessions': count,
            'sources': sources, 'sessions': sessions, 'pending_sessions': pending,
            'issues': list(dict.fromkeys(issues))}


class ReceivedOutput:
    """Count new usage-report deltas at receipt time; keep sixty one-second bins in RAM.

    Readers already exclude startup replay and historical imports. Retained samples
    are cumulative per response, so repeated heartbeats and copies add no tokens.
    This measures reported output arriving, not unobserved network-stream chunks.
    """
    def __init__(self, now):
        self.seconds, self.responses, self.telemetry_sessions = {}, {}, {}
        self.next_second = math.floor(now)
        self.last_tick = now
        self.started_at = now

    def _second(self, second):
        return self.seconds.setdefault(second, {'tokens': 0, 'available': False, 'partial': True})

    def _exclude(self, response, reason='conflict'):
        if not response['excluded']:
            for second, delta in response['arrivals']:
                if second in self.seconds:
                    self.seconds[second]['tokens'] -= delta
                    self.seconds[second]['partial'] = True
                    self.seconds[second]['excluded'] = True
        if reason == 'native_authority':
            response['arrivals'].clear()
        response['excluded'] = reason

    def receive(self, states, now):
        candidates = {}
        for name, state in states.items():
            packet, received = state.get('packet'), state.get('received_at')
            if packet is None or now - received >= HEARTBEAT_TIMEOUT:
                continue
            for sample in packet['samples']:
                if sample['end'] < packet['sampled_at'] - WINDOW_SECONDS:
                    continue
                candidates.setdefault(sample['event_key'], []).append((name, sample, received))
                if sample['runtime'] == 'codex' and sample['basis'] == 'codex_otel' and sample['session_key']:
                    self.telemetry_sessions[sample['session_key']] = now
        # A later OTel copy may use a different response ID. Retract the native
        # copy from retained bars before giving that session one authority.
        for response in self.responses.values():
            if (not response['excluded'] and response['runtime'] == 'codex' and
                    response['basis'] != 'codex_otel' and response['session'] in self.telemetry_sessions):
                self._exclude(response, 'native_authority')
        selected, conflicts = _canonical_responses(candidates, {name: i for i, name in enumerate(states)},
                                                  self.telemetry_sessions)
        for key, _ in conflicts:
            previous = self.responses.get(key)
            if previous:
                self._exclude(previous)
                previous['last_seen'] = now
            else:
                self.responses[key] = dict(tokens=0, runtime=None, session=None, basis=None,
                                           arrivals=[], excluded='conflict', last_seen=now)
        for _, sample, received in selected:
            key = sample['event_key']
            previous = self.responses.setdefault(key, dict(tokens=0, runtime=sample['runtime'],
                session=sample['session_key'], basis=sample['basis'], arrivals=[], excluded=False, last_seen=now))
            previous['last_seen'] = now
            if previous['excluded'] == 'native_authority' and sample['basis'] == 'codex_otel':
                previous.update(tokens=0, basis='codex_otel', excluded=False)
            if previous['runtime'] is None:
                previous.update(runtime=sample['runtime'], session=sample['session_key'], basis=sample['basis'])
            if previous['excluded'] == 'native_authority':
                self._second(math.floor(received))['excluded'] = True
                continue
            if previous['runtime'] != sample['runtime'] or (previous['session'] and sample['session_key']
                                                          and previous['session'] != sample['session_key']):
                self._exclude(previous)
                self._second(math.floor(received))['excluded'] = True
                continue
            if previous['excluded'] == 'conflict':
                # As copied cumulative reports catch up, restore their original
                # receipt bins; reconciliation is not another token delivery.
                previous['excluded'] = False
                for second, delta in previous['arrivals']:
                    if second in self.seconds:
                        self.seconds[second]['tokens'] += delta
            if not previous['session']:
                previous['session'] = sample['session_key']
            previous['basis'] = sample['basis']
            delta = max(0, sample['output_tokens'] - previous['tokens'])
            previous['tokens'] = max(previous['tokens'], sample['output_tokens'])
            if delta:
                second = math.floor(received)
                self._second(second)['tokens'] += delta
                previous['arrivals'].append((second, delta))

    def finish(self, snapshot, now):
        current = math.floor(now)
        # Sleep or a stalled consumer creates gaps, never invented zero readings.
        continuous = now - self.last_tick <= 2.5
        for second in range(max(self.next_second, current - 60), current):
            bucket = self._second(second)
            if continuous:
                bucket['available'] = snapshot['total_tps'] is not None and second >= self.started_at
                bucket['partial'] = (snapshot['status'] != 'complete' or second < self.started_at or
                                     bucket.get('excluded', False))
        self.next_second, self.last_tick = current, now
        self.seconds = {s: b for s, b in self.seconds.items() if s >= current - 60}
        self.responses = {k: r for k, r in self.responses.items() if r['last_seen'] >= now - 600}
        self.telemetry_sessions = {s: stamp for s, stamp in self.telemetry_sessions.items() if stamp >= now - 600}
        for response in self.responses.values():
            response['arrivals'] = [(s, n) for s, n in response['arrivals'] if s >= current - 60]

    def snapshot(self, now):
        current = math.floor(now)
        return {'interval_seconds': 1, 'basis': 'reported_output', 'buckets': [
            {'age_seconds': max(0, now - second - 1),
             'tokens': bucket['tokens'] if bucket['tokens'] or bucket['available'] else None,
             'partial': bucket['partial']}
            for second, bucket in sorted(self.seconds.items()) if current - 60 <= second < current]}


def _stop_process(process):
    if process.stdin:
        try:
            process.stdin.close()  # Read-only remote probes exit on EOF.
        except (BrokenPipeError, OSError):
            pass
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=2)


class LiveMeter:
    def __init__(self, config, history=None):
        parser = configparser.ConfigParser(interpolation=None)
        if not parser.read(config, encoding='utf-8'):
            raise ValueError('Cannot read live source configuration')
        self.items = [(s[7:], dict(parser[s])) for s in parser.sections() if s.startswith('source:')]
        if not self.items:
            raise ValueError('No live source sections configured')
        for _, cfg in self.items:
            if cfg.get('transport') not in ('local', 'ssh'):
                raise ValueError('Unsupported live source transport')
            if cfg['transport'] == 'ssh' and (not cfg.get('host') or not cfg.get('python')):
                raise ValueError('SSH live sources require host and python')
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.events = queue.Queue(maxsize=max(4, len(self.items) * 4))
        self.states = {name: {'packet': None, 'received_at': None, 'error': None} for name, _ in self.items}
        self.processes = {}
        self.threads = []
        self.history = history
        self.received_output = ReceivedOutput(time.monotonic())

    def start(self):
        self.consumer = threading.Thread(target=self._consume, name='live-meter-consumer')
        self.consumer.start()
        for name, cfg in self.items:
            worker = threading.Thread(target=self._run_source, args=(name, cfg), name='live-meter-' + name)
            self.threads.append(worker)
            worker.start()

    def _send(self, event):
        while not self.stop.is_set():
            try:
                self.events.put(event, timeout=.2)
                return
            except queue.Full:
                continue  # Backpressure preserves snapshots; no silent dropped tokens.

    def _consume(self):
        next_sample = math.floor(time.monotonic()) + 1
        while not self.stop.is_set():
            try:
                name, received, packet, error = self.events.get(timeout=.2)
            except queue.Empty:
                pass
            else:
                with self.lock:
                    state = self.states[name]
                    if packet is not None:
                        state.update(packet=packet, received_at=received, error=None)
                        self.received_output.receive(self.states, received)
                    elif error:
                        state['error'] = error
            now = time.monotonic()
            if now >= next_sample:
                with self.lock:
                    snapshot = project_sources(self.states, now)
                    self.received_output.finish(snapshot, now)
                if self.history is not None:
                    self.history.observe(snapshot, wall=time.time(), monotonic=now)
                next_sample = math.floor(now) + 1

    def _command(self, cfg):
        if cfg['transport'] == 'local':
            return [sys.executable, '-u', '-c', BOOTSTRAP]
        python = cfg['python']
        if any(c in python for c in '\"\r\n') or cfg['host'].startswith('-'):
            raise ValueError('Invalid SSH live interpreter or destination')
        remote = '"' + python + '" -u -c "' + BOOTSTRAP + '"'
        return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                '-o', 'ServerAliveInterval=2', '-o', 'ServerAliveCountMax=2', cfg['host'], remote]

    def _run_source(self, name, cfg):
        if cfg['transport'] == 'local':
            self._run_local(name, cfg)
            return
        delay = 1
        while not self.stop.is_set():
            process = None
            stderr_thread = None
            try:
                payload = probe_payload(cfg)
                process = subprocess.Popen(self._command(cfg), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, start_new_session=(os.name == 'posix'))
                with self.lock:
                    self.processes[name] = process
                if self.stop.is_set():
                    return
                stderr_thread = threading.Thread(target=self._drain_errors, args=(name, process),
                                                 name='live-stderr-' + name)
                stderr_thread.start()
                process.stdin.write(str(len(payload)).encode('ascii') + b'\n' + payload)
                process.stdin.flush()
                while not self.stop.is_set():
                    line = process.stdout.readline(1024 * 1024 + 1)
                    if not line:
                        raise RuntimeError('Live reader disconnected')
                    if len(line) > 1024 * 1024:
                        raise ValueError('Live reader snapshot exceeded size limit')
                    packet = validate_packet(json.loads(line))
                    self._send((name, time.monotonic(), packet, None))
                    delay = 1
            except (OSError, RuntimeError, ValueError) as error:
                if not self.stop.is_set():
                    print(f'{name}: live reader unavailable ({type(error).__name__}); retrying.', file=sys.stderr, flush=True)
                    self._send((name, time.monotonic(), None, 'Live reader unavailable; reconnecting'))
            finally:
                if process is not None:
                    _stop_process(process)
                    if stderr_thread:
                        stderr_thread.join(timeout=3)
                    for stream in (process.stdout, process.stderr):
                        if stream:
                            stream.close()
                with self.lock:
                    self.processes.pop(name, None)
            self.stop.wait(delay)
            delay = min(delay * 2, 30)

    def _run_local(self, name, cfg):
        # A frozen server is an application, not a Python interpreter for -c.
        from live_probe import Probe
        delay = 1
        options = {key: cfg[key] for key in PROBE_OPTIONS if key in cfg}
        while not self.stop.is_set():
            probe = None
            try:
                probe = Probe(options)
                while not self.stop.is_set():
                    tick = time.monotonic()
                    packet = validate_packet(probe.snapshot())
                    self._send((name, time.monotonic(), packet, None))
                    delay = 1
                    self.stop.wait(max(0, tick + 1 - time.monotonic()))
            except (OSError, RuntimeError, ValueError) as error:
                if not self.stop.is_set():
                    print(f'{name}: local live reader unavailable ({type(error).__name__}); retrying.', file=sys.stderr, flush=True)
                    self._send((name, time.monotonic(), None, 'Local live reader unavailable; retrying'))
            finally:
                if probe is not None:
                    probe.close()
            self.stop.wait(delay)
            delay = min(delay * 2, 30)

    def _drain_errors(self, name, process):
        for line in iter(process.stderr.readline, b''):
            # Details remain in the operator's log, never in the public projection.
            text = line.decode('utf-8', errors='replace').rstrip()[:2000]
            print(f'{name}: live probe: {text}', file=sys.stderr, flush=True)

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            result = project_sources(self.states, now)
            result['received_output'] = self.received_output.snapshot(now)
            return result

    def close(self):
        self.stop.set()
        with self.lock:
            processes = list(self.processes.values())
        # Closing stdin first lets all probes leave their EOF watchers concurrently.
        for process in processes:
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
        for process in processes:
            if process.poll() is None:
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    process.terminate()
        for worker in self.threads:
            worker.join(timeout=7)
            if worker.is_alive():
                raise RuntimeError('Live source worker did not stop')
        if hasattr(self, 'consumer'):
            self.consumer.join(timeout=2)
            if self.consumer.is_alive():
                raise RuntimeError('Live snapshot consumer did not stop')
