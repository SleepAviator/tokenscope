import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app import collect_snapshot
from daily_archive import DailyArchive, SAVE_INTERVAL, source_identities
from test_usage import row


def snapshot(name, output=20, model='model'):
    request = row(request_id=name, provider_id='_session', model=model,
                  input_tokens=1000, cache_read_tokens=100, cache_creation_tokens=0,
                  output_tokens=output, input_token_semantics=1,
                  session_title='Private chat', session_key='secret-session', total_cost_usd='0.10')
    return name, dict(collected_at='2026-10-08T15:00:00Z', host_timezone='UTC', providers=[],
                      proxy_request_logs=[request], usage_daily_rollups=[])


class DailyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.tmp)
        self.config = self.root / 'config.ini'
        self.config.write_text('[source:a]\ntransport=local\ndatabase=/private/a.db\n'
                               '[source:b]\ntransport=ssh\nhost=b\ndatabase=/private/b.db\n')
        self.folder = self.root / 'archive'

    def collect(self, results, previous=None):
        def fetch(item):
            value = results[item[0]]
            if isinstance(value, Exception):
                raise value
            return value
        with patch('update.fetch', side_effect=fetch):
            return collect_snapshot(self.config, hashlib.sha256(self.config.read_bytes()).hexdigest(), previous)

    def test_restart_offline_then_reconnect_replaces_absolute_counts(self):
        archive = DailyArchive(self.folder, self.config, start_writer=False)
        first = self.collect({'a': snapshot('a'), 'b': snapshot('b', output=40)})
        expected = 1020 + 1040  # Cached input is already contained in the 1000 input total.
        self.assertEqual(sum(r['tokens'] for r in archive.merge(first['data'])['rows']), expected)
        archive.close()
        restored = DailyArchive(self.folder, self.config, start_writer=False)
        self.assertEqual(sum(r['tokens'] for r in restored.startup_snapshot()['rows']), expected)
        offline = self.collect({'a': snapshot('a', output=30), 'b': RuntimeError('offline')})
        merged = restored.merge(offline['data'])
        self.assertEqual(sum(r['tokens'] for r in merged['rows']), 1030 + 1040)
        self.assertEqual(sum(r['tokens'] for r in merged['hourly_rows']), 1030 + 1040)
        self.assertEqual(merged['sources']['b']['status'], 'archived')
        self.assertEqual(merged['sources']['b']['collected_at'], '2026-10-08T15:00:00Z')
        self.assertTrue(any('Offline daily totals are approximate' in w for w in merged['warnings']))
        reconnect = self.collect({'a': snapshot('a', output=30), 'b': snapshot('b', output=50)})
        merged = restored.merge(reconnect['data'])
        self.assertEqual(sum(r['tokens'] for r in merged['rows']), 1030 + 1050)
        self.assertEqual(merged['warnings'], [])
        # Corrections, including a model rename and lower counts, are replacements.
        correction = self.collect({'a': snapshot('a', output=10, model='corrected'), 'b': snapshot('b', output=50)})
        merged = restored.merge(correction['data'])
        self.assertEqual(sum(r['tokens'] for r in merged['rows']), 1010 + 1050)
        self.assertEqual({r['model'] for r in merged['rows'] if r['host'] == 'a'}, {'corrected'})
        restored.close()

    def test_copied_source_reconnect_can_replace_archived_day_with_zero(self):
        archive = DailyArchive(self.folder, self.config, start_writer=False)
        archive.merge(self.collect({'a': snapshot('a'), 'b': snapshot('b')})['data'])
        archive.flush()
        # b now contains only a copied request. Native aggregation assigns it to a.
        copied = self.collect({'a': snapshot('a'), 'b': ('b', snapshot('a')[1])})
        self.assertFalse(any(r['host'] == 'b' for r in copied['data']['rows']))
        merged = archive.merge(copied['data'])
        self.assertEqual(sum(r['tokens'] for r in merged['rows']), 1020)
        self.assertFalse(any(r['host'] == 'b' for r in merged['rows']))
        archive.close()
        restored = DailyArchive(self.folder, self.config, start_writer=False)
        all_offline = self.collect({'a': RuntimeError('offline'), 'b': RuntimeError('offline')})
        self.assertEqual(sum(r['tokens'] for r in restored.merge(all_offline['data'])['rows']), 1020)
        restored.close()

    def test_missing_older_dates_remain_daily_only_and_no_private_details_are_written(self):
        archive = DailyArchive(self.folder, self.config, start_writer=False)
        data = self.collect({'a': snapshot('a'), 'b': snapshot('b')})['data']
        day = data['rows'][0]['date']
        archive.merge(data)
        self.assertEqual(list(self.folder.iterdir()), [])
        archive.flush()
        record = next(self.folder.glob('*.json'))
        saved = record.read_text()
        self.assertNotIn('secret-session', saved)
        self.assertNotIn('Private chat', saved)
        self.assertNotIn('/private/a.db', saved)
        self.assertNotIn('session_rows', saved)
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.folder.stat().st_mode & 0o777, 0o700)
        # Entire older days absent from a later source read are preserved once.
        later = copy.deepcopy(data)
        for r in later['rows'] + later['hourly_rows']:
            r['date'] = '2026-10-08'
        for info in later['sources'].values():
            info['covered_dates'] = ['2026-10-08']
        merged = archive.merge(later)
        self.assertEqual(sum(r['tokens'] for r in merged['rows']), 2 * 2040)
        self.assertTrue(any(r['date'] == day and r['hour'] == 'Unknown hour' for r in merged['hourly_rows']))
        archive.close()

    def test_source_identity_is_independent_and_old_logs_are_preserved(self):
        archive = DailyArchive(self.folder, self.config, start_writer=False)
        archive.merge(self.collect({'a': snapshot('a'), 'b': snapshot('b')})['data'])
        archive.close()
        initial = source_identities(self.config)
        self.config.write_text(self.config.read_text() + 'codex_otel_port=5123\npython=/another/python\n')
        self.assertEqual(source_identities(self.config), initial)
        self.config.write_text(self.config.read_text().replace('database=/private/b.db', 'database=/private/new-b.db'))
        restored = DailyArchive(self.folder, self.config, start_writer=False)
        self.assertEqual({r['host'] for r in restored.startup_snapshot()['rows']}, {'a'})
        restored.merge(self.collect({'a': snapshot('a'), 'b': snapshot('b', output=30)})['data'])
        restored.close()
        identities = {source['identity'] for path in self.folder.glob('*.json')
                      for source in json.loads(path.read_text())['sources']}
        self.assertIn(initial['b'], identities)  # Do not delete an unrelated old input archive.
        self.assertEqual(len(identities), 3)

    def test_initial_checkpoint_then_refreshes_stay_in_ram_until_quit(self):
        archive = DailyArchive(self.folder, self.config)
        data = self.collect({'a': snapshot('a'), 'b': snapshot('b')})['data']
        archive.merge(data)
        deadline = time.monotonic() + 3
        while archive.status()['save_batches'] < 1 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(archive.status()['save_batches'], 1)
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.folder.glob('*.json')}
        for n in range(20):
            changed = copy.deepcopy(data)
            changed['rows'][0]['tokens'] += n
            archive.merge(changed)
        time.sleep(.03)
        self.assertEqual(archive.status()['save_batches'], 1)
        self.assertEqual({p: (p.stat().st_mtime_ns, p.read_bytes()) for p in before}, before)
        archive.close()
        self.assertFalse(archive.worker.is_alive())
        self.assertEqual(archive.status()['save_batches'], 2)
        self.assertEqual(SAVE_INTERVAL, 86400)
        archive.close()

    def test_atomic_failure_preserves_previous_checkpoint_and_invalid_log_fails(self):
        archive = DailyArchive(self.folder, self.config, start_writer=False)
        data = self.collect({'a': snapshot('a'), 'b': snapshot('b')})['data']
        archive.merge(data)
        archive.flush()
        path = next(self.folder.glob('*.json'))
        before = path.read_bytes()
        data['rows'][0]['tokens'] += 100
        archive.merge(data)
        with patch('daily_archive.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                archive.flush()
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(archive.dirty)
        self.assertFalse(list(self.folder.glob('.daily-*')))
        archive.close()
        path.write_text('{broken')
        with self.assertRaises(ValueError):
            DailyArchive(self.folder, self.config, start_writer=False)


if __name__ == '__main__':
    unittest.main()
