"""Integration checks for optional RAM-only Codex telemetry in source probes."""
import copy
import io
import json
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

from live_meter import BOOTSTRAP, probe_payload, project_sources, validate_packet
from live_probe import CODEX_ACTIVITY_GAP, Probe, opaque, run_probe

BASE = 1767225600
SID = '00000000-0000-0000-0000-000000000001'
SIDE = '00000000-0000-0000-0000-000000000002'


def sample(response='response', sid=SID, tokens=300, start=1, end=6, basis='codex_otel', runtime='codex'):
    return dict(event_key=opaque(runtime, 'response', response), session_key=opaque(runtime, sid),
                runtime=runtime, provider='OpenAI', model='gpt-test', output_tokens=tokens,
                start=BASE + start, end=BASE + end, basis=basis)


def pending(sid=SID, status='awaiting_usage'):
    return dict(runtime='codex', session_key=opaque('codex', sid), provider='OpenAI',
                model='gpt-test', status=status)


def telemetry_frame(samples=None, pending_sessions=None, covered=None, activity=None, issues=None):
    pending_sessions = pending_sessions or []
    issues = issues or []
    return dict(samples=samples or [], pending_sessions=pending_sessions,
                covered_session_keys=covered or [], session_activity=activity or {},
                coverage=dict(status='partial' if issues or pending_sessions else 'complete', issues=issues))


class ProbeTelemetryTests(unittest.TestCase):
    def setUp(self):
        # All source I/O is replaced. These tests never open a production DB/log.
        self.receiver = Mock()
        self.receiver.snapshot.return_value = telemetry_frame()
        self.constructor = self.enterContext(patch('live_telemetry.CodexTelemetryReceiver', return_value=self.receiver))
        self.enterContext(patch.object(Probe, 'discover', return_value=None))
        self.enterContext(patch.object(Probe, 'read_database', return_value=[]))
        self.enterContext(patch.object(Probe, 'read_codex_activity', return_value=[]))
        self.enterContext(patch('live_probe.time.time', return_value=BASE))

    def probe(self, **config):
        probe = Probe(dict(codex_otel_port='4319', **config))
        self.addCleanup(probe.close)
        return probe

    @staticmethod
    def tail(value=None, pending_value=None, sid=SID):
        state = SimpleNamespace(sid=sid, pending_live=False, last_activity=0,
                                pending=lambda _now: copy.deepcopy(pending_value))
        return SimpleNamespace(runtime='codex', state=state, issues=set(),
                               read=lambda probe: probe.record(value, probe.now) if value else None)

    def test_telemetry_authority_removes_unjoinable_native_duplicate(self):
        probe = self.probe()
        probe.tails['native'] = self.tail(sample('native-id', basis='native_log'))
        probe.samples['other'] = sample('other-native', sid=SIDE, basis='native_log')
        probe.samples['claude'] = sample('claude-response', basis='native_log', runtime='claude')
        self.receiver.snapshot.return_value = telemetry_frame(
            samples=[sample('telemetry-id')], covered=[opaque('codex', SID)])
        packet = probe.snapshot(BASE + 6)
        self.assertEqual({entry['event_key'] for entry in packet['samples']}, {
            opaque('codex', 'response', 'telemetry-id'),
            opaque('codex', 'response', 'other-native'),
            opaque('claude', 'response', 'claude-response')})
        # The following native reread cannot add the same session a second time.
        self.assertEqual(probe.snapshot(BASE + 6)['samples'], packet['samples'])

    def test_authority_changes_after_first_observed_telemetry(self):
        probe = self.probe()
        probe.tails['native'] = self.tail(sample('native-id', basis='native_log'))
        self.assertEqual(probe.snapshot(BASE + 6)['samples'][0]['basis'], 'native_log')
        self.receiver.snapshot.return_value = telemetry_frame(
            samples=[sample('telemetry-id')], covered=[opaque('codex', SID)])
        packet = probe.snapshot(BASE + 6)
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual(packet['samples'][0]['basis'], 'codex_otel')

    def test_side_chat_and_parent_remain_distinct_contributors(self):
        probe = self.probe()
        self.receiver.snapshot.return_value = telemetry_frame(
            samples=[sample('parent', tokens=300), sample('side', sid=SIDE, tokens=200)],
            covered=[opaque('codex', SID), opaque('codex', SIDE)])
        packet = validate_packet(probe.snapshot(BASE + 6))
        result = project_sources({'lab-source': dict(packet=packet, received_at=100, error=None)}, 100)
        self.assertEqual(result['total_tps'], 100)
        self.assertEqual(result['average_tps'], 50)
        self.assertEqual(result['contributing_sessions'], 2)

    def test_telemetry_authority_deduplicates_native_copy_on_other_machine(self):
        probe = self.probe()
        self.receiver.snapshot.return_value = telemetry_frame(
            samples=[sample('telemetry-without-provider-response-id')], covered=[opaque('codex', SID)])
        live = validate_packet(probe.snapshot(BASE + 6))
        copied = dict(live, samples=[sample('copied-native-response-id', basis='native_log')])
        result = project_sources({
            'lab-source': dict(packet=live, received_at=100, error=None),
            'workstation': dict(packet=copied, received_at=100, error=None)}, 100)
        self.assertEqual(result['total_tps'], 60)
        self.assertEqual(result['average_tps'], 60)
        self.assertEqual(result['contributing_sessions'], 1)
        self.assertEqual(result['sessions'][0]['source'], 'lab-source')

    def test_only_matching_native_pending_is_replaced(self):
        probe = self.probe()
        probe.tails['parent'] = self.tail(pending_value=pending())
        probe.tails['side'] = self.tail(pending_value=pending(SIDE), sid=SIDE)
        self.receiver.snapshot.return_value = telemetry_frame(
            pending_sessions=[pending(status='missing_timing')], covered=[opaque('codex', SID)])
        packet = probe.snapshot(BASE + 6)
        self.assertEqual({(entry['session_key'], entry['status']) for entry in packet['pending_sessions']}, {
            (opaque('codex', SID), 'missing_timing'), (opaque('codex', SIDE), 'awaiting_usage')})

    def test_matching_measured_session_clears_gap_other_session_remains(self):
        probe = self.probe()
        probe.trace_active = {SID: BASE + 5, SIDE: BASE + 5}
        self.receiver.snapshot.return_value = telemetry_frame(
            samples=[sample()], covered=[opaque('codex', SID)])
        packet = probe.snapshot(BASE + 6)
        self.assertEqual(probe.trace_active, {SIDE: BASE + 5})
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        self.assertEqual(packet['pending_sessions'], [dict(pending(SIDE), provider='Provider unreported', model='Unknown')])
        self.assertNotIn(SIDE, json.dumps(packet))

    def test_matching_live_pending_clears_gap_other_session_remains(self):
        probe = self.probe()
        probe.trace_active = {SID: BASE + 5, SIDE: BASE + 5}
        key = opaque('codex', SID)
        self.receiver.snapshot.return_value = telemetry_frame(
            pending_sessions=[pending()], covered=[key],
            activity={key: dict(pending=True, last_event_at=BASE + 4)})
        packet = probe.snapshot(BASE + 6)
        self.assertEqual(probe.trace_active, {SIDE: BASE + 5})
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        self.assertEqual(len(packet['pending_sessions']), 2)

    def test_covered_identity_alone_cannot_clear_native_gap(self):
        probe = self.probe()
        probe.trace_active = {SID: BASE + 5}
        self.receiver.snapshot.return_value = telemetry_frame(covered=[opaque('codex', SID)])
        packet = probe.snapshot(BASE + 6)
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        self.assertEqual(probe.trace_active, {SID: BASE + 5})

    def test_missing_timing_remains_explicit_and_unmeasured(self):
        probe = self.probe()
        probe.tails['native'] = self.tail(sample('native-id', basis='native_log'))
        issue = 'Codex telemetry output has no reliable response interval'
        self.receiver.snapshot.return_value = telemetry_frame(
            pending_sessions=[pending(status='missing_timing')],
            covered=[opaque('codex', SID)], issues=[issue])
        packet = validate_packet(probe.snapshot(BASE + 6))
        self.assertEqual(packet['samples'], [])
        self.assertEqual(packet['coverage']['status'], 'partial')
        self.assertIn(issue, packet['coverage']['issues'])
        self.assertEqual(packet['pending_sessions'][0]['status'], 'missing_timing')

    def test_completed_response_does_not_clear_newer_trace_in_same_window(self):
        probe = self.probe()
        probe.trace_active = {SID: BASE + 8}
        key = opaque('codex', SID)
        self.receiver.snapshot.return_value = telemetry_frame(
            samples=[sample(end=5)], covered=[key],
            activity={key: dict(pending=False, last_event_at=BASE + 5)})
        packet = probe.snapshot(BASE + 9)
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual(probe.trace_active, {SID: BASE + 8})
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])

    def test_expired_completed_response_does_not_clear_new_trace(self):
        probe = self.probe()
        probe.trace_active = {SID: BASE + 8}
        self.receiver.snapshot.return_value = telemetry_frame(
            samples=[sample(end=2)], covered=[opaque('codex', SID)])
        packet = probe.snapshot(BASE + 9)
        self.assertEqual(packet['samples'], [])
        self.assertEqual(probe.trace_active, {SID: BASE + 8})
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])

    def test_port_validation_and_disabled_default(self):
        for value in ('', '-1', '65536', '4.5', '4319x', True, False):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'codex_otel_port'):
                Probe(dict(codex_otel_port=value))
        for config in ({}, dict(codex_otel_port='0')):
            probe = Probe(config)
            self.assertIsNone(probe.telemetry)
            probe.close()
        self.constructor.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'requires codex_native_tps'):
            Probe(dict(codex_otel_port='4319', codex_native_tps='false'))

    def test_probe_close_releases_receiver(self):
        probe = self.probe()
        probe.close()
        self.receiver.close.assert_called_once_with()


class ProbeCleanupTests(unittest.TestCase):
    @staticmethod
    def controller(snapshot_started):
        # Allow one tick before EOF, so the cleanup path is deterministic.
        def read(_size):
            snapshot_started.wait(2)
            return b''
        return SimpleNamespace(buffer=SimpleNamespace(read=read))

    def test_run_probe_closes_receiver_on_controller_eof(self):
        started = threading.Event()
        probe = Mock()
        probe.snapshot.side_effect = lambda: (started.set(), dict(version=1))[1]
        output = io.StringIO()
        with patch('live_probe.Probe', return_value=probe), patch('live_probe.sys.stdin', self.controller(started)), patch('live_probe.sys.stdout', output):
            run_probe({})
        probe.close.assert_called_once_with()

    def test_run_probe_closes_receiver_on_snapshot_error(self):
        started = threading.Event()
        probe = Mock()
        def fail():
            started.set()
            raise RuntimeError('Synthetic reader failure')
        probe.snapshot.side_effect = fail
        with patch('live_probe.Probe', return_value=probe), patch('live_probe.sys.stdin', self.controller(started)):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic reader failure'):
                run_probe({})
        probe.close.assert_called_once_with()

    def test_run_probe_closes_receiver_on_output_broken_pipe(self):
        started = threading.Event()
        probe = Mock()
        probe.snapshot.side_effect = lambda: (started.set(), dict(version=1))[1]
        with patch('live_probe.Probe', return_value=probe), patch('live_probe.sys.stdin', self.controller(started)), patch('live_probe.print', side_effect=BrokenPipeError):
            run_probe({})
        probe.close.assert_called_once_with()


class RemotePayloadTests(unittest.TestCase):
    def test_in_memory_import_on_clean_source_and_eof_port_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / 'codex'
            claude = root / 'claude'
            codex.mkdir()
            claude.mkdir()
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            payload = probe_payload(dict(codex_otel_port=str(port), codex_home=str(root),
                                         codex_session_roots=str(codex), claude_projects=str(claude),
                                         database=str(root / 'absent.db')))
            process = subprocess.Popen([sys.executable, '-I', '-u', '-c', BOOTSTRAP],
                                       cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
            try:
                process.stdin.write(str(len(payload)).encode('ascii') + b'\n' + payload)
                process.stdin.flush()
                reader.start()
                first = lines.get(timeout=6)
                if not first:
                    error = process.stderr.read().decode('utf-8', errors='replace') if process.poll() is not None else ''
                    self.fail('Compressed probe produced no heartbeat: ' + error)
                packet = validate_packet(json.loads(first))
                self.assertEqual(packet['version'], 1)
                request = Request(f'http://127.0.0.1:{port}/v1/logs', data=b'{"resourceLogs":[]}',
                                  headers={'Content-Type': 'application/json'})
                with urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                process.stdin.close()
                self.assertEqual(process.wait(timeout=6), 0)
                # The listener is not an orphan daemon after SSH/controller EOF.
                with socket.socket() as reusable:
                    reusable.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    reusable.bind(('127.0.0.1', port))
                self.assertEqual({path.name for path in root.iterdir()}, {'codex', 'claude'})
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()
                if reader.ident is not None:
                    reader.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
