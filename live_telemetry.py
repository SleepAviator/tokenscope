"""Bounded, RAM-only Codex OTLP log receiver for passive response estimates.

Raw OTLP packets are projected in the HTTP thread and immediately discarded.
Only counts, source timestamps, hashed identities, and allowlisted metadata reach
the worker. Traces, prompt bodies, tool output, and exception text are not used.
"""
from collections import Counter, OrderedDict
import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import queue
import re
import threading
import time
import zlib

WINDOW_SECONDS = 5
DEFAULT_PORT = 4319
MAX_REQUEST_BYTES = 1024 * 1024
MAX_EVENTS = 4096
MAX_QUEUE = 32
MAX_STATE = 1024
MAX_SESSIONS = 128
MAX_AGE_SECONDS = 1800
EXPORT_TIMEOUT_SECONDS = 30
MAX_COUNTER = (1 << 63) - 1

_LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/+ -]{0,119}\Z')
_INTEGER = re.compile(r'[0-9]{1,19}\Z')
_KINDS = {'response.created', 'response.completed'}
_FIELDS = {
    'event.name', 'event.kind', 'event.timestamp', 'conversation.id', 'thread.id', 'thread_id',
    'response.id', 'response_id', 'gen_ai.response.id',
    'output_token_count', 'output_tokens', 'usage.output_tokens',
    'gen_ai.usage.output_tokens', 'model', 'model.name', 'gen_ai.request.model',
    'model_provider', 'model.provider', 'gen_ai.provider.name', 'gen_ai.system',
    'duration_ms', 'success', 'error',
}


def opaque(*parts):
    # Match native-log identities so response IDs can deduplicate both sources.
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def _identity(value, *prefix):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256 or
            value.strip().lower() in {'unknown', 'none', 'null', 'default'}):
        return None
    return opaque(*prefix, value.strip())


def _label(value, default):
    if isinstance(value, str) and (re.match(r'^[A-Za-z]:[/\\]', value) or
                                   re.match(r'^[A-Za-z][A-Za-z0-9+.-]*://', value)):
        return default
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else default


def _integer(value):
    if isinstance(value, bool):
        raise ValueError('Invalid numeric telemetry')
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= MAX_COUNTER:
        raise ValueError('Invalid numeric telemetry')
    return value


def _attributes(items):
    if items is None:
        return {}
    if not isinstance(items, list) or len(items) > MAX_EVENTS:
        raise ValueError('Invalid OTLP attributes')
    result = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Invalid OTLP attribute')
        key = item.get('key')
        if key not in _FIELDS:
            continue
        value = item.get('value')
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError('Invalid OTLP attribute value')
        kind = next(iter(value))
        if kind not in {'stringValue', 'intValue', 'doubleValue', 'boolValue'} or key in result:
            raise ValueError('Invalid OTLP attribute value')
        # Failure text is used only as a presence flag, never retained even in
        # the allowlisted metadata projection.
        result[key] = bool(value[kind]) if key == 'error' else value[kind]
    return result


def _first(attributes, names):
    for name in names:
        if name in attributes:
            return attributes[name]
    return None


def _source_timestamp(record, attributes):
    nano = record.get('timeUnixNano')
    if nano is not None and _integer(nano) > 0:
        return _integer(nano) / 1_000_000_000
    # Codex's tracing log exporter currently carries the event timestamp as
    # an RFC3339 attribute while leaving the OTLP timestamp unset.
    value = attributes.get('event.timestamp')
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Missing source event timestamp')
    stamp = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Ambiguous source event timestamp')
    return stamp.timestamp()


def project_otlp(payload, received_at):
    """Return a numeric-only projection and counters; never return raw values."""
    if not isinstance(payload, dict) or not isinstance(payload.get('resourceLogs'), list):
        raise ValueError('Invalid OTLP log request')
    events, counts = [], Counter()
    resources = payload['resourceLogs']
    if len(resources) > MAX_EVENTS:
        raise ValueError('OTLP request exceeds supported event limit')
    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError('Invalid OTLP resource')
        metadata = resource.get('resource', {})
        if not isinstance(metadata, dict):
            raise ValueError('Invalid OTLP resource metadata')
        shared = _attributes(metadata.get('attributes'))
        scopes = resource.get('scopeLogs', [])
        if not isinstance(scopes, list) or len(scopes) > MAX_EVENTS:
            raise ValueError('Invalid OTLP scopes')
        for scope in scopes:
            if not isinstance(scope, dict):
                raise ValueError('Invalid OTLP scope')
            metadata = scope.get('scope', {})
            if not isinstance(metadata, dict):
                raise ValueError('Invalid OTLP scope metadata')
            scope_values = dict(shared)
            scope_values.update(_attributes(metadata.get('attributes')))
            records = scope.get('logRecords', [])
            if not isinstance(records, list):
                raise ValueError('Invalid OTLP log records')
            for record in records:
                counts['records_received'] += 1
                if counts['records_received'] > MAX_EVENTS:
                    raise ValueError('OTLP request exceeds supported event limit')
                if not isinstance(record, dict):
                    counts['invalid_records'] += 1
                    continue
                try:
                    attributes = dict(scope_values)
                    attributes.update(_attributes(record.get('attributes')))
                    kind = attributes.get('event.kind')
                    name = attributes.get('event.name')
                    websocket_request = name == 'codex.websocket_request'
                    if kind not in _KINDS and not websocket_request:
                        counts['other_records'] += 1
                        continue
                    session = _identity(_first(attributes, ('conversation.id', 'thread.id', 'thread_id')), 'codex')
                    # Some releases replace event.name with a Rust callsite.
                    # Exact response kinds plus a conversation still establish
                    # the Codex schema without accepting arbitrary log bodies.
                    if session is None and name != 'codex.sse_event':
                        counts['missing_identity_records'] += 1
                        continue
                    stamp = _source_timestamp(record, attributes)
                    source_stamp = stamp
                    if 'event.timestamp' in attributes:
                        counts['event_timestamp_fields'] += 1
                    if stamp <= 0 or stamp > received_at + 1:
                        raise ValueError('Invalid source event timestamp')
                    if websocket_request:
                        success = attributes.get('success')
                        if isinstance(success, str) and success in ('true', 'false'):
                            success = success == 'true'
                        if 'success' in attributes and not isinstance(success, bool):
                            raise ValueError('Invalid request success metadata')
                        if success is False or attributes.get('error'):
                            counts['failed_request_events'] += 1
                            continue
                        duration = _integer(attributes.get('duration_ms'))
                        if duration > MAX_AGE_SECONDS * 1000:
                            raise ValueError('Invalid request duration')
                        stamp -= duration / 1000
                        if stamp <= 0:
                            raise ValueError('Invalid request boundary')
                        kind = 'response.created'
                        counts['websocket_request_events'] += 1
                    response = _identity(_first(attributes, ('response.id', 'response_id', 'gen_ai.response.id')),
                                         'codex', 'response')
                    count = None
                    if kind == 'response.completed':
                        if 'output_token_count' in attributes:
                            counts['output_token_count_fields'] += 1
                        if 'usage.output_tokens' in attributes:
                            counts['usage_output_tokens_fields'] += 1
                        raw_count = _first(attributes, ('output_token_count', 'usage.output_tokens',
                                                       'gen_ai.usage.output_tokens', 'output_tokens'))
                        if raw_count is not None:
                            count = _integer(raw_count)
                            counts['output_counts_received'] += 1
                    events.append(dict(kind=kind, session_key=session, response_key=response,
                                       stamp=stamp, source_event_at=source_stamp, output_tokens=count,
                                       start_origin='request' if websocket_request else 'response',
                                       provider=_label(_first(attributes, ('model_provider', 'model.provider',
                                                                         'gen_ai.provider.name', 'gen_ai.system')),
                                                       'Provider unreported'),
                                       model=_label(_first(attributes, ('model', 'model.name', 'gen_ai.request.model')),
                                                    'Unknown')))
                    if not websocket_request:
                        counts['created_events' if kind == 'response.created' else 'completed_events'] += 1
                except (ValueError, TypeError):
                    counts['invalid_records'] += 1
    return events, dict(counts)


class TelemetryState:
    """Single-worker response pairing with bounded, sanitized state."""
    def __init__(self, started_at=None):
        self.started_at = time.time() if started_at is None else float(started_at)
        self.active = OrderedDict()
        self.orphans = OrderedDict()
        self.samples = OrderedDict()
        self.seen = OrderedDict()
        self.sessions = OrderedDict()
        self.gaps = OrderedDict()
        self.delivery_issues = OrderedDict()
        self.counters = Counter()
        self.last_received = None
        self.last_event = None
        self.last_delay = None

    def count(self, key, amount=1):
        self.counters[key] = min(MAX_COUNTER, self.counters[key] + amount)

    def _bounded(self, mapping, key, value, maximum=MAX_STATE):
        mapping[key] = value
        mapping.move_to_end(key)
        if len(mapping) > maximum:
            mapping.popitem(last=False)
            self.count('state_entries_dropped')

    def _gap(self, key, event, status, now, issue):
        pending = dict(runtime='codex', session_key=event['session_key'],
                       provider=event['provider'], model=event['model'], status=status)
        self._bounded(self.gaps, key, (now, pending, issue))

    @staticmethod
    def _pair_key(event):
        return ('response', event['response_key']) if event['response_key'] else ('session', event['session_key'])

    def _matching_key(self, mapping, event):
        key = self._pair_key(event)
        if key in mapping:
            return key
        # A provider may expose the response ID only at completion. Use a
        # single matching conversation when one boundary lacks an ID; never
        # pair two different explicit IDs or multiple concurrent candidates.
        if event['session_key'] is None:
            return None
        candidates = [other for other, value in mapping.items()
                      if value['session_key'] == event['session_key'] and
                      (value['response_key'] is None or event['response_key'] is None)]
        return candidates[0] if len(candidates) == 1 else None

    def _complete(self, start, event, now):
        tokens = event['output_tokens']
        key = event['response_key'] or opaque('codex', 'otel', event['session_key'], event['stamp'], tokens)
        if key in self.seen:
            self.count('duplicate_events')
            return
        self._bounded(self.seen, key, now)
        if tokens is None:
            self._gap(key, event, 'awaiting_usage', now, 'Codex telemetry completion has no output-token count')
            self.count('missing_usage_responses')
            return
        if tokens == 0:
            return
        if start is None or event['stamp'] <= start['stamp']:
            self._gap(key, event, 'missing_timing', now, 'Codex telemetry output has no reliable response interval')
            self.count('missing_timing_responses')
            return
        if start['session_key'] != event['session_key']:
            self._gap(key, event, 'ambiguous_identity', now, 'Codex telemetry response identities conflict')
            self.count('identity_conflicts')
            return
        if event['session_key'] is not None:
            self.sessions[event['session_key']]['authoritative'] = True
        if event['stamp'] < now - WINDOW_SECONDS:
            self._bounded(self.delivery_issues, key, (now,
                          'Codex telemetry arrived after the five-second window; output was excluded'))
            self.count('late_responses')
            self.gaps.pop(key, None)
            return
        sample = dict(event_key=key, runtime='codex', session_key=event['session_key'],
                      provider=event['provider'], model=event['model'], output_tokens=tokens,
                      start=start['stamp'], end=event['stamp'], basis='codex_otel')
        self._bounded(self.samples, key, sample)
        self.gaps.pop(key, None)
        self.count('measured_responses')
        if event['session_key'] is None:
            self._gap(key, event, 'ambiguous_identity', now, 'Codex telemetry output has no reliable session identity')

    def process(self, events, received_at, diagnostics=None):
        self.last_received = received_at
        self.count('export_batches_received')
        for key, amount in (diagnostics or {}).items():
            self.count(key, amount)
        # Exporters may send records out of order inside an otherwise intact
        # batch. Only source timestamps establish intervals.
        for event in sorted(events, key=lambda value: (value['stamp'], value['kind'] != 'response.created')):
            stamp = event['stamp']
            activity_stamp = event.get('source_event_at', stamp)
            self.last_event = max(activity_stamp, self.last_event or activity_stamp)
            self.last_delay = max(0, received_at - self.last_event)
            if stamp < self.started_at:
                self.count('startup_events_ignored')
                continue
            session = event['session_key']
            if session is not None:
                previous = self.sessions.get(session, {})
                self._bounded(self.sessions, session,
                              dict(last_event_at=max(activity_stamp, previous.get('last_event_at', activity_stamp)),
                                   authoritative=previous.get('authoritative', False)), MAX_SESSIONS)
            pair = self._pair_key(event)
            if pair[1] is None:
                self._gap(('identity', stamp), event, 'ambiguous_identity', received_at,
                          'Codex telemetry output has no reliable session identity')
                self.count('missing_identity_responses')
                continue
            if event['kind'] == 'response.created':
                created_key = opaque('codex', 'otel_created', session, event['response_key'], stamp)
                if created_key in self.seen:
                    self.count('duplicate_events')
                    continue
                self._bounded(self.seen, created_key, received_at)
                if session is not None:
                    self.sessions[session]['authoritative'] = True
                old_key = self._matching_key(self.active, event)
                old = self.active.get(old_key)
                if old is not None:
                    old_origin = old.get('start_origin', 'response')
                    new_origin = event.get('start_origin', 'response')
                    if old_origin != new_origin and ((old_origin == 'request' and stamp >= old['stamp']) or
                                                     (new_origin == 'request' and stamp <= old['stamp'])):
                        # A later response.created refines a sent request;
                        # retain the request start, including first-token wait.
                        earliest = old if old_origin == 'request' else event
                        refined = dict(earliest, response_key=event['response_key'] or old['response_key'])
                        self.active.pop(old_key)
                        refined_key = self._pair_key(refined)
                        self._bounded(self.active, refined_key, refined, MAX_SESSIONS)
                        continue
                    if old['stamp'] == stamp:
                        self.count('duplicate_events')
                        continue
                    # Without response IDs two overlapping requests cannot be
                    # paired reliably. Preserve the gap instead of guessing.
                    self._gap(pair, event, 'missing_timing', received_at,
                              'Codex telemetry has overlapping responses without stable response identities')
                    self.active.pop(old_key, None)
                    self._bounded(self.active, pair, dict(event, ambiguous=True))
                    continue
                orphan_key = self._matching_key(self.orphans, event)
                orphan = self.orphans.get(orphan_key)
                if orphan is not None and stamp < orphan['stamp']:
                    self.orphans.pop(orphan_key)
                    self.seen.pop(orphan['response_key'] or opaque('codex', 'otel', orphan['session_key'],
                                                                 orphan['stamp'], orphan['output_tokens']), None)
                    self._complete(event, orphan, received_at)
                else:
                    self._bounded(self.active, pair, event, MAX_SESSIONS)
            else:
                completion_key = event['response_key'] or opaque('codex', 'otel', session, stamp,
                                                                  event['output_tokens'])
                if completion_key in self.seen:
                    self.count('duplicate_events')
                    continue
                start_key = self._matching_key(self.active, event)
                start = self.active.pop(start_key, None)
                if start is not None and start.get('ambiguous'):
                    start = None
                if start is None:
                    self._bounded(self.orphans, pair, event, MAX_SESSIONS)
                self._complete(start, event, received_at)
        self._prune(received_at)

    def _prune(self, now):
        self.samples = OrderedDict((key, value) for key, value in self.samples.items()
                                   if value['end'] >= now - WINDOW_SECONDS)
        self.gaps = OrderedDict((key, value) for key, value in self.gaps.items()
                                if value[0] >= now - WINDOW_SECONDS)
        self.delivery_issues = OrderedDict((key, value) for key, value in self.delivery_issues.items()
                                           if value[0] >= now - WINDOW_SECONDS)
        self.orphans = OrderedDict((key, value) for key, value in self.orphans.items()
                                   if value['stamp'] >= now - WINDOW_SECONDS)
        self.seen = OrderedDict((key, value) for key, value in self.seen.items() if value >= now - MAX_AGE_SECONDS)
        self.sessions = OrderedDict((key, value) for key, value in self.sessions.items()
                                    if value['last_event_at'] >= now - 600)
        expired = [key for key, value in self.active.items() if value['stamp'] < now - MAX_AGE_SECONDS]
        for key in expired:
            value = self.active.pop(key)
            self._gap(key, value, 'awaiting_usage', now, 'Codex telemetry response timed out before usage was reported')
            self.count('pending_responses_expired')

    def snapshot(self, now=None):
        now = time.time() if now is None else float(now)
        self._prune(now)
        fresh = self.last_received is not None and now - self.last_received <= EXPORT_TIMEOUT_SECONDS
        pending = [dict(runtime='codex', session_key=value['session_key'], provider=value['provider'],
                        model=value['model'], status='awaiting_usage') for value in self.active.values()]
        pending.extend(value[1].copy() for value in self.gaps.values())
        pending = list({(value['session_key'], value['status']): value for value in pending}.values())
        issues = {value[2] for value in self.gaps.values()}
        issues.update(value[1] for value in self.delivery_issues.values())
        if not fresh:
            issues.add('Waiting for a recent Codex telemetry export; side-chat coverage is unavailable')
        if self.counters['state_entries_dropped']:
            issues.add('Codex telemetry exceeded its bounded in-memory state')
        if self.counters['queue_batches_dropped']:
            issues.add('Codex telemetry export queue dropped a batch')
        if self.counters['invalid_records'] or self.counters['missing_identity_records']:
            issues.add('Codex telemetry contains unsupported response metadata')
        activity = {key: dict(last_event_at=value['last_event_at'],
                              pending=any(event['session_key'] == key for event in self.active.values()))
                    for key, value in self.sessions.items() if fresh and value['authoritative']}
        return dict(version=1, sampled_at=now, samples=list(self.samples.values()), pending_sessions=pending,
                    coverage=dict(status='partial' if issues or pending else 'complete', issues=sorted(issues)),
                    covered_session_keys=list(activity), session_activity=activity,
                    telemetry=dict(last_received_at=self.last_received, last_event_at=self.last_event,
                                   export_delay_seconds=self.last_delay, fresh=fresh,
                                   diagnostics=dict(self.counters)))


class CodexTelemetryReceiver:
    """One HTTP ingestion thread and one worker; no persistence or HTTP logs."""
    def __init__(self, host='127.0.0.1', port=DEFAULT_PORT, clock=time.time):
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError('Codex telemetry must bind a loopback address')
        if ':' in host:
            raise ValueError('This Codex telemetry receiver requires IPv4 loopback')
        self.clock = clock
        self.state = TelemetryState(clock())
        self.lock = threading.Lock()
        self.queue = queue.Queue(MAX_QUEUE)
        self.stopping = threading.Event()
        self.worker_failed = False
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, _format, *_args):
                pass

            def send_error(self, code, message=None, explain=None):
                self._reply(code)

            def do_GET(self):
                self._reply(405)

            def _reply(self, status):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', '2')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(b'{}')
                self.close_connection = True

            def do_POST(self):
                self.connection.settimeout(2)
                if self.path != '/v1/logs' or not ipaddress.ip_address(self.client_address[0]).is_loopback:
                    self._reply(404)
                    return
                content_type = self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                if content_type != 'application/json':
                    self._reply(415)
                    return
                try:
                    length = _integer(self.headers.get('Content-Length'))
                    if not 0 < length <= MAX_REQUEST_BYTES:
                        self._reply(413)
                        return
                    if self.headers.get('Transfer-Encoding'):
                        self._reply(400)
                        return
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError('Incomplete OTLP request')
                    encoding = self.headers.get('Content-Encoding', 'identity').lower()
                    if encoding == 'gzip':
                        decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
                        body = decoder.decompress(body, MAX_REQUEST_BYTES + 1)
                        if len(body) > MAX_REQUEST_BYTES or not decoder.eof or decoder.unused_data:
                            raise ValueError('Unsupported compressed OTLP request')
                    elif encoding != 'identity':
                        self._reply(415)
                        return
                    received = receiver.clock()
                    events, diagnostics = project_otlp(json.loads(body), received)
                    del body
                except (ValueError, TypeError, RecursionError, UnicodeError, zlib.error, TimeoutError):
                    with receiver.lock:
                        receiver.state.count('invalid_requests')
                    self._reply(400)
                    return
                try:
                    receiver.queue.put_nowait((events, received, diagnostics))
                except queue.Full:
                    with receiver.lock:
                        receiver.state.count('queue_batches_dropped')
                    self._reply(503)
                    return
                self._reply(200)

        class Server(HTTPServer):
            def get_request(self):
                request, address = super().get_request()
                # Bound request-line/header reads too, before Handler.do_POST.
                request.settimeout(2)
                return request, address

            def handle_error(self, _request, _client_address):
                # The standard server prints request exception text to stderr.
                with receiver.lock:
                    receiver.state.count('http_connection_errors')

        self.server = Server((host, port), Handler)
        self.port = self.server.server_address[1]
        self.worker = threading.Thread(target=self._work, name='tokenscope-otel-worker')
        self.http_thread = threading.Thread(target=self.server.serve_forever,
                                            kwargs={'poll_interval': .1}, name='tokenscope-otel-http')
        self.worker.start()
        self.http_thread.start()

    def _work(self):
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                events, received, diagnostics = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    self.state.process(events, received, diagnostics)
            except (ValueError, TypeError, KeyError):
                with self.lock:
                    self.state.count('worker_processing_errors')
                    self.worker_failed = True
            finally:
                self.queue.task_done()

    def snapshot(self, now=None):
        with self.lock:
            result = self.state.snapshot(self.clock() if now is None else now)
            if self.worker_failed:
                result['coverage']['status'] = 'partial'
                result['coverage']['issues'].append('Codex telemetry worker could not process a numeric event')
            return result

    def close(self):
        if self.stopping.is_set():
            return
        self.server.shutdown()
        self.server.server_close()
        self.stopping.set()
        self.http_thread.join(3)
        self.worker.join(3)
        if self.http_thread.is_alive() or self.worker.is_alive():
            raise RuntimeError('Codex telemetry worker did not stop')
