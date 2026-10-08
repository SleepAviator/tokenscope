"""Read-only, incremental live usage probe, executable in memory on each host.

Only numeric usage, opaque identities, and provider/model labels leave the host.
Native logs report usage in batches; the intervals are passive estimates rather
than server-side decoding timers. This module deliberately has no app imports so
the same code can run over SSH without installing TokenScope on the source.
"""
import configparser
import datetime
import hashlib
import json
import math
import os
import pathlib
import sys
import threading
import time

_dll_dir = pathlib.Path(sys.prefix) / 'Library' / 'bin'
_dll_handle = os.add_dll_directory(str(_dll_dir)) if os.name == 'nt' and _dll_dir.is_dir() else None
import sqlite3

WINDOW_SECONDS = 5
DISCOVERY_SECONDS = 5
BOOTSTRAP_BYTES = 512 * 1024
READ_BYTES = 4 * 1024 * 1024
TRACE_ROWS = 4096
TRACE_SESSIONS = 128
CODEX_ACTIVITY_GAP = 'Codex sessions without readable native token usage were detected'


def opaque(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def label(value, default='Unknown'):
    """Labels are source metadata, never instructions or arbitrary log text."""
    if not isinstance(value, str) or not value.strip():
        return default
    return ''.join(c for c in value.strip() if c.isprintable())[:120] or default


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    elif isinstance(value, str):
        result = datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    else:
        raise ValueError('Invalid timestamp')
    if not math.isfinite(result):
        raise ValueError('Invalid timestamp')
    return result


def output_count(usage):
    value = usage.get('output_tokens') if isinstance(usage, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError('Invalid output usage')
    # reasoning_output_tokens / thinking_tokens are already part of this total.
    return value


def usage_signature(usage):
    if not isinstance(usage, dict):
        raise ValueError('Invalid usage record')
    return tuple(usage.get(k, 0) for k in ('input_tokens', 'cached_input_tokens', 'output_tokens'))


def valid_session(value):
    return isinstance(value, str) and value.strip() and value.strip().lower() not in {'unknown', 'none', 'null', 'default'}


class NativeState:
    """A small parser state per growing log; no message content is retained."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.sid = None
        self.provider = 'Provider unreported'
        self.model = 'Unknown'
        self.inherited_until = 0
        self.last_input = self.start = self.end = None
        self.calls = set()
        self.last_activity = 0
        self.pending_status = None
        self.pending_at = 0
        self.pending_key = None
        self.pending_live = False
        self.native_usage = None
        self.native_at = 0
        self.native_mirror_pending = False
        self.last_total = None
        self.claude_response = None

    def identity(self):
        return opaque(self.runtime, self.sid) if valid_session(self.sid) else None

    def pending(self, now):
        status = self.pending_status
        if not status and self.last_input is not None and not self.calls:
            status = 'awaiting_usage'
        if not status or self.last_activity < now - 120 and not (status == 'awaiting_usage' and self.pending_live):
            return None
        if status != 'awaiting_usage' and self.pending_at < now - WINDOW_SECONDS:
            return None
        return dict(runtime=self.runtime, session_key=self.identity(), provider=self.provider,
                    model=self.model, status=status)

    def reset(self):
        self.last_input = self.start = self.end = None
        self.calls.clear()
        self.pending_status = None
        self.pending_key = None
        self.pending_live = False
        self.claude_response = None

    def sample(self, probe, key, usage, start, end, observed, emit):
        count = output_count(usage)
        if not count:
            return
        if not emit or observed < probe.started_at or end is not None and end < probe.now - WINDOW_SECONDS:
            probe.remember(key, observed)
            return
        if key in probe.seen and key not in probe.samples:
            return
        if start is None or end is None or end <= start:
            self.pending_status, self.pending_at = 'missing_timing', observed
            self.pending_key = key
            probe.unmeasured[key] = (self.identity(), observed)
            probe.remember(key, observed)
            return
        sample = dict(event_key=key, runtime=self.runtime, session_key=self.identity(),
                      provider=self.provider, model=self.model, output_tokens=count,
                      start=start, end=end, basis='native_log')
        probe.record(sample, observed)
        self.pending_status = None
        self.pending_key = None
        self.pending_live = False

    def codex(self, entry, ordinal, probe, emit):
        kind, p = entry.get('type'), entry.get('payload', {})
        if not isinstance(p, dict):
            raise ValueError('Invalid native payload')
        if kind == 'session_meta':
            if self.sid is None:
                self.sid = p.get('id')
                self.provider = label(p.get('model_provider'), 'Provider unreported')
                inherited = p.get('subagent_history_start_ordinal', 0)
                if isinstance(inherited, int) and inherited >= 0:
                    self.inherited_until = inherited
            # Forked files embed another session_meta from their parent history.
            return
        if ordinal <= self.inherited_until:
            return
        stamp = timestamp(entry.get('timestamp'))
        self.last_activity = stamp
        typ = p.get('type')
        if kind == 'turn_context':
            self.model = label(p.get('model'))
        elif kind == 'event_msg' and typ == 'task_started':
            self.reset()
            # Task starts also delimit subagent turns, whose incoming message
            # is an agent_message rather than a user response_item.
            self.last_input = stamp
            self.pending_live = emit and stamp >= probe.started_at
        elif kind == 'event_msg' and typ in ('task_complete', 'turn_aborted') or kind == 'compacted':
            self.reset()
        elif kind == 'response_item':
            if typ == 'message' and p.get('role') == 'user' or typ in ('function_call_output', 'custom_tool_call_output'):
                self.calls.discard(p.get('call_id'))
                self.last_input = stamp if not self.calls else None
                self.pending_live = self.last_input is not None and emit and stamp >= probe.started_at
            elif typ in ('reasoning', 'function_call', 'custom_tool_call') or typ == 'message' and p.get('role') == 'assistant':
                if self.start is None:
                    self.start = self.last_input if not self.calls else None
                self.end = stamp
                if emit and stamp >= probe.started_at:
                    self.pending_live = True
                if typ in ('function_call', 'custom_tool_call'):
                    self.calls.add(p.get('call_id'))
        elif kind == 'token_usage_record':
            # thread_id, not root session_id, identifies a real subagent.
            if p.get('thread_id') and valid_session(self.sid) and p['thread_id'] != self.sid:
                return
            usage = p.get('usage', {})
            rid = p.get('response_id')
            key = opaque('codex', 'response', rid) if valid_session(rid) else opaque('codex', self.sid, int(stamp), usage_signature(usage))
            self.sample(probe, key, usage, self.start, self.end, stamp, emit)
            self.native_usage, self.native_at = usage_signature(usage), stamp
            self.native_mirror_pending = True
            if self.end is None or self.last_input is None or self.last_input <= self.end:
                self.last_input = None
            self.start = self.end = None
        elif kind == 'event_msg' and typ == 'token_count':
            info = p.get('info') or {}
            if not isinstance(info, dict) or not isinstance(info.get('last_token_usage'), dict):
                return
            usage = info['last_token_usage']
            total = info.get('total_token_usage')
            total_signature = tuple(sorted(total.items())) if isinstance(total, dict) else None
            if total_signature is not None and total_signature == self.last_total:
                return
            self.last_total = total_signature
            # Modern logs write the usage record and its legacy mirror separately.
            if usage_signature(usage) == self.native_usage and self.native_mirror_pending:
                # The mirror can arrive after a long tool run. Consume it once
                # without destroying the next model's tool-result boundary.
                self.native_mirror_pending = False
                return
            key = opaque('codex', self.sid, 'legacy', total_signature if total_signature is not None else (int(stamp), usage_signature(usage)))
            self.sample(probe, key, usage, self.start, self.end, stamp, emit)
            if self.end is None or self.last_input is None or self.last_input <= self.end:
                self.last_input = None
            self.start = self.end = None

    def claude(self, entry, ordinal, probe, emit):
        typ = entry.get('type')
        if typ not in ('user', 'assistant'):
            return
        stamp = timestamp(entry.get('timestamp'))
        self.last_activity = stamp
        sid = entry.get('sessionId')
        if valid_session(sid):
            agent = entry.get('agentId')
            self.sid = sid + ':agent:' + agent if valid_session(agent) else sid
        message = entry.get('message', {})
        if not isinstance(message, dict):
            raise ValueError('Invalid Claude message')
        content = message.get('content', [])
        blocks = content if isinstance(content, list) else []
        if typ == 'user':
            for block in blocks:
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    self.calls.discard(block.get('tool_use_id'))
            self.last_input = stamp if not self.calls else None
            self.pending_live = self.last_input is not None and emit and stamp >= probe.started_at
            self.pending_status = None
            self.pending_key = None
            return
        self.model = label(message.get('model'))
        self.provider = label(entry.get('model_provider') or entry.get('provider'), self.provider)
        mid = message.get('id')
        if not valid_session(mid):
            self.pending_status, self.pending_at = 'ambiguous_identity', stamp
            return
        if mid != self.claude_response:
            self.claude_response = mid
            self.start = self.last_input if not self.calls else None
        self.end = stamp
        if emit and stamp >= probe.started_at:
            self.pending_live = True
        for block in blocks:
            if isinstance(block, dict) and block.get('type') == 'tool_use':
                self.calls.add(block.get('id'))
        usage = message.get('usage')
        if isinstance(usage, dict) and usage.get('output_tokens', 0) > 0:
            self.sample(probe, opaque('claude', 'response', mid), usage, self.start, self.end, stamp, emit)
            # Duplicate streamed blocks with the same message ID keep the same
            # input boundary; the next response needs a new user/tool result.
            if self.last_input is None or self.last_input <= self.end:
                self.last_input = None
        elif entry.get('isApiErrorMessage'):
            # A failed request is not a session still generating tokens.
            # Earlier measurable responses stay in the probe's sample cache.
            self.reset()
        elif message.get('stop_reason') is not None:
            self.last_input = None
            self.pending_status = None
            self.pending_key = None
            self.pending_live = False


class Tail:
    def __init__(self, path, runtime, probe, bootstrap_size=None):
        self.path, self.runtime = path, runtime
        self.state = NativeState(runtime)
        self.offset = 0
        self.ordinal = 0
        self.identity = None
        self.issues = set()
        if bootstrap_size is not None:
            self.read(probe, emit=False, limit=bootstrap_size, bootstrap=True)

    def read(self, probe, emit=True, limit=None, bootstrap=False):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity is not None and (self.identity != identity or stat.st_size < self.offset):
            self.offset, self.ordinal, self.state = 0, 0, NativeState(self.runtime)
            self.issues.clear()
        self.identity = identity
        size = min(stat.st_size, limit) if limit is not None else stat.st_size
        with self.path.open('rb') as stream:
            if bootstrap and size > BOOTSTRAP_BYTES:
                # Preserve the authoritative Codex header without loading the
                # session history or retaining conversation text.
                if self.runtime == 'codex':
                    line = stream.readline(READ_BYTES)
                    if line.endswith(b'\n'):
                        self.consume(line, probe, False)
                stream.seek(size - BOOTSTRAP_BYTES)
                stream.readline()  # align to a complete record
                self.offset = stream.tell()
                # An inherited prefix may exceed the tail; thread_id checks
                # remain authoritative for usage records in that case.
                self.ordinal = self.state.inherited_until + 1
            stream.seek(self.offset)
            budget = size - self.offset if bootstrap else min(size - self.offset, READ_BYTES)
            data = stream.read(max(0, budget))
        complete = data.rfind(b'\n') + 1
        if complete:
            for line in data[:complete].splitlines():
                self.consume(line, probe, emit)
            self.offset += complete
        elif len(data) >= READ_BYTES:
            self.issues.add(self.runtime.title() + ' native record exceeds the supported size')
            self.offset += len(data)

    def consume(self, line, probe, emit):
        ordinal = self.ordinal
        self.ordinal += 1
        if not line.strip():
            return
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError('Invalid native record')
            parser = self.state.codex if self.runtime == 'codex' else self.state.claude
            parser(entry, ordinal, probe, emit)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, OverflowError):
            self.issues.add(self.runtime.title() + ' native log contains a malformed complete record')


class Probe:
    def __init__(self, config):
        self.config = dict(config)
        self.started_at = self.now = time.time()
        self.database = pathlib.Path(config.get('database', '~/.cc-switch/cc-switch.db')).expanduser()
        native = str(config.get('codex_native_tps', 'true')).lower()
        if native not in configparser.ConfigParser.BOOLEAN_STATES:
            raise ValueError('codex_native_tps must be true or false')
        self.codex_enabled = configparser.ConfigParser.BOOLEAN_STATES[native]
        port = config.get('codex_otel_port', '0')
        if isinstance(port, bool) or not str(port).isdigit() or not 0 <= int(port) <= 65535:
            raise ValueError('codex_otel_port must be an integer from 0 to 65535')
        self.telemetry_port = int(port)
        if self.telemetry_port and not self.codex_enabled:
            raise ValueError('Codex telemetry requires codex_native_tps to be enabled')
        self.telemetry = None
        self.telemetry_sessions = set()
        self.telemetry_activity = {}
        self.codex_home = pathlib.Path(config.get('codex_home') or os.environ.get('CODEX_HOME') or '~/.codex').expanduser()
        roots = config.get('codex_session_roots')
        self.codex_roots = ([pathlib.Path(p.strip()).expanduser() for p in roots.split(';') if p.strip()]
                            if roots is not None else [self.codex_home / 'sessions', self.codex_home / 'archived_sessions'])
        self.claude_roots = [pathlib.Path(config.get('claude_projects', '~/.claude/projects')).expanduser()]
        support = pathlib.Path(os.environ.get('APPDATA', str(pathlib.Path.home() / 'Library/Application Support')))
        self.claude_roots.extend(support / app / 'claude-code-sessions' for app in ('Claude-3p', 'Claude'))
        self.tails = {}
        self.known = {}
        self.samples = {}
        self.seen = {}
        self.unmeasured = {}
        self.proxy_seen = {}
        self.providers = {}
        self.proxy_pending = {}
        self.last_discovery = float('-inf')
        self.last_provider_read = float('-inf')
        self.index_name = None
        self.db_bootstrapped = False
        self.discovery_issues = []
        self.trace_identity = None
        self.trace_cursor = None
        self.trace_active = {}
        self.trace_overflow = False
        self.discover(bootstrap=True)
        self.read_database(bootstrap=True)
        self.read_codex_activity()
        if self.telemetry_port:
            from live_telemetry import CodexTelemetryReceiver
            self.telemetry = CodexTelemetryReceiver(port=self.telemetry_port)

    def remember(self, key, stamp):
        self.seen[key] = stamp

    def record(self, sample, observed):
        if (sample['runtime'] == 'codex' and sample['basis'] != 'codex_otel'
                and sample['session_key'] in self.telemetry_sessions):
            return  # One authority per session; unjoinable native IDs cannot double count.
        key = sample['event_key']
        existing = self.samples.get(key)
        if existing is not None:
            if existing['session_key'] != sample['session_key']:
                self.discovery_issues.append('A duplicated response has conflicting session identities')
                del self.samples[key]
                self.remember(key, observed)
                return
            if sample['output_tokens'] <= existing['output_tokens']:
                return
        elif key in self.seen and key not in self.unmeasured:
            return
        self.remember(key, observed)
        self.samples[key] = sample
        self.unmeasured.pop(key, None)

    def discover(self, bootstrap=False):
        if not bootstrap and self.now - self.last_discovery < DISCOVERY_SECONDS:
            return
        self.last_discovery = self.now
        self.discovery_issues = []
        categories = [('claude', self.claude_roots)]
        if self.codex_enabled:
            categories.append(('codex', self.codex_roots))
        else:
            self.discovery_issues.append('Codex native timing is disabled')
        for runtime, roots in categories:
            exists = False
            for root in roots:
                if not root.is_dir():
                    continue
                exists = True
                try:
                    for path in root.glob('**/*.jsonl'):
                        stat = path.stat()
                        old = self.known.get(path)
                        if path not in self.tails:
                            if bootstrap:
                                # Baseline all filenames, restore only recently
                                # touched parser states. No usage is replayed.
                                if stat.st_mtime >= self.now - 300:
                                    self.tails[path] = Tail(path, runtime, self, stat.st_size)
                            elif old is not None:
                                if (stat.st_dev, stat.st_ino) != old[:2] or stat.st_size != old[2]:
                                    limit = old[2] if (stat.st_dev, stat.st_ino) == old[:2] and stat.st_size >= old[2] else None
                                    self.tails[path] = Tail(path, runtime, self, limit)
                            else:
                                self.tails[path] = Tail(path, runtime, self)
                        self.known[path] = (stat.st_dev, stat.st_ino, stat.st_size)
                except OSError as error:
                    self.discovery_issues.append(runtime.title() + ' native logs could not be inspected (' + type(error).__name__ + ')')
            if not exists:
                self.discovery_issues.append(runtime.title() + ' native logs are unavailable')

    def read_codex_activity(self):
        """Identify observable Codex activity without inventing token counts."""
        if not self.codex_enabled:
            return []
        path = self.codex_home / 'logs_2.sqlite'
        try:
            if not path.is_file():
                self.trace_identity = self.trace_cursor = None
                return []  # This optional tracing database does not exist on every client.
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            wal = path.with_name(path.name + '-wal')
            shm = path.with_name(path.name + '-shm')
            if wal.exists() and not shm.exists():
                return ['Codex native activity cannot be read without its existing WAL shared memory']
            # A WAL database with no sidecars may otherwise create them even
            # through mode=ro. Immutable inspection cannot create sidecars.
            uri = path.resolve().as_uri() + ('?mode=ro' if wal.exists() else '?mode=ro&immutable=1')
            db = sqlite3.connect(uri, uri=True, timeout=.1)
            try:
                db.execute('PRAGMA query_only=ON')
                columns = {row[1]: row for row in db.execute('PRAGMA table_info(logs)')}
                required = {'id', 'ts', 'ts_nanos', 'target', 'thread_id'}
                if required - columns.keys() or columns['id'][2].upper() != 'INTEGER' or columns['id'][5] != 1 or sum(bool(row[5]) for row in columns.values()) != 1:
                    return ['Codex native activity schema is unsupported']
                sql = 'SELECT id,ts,ts_nanos,target,thread_id FROM logs WHERE id > ? ORDER BY id LIMIT ?'
                plan = db.execute('EXPLAIN QUERY PLAN ' + sql, (0, TRACE_ROWS)).fetchall()
                if not any('INTEGER PRIMARY KEY' in row[3] for row in plan):
                    return ['Codex native activity needs an existing integer primary-key index']
                latest = db.execute('SELECT id FROM logs ORDER BY id DESC LIMIT 1').fetchone()
                latest_id = latest[0] if latest else 0
                if self.trace_identity != identity or self.trace_cursor is None or latest_id < self.trace_cursor:
                    # Startup, replacement and reconnection baseline the current
                    # high-water mark. Retained tracing is never live activity.
                    self.trace_identity, self.trace_cursor = identity, latest_id
                    return []
                rows = db.execute(sql, (self.trace_cursor, TRACE_ROWS)).fetchall()
                for rid, seconds, nanos, target, sid in rows:
                    self.trace_cursor = rid
                    if target != 'codex_core::stream_events_utils' or not valid_session(sid):
                        continue
                    if not isinstance(nanos, int) or isinstance(nanos, bool) or not 0 <= nanos < 1000000000:
                        raise ValueError('Invalid native activity timestamp')
                    stamp = timestamp(seconds) + nanos / 1000000000
                    if stamp >= self.started_at and stamp >= self.now - WINDOW_SECONDS:
                        self.trace_active[sid] = max(stamp, self.trace_active.get(sid, stamp))
                        if len(self.trace_active) > TRACE_SESSIONS:
                            del self.trace_active[min(self.trace_active, key=self.trace_active.get)]
                            self.trace_overflow = True
                return ['Codex native activity reader is catching up'] if self.trace_cursor < latest_id else []
            finally:
                db.close()
        except (OSError, sqlite3.Error) as error:
            return ['Codex native activity could not be read (' + type(error).__name__ + ')']
        except (ValueError, TypeError, OverflowError):
            return ['Codex native activity contains invalid metadata']

    def codex_activity_pending(self):
        measured = {}
        for sample in self.samples.values():
            if (sample['runtime'] == 'codex' and sample['basis'] in ('native_log', 'codex_otel')
                    and sample['end'] >= self.now - WINDOW_SECONDS):
                measured[sample['session_key']] = max(sample['end'], measured.get(sample['session_key'], 0))
        for sid, stamp in list(self.trace_active.items()):
            key = opaque('codex', sid)
            activity = self.telemetry_activity.get(key, {})
            readable = (measured.get(key, 0) >= stamp or activity.get('pending', False)
                        or activity.get('last_event_at', 0) >= stamp or any(
                tail.runtime == 'codex' and tail.state.sid == sid
                and (tail.state.pending_live or tail.state.last_activity >= stamp) for tail in self.tails.values()))
            if readable:
                del self.trace_active[sid]
        return [dict(runtime='codex', session_key=opaque('codex', sid), provider='Provider unreported',
                     model='Unknown', status='awaiting_usage') for sid, stamp in self.trace_active.items() if stamp >= self.now - 120]

    def read_database(self, bootstrap=False):
        issues = []
        if not self.database.is_file():
            return ['CC-Switch database is unavailable']
        try:
            db = sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA query_only=ON')
                columns = {r[1] for r in db.execute('PRAGMA table_info(proxy_request_logs)')}
                required = {'request_id', 'app_type', 'provider_id', 'model', 'output_tokens', 'created_at', 'status_code', 'data_source'}
                if required - columns:
                    return ['CC-Switch live request schema is unsupported']
                indexes = list(db.execute('PRAGMA index_list(proxy_request_logs)'))
                self.index_name = None
                for index in indexes:
                    name = index[1]
                    fields = list(db.execute('PRAGMA index_info("' + name.replace('"', '""') + '")'))
                    if fields and fields[0][2] == 'created_at':
                        self.index_name = name
                        break
                if self.index_name is None:
                    return ['CC-Switch live requests need an existing created_at index']
                if self.now - self.last_provider_read >= 30:
                    provider_columns = {r[1] for r in db.execute('PRAGMA table_info(providers)')}
                    if {'id', 'app_type', 'name'} <= provider_columns:
                        self.providers = {(r['id'], r['app_type']): label(r['name']) for r in db.execute('SELECT id,app_type,name FROM providers')}
                    self.last_provider_read = self.now
                selected = sorted(required | ({'session_id', 'duration_ms', 'latency_ms'} & columns))
                sql = ('SELECT ' + ','.join(selected) + ' FROM proxy_request_logs INDEXED BY "' + self.index_name.replace('"', '""') + '" '
                       "WHERE created_at >= ? AND (data_source='proxy' OR data_source IS NULL) AND status_code >= 200 AND status_code < 300")
                rows = list(db.execute(sql, (self.now - 10,)))
                for row in rows:
                    runtime = 'claude' if row['app_type'] in ('claude', 'claude-desktop') else label(row['app_type']).lower()
                    rid, stamp = row['request_id'], timestamp(row['created_at'])
                    if not valid_session(rid):
                        issues.append('A live proxy response has no stable request identity')
                        continue
                    key = opaque(runtime, 'response', rid[8:]) if rid.startswith('session:') else opaque(runtime, 'proxy', rid)
                    if bootstrap:
                        self.proxy_seen[key] = stamp
                        continue
                    if stamp < self.started_at or key in self.proxy_seen:
                        continue
                    self.proxy_seen[key] = stamp
                    # Native Codex IDs cannot be joined to arbitrary proxy IDs.
                    # Use the native authority, rather than counting both.
                    if runtime == 'codex' and self.codex_enabled and (self.telemetry is not None or any(p.is_dir() for p in self.codex_roots)):
                        continue
                    count = output_count(dict(row))
                    if count == 0:
                        continue
                    session = row['session_id'] if 'session_id' in row.keys() else None
                    linked = self.samples.get(key)
                    known = {tail.state.identity() for tail in self.tails.values() if tail.state.runtime == runtime and tail.state.identity() is not None}
                    skey = None
                    if linked is not None:
                        skey = linked['session_key']
                    elif key in self.unmeasured:
                        skey = self.unmeasured[key][0]
                    elif valid_session(session) and session not in (rid, 'session:' + rid):
                        candidate = opaque(runtime, session)
                        if candidate in known:
                            skey = candidate
                        elif runtime in ('codex', 'grokbuild') and session.startswith(runtime + '_') and len(session) > len(runtime) + 11:
                            # CC-Switch adds this prefix only to client-provided
                            # IDs. Its unprefixed UUID fallback is per request;
                            # the DB does not retain client_provided/source.
                            skey = opaque(runtime, session[len(runtime) + 1:])
                    duration = None
                    for field in ('duration_ms', 'latency_ms'):
                        value = row[field] if field in row.keys() else None
                        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
                            duration = value / 1000
                            break
                    provider = self.providers.get((row['provider_id'], row['app_type']), 'Unresolved provider')
                    if linked is not None:
                        if linked['provider'] == 'Provider unreported':
                            linked['provider'] = provider
                        continue  # retain native timing for the exact same ID
                    if duration is None:
                        self.proxy_pending[key] = (stamp, dict(runtime=runtime, session_key=skey, provider=provider,
                                                               model=label(row['model']), status='missing_timing'))
                        continue
                    # A proxy's elapsed timer includes its first-token wait. It
                    # is a passive completion interval, never pure decoding.
                    sample = dict(event_key=key, runtime=runtime, session_key=skey, provider=provider,
                                  model=label(row['model']), output_tokens=count,
                                  start=stamp - duration, end=stamp, basis='proxy_elapsed')
                    if key not in self.samples and (key not in self.seen or key in self.unmeasured):
                        self.record(sample, stamp)
                        for tail in self.tails.values():
                            if tail.state.pending_key == key:
                                tail.state.pending_status = None
                                tail.state.pending_key = None
            finally:
                db.close()
            self.db_bootstrapped = True
        except sqlite3.Error as error:
            issues.append('CC-Switch live requests could not be read (' + type(error).__name__ + ')')
        except (ValueError, TypeError, OverflowError):
            issues.append('CC-Switch live requests contain invalid usage or timing')
        return issues

    def snapshot(self, now=None):
        self.now = time.time() if now is None else now
        self.discover()
        issues = list(self.discovery_issues)
        pending = []
        for path, tail in list(self.tails.items()):
            try:
                tail.read(self)
                issues.extend(tail.issues)
            except FileNotFoundError:
                del self.tails[path]
            except OSError as error:
                issues.append(tail.runtime.title() + ' native log could not be read (' + type(error).__name__ + ')')
        issues.extend(self.read_database())
        issues.extend(self.read_codex_activity())
        if self.telemetry is not None:
            frame = self.telemetry.snapshot(self.now)
            self.telemetry_sessions = set(frame['covered_session_keys'])
            self.telemetry_activity = frame['session_activity']
            # Telemetry has response boundaries even for ephemeral forks. Once
            # it observes a session, retain one authority across both readers.
            for key, sample in list(self.samples.items()):
                if (sample['runtime'] == 'codex' and sample['basis'] != 'codex_otel'
                        and sample['session_key'] in self.telemetry_sessions):
                    del self.samples[key]
                    self.seen.pop(key, None)  # Permit the canonical telemetry copy to replace it.
            for sample in frame['samples']:
                self.record(sample, self.now)
            pending.extend(frame['pending_sessions'])
            issues.extend(frame['coverage']['issues'])
        for tail in self.tails.values():
            state = tail.state.pending(self.now)
            if state and not (state['runtime'] == 'codex' and state['session_key'] in self.telemetry_sessions):
                pending.append(state)
        self.proxy_pending = {key: value for key, value in self.proxy_pending.items() if value[0] >= self.now - WINDOW_SECONDS}
        pending.extend(value[1] for value in self.proxy_pending.values())
        activity_pending = self.codex_activity_pending()
        if self.trace_active or self.trace_overflow:
            issues.append(CODEX_ACTIVITY_GAP)
        pending.extend(activity_pending)
        self.samples = {key: sample for key, sample in self.samples.items() if sample['end'] >= self.now - WINDOW_SECONDS}
        self.seen = {key: stamp for key, stamp in self.seen.items() if stamp >= self.now - 600}
        self.unmeasured = {key: value for key, value in self.unmeasured.items() if value[1] >= self.now - WINDOW_SECONDS}
        self.proxy_seen = {key: stamp for key, stamp in self.proxy_seen.items() if stamp >= self.now - 60}
        # Identical copied logs must not inflate pending-session counts either.
        pending = list({(p['runtime'], p['session_key'], p['status']): p for p in pending}.values())
        issues.extend(self.discovery_issues)
        issues = sorted(set(issues))
        return dict(version=1, sampled_at=self.now, samples=list(self.samples.values()), pending_sessions=pending,
                    coverage=dict(status='partial' if issues or pending else 'complete', issues=issues))

    def close(self):
        if self.telemetry is not None:
            self.telemetry.close()


def run_probe(config):
    """Heartbeat until its controller closes stdin; no daemon or remote files."""
    stop = threading.Event()

    def watch_controller():
        while sys.stdin.buffer.read(1):
            pass
        stop.set()

    threading.Thread(target=watch_controller, name='live-probe-controller', daemon=True).start()
    probe = Probe(config)
    try:
        while not stop.is_set():
            began = time.monotonic()
            try:
                print(json.dumps(probe.snapshot(), separators=(',', ':')), flush=True)
            except BrokenPipeError:
                return
            stop.wait(max(0, 1 - (time.monotonic() - began)))
    finally:
        probe.close()


if __name__ == '__main__':
    run_probe(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {})
