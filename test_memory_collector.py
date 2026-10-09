import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from app import (Collector, MAX_WORKER_FRAME, public_data, public_snapshot,
                 read_worker_frame, write_worker_frame)
from collect import collect
from daily_archive import DailyArchive
from test_memory_aggregation import FixedDatetime, sources
from update import aggregate, build


class ShortReads(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 3))


class MemoryTransportTests(unittest.TestCase):
    def test_frames_survive_partial_reads_and_multiple_results(self):
        stream = io.BytesIO()
        values = [{'data': {'label': '测试', 'tokens': 120}}, {'error': 'offline'}]
        for value in values:
            write_worker_frame(stream, value)
        reader = ShortReads(stream.getvalue())
        self.assertEqual([read_worker_frame(reader) for _ in values], values)
        self.assertIsNone(read_worker_frame(reader))

    def test_bad_frames_fail_explicitly(self):
        for body in (b'invalid\n{}', b'0\n', str(MAX_WORKER_FRAME + 1).encode() + b'\n',
                     b'4\n{}', b'2\n[]'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                read_worker_frame(io.BytesIO(body))
        with self.assertRaises(ValueError):
            write_worker_frame(io.BytesIO(), {'rate': float('nan')})

    def test_ram_projection_preserves_the_exported_dashboard_contract(self):
        with tempfile.TemporaryDirectory() as directory, patch('update.datetime', FixedDatetime):
            result = aggregate(sources())
            build(sources(), Path(directory), render_figures=False)
            self.assertEqual(public_snapshot(result), public_data(Path(directory)))


def local_fixture(root):
    database = root / 'source.db'
    fields = {
        'providers': 'id app_type name',
        'proxy_request_logs': ('request_id provider_id app_type model session_id input_tokens output_tokens '
                               'cache_read_tokens cache_creation_tokens total_cost_usd latency_ms '
                               'first_token_ms duration_ms status_code created_at data_source'),
        'usage_daily_rollups': ('date provider_id app_type model request_count success_count input_tokens '
                               'output_tokens cache_read_tokens cache_creation_tokens total_cost_usd'),
    }
    with sqlite3.connect(database) as connection:
        for table, names in fields.items():
            connection.execute('CREATE TABLE ' + table + '(' + ','.join(names.split()) + ')')
        connection.execute('INSERT INTO providers VALUES(?,?,?)', ('provider', 'codex', 'Example'))
        connection.execute('INSERT INTO proxy_request_logs VALUES(' + ','.join('?' * 16) + ')',
                           ('response', 'provider', 'codex', 'model', 'session', 100, 20,
                            0, 0, '0.10', 1000, 100, 1000, 200, 1791396000, 'proxy'))
    config = root / 'config.ini'
    config.write_text('[source:local]\ntransport=local\ndatabase=' + str(database) +
                      '\ncodex_session_roots=' + str(root / 'absent-sessions') +
                      '\ncodex_home=' + str(root / 'absent-codex') +
                      '\nclaude_projects=' + str(root / 'absent-claude') + '\ncodex_native_tps=false\n')
    return config, database


class PersistentMemoryCollectorTests(unittest.TestCase):
    def await_result(self, collector):
        deadline = time.monotonic() + 10
        while collector.status()['refreshing'] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(collector.status()['refreshing'], 'Worker did not finish')
        self.assertIsNone(collector.status()['error'])

    def test_refreshes_reuse_worker_and_leave_source_and_seed_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, database = local_fixture(root)
            cache = root / 'startup.json'
            cache.write_text(json.dumps({'config_hash': hashlib.sha256(config.read_bytes()).hexdigest(),
                                         'data': {'generated_at': 'seed', 'rows': []}}))
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()}
            collector = Collector(config, 300, cache)
            try:
                for version in (2, 3):
                    self.assertTrue(collector.refresh())
                    self.await_result(collector)
                    self.assertEqual(collector.version, version)
                    self.assertEqual(sum(row['tokens'] for row in collector.data['rows']), 120)
                    if version == 2:
                        pid = collector.process.pid
                    self.assertEqual(collector.process.pid, pid)
                after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()}
                self.assertEqual(after, before)
                self.assertEqual(collector.status()['storage'], 'memory')
                # A missing source retains this process's last successful raw snapshot.
                database.rename(root / 'offline.db')
                self.assertTrue(collector.refresh())
                self.await_result(collector)
                self.assertEqual(collector.process.pid, pid)
                self.assertEqual(collector.data['sources']['local']['status'], 'stale')
                self.assertEqual(sum(row['tokens'] for row in collector.data['rows']), 120)
                (root / 'offline.db').rename(database)
            finally:
                process = collector.process
                collector.close()
            self.assertIsNotNone(process.poll())
            self.assertTrue(all(not reader.is_alive() for reader in collector.readers))
            # An ordinary new process does not pick up a cache implicitly.
            restarted = Collector(config, 300)
            self.assertIsNone(restarted.data)
            restarted.close()

    def test_worker_crash_retains_data_and_next_refresh_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = local_fixture(Path(directory))
            collector = Collector(config, 300)
            try:
                self.assertTrue(collector.refresh())
                self.await_result(collector)
                previous, pid = collector.data, collector.process.pid
                collector.process.kill()
                collector.process.wait(timeout=3)
                deadline = time.monotonic() + 3
                while not collector.worker_failed and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(collector.worker_failed)
                self.assertIs(collector.data, previous)
                self.assertIn('Previous data retained', collector.error)
                self.assertTrue(collector.refresh())
                self.await_result(collector)
                self.assertNotEqual(collector.process.pid, pid)
                self.assertEqual(collector.data['sources']['local']['status'], 'fresh')
            finally:
                collector.close()

    def test_daily_archive_survives_worker_restart_offline_and_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, database = local_fixture(root)
            before = database.read_bytes(), database.stat().st_mtime_ns
            folder = root / 'daily'
            archive = DailyArchive(folder, config, start_writer=False)
            collector = Collector(config, 300)
            collector.archive = archive
            try:
                self.assertTrue(collector.refresh())
                self.await_result(collector)
                self.assertEqual(sum(r['tokens'] for r in collector.data['rows']), 120)
                self.assertTrue(collector.status()['daily_archive']['enabled'])
                self.assertEqual(list(folder.iterdir()), [])
            finally:
                collector.close()
                archive.close()
            restored = DailyArchive(folder, config, start_writer=False)
            restarted = Collector(config, 300)
            restarted.archive = restored
            restarted.data = restored.startup_snapshot()
            database.rename(root / 'offline.db')
            try:
                self.assertTrue(restarted.refresh())
                self.await_result(restarted)
                self.assertEqual(restarted.data['sources']['local']['status'], 'archived')
                self.assertEqual(sum(r['tokens'] for r in restarted.data['rows']), 120)
                (root / 'offline.db').rename(database)
                self.assertTrue(restarted.refresh())
                self.await_result(restarted)
                self.assertEqual(restarted.data['sources']['local']['status'], 'fresh')
                self.assertEqual(sum(r['tokens'] for r in restarted.data['rows']), 120)
                self.assertEqual(restarted.data['warnings'], [])
                self.assertEqual((database.read_bytes(), database.stat().st_mtime_ns), before)
            finally:
                restarted.close()
                restored.close()

    def test_failed_source_releases_read_lock_without_garbage_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, database = local_fixture(root)
            with sqlite3.connect(database) as connection:
                connection.execute('DROP TABLE usage_daily_rollups')
            with self.assertRaisesRegex(RuntimeError, 'Required CC-Switch table missing'):
                collect(str(database), [], str(root / 'absent'), str(root / 'absent'), False)
            with sqlite3.connect(database, timeout=0) as connection:
                connection.execute('BEGIN EXCLUSIVE')
                connection.rollback()


if __name__ == '__main__':
    unittest.main()
