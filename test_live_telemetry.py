import gzip
import http.client
import json
import socket
import time
import unittest
from datetime import datetime, timezone

from live_meter import project_sources, validate_packet
from live_telemetry import (CodexTelemetryReceiver, MAX_EVENTS, MAX_REQUEST_BYTES,
                            MAX_SESSIONS, TelemetryState, opaque, project_otlp)

BASE = 1_790_000_000
SID = '01a11375-c881-71e1-8ac5-90adf49a464a'
SIDE = '01a11885-0a26-75c3-84bc-559378f21862'


def attribute(key, value):
    kind = 'boolValue' if isinstance(value, bool) else 'intValue' if isinstance(value, int) else 'stringValue'
    return dict(key=key, value={kind: str(value) if kind == 'intValue' else value})


def record(second, kind, sid=SID, tokens=None, response=None, **extra):
    values = {'event.name': 'codex.sse_event', 'event.kind': kind,
              'model': 'gpt-6.1-sol', 'model_provider': 'OpenAI', **extra}
    if sid is not None:
        values['conversation.id'] = sid
    if tokens is not None:
        values['output_token_count'] = tokens
    if response is not None:
        values['response_id'] = response
    return dict(timeUnixNano=str(int((BASE + second) * 1_000_000_000)),
                attributes=[attribute(key, value) for key, value in values.items()])


def packet(*records, shared=None):
    return dict(resourceLogs=[dict(resource=dict(attributes=[attribute(key, value)
                                                             for key, value in (shared or {}).items()]),
                                  scopeLogs=[dict(logRecords=list(records))])])


def websocket_request(second, sid=SID, duration='1', **extra):
    stamp = datetime.fromtimestamp(BASE + second, timezone.utc).isoformat()
    result = record(second, None, sid, **{'event.name': 'codex.websocket_request',
                                        'event.timestamp': stamp, 'duration_ms': duration, **extra})
    result['timeUnixNano'] = '0'
    result['attributes'] = [value for value in result['attributes'] if value['key'] != 'event.kind']
    return result


class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = TelemetryState(BASE)

    def feed(self, payload, now=6):
        events, diagnostics = project_otlp(payload, BASE + now)
        self.state.process(events, BASE + now, diagnostics)
        return self.state.snapshot(BASE + now)

    def test_three_sessions_total_and_average_include_side_chat(self):
        values = []
        for sid, tokens in ((SID, 300), (SIDE, 200), ('third', 100)):
            values.extend((record(1, 'response.created', sid), record(6, 'response.completed', sid, tokens)))
        result = self.feed(packet(*values))
        projected = project_sources({'Yienware': dict(packet=validate_packet(result),
                                                      received_at=BASE + 6, error=None)}, BASE + 6)
        self.assertEqual(projected['total_tps'], 120)
        self.assertEqual(projected['average_tps'], 40)
        self.assertEqual(projected['contributing_sessions'], 3)
        self.assertIn(opaque('codex', SIDE), result['covered_session_keys'])
        self.assertEqual(result['session_activity'][opaque('codex', SIDE)],
                         {'last_event_at': BASE + 6, 'pending': False})

    def test_response_ids_deduplicate_native_and_exporter_retries(self):
        payload = packet(record(1, 'response.created', response='resp-1'),
                         record(6, 'response.completed', tokens=300, response='resp-1'))
        self.feed(payload)
        result = self.feed(payload)
        self.assertEqual(len(result['samples']), 1)
        self.assertEqual(result['samples'][0]['event_key'], opaque('codex', 'response', 'resp-1'))
        self.assertEqual(result['pending_sessions'], [])
        self.assertEqual(result['telemetry']['diagnostics']['measured_responses'], 1)
        self.assertEqual(result['telemetry']['diagnostics']['duplicate_events'], 2)

    def test_anonymous_response_keys_are_deterministic_and_deduplicated(self):
        payload = packet(record(1, 'response.created'), record(6, 'response.completed', tokens=300))
        self.feed(payload)
        result = self.feed(payload)
        self.assertEqual(len(result['samples']), 1)
        self.assertEqual(result['pending_sessions'], [])

    def test_reasoning_is_already_in_output_not_added_again(self):
        result = self.feed(packet(record(1, 'response.created'),
                                  record(6, 'response.completed', tokens='300',
                                         reasoning_token_count='200', input_token_count='50000',
                                         cached_token_count='40000')))
        self.assertEqual(result['samples'][0]['output_tokens'], 300)

    def test_tool_waits_and_sse_handling_duration_are_excluded(self):
        self.feed(packet(record(1, 'response.created'),
                         record(2, 'response.completed', tokens=100, duration_ms='1')))
        result = self.feed(packet(record(20, 'response.created'),
                                  record(25, 'response.completed', tokens=300, duration_ms='100000')), now=25)
        self.assertEqual(len(result['samples']), 1)
        self.assertEqual((result['samples'][0]['start'], result['samples'][0]['end']), (BASE + 20, BASE + 25))
        self.assertEqual(self.state.snapshot(BASE + 31)['samples'], [])

    def test_missing_start_reports_gap_without_inventing_timing(self):
        result = self.feed(packet(record(6, 'response.completed', tokens=300)))
        self.assertEqual(result['samples'], [])
        self.assertEqual(result['pending_sessions'][0]['status'], 'missing_timing')
        self.assertEqual(result['covered_session_keys'], [])
        self.assertEqual(result['session_activity'], {})

    def test_reordered_export_batches_can_pair_source_boundaries(self):
        self.feed(packet(record(6, 'response.completed', tokens=300)))
        result = self.feed(packet(record(1, 'response.created')), now=7)
        self.assertEqual(len(result['samples']), 1)
        self.assertEqual(result['pending_sessions'], [])
        self.assertEqual(result['samples'][0]['start'], BASE + 1)

    def test_overlapping_without_response_ids_is_unmeasurable(self):
        result = self.feed(packet(record(1, 'response.created'), record(2, 'response.created'),
                                  record(5, 'response.completed', tokens=300),
                                  record(6, 'response.completed', tokens=200)))
        self.assertEqual(result['samples'], [])
        self.assertTrue(any('overlapping responses' in text for text in result['coverage']['issues']))

    def test_overlapping_with_ids_keeps_separate_responses(self):
        result = self.feed(packet(record(1, 'response.created', response='a'),
                                  record(2, 'response.created', response='b'),
                                  record(5, 'response.completed', tokens=300, response='a'),
                                  record(6, 'response.completed', tokens=200, response='b')))
        self.assertEqual(len(result['samples']), 2)
        self.assertEqual(sum(value['output_tokens'] for value in result['samples']), 500)

    def test_response_id_first_reported_at_completion_pairs_unique_session(self):
        result = self.feed(packet(record(1, 'response.created'),
                                  record(6, 'response.completed', tokens=300, response='completed-id')))
        self.assertEqual(len(result['samples']), 1)
        self.assertEqual(result['samples'][0]['event_key'], opaque('codex', 'response', 'completed-id'))
        self.assertEqual(result['pending_sessions'], [])

    def test_different_explicit_ids_are_never_paired(self):
        result = self.feed(packet(record(1, 'response.created', response='first'),
                                  record(6, 'response.completed', tokens=300, response='second')))
        self.assertEqual(result['samples'], [])
        self.assertTrue(any(value['status'] == 'missing_timing' for value in result['pending_sessions']))

    def test_callsite_event_name_still_matches_exact_response_schema(self):
        result = self.feed(packet(record(1, 'response.created', **{'event.name': 'event otel/src/events/session_telemetry.rs:1103'}),
                                  record(6, 'response.completed', tokens=300,
                                         **{'event.name': 'event otel/src/events/session_telemetry.rs:1103'})))
        self.assertEqual(len(result['samples']), 1)

    def test_codex_event_timestamp_attribute_with_unset_otlp_timestamp(self):
        records = [record(1, 'response.created'), record(6, 'response.completed', tokens=300)]
        for index, value in enumerate(records):
            value['timeUnixNano'] = '0'
            stamp = datetime.fromtimestamp(BASE + (1 if index == 0 else 6), timezone.utc).isoformat()
            value['attributes'].append(attribute('event.timestamp', stamp))
        result = self.feed(packet(*records))
        self.assertEqual(len(result['samples']), 1)
        self.assertEqual((result['samples'][0]['start'], result['samples'][0]['end']), (BASE + 1, BASE + 6))
        self.assertEqual(result['telemetry']['diagnostics']['event_timestamp_fields'], 2)

    def test_missing_and_naive_event_timestamps_are_not_guessed(self):
        for stamp in (None, '2026-10-07T12:30:00', 'not a timestamp'):
            value = record(6, 'response.completed', tokens=300)
            value.pop('timeUnixNano')
            if stamp is not None:
                value['attributes'].append(attribute('event.timestamp', stamp))
            events, counts = project_otlp(packet(value), BASE + 6)
            self.assertEqual(events, [])
            self.assertEqual(counts['invalid_records'], 1)

    def test_real_websocket_anchor_consumes_zero_token_prewarm_and_counts_fork(self):
        result = self.feed(packet(websocket_request(1, SIDE, duration='0'),
                                  record(1.5, 'response.completed', SIDE, tokens=0),
                                  websocket_request(3, SIDE, duration='1'),
                                  record(4.852, 'response.completed', SIDE, tokens=37)), now=5)
        self.assertEqual(len(result['samples']), 1)
        sample = result['samples'][0]
        self.assertAlmostEqual(sample['start'], BASE + 2.999, places=5)
        self.assertAlmostEqual(sample['end'] - sample['start'], 1.853, places=5)
        self.assertEqual(sample['output_tokens'], 37)
        self.assertEqual(sample['session_key'], opaque('codex', SIDE))
        self.assertEqual(result['pending_sessions'], [])
        self.assertEqual(result['telemetry']['diagnostics']['websocket_request_events'], 2)
        self.assertNotIn('created_events', result['telemetry']['diagnostics'])

    def test_websocket_sessions_and_tool_waits_keep_separate_intervals(self):
        self.feed(packet(websocket_request(1), record(2, 'response.completed', tokens=100)), now=2)
        result = self.feed(packet(websocket_request(21), websocket_request(22, SIDE),
                                  record(25, 'response.completed', tokens=300),
                                  record(25, 'response.completed', SIDE, tokens=100)), now=25)
        self.assertEqual(len(result['samples']), 2)
        primary = next(value for value in result['samples'] if value['session_key'] == opaque('codex', SID))
        self.assertAlmostEqual(primary['start'], BASE + 20.999, places=5)
        self.assertEqual(primary['end'], BASE + 25)
        self.assertEqual(result['pending_sessions'], [])

    def test_response_created_refines_websocket_request_without_overlapping(self):
        result = self.feed(packet(websocket_request(1), record(1.3, 'response.created', response='known-id'),
                                  record(6, 'response.completed', tokens=300, response='known-id')))
        self.assertEqual(len(result['samples']), 1)
        self.assertAlmostEqual(result['samples'][0]['start'], BASE + .999, places=5)
        self.assertEqual(result['samples'][0]['event_key'], opaque('codex', 'response', 'known-id'))
        self.assertEqual(result['pending_sessions'], [])

    def test_reordered_websocket_boundary_refines_existing_response_start(self):
        self.feed(packet(record(1.3, 'response.created', response='known-id')), now=2)
        self.feed(packet(websocket_request(1)), now=3)
        result = self.feed(packet(record(6, 'response.completed', tokens=300, response='known-id')))
        self.assertEqual(len(result['samples']), 1)
        self.assertAlmostEqual(result['samples'][0]['start'], BASE + .999, places=5)

    def test_long_response_completion_restores_expired_session_authority(self):
        self.feed(packet(websocket_request(1)), now=2)
        self.state.snapshot(BASE + 602)
        self.assertEqual(self.state.sessions, {})
        result = self.feed(packet(record(1200, 'response.completed', tokens=1000)), now=1200)
        self.assertEqual(len(result['samples']), 1)
        self.assertIn(opaque('codex', SID), result['covered_session_keys'])
        self.assertEqual(result['session_activity'][opaque('codex', SID)]['pending'], False)

    def test_malformed_or_explicit_failed_websocket_requests_do_not_create_usage(self):
        secret = 'private error /Users/Crear secret API_KEY'
        events, counts = project_otlp(packet(websocket_request(1, success=False),
                                            websocket_request(2, error=secret),
                                            websocket_request(3, success='false')), BASE + 6)
        self.assertEqual(events, [])
        self.assertEqual(counts['failed_request_events'], 3)
        self.assertNotIn(secret, json.dumps(counts))
        for value in ('invalid', '-1', True):
            events, counts = project_otlp(packet(websocket_request(1, duration=value)), BASE + 6)
            self.assertEqual(events, [])
            self.assertEqual(counts['invalid_records'], 1)
        events, counts = project_otlp(packet(websocket_request(1, success='unknown')), BASE + 6)
        self.assertEqual(events, [])
        self.assertEqual(counts['invalid_records'], 1)

    def test_unscoped_api_request_is_not_used_as_generation_start(self):
        request = record(1, None, sid=None, **{'event.name': 'codex.api_request',
                                             'duration_ms': '325', 'success': True})
        result = self.feed(packet(request, record(6, 'response.completed', tokens=300)))
        self.assertEqual(result['samples'], [])
        self.assertEqual(result['pending_sessions'][0]['status'], 'missing_timing')
        self.assertNotIn('websocket_request_events', result['telemetry']['diagnostics'])

    def test_unknown_identity_can_count_but_average_unavailable_with_stable_response(self):
        result = self.feed(packet(record(1, 'response.created', sid=None, response='a'),
                                  record(6, 'response.completed', sid=None, tokens=300, response='a')))
        self.assertEqual(result['samples'][0]['session_key'], None)
        projected = project_sources({'host': dict(packet=validate_packet(result), received_at=BASE + 6,
                                                 error=None)}, BASE + 6)
        self.assertEqual(projected['total_tps'], 60)
        self.assertIsNone(projected['average_tps'])

    def test_startup_does_not_replay_old_responses(self):
        result = self.feed(packet(record(-5, 'response.created'), record(-1, 'response.completed', tokens=300)))
        self.assertEqual(result['samples'], [])
        self.assertEqual(result['pending_sessions'], [])
        self.assertEqual(result['covered_session_keys'], [])
        self.assertEqual(result['telemetry']['diagnostics']['startup_events_ignored'], 2)

    def test_zero_output_clears_pending_without_a_sample(self):
        self.feed(packet(record(1, 'response.created')))
        result = self.feed(packet(record(6, 'response.completed', tokens=0)))
        self.assertEqual(result['samples'], [])
        self.assertEqual(result['pending_sessions'], [])

    def test_missing_count_and_bad_count_explicitly_fail_coverage(self):
        result = self.feed(packet(record(1, 'response.created'), record(6, 'response.completed')))
        self.assertEqual(result['samples'], [])
        self.assertIn('Codex telemetry completion has no output-token count', result['coverage']['issues'])
        for value in (-1, 'one million', True):
            events, counts = project_otlp(packet(record(6, 'response.completed', tokens=value)), BASE + 6)
            self.assertEqual(events, [])
            self.assertEqual(counts['invalid_records'], 1)

    def test_export_delay_is_numeric_and_freshness_expires(self):
        result = self.feed(packet(record(1, 'response.created'), record(6, 'response.completed', tokens=300)), now=11)
        self.assertEqual(result['telemetry']['export_delay_seconds'], 5)
        self.assertTrue(result['telemetry']['fresh'])
        expired = self.state.snapshot(BASE + 42)
        self.assertFalse(expired['telemetry']['fresh'])
        self.assertEqual(expired['covered_session_keys'], [])
        self.assertEqual(expired['session_activity'], {})
        self.assertTrue(expired['coverage']['issues'])

    def test_late_output_is_explicitly_excluded_from_live_window(self):
        result = self.feed(packet(record(1, 'response.created'), record(6, 'response.completed', tokens=300)), now=12)
        self.assertEqual(result['samples'], [])
        self.assertEqual(result['pending_sessions'], [])
        self.assertIn('Codex telemetry arrived after the five-second window; output was excluded',
                      result['coverage']['issues'])
        self.assertEqual(result['telemetry']['diagnostics']['late_responses'], 1)

    def test_model_and_provider_paths_are_rejected(self):
        result = self.feed(packet(record(1, 'response.created'),
                                  record(6, 'response.completed', tokens=300,
                                         model='C:/Users/Crear/private', model_provider='https://example.com/private')))
        self.assertEqual(result['samples'][0]['model'], 'Unknown')
        self.assertEqual(result['samples'][0]['provider'], 'Provider unreported')
        self.assertNotIn('Users', json.dumps(result))

    def test_bounded_pending_sessions(self):
        self.feed(packet(*(record(1, 'response.created', sid=f'session-{index}')
                           for index in range(MAX_SESSIONS + 1))))
        self.assertLessEqual(len(self.state.active), MAX_SESSIONS)
        self.assertLessEqual(len(self.state.sessions), MAX_SESSIONS)
        self.assertIn('Codex telemetry exceeded its bounded in-memory state',
                      self.state.snapshot(BASE + 6)['coverage']['issues'])

    def test_projection_never_retains_prompt_tool_paths_credentials_or_raw_ids(self):
        secret = 'private secret prompt /Users/Crear/.codex tool-output API_KEY_abcdef'
        first = record(1, 'response.created', prompt=secret, tool_output=secret,
                       error=secret, cwd=secret, authorization=secret)
        first['body'] = {'stringValue': secret}
        result = self.feed(packet(first, record(6, 'response.completed', tokens=300), shared={'user.email': secret}))
        projected = json.dumps(result)
        self.assertNotIn(secret, projected)
        self.assertNotIn(SID, projected)
        self.assertNotIn('prompt', projected)
        state_values = json.dumps(vars(self.state), default=dict)
        self.assertNotIn(secret, state_values)
        self.assertNotIn(SID, state_values)

    def test_future_timestamp_bad_shapes_and_event_limit_rejected(self):
        events, counts = project_otlp(packet(record(8, 'response.created')), BASE + 6)
        self.assertEqual(events, [])
        self.assertEqual(counts['invalid_records'], 1)
        for value in (None, {}, {'resourceLogs': [None]}, {'resourceLogs': [{'resource': None}]}):
            with self.assertRaises(ValueError):
                project_otlp(value, BASE + 6)
        with self.assertRaises(ValueError):
            project_otlp(packet(*([{}] * (MAX_EVENTS + 1))), BASE + 6)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.receiver = CodexTelemetryReceiver(port=0, clock=lambda: BASE + 6)
        # The fixed test clock starts after response.created; advance the
        # baseline explicitly so startup replay protection remains exercised.
        self.receiver.state.started_at = BASE

    def tearDown(self):
        self.receiver.close()

    def request(self, body, headers=None, path='/v1/logs', method='POST'):
        connection = http.client.HTTPConnection('127.0.0.1', self.receiver.port, timeout=3)
        try:
            connection.request(method, path, body, {'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def wait_samples(self):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            result = self.receiver.snapshot()
            if result['samples']:
                return result
            time.sleep(.01)
        self.fail('Telemetry worker did not ingest the request')

    def test_http_numeric_ingestion_and_shutdown(self):
        body = json.dumps(packet(record(1, 'response.created'), record(6, 'response.completed', tokens=300)))
        self.assertEqual(self.request(body), (200, b'{}'))
        self.assertEqual(self.wait_samples()['samples'][0]['output_tokens'], 300)
        self.receiver.close()
        self.assertFalse(self.receiver.worker.is_alive())
        self.assertFalse(self.receiver.http_thread.is_alive())

    def test_gzip_supported_with_bounded_decompression(self):
        body = json.dumps(packet(record(1, 'response.created'), record(6, 'response.completed', tokens=300))).encode()
        self.assertEqual(self.request(gzip.compress(body), {'Content-Encoding': 'gzip'})[0], 200)
        self.assertEqual(len(self.wait_samples()['samples']), 1)
        self.assertEqual(self.request(gzip.compress(b'x' * (MAX_REQUEST_BYTES + 1)),
                                      {'Content-Encoding': 'gzip'})[0], 400)

    def test_malformed_large_unsupported_requests_and_paths(self):
        self.assertEqual(self.request('{bad')[0], 400)
        self.assertEqual(self.request('[]')[0], 400)
        self.assertEqual(self.request('{}', {'Content-Type': 'application/x-protobuf'})[0], 415)
        self.assertEqual(self.request(b'', {'Content-Length': str(MAX_REQUEST_BYTES + 1)})[0], 413)
        self.assertEqual(self.request('{}', path='/v1/traces')[0], 404)
        self.assertEqual(self.request(None, method='GET')[0], 405)
        self.assertEqual(self.receiver.snapshot()['samples'], [])

    def test_loopback_binding_required(self):
        with self.assertRaises(ValueError):
            CodexTelemetryReceiver(host='0.0.0.0', port=0)

    def test_stalled_headers_do_not_block_shutdown_or_retain_port(self):
        connection = socket.create_connection(('127.0.0.1', self.receiver.port), timeout=3)
        try:
            connection.sendall(b'POST /v1/logs HTTP/1.1\r\nHost: localhost\r\n')
            # Let the HTTP thread enter its bounded header read.
            time.sleep(.05)
            started = time.monotonic()
            self.receiver.close()
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse(self.receiver.worker.is_alive())
            self.assertFalse(self.receiver.http_thread.is_alive())
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', self.receiver.port), timeout=.5)
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
