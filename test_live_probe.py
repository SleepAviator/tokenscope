import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from live_probe import CODEX_ACTIVITY_GAP, TRACE_ROWS, TRACE_SESSIONS, Probe, opaque

BASE = 1767225600
SID = '00000000-0000-0000-0000-000000000001'
CHILD = '00000000-0000-0000-0000-000000000002'


def event(second, kind, **payload):
    return dict(timestamp=datetime.datetime.fromtimestamp(BASE + second, datetime.timezone.utc).isoformat(),
                type=kind, payload=payload)


def usage(count=300):
    return dict(input_tokens=1000, cached_input_tokens=900, output_tokens=count,
                reasoning_output_tokens=80)


def response(start, end, rid='resp-1', count=300, sid=SID, call=None, native=True):
    records = [event(start, 'response_item', type='message', role='user'),
               event(end, 'response_item', **(dict(type='function_call', call_id=call) if call else dict(type='message', role='assistant')))]
    if native:
        records.append(event(end + .1, 'token_usage_record', response_id=rid, thread_id=sid, usage=usage(count)))
    records.append(event(end + .2, 'event_msg', type='token_count', info=dict(last_token_usage=usage(count), total_token_usage=usage(count))))
    return records


def claude(second, kind, mid=None, count=0, content=None, agent=None):
    message = dict(role=kind, content=content if content is not None else [], model='claude-test')
    if mid:
        message.update(id=mid, usage=dict(output_tokens=count), stop_reason='tool_use' if content else 'end_turn')
    return dict(type=kind, timestamp=datetime.datetime.fromtimestamp(BASE + second, datetime.timezone.utc).isoformat(),
                sessionId=SID, agentId=agent, message=message)


class LiveProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.codex = self.root / 'codex'
        self.claude = self.root / 'claude'
        self.codex.mkdir()
        self.claude.mkdir()
        self.log = self.codex / ('rollout-' + SID + '.jsonl')
        self.write(self.log, [event(0, 'session_meta', id=SID, model_provider='OpenAI'), event(0, 'turn_context', model='gpt-test')])
        self.dbpath = self.root / 'usage.db'
        with sqlite3.connect(self.dbpath) as db:
            db.execute('CREATE TABLE proxy_request_logs (request_id TEXT PRIMARY KEY, app_type TEXT, provider_id TEXT, model TEXT, output_tokens INTEGER, created_at REAL, status_code INTEGER, data_source TEXT, session_id TEXT, duration_ms REAL, latency_ms REAL)')
            db.execute('CREATE INDEX idx_created ON proxy_request_logs(created_at)')
            db.execute('CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT)')
            db.execute("INSERT INTO providers VALUES ('provider-1', 'gemini', 'Google')")
        self.config = dict(database=str(self.dbpath), codex_home=str(self.root / 'native-home'),
                           codex_session_roots=str(self.codex), claude_projects=str(self.claude))

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, records, append=False):
        with path.open('a' if append else 'w', encoding='utf-8') as stream:
            for record in records:
                stream.write(json.dumps(record) + '\n')

    def probe(self):
        with patch('live_probe.time.time', return_value=BASE):
            return Probe(self.config)

    def proxy(self, second=3, rid='req-1', runtime='gemini', sid=SID, duration=2000, output=200, source='proxy'):
        with sqlite3.connect(self.dbpath) as db:
            db.execute('INSERT INTO proxy_request_logs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (rid, runtime, 'provider-1', 'test-model', output, BASE + second, 200, source, sid, duration, 0))

    def trace_database(self):
        path = Path(self.config['codex_home']) / 'logs_2.sqlite'
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, ts_nanos INTEGER, target TEXT, thread_id TEXT, feedback_log_body TEXT)')
        return path

    @staticmethod
    def trace(path, second=3, sid=CHILD, target='codex_core::stream_events_utils'):
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO logs(ts,ts_nanos,target,thread_id,feedback_log_body) VALUES(?,?,?,?,?)',
                       (BASE + second, 100000000, target, sid, 'private content must never be read'))

    def test_unpersisted_codex_activity_reports_gap_without_guessing_tokens(self):
        path = self.trace_database()
        p = self.probe()
        self.trace(path)
        packet = p.snapshot(BASE + 4)
        self.assertEqual(packet['samples'], [])
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        self.assertEqual(packet['pending_sessions'], [dict(runtime='codex', session_key=opaque('codex', CHILD),
                         provider='Provider unreported', model='Unknown', status='awaiting_usage')])
        self.assertNotIn(CHILD, json.dumps(packet))
        # Missing persistence remains a coverage gap after activity/awaiting
        # indicators expire; idle time cannot establish complete coverage.
        self.assertIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 10)['coverage']['issues'])
        expired = p.snapshot(BASE + 124)
        self.assertEqual(expired['pending_sessions'], [])
        self.assertIn(CODEX_ACTIVITY_GAP, expired['coverage']['issues'])

    def test_readable_codex_usage_and_pending_generation_do_not_gain_trace_gap(self):
        path = self.trace_database()
        p = self.probe()
        self.write(self.log, [event(1, 'event_msg', type='task_started')], append=True)
        self.trace(path, sid=SID)
        packet = p.snapshot(BASE + 4)
        self.assertNotIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        self.assertEqual(packet['pending_sessions'][0]['status'], 'awaiting_usage')
        self.write(self.log, response(4, 5), append=True)
        self.trace(path, second=5, sid=SID)
        packet = p.snapshot(BASE + 6)
        self.assertNotIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        self.assertEqual(len(packet['samples']), 1)

    def test_stale_known_codex_stub_is_gap_until_native_logs_resume(self):
        path = self.trace_database()
        p = self.probe()
        self.trace(path, sid=SID)
        self.assertIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 4)['coverage']['issues'])
        self.write(self.log, response(4, 5), append=True)
        packet = p.snapshot(BASE + 6)
        self.assertNotIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual(p.trace_active, {})

    def test_expired_native_sample_does_not_hide_new_unpersisted_activity(self):
        path = self.trace_database()
        p = self.probe()
        self.write(self.log, response(1, 3), append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)
        self.trace(path, second=8, sid=SID)
        packet = p.snapshot(BASE + 9)
        self.assertEqual(packet['samples'], [])
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])

    def test_trace_startup_and_replacement_do_not_replay_existing_activity(self):
        path = self.trace_database()
        self.trace(path)
        p = self.probe()
        self.assertNotIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 4)['coverage']['issues'])
        path.rename(path.with_suffix('.old'))
        replacement = self.trace_database()
        self.trace(replacement, second=5)
        self.assertNotIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 6)['coverage']['issues'])
        self.trace(replacement, second=7)
        self.assertIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 8)['coverage']['issues'])

    def test_trace_reconnection_preserves_detected_gap_and_baselines_new_database(self):
        path = self.trace_database()
        p = self.probe()
        self.trace(path)
        p.snapshot(BASE + 4)
        path.rename(path.with_suffix('.offline'))
        self.assertIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 5)['coverage']['issues'])
        self.trace_database()
        self.trace(path, second=6, sid='new-session')
        p.snapshot(BASE + 7)
        self.assertEqual(set(p.trace_active), {CHILD})

    def test_trace_reads_are_bounded_indexed_and_exclude_message_column(self):
        path = self.trace_database()
        p = self.probe()
        with sqlite3.connect(path) as db:
            db.executemany('INSERT INTO logs(ts,ts_nanos,target,thread_id) VALUES(?,?,?,?)',
                           [(BASE + 3, 0, 'unrelated_target', CHILD)] * (TRACE_ROWS + 1))
        statements = []
        connect = sqlite3.connect
        def inspection(*args, **kwargs):
            db = connect(*args, **kwargs)
            if 'logs_2.sqlite' in str(args[0]):
                db.set_trace_callback(statements.append)
                db.set_authorizer(lambda action, table, column, *_:
                                  sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and column == 'feedback_log_body' else sqlite3.SQLITE_OK)
            return db
        with patch('live_probe.sqlite3.connect', side_effect=inspection):
            packet = p.snapshot(BASE + 4)
        self.assertEqual(p.trace_cursor, TRACE_ROWS)
        self.assertIn('Codex native activity reader is catching up', packet['coverage']['issues'])
        reads = [sql for sql in statements if sql.startswith('SELECT id,ts,ts_nanos,target,thread_id')]
        self.assertEqual(len(reads), 1)
        self.assertIn('WHERE id > 0 ORDER BY id LIMIT 4096', reads[0])
        self.assertNotIn('feedback_log_body', ' '.join(statements))
        p.snapshot(BASE + 5)
        self.assertEqual(p.trace_cursor, TRACE_ROWS + 1)

    def test_trace_failure_recovers_cursor_without_keeping_read_connection(self):
        path = self.trace_database()
        p = self.probe()
        self.trace(path)
        with patch('live_probe.sqlite3.connect', side_effect=sqlite3.OperationalError('database is locked')):
            packet = p.snapshot(BASE + 4)
        self.assertIn('Codex native activity could not be read (OperationalError)', packet['coverage']['issues'])
        self.assertIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 5)['coverage']['issues'])
        with sqlite3.connect(path, timeout=0) as db:
            db.execute('BEGIN EXCLUSIVE')
            db.rollback()

    def test_trace_read_does_not_modify_database_or_create_sidecars(self):
        path = self.trace_database()
        self.trace(path)
        before = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.parent.iterdir()}
        p = self.probe()
        p.snapshot(BASE + 4)
        after = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.parent.iterdir()}
        self.assertEqual(before, after)

    def test_trace_wal_without_shared_memory_is_explicit_and_not_created(self):
        path = self.trace_database()
        path.with_name(path.name + '-wal').touch()
        p = self.probe()
        packet = p.snapshot(BASE + 4)
        self.assertIn('Codex native activity cannot be read without its existing WAL shared memory', packet['coverage']['issues'])
        self.assertFalse(path.with_name(path.name + '-shm').exists())

    def test_existing_trace_wal_is_read_without_new_files_or_database_changes(self):
        path = self.trace_database()
        with sqlite3.connect(path) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('INSERT INTO logs(ts,ts_nanos,target,thread_id) VALUES(?,?,?,?)',
                           (BASE, 0, 'unrelated_target', CHILD))
            writer.commit()
            p = self.probe()
            writer.execute('INSERT INTO logs(ts,ts_nanos,target,thread_id) VALUES(?,?,?,?)',
                           (BASE + 3, 0, 'codex_core::stream_events_utils', CHILD))
            writer.commit()
            before = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.parent.iterdir()}
            packet = p.snapshot(BASE + 4)
            after = {item.name: (item.read_bytes(), item.stat().st_mtime_ns) for item in path.parent.iterdir()}
            self.assertEqual(set(before), set(after))
            for name in before:
                self.assertEqual(before[name][1], after[name][1])
                if not name.endswith('-shm'):
                    self.assertEqual(before[name][0], after[name][0])
                # SQLite read-only WAL readers coordinate through existing
                # shared-memory reader marks; DB and WAL records stay intact.
            self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])

    def test_existing_trace_wal_lock_is_explicit_and_recovers_without_history(self):
        path = self.trace_database()
        writer = sqlite3.connect(path)
        try:
            writer.execute('PRAGMA locking_mode=EXCLUSIVE')
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('INSERT INTO logs(ts,ts_nanos,target,thread_id) VALUES(?,?,?,?)',
                           (BASE + 3, 0, 'codex_core::stream_events_utils', CHILD))
            writer.commit()
            p = self.probe()
            packet = p.snapshot(BASE + 4)
            # SQLite's exclusive WAL writer has no shared-memory file. The
            # probe reports this explicitly instead of creating one.
            self.assertIn('Codex native activity cannot be read without its existing WAL shared memory', packet['coverage']['issues'])
        finally:
            writer.close()
        self.assertNotIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 5)['coverage']['issues'])
        self.trace(path, second=6)
        self.assertIn(CODEX_ACTIVITY_GAP, p.snapshot(BASE + 7)['coverage']['issues'])

    def test_trace_unsupported_schema_and_session_bound_are_explicit(self):
        path = self.trace_database()
        p = self.probe()
        with sqlite3.connect(path) as db:
            db.executemany('INSERT INTO logs(ts,ts_nanos,target,thread_id) VALUES(?,?,?,?)',
                           [(BASE + 3, 0, 'codex_core::stream_events_utils', 'session-' + str(i)) for i in range(TRACE_SESSIONS + 1)])
        packet = p.snapshot(BASE + 4)
        self.assertEqual(len(p.trace_active), TRACE_SESSIONS)
        self.assertTrue(p.trace_overflow)
        self.assertIn(CODEX_ACTIVITY_GAP, packet['coverage']['issues'])
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE logs')
            db.execute('CREATE TABLE logs(id TEXT PRIMARY KEY, ts INTEGER, ts_nanos INTEGER, target TEXT, thread_id TEXT)')
        packet = p.snapshot(BASE + 5)
        self.assertIn('Codex native activity schema is unsupported', packet['coverage']['issues'])

    def test_configured_codex_home_supplies_default_roots(self):
        home = self.root / 'native-home'
        sessions = home / 'sessions'
        sessions.mkdir(parents=True)
        log = sessions / self.log.name
        self.write(log, [event(0, 'session_meta', id=SID, model_provider='OpenAI')])
        del self.config['codex_session_roots']
        with patch.dict('live_probe.os.environ', {'CODEX_HOME': str(self.root / 'ignored-home')}):
            p = self.probe()
        self.assertEqual(p.codex_home, home)
        self.assertEqual(p.codex_roots, [sessions, home / 'archived_sessions'])
        self.write(log, response(1, 3), append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)

    def test_codex_home_environment_supplies_default_roots(self):
        home = self.root / 'environment-home'
        sessions = home / 'sessions'
        sessions.mkdir(parents=True)
        log = sessions / self.log.name
        self.write(log, [event(0, 'session_meta', id=SID, model_provider='OpenAI')])
        del self.config['codex_home']
        del self.config['codex_session_roots']
        with patch.dict('live_probe.os.environ', {'CODEX_HOME': str(home)}):
            p = self.probe()
        self.assertEqual(p.codex_home, home)
        self.assertEqual(p.codex_roots, [sessions, home / 'archived_sessions'])
        self.write(log, response(1, 3), append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)

    def test_default_codex_home_is_source_user_home(self):
        del self.config['codex_home']
        del self.config['codex_session_roots']
        with patch.dict('live_probe.os.environ', {'HOME': str(self.root), 'USERPROFILE': str(self.root), 'CODEX_HOME': ''}):
            p = self.probe()
        self.assertEqual(p.codex_home, self.root / '.codex')
        self.assertEqual(p.codex_roots, [self.root / '.codex' / 'sessions', self.root / '.codex' / 'archived_sessions'])

    def test_explicit_codex_roots_override_home_and_environment(self):
        with patch.dict('live_probe.os.environ', {'CODEX_HOME': str(self.root / 'ignored-home')}):
            p = self.probe()
        self.assertEqual(p.codex_roots, [self.codex])
        self.write(self.log, response(1, 3), append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)

    def test_native_reasoning_and_mirror_deduplication(self):
        p = self.probe()
        self.write(self.log, response(1, 4), append=True)
        packet = p.snapshot(BASE + 5)
        self.assertEqual(len(packet['samples']), 1)
        sample = packet['samples'][0]
        self.assertEqual(sample['output_tokens'], 300)  # not 380
        self.assertEqual((sample['start'], sample['end']), (BASE + 1, BASE + 4))
        self.assertEqual(sample['provider'], 'OpenAI')
        self.assertEqual(packet['pending_sessions'], [])
        self.assertEqual(p.snapshot(BASE + 10)['samples'], [])

    def test_task_start_after_user_message_establishes_generation_boundary(self):
        p = self.probe()
        self.write(self.log, [event(1, 'response_item', type='message', role='user'),
                             event(2, 'event_msg', type='task_started'),
                             event(3, 'response_item', type='reasoning'),
                             event(4, 'response_item', type='message', role='assistant'),
                             event(4.1, 'token_usage_record', response_id='task-start', thread_id=SID,
                                   usage=usage())], append=True)
        packet = p.snapshot(BASE + 5)
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual((packet['samples'][0]['start'], packet['samples'][0]['end']),
                         (BASE + 2, BASE + 4))
        self.assertEqual(packet['samples'][0]['output_tokens'], 300)
        self.assertEqual(packet['pending_sessions'], [])

    def test_task_start_waits_for_usage_and_agent_message_does_not_shift_boundary(self):
        p = self.probe()
        self.write(self.log, [event(1, 'event_msg', type='task_started'),
                             event(1.1, 'response_item', type='agent_message', author='agent', recipient='all'),
                             event(2, 'response_item', type='reasoning')], append=True)
        pending = p.snapshot(BASE + 2.1)
        self.assertEqual(pending['samples'], [])
        self.assertEqual(pending['pending_sessions'][0]['status'], 'awaiting_usage')
        self.write(self.log, [event(2.5, 'response_item', type='agent_message', author='agent', recipient='all'),
                             event(3, 'response_item', type='function_call', call_id='first-tool'),
                             event(3.1, 'token_usage_record', response_id='agent-first', thread_id=SID,
                                   usage=usage()),
                             event(3.2, 'event_msg', type='token_count',
                                   info=dict(last_token_usage=usage(), total_token_usage=usage()))], append=True)
        packet = p.snapshot(BASE + 4)
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual((packet['samples'][0]['start'], packet['samples'][0]['end']),
                         (BASE + 1, BASE + 3))
        self.assertEqual(packet['samples'][0]['output_tokens'], 300)
        self.assertEqual(packet['pending_sessions'], [])

    def test_tool_wait_excluded_and_new_input_preserved(self):
        p = self.probe()
        self.write(self.log, response(1, 4, call='tool'), append=True)
        p.snapshot(BASE + 5)
        self.write(self.log, [event(20, 'response_item', type='function_call_output', call_id='tool'),
                             event(23, 'response_item', type='message', role='assistant'),
                             event(23.1, 'token_usage_record', response_id='resp-2', thread_id=SID, usage=usage(600))], append=True)
        packet = p.snapshot(BASE + 24)
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual(packet['samples'][0]['start'], BASE + 20)
        self.assertEqual(packet['samples'][0]['end'], BASE + 23)

    def test_parallel_tool_results_require_all_calls(self):
        p = self.probe()
        self.write(self.log, [event(1, 'response_item', type='message', role='user'),
                             event(2, 'response_item', type='function_call', call_id='one'),
                             event(2, 'response_item', type='function_call', call_id='two'),
                             event(2.1, 'token_usage_record', response_id='tools', thread_id=SID, usage=usage()),
                             event(3, 'response_item', type='function_call_output', call_id='one')], append=True)
        self.assertEqual(p.snapshot(BASE + 3.5)['pending_sessions'], [])
        self.write(self.log, [event(4, 'response_item', type='function_call_output', call_id='two'),
                             event(6, 'response_item', type='message', role='assistant'),
                             event(6.1, 'token_usage_record', response_id='post-tools', thread_id=SID, usage=usage())], append=True)
        samples = p.snapshot(BASE + 7)['samples']
        second = next(s for s in samples if s['event_key'] == opaque('codex', 'response', 'post-tools'))
        self.assertEqual(second['start'], BASE + 4)

    def test_legacy_repeated_cumulative_counter_deduplicated(self):
        p = self.probe()
        records = response(1, 3, native=False)
        records.append(event(3.3, 'event_msg', type='token_count', info=dict(last_token_usage=usage(), total_token_usage=usage())))
        self.write(self.log, records, append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)

    def test_native_mirror_delayed_by_tool_preserves_next_boundary(self):
        p = self.probe()
        records = response(1, 3, call='tool')[:-1]
        self.write(self.log, records, append=True)
        p.snapshot(BASE + 4)
        self.write(self.log, [event(50, 'response_item', type='function_call_output', call_id='tool'),
                             event(50.1, 'event_msg', type='token_count', info=dict(last_token_usage=usage(), total_token_usage=usage()))], append=True)
        packet = p.snapshot(BASE + 51)
        self.assertEqual(packet['samples'], [])
        self.assertEqual(packet['pending_sessions'][0]['status'], 'awaiting_usage')
        self.write(self.log, [event(53, 'response_item', type='message', role='assistant'),
                             event(53.1, 'token_usage_record', response_id='after-long-tool', thread_id=SID, usage=usage())], append=True)
        sample = p.snapshot(BASE + 54)['samples'][0]
        self.assertEqual(sample['start'], BASE + 50)
        self.assertEqual(sample['end'], BASE + 53)

    def test_bootstrap_does_not_replay_and_restores_boundary(self):
        self.write(self.log, response(1, 2), append=True)
        p = self.probe()
        self.assertEqual(p.snapshot(BASE + 3)['samples'], [])
        self.write(self.log, response(3, 4, rid='resp-new'), append=True)
        self.assertEqual(len(p.snapshot(BASE + 5)['samples']), 1)

    def test_partial_tail_completed_later_and_safe_malformed_issue(self):
        p = self.probe()
        self.write(self.log, response(1, 3)[:2], append=True)
        record = json.dumps(event(3.1, 'token_usage_record', response_id='partial', thread_id=SID, usage=usage()))
        with self.log.open('a') as stream:
            stream.write(record[:30])
        self.assertEqual(p.snapshot(BASE + 3.2)['samples'], [])
        with self.log.open('a') as stream:
            stream.write(record[30:] + '\nDO NOT EXPOSE PRIVATE RAW TEXT\n')
        packet = p.snapshot(BASE + 4)
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual(packet['coverage']['status'], 'partial')
        self.assertNotIn('PRIVATE RAW', json.dumps(packet))

    def test_rotation_and_copy_do_not_repeat_response(self):
        p = self.probe()
        self.write(self.log, response(1, 3), append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)
        rotated = self.log.with_suffix('.old')
        self.log.rename(rotated)
        self.write(self.log, [event(0, 'session_meta', id=SID), *response(1, 3), *response(4, 5, rid='new')])
        samples = p.snapshot(BASE + 6)['samples']
        self.assertEqual(len(samples), 2)
        self.assertEqual(len({s['event_key'] for s in samples}), 2)

    def test_subagent_inherited_prefix_and_root_session_id(self):
        p = self.probe()
        child = self.codex / ('rollout-' + CHILD + '.jsonl')
        inherited = [event(1, 'session_meta', id=CHILD, model_provider='OpenAI', subagent_history_start_ordinal=5),
                     event(1, 'session_meta', id=SID), *response(1, 2, rid='inherited')]
        records = inherited + [event(3, 'event_msg', type='task_started'),
                               event(3.1, 'response_item', type='agent_message', author='agent', recipient='all'),
                               event(3.4, 'response_item', type='reasoning'),
                               event(4, 'response_item', type='message', role='assistant'),
                               event(4.1, 'token_usage_record', response_id='child-new', thread_id=CHILD,
                                     session_id=SID, usage=usage()),
                               event(4.2, 'event_msg', type='token_count',
                                     info=dict(last_token_usage=usage(), total_token_usage=usage()))]
        self.write(child, records)
        samples = p.snapshot(BASE + 5)['samples']
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['session_key'], opaque('codex', CHILD))
        self.assertEqual(samples[0]['event_key'], opaque('codex', 'response', 'child-new'))
        self.assertEqual((samples[0]['start'], samples[0]['end']), (BASE + 3, BASE + 4))

    def test_missing_timing_pending_and_disabled_native(self):
        p = self.probe()
        self.write(self.log, [event(2, 'token_usage_record', response_id='no-boundary', thread_id=SID, usage=usage())], append=True)
        packet = p.snapshot(BASE + 3)
        self.assertEqual(packet['samples'], [])
        self.assertEqual(packet['pending_sessions'][0]['status'], 'missing_timing')
        self.config['codex_native_tps'] = 'false'
        p = self.probe()
        self.assertIn('Codex native timing is disabled', p.snapshot(BASE + 3)['coverage']['issues'])

    def test_long_generation_stays_pending_until_explicit_completion(self):
        p = self.probe()
        self.write(self.log, [event(1, 'response_item', type='message', role='user')], append=True)
        self.assertEqual(p.snapshot(BASE + 2)['pending_sessions'][0]['status'], 'awaiting_usage')
        self.assertEqual(p.snapshot(BASE + 200)['pending_sessions'][0]['status'], 'awaiting_usage')
        self.write(self.log, [event(201, 'event_msg', type='turn_aborted')], append=True)
        self.assertEqual(p.snapshot(BASE + 202)['pending_sessions'], [])

    def test_historical_unfinished_baseline_can_expire(self):
        self.write(self.log, [event(-20, 'response_item', type='message', role='user')], append=True)
        p = self.probe()
        self.assertEqual(p.snapshot(BASE + 200)['pending_sessions'], [])

    def test_malformed_usage_without_response_id_is_safe_partial(self):
        p = self.probe()
        self.write(self.log, [event(1, 'token_usage_record', usage=None), *response(2, 3)], append=True)
        packet = p.snapshot(BASE + 4)
        self.assertEqual(len(packet['samples']), 1)
        self.assertEqual(packet['coverage']['status'], 'partial')
        self.assertTrue(any('malformed complete record' in issue for issue in packet['coverage']['issues']))

    def test_claude_message_identity_tool_wait_and_subagent(self):
        log = self.claude / 'session.jsonl'
        self.write(log, [])
        p = self.probe()
        self.write(log, [claude(1, 'user'), claude(2, 'assistant', 'm1', 0),
                         claude(3, 'assistant', 'm1', 100, [dict(type='tool_use', id='tool')]),
                         claude(3.1, 'assistant', 'm1', 100, [dict(type='tool_use', id='tool')])], append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)
        self.write(log, [claude(20, 'user', content=[dict(type='tool_result', tool_use_id='tool')]),
                         claude(22, 'assistant', 'm2', 100)], append=True)
        samples = p.snapshot(BASE + 23)['samples']
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['start'], BASE + 20)
        self.assertEqual(samples[0]['end'], BASE + 22)
        self.assertEqual(samples[0]['provider'], 'Provider unreported')

    def test_claude_failed_and_final_zero_usage_clear_pending(self):
        log = self.claude / 'session.jsonl'
        self.write(log, [])
        p = self.probe()
        interim = claude(2, 'assistant', 'm1', 0)
        interim['message']['stop_reason'] = None
        self.write(log, [claude(1, 'user'), interim], append=True)
        self.assertEqual(p.snapshot(BASE + 2.5)['pending_sessions'][0]['status'], 'awaiting_usage')
        self.write(log, [claude(3, 'assistant', 'm1', 100), claude(4, 'user')], append=True)
        p.snapshot(BASE + 4.5)
        failed = claude(5, 'assistant', 'error', 0)
        failed['isApiErrorMessage'] = True
        failed['message']['model'] = '<synthetic>'
        self.write(log, [failed], append=True)
        packet = p.snapshot(BASE + 5.5)
        self.assertEqual(packet['pending_sessions'], [])
        self.assertEqual(len(packet['samples']), 1)
        self.write(log, [claude(6, 'user'), claude(7, 'assistant', 'zero', 0)], append=True)
        self.assertEqual(p.snapshot(BASE + 7.5)['pending_sessions'], [])

    def test_proxy_indexed_only_bootstrap_imports_and_no_session(self):
        self.proxy(second=1, rid='before')
        p = self.probe()
        self.proxy(second=3, rid='after', sid=None)
        self.proxy(second=3, rid='history-import', source='gemini_session')
        samples = p.snapshot(BASE + 4)['samples']
        self.assertEqual(len(samples), 1)
        self.assertIsNone(samples[0]['session_key'])
        self.assertEqual(samples[0]['provider'], 'Google')
        self.assertEqual(samples[0]['start'], BASE + 1)
        self.assertEqual(p.index_name, 'idx_created')

    def test_proxy_missing_timing_remains_pending_for_window(self):
        p = self.probe()
        self.proxy(second=3, duration=None)
        self.assertEqual(p.snapshot(BASE + 4)['pending_sessions'][0]['status'], 'missing_timing')
        self.assertEqual(p.snapshot(BASE + 5)['pending_sessions'][0]['status'], 'missing_timing')
        self.assertEqual(p.snapshot(BASE + 9)['pending_sessions'], [])

    def test_proxy_generated_uuid_is_not_assumed_to_be_session(self):
        p = self.probe()
        self.proxy(second=3, sid=SID)
        self.proxy(second=3, rid='req-2', sid='req-2')
        packet = p.snapshot(BASE + 4)
        self.assertTrue(all(sample['session_key'] is None for sample in packet['samples']))

    def test_proxy_verified_client_prefix_has_session_identity(self):
        p = self.probe()
        self.proxy(second=3, runtime='grokbuild', sid='grokbuild_' + SID)
        sample = p.snapshot(BASE + 4)['samples'][0]
        self.assertEqual(sample['session_key'], opaque('grokbuild', SID))

    def test_claude_exact_proxy_id_reuses_native_identity_and_provider(self):
        log = self.claude / 'session.jsonl'
        self.write(log, [])
        with sqlite3.connect(self.dbpath) as db:
            db.execute("INSERT INTO providers VALUES ('provider-1', 'claude', 'Anthropic')")
        p = self.probe()
        self.write(log, [claude(1, 'user'), claude(3, 'assistant', 'message-1', 100)], append=True)
        self.proxy(second=3.1, rid='session:message-1', runtime='claude', sid='random-proxy-uuid', output=100)
        sample = p.snapshot(BASE + 4)['samples'][0]
        self.assertEqual(sample['session_key'], opaque('claude', SID))
        self.assertEqual(sample['provider'], 'Anthropic')
        self.assertEqual(sample['basis'], 'native_log')

    def test_claude_exact_proxy_id_can_supply_missing_native_timing(self):
        log = self.claude / 'session.jsonl'
        self.write(log, [])
        p = self.probe()
        self.write(log, [claude(3, 'assistant', 'missing-start', 100)], append=True)
        self.proxy(second=3.1, rid='session:missing-start', runtime='claude', sid=None, output=100)
        packet = p.snapshot(BASE + 4)
        sample = packet['samples'][0]
        self.assertEqual(sample['session_key'], opaque('claude', SID))
        self.assertEqual(sample['basis'], 'proxy_elapsed')
        self.assertEqual(packet['pending_sessions'], [])

    def test_initial_database_lock_does_not_rebaseline_live_rows(self):
        db = sqlite3.connect(self.dbpath)
        try:
            db.execute('BEGIN EXCLUSIVE')
            p = self.probe()
        finally:
            db.rollback()
            db.close()
        self.proxy(second=3)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)

    def test_native_authority_suppresses_codex_proxy_copy(self):
        p = self.probe()
        self.proxy(second=3, runtime='codex')
        self.write(self.log, response(1, 3), append=True)
        self.assertEqual(len(p.snapshot(BASE + 4)['samples']), 1)

    def test_no_created_at_index_reports_instead_of_scanning(self):
        with sqlite3.connect(self.dbpath) as db:
            db.execute('DROP INDEX idx_created')
        p = self.probe()
        self.assertIn('CC-Switch live requests need an existing created_at index', p.snapshot(BASE + 3)['coverage']['issues'])

    def test_database_lock_explicit_and_recovers(self):
        p = self.probe()
        db = sqlite3.connect(self.dbpath)
        try:
            db.execute('BEGIN EXCLUSIVE')
            packet = p.snapshot(BASE + 3)
            self.assertTrue(any('OperationalError' in issue for issue in packet['coverage']['issues']))
        finally:
            db.rollback()
            db.close()
        self.proxy(second=4)
        self.assertEqual(len(p.snapshot(BASE + 5)['samples']), 1)

    def test_no_first_token_duration_fallback(self):
        p = self.probe()
        self.proxy(second=3, duration=0)
        packet = p.snapshot(BASE + 4)
        self.assertEqual(packet['samples'], [])
        self.assertEqual(packet['pending_sessions'][0]['status'], 'missing_timing')


if __name__ == '__main__':
    unittest.main()
