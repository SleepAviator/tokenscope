from datetime import datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import Handler
from meter_history import MeterHistory, RANGES, SAVE_INTERVAL
from test_memory_aggregation import FixedDatetime, sources
from update import aggregate


class HistoryTests(unittest.TestCase):
    def test_receipts_are_absolute_and_missing_time_is_not_normalized_away(self):
        history = MeterHistory()
        now = 1791396000
        updates = [{'start': now + 10, 'output_tokens': 3000, 'observed': True, 'partial': False},
                   {'start': now + 11, 'output_tokens': 0, 'observed': True, 'partial': True}]
        history.observe_receipts(updates, now=now + 12)
        history.observe_receipts(updates, now=now + 12)
        rows = history.snapshot(600, now + 12)['receipt_buckets']
        burst = next(row for row in rows if row['output_tokens'])
        self.assertEqual(burst['output_tokens'], 3000)
        self.assertEqual(burst['receipt_tps'], 600)  # 3000 / full five seconds
        self.assertEqual(burst['observed_seconds'], 2)
        self.assertTrue(burst['partial'])
        self.assertEqual(sum(row['output_tokens'] or 0 for row in rows), 3000)
        self.assertTrue(any(row['receipt_tps'] is None for row in rows))
        history.observe_receipts([dict(updates[0], output_tokens=0, partial=True)], now=now + 12)
        self.assertEqual(sum(row['output_tokens'] or 0 for row in history.snapshot(600, now + 12)['receipt_buckets']), 0)
        self.assertEqual(history.receipt_rows[now][2], 0)

    def test_receipt_minutes_are_batched_restored_and_not_mixed_with_estimates(self):
        with tempfile.TemporaryDirectory() as folder:
            history = MeterHistory(folder, start_writer=False)
            now = int(datetime.now().timestamp()) // 60 * 60
            updates = [{'start': now + second, 'output_tokens': 6000 if second == 10 else 0,
                        'observed': True, 'partial': False} for second in range(60)]
            history.observe_receipts(updates, now=now + 60)
            self.assertFalse(list(Path(folder).glob('*.jsonl')))
            self.assertEqual(history.receipt_rows[now], [2, now, 6000, 60, 0])
            history.flush()
            journal = next(Path(folder).glob('receipts-*.jsonl'))
            self.assertEqual(json.loads(journal.read_text()), [2, now, 6000, 60, 0])
            self.assertLess(journal.stat().st_size, 100)
            size = journal.stat().st_size
            history.observe_receipts(updates, now=now + 60)
            history.flush()
            self.assertEqual(journal.stat().st_size, size)
            restored = MeterHistory(folder, start_writer=False)
            rows = restored.snapshot(3600, now + 59)['receipt_buckets']
            halves = [row for row in rows if now <= row['start'] < now + 60]
            self.assertEqual([row['receipt_tps'] for row in halves], [100, 100])
            self.assertEqual(sum(row['receipt_tps'] * 30 for row in halves), 6000)
            self.assertTrue(all(row['coarse_receipts'] for row in halves))
            self.assertIsNone(restored.snapshot(600, now + 59)['total_tps'])
            self.assertEqual(SAVE_INTERVAL, 900)
            history.close();restored.close()

    def test_receipt_torn_tail_recovery_and_old_generation_history(self):
        with tempfile.TemporaryDirectory() as folder:
            history = MeterHistory(folder, start_writer=False)
            now = int(datetime.now().timestamp()) // 60 * 60
            history.observe({'total_tps': 15, 'average_tps': 15, 'status': 'complete'}, now, 0)
            history.observe({'total_tps': 15, 'average_tps': 15, 'status': 'complete'}, now + 1, 1)
            history.observe_receipts([{'start': now + 2, 'output_tokens': 300,
                                      'observed': True, 'partial': False}], now=now + 3)
            history.close()
            journal = next(Path(folder).glob('receipts-*.jsonl'))
            with journal.open('ab') as stream:stream.write(b'[2,')
            restored = MeterHistory(folder, start_writer=False)
            result = restored.snapshot(600, now + 4)
            self.assertEqual(result['total_tps'], 15)
            self.assertEqual(sum(row['output_tokens'] or 0 for row in result['receipt_buckets']), 300)
            self.assertIsNotNone(restored.storage_error)
            restored.observe_receipts([{'start': now + 4, 'output_tokens': 200,
                                       'observed': True, 'partial': False}], now=now + 5)
            restored.close()
            self.assertTrue(journal.read_bytes().endswith(b'\n'))
            again = MeterHistory(folder, start_writer=False)
            self.assertEqual(again.receipt_rows[now][2], 500)
            again.close()

    def test_all_receipt_ranges_conserve_tokens_and_do_not_relabel_legacy_rates(self):
        history = MeterHistory()
        now = 1791396000
        for second in range(60):
            history.observe_receipts([{'start': now + second, 'output_tokens': 1200 if second == 30 else 0,
                                      'observed': True, 'partial': False}], now=now + 60)
        for period in RANGES:
            with self.subTest(period=period):
                result = history.snapshot(period, now + 59)
                rows = result['receipt_buckets']
                self.assertEqual(len(rows), 120)
                self.assertAlmostEqual(sum((row['receipt_tps'] or 0) * result['bucket_seconds'] for row in rows), 1200)
                self.assertLessEqual(len(history.receipt_seconds), 600)
        legacy = MeterHistory()
        legacy.observe({'total_tps': 15, 'average_tps': 15, 'status': 'complete'}, now, 0)
        legacy.observe({'total_tps': 15, 'average_tps': 15, 'status': 'complete'}, now + 1, 1)
        self.assertTrue(all(row['receipt_tps'] is None for row in legacy.snapshot(600, now + 1)['receipt_buckets']))

    def test_receipt_reconciliation_keeps_previous_bin_across_wall_clock_jump(self):
        history = MeterHistory()
        now = 1791396000
        item = {'start': now + 1, 'output_tokens': 500, 'observed': True, 'partial': False}
        history.observe_receipts([item], now=now + 2)
        history.observe_receipts([{'start': now + 3600, 'output_tokens': 0,
                                  'observed': True, 'partial': True}], now=now + 3601)
        history.observe_receipts([dict(item, output_tokens=0, partial=True)], now=now + 3601)
        self.assertEqual(history.receipt_rows[now][2], 0)

    def test_three_sessions_and_gaps_have_weighted_rates(self):
        history = MeterHistory()
        now = 1791396000
        live = {'total_tps': 60 + 40 + 20, 'average_tps': 40, 'status': 'complete'}
        for second in range(61):
            history.observe(live, now + second, second)
        snapshot = history.snapshot(600, now + 61)
        self.assertEqual(snapshot['total_tps'], 120)
        self.assertEqual(snapshot['average_tps'], 40)
        self.assertEqual(snapshot['observed_seconds'], 60)
        self.assertIsNone(snapshot['total_tokens'])
        # Sleep and wall-clock jumps do not fill missing time or invent zero TPS.
        history.observe(live, now + 180, 180)
        history.observe({'total_tps': None, 'average_tps': None, 'status': 'partial'}, now + 181, 181)
        history.observe(live, now + 182, 182)
        self.assertEqual(history.snapshot(600, now + 183)['observed_seconds'], 61)
        history.observe(live, now + 500, 183)
        self.assertEqual(history.snapshot(600, now + 501)['observed_seconds'], 61)
        self.assertIsNone(history.snapshot(600, now + 1200)['total_tps'])

    def test_usage_is_absolute_and_partial_sources_cannot_erase_counts(self):
        history = MeterHistory()
        minute = 1791396000
        projection = {'start': minute, 'end': minute + 60, 'minutes': {str(minute): 1100},
                      'day_only': {}, 'available': True, 'partial': False}
        history.account(projection)
        history.account(projection)
        self.assertEqual(history.snapshot(600, minute + 30)['total_tokens'], 1100)
        history.account(dict(projection, minutes={}, available=False, partial=True))
        history.account(dict(projection, minutes={}, partial=True))
        self.assertEqual(history.snapshot(600, minute + 30)['total_tokens'], 1100)
        self.assertTrue(history.snapshot(600, minute + 30)['partial'])
        history.account(dict(projection, minutes={str(minute): 1200}))
        self.assertEqual(history.snapshot(600, minute + 30)['total_tokens'], 1200)

    def test_ten_minutes_have_one_second_ram_points_and_real_gaps(self):
        history = MeterHistory()
        now = 1791396000
        for second in range(650):
            if second == 600:
                continue
            history.observe({'total_tps': 120 + second, 'average_tps': 40, 'status': 'complete'},
                            now + second, second)
        result = history.snapshot(600, now + 649)
        self.assertEqual(len(result['rate_buckets']), 600)
        self.assertEqual(result['rate_bucket_seconds'], 1)
        self.assertEqual(result['rate_start'], now + 50)
        self.assertEqual(result['rate_buckets'][0]['total_tps'], 170)
        self.assertEqual(result['rate_buckets'][-1]['total_tps'], 769)
        missing = result['rate_buckets'][550]
        self.assertIsNone(missing['total_tps'])
        self.assertTrue(missing['partial'])
        self.assertLessEqual(len(history.seconds), 600)
        self.assertEqual(len(result['buckets']), 120)
        self.assertNotIn('rate_buckets', history.snapshot(3600, now + 649))
        self.assertTrue(all(r['total_tps'] is None for r in history.snapshot(600, now + 1300)['rate_buckets']))

    def test_hour_has_distinct_half_minute_readings_without_extra_journal_rows(self):
        history = MeterHistory()
        now = 1791396000
        for second in range(3600):
            rate = 60 if second % 60 < 30 else 180
            history.observe({'total_tps': rate, 'average_tps': rate / 3, 'status': 'complete'},
                            now + second, second)
        result = history.snapshot(3600, now + 3599.5)
        self.assertEqual(len(result['buckets']), 120)
        self.assertEqual(result['bucket_seconds'], 30)
        self.assertEqual(result['buckets'][0]['total_tps'], 60)
        self.assertEqual(result['buckets'][1]['total_tps'], 180)
        self.assertFalse(result['buckets'][0]['coarse_rate'])
        self.assertLessEqual(len(history.fine_rates), 733)
        self.assertEqual(len(history.rows), 60)
        self.assertTrue(all(history._valid(row) for row in history.dirty.values()))

    def test_fine_usage_preserves_real_timing_and_does_not_replace_larger_partial_totals(self):
        history = MeterHistory()
        now = 1791396000
        projection = {'start': now, 'end': now + 60, 'minutes': {str(now): 1100},
                      'day_only': {}, 'available': True, 'partial': False,
                      'fine': {'start': now, 'end': now + 60,
                               'counts': {str(now + 5): 1000, str(now + 35): 100}}}
        history.account(projection)
        result = history.snapshot(3600, now + 59)
        halves = [row for row in result['buckets'] if now <= row['start'] < now + 60]
        self.assertEqual([row['tokens'] for row in halves], [1000, 100])
        self.assertTrue(all(not row['coarse_tokens'] for row in halves))
        self.assertEqual(result['total_tokens'], 1100)
        short = history.snapshot(600, now + 59)
        nonzero = [(row['start'], row['tokens']) for row in short['buckets'] if row['tokens']]
        self.assertEqual(nonzero, [(now + 5, 1000), (now + 35, 100)])
        history.account(dict(projection, partial=True, fine={'start': now, 'end': now + 60, 'counts': {}}))
        self.assertEqual(history.snapshot(3600, now + 59)['total_tokens'], 1100)
        history.account(dict(projection, partial=True, minutes={str(now): 1200}))
        fallback = history.snapshot(3600, now + 59)
        halves = [row for row in fallback['buckets'] if now <= row['start'] < now + 60]
        self.assertEqual([row['tokens'] for row in halves], [1200, None])
        self.assertTrue(halves[0]['coarse_tokens'])
        history.account(projection)
        self.assertEqual(history.snapshot(3600, now + 59)['total_tokens'], 1100)
        self.assertEqual(len(history.rows), 1)

    def test_restored_minute_means_remain_coarse_and_counts_are_not_split(self):
        with tempfile.TemporaryDirectory() as folder:
            history = MeterHistory(folder, start_writer=False)
            now = int(datetime.now().timestamp()) // 60 * 60
            for second in range(61):
                history.observe({'total_tps': 120, 'average_tps': 40, 'status': 'complete'},
                                now + second, second)
            history.account({'start': now, 'end': now + 60, 'minutes': {str(now): 1000},
                             'day_only': {}, 'available': True, 'partial': False})
            history.close()
            restored = MeterHistory(folder, start_writer=False)
            result = restored.snapshot(3600, now + 59)
            halves = [row for row in result['buckets'] if now <= row['start'] < now + 60]
            self.assertEqual([row['total_tps'] for row in halves], [120, 120])
            self.assertTrue(all(row['coarse_rate'] for row in halves))
            self.assertEqual([row['tokens'] for row in halves], [1000, None])
            self.assertEqual(result['total_tokens'], 1000)
            restored.close()

    def test_cache_is_included_once_and_copied_requests_are_deduplicated(self):
        data = sources()
        for _, source in data:
            for index, request in enumerate(source['proxy_request_logs']):
                request['input_token_semantics'] = 1
                request['created_at'] = 1791396000 - 3000 + index * 900
        with patch('update.datetime', FixedDatetime):
            result = aggregate(data, meter_history=True)
        usage = result['meter_usage']
        self.assertEqual(sum(usage['minutes'].values()), 3 * 1020)
        self.assertEqual(sum(usage['fine']['counts'].values()), 3 * 1020)
        self.assertEqual(sum(usage['day_only'].values()), 2 * 1120)
        self.assertNotIn('meter_usage', aggregate(data))
        self.assertFalse(any(isinstance(v, str) for v in usage['minutes'].values()))

    def test_no_tick_writes_and_one_batched_numeric_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'history'
            history = MeterHistory(folder, start_writer=False)
            now = int(datetime.now().timestamp()) // 60 * 60
            for second in range(121):
                history.observe({'total_tps': 120, 'average_tps': 40, 'status': 'complete'},
                                now + second, second)
            self.assertEqual(list(folder.iterdir()), [])
            history.flush()
            journals = list(folder.glob('*.jsonl'))
            self.assertEqual(len(journals), 1)
            rows = [json.loads(line) for line in journals[0].read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(all(type(v) in (int, float) for v in row) for row in rows))
            self.assertEqual(journals[0].stat().st_mode & 0o777, 0o600)
            size = journals[0].stat().st_size
            history.flush()
            self.assertEqual(journals[0].stat().st_size, size)
            restored = MeterHistory(folder, start_writer=False)
            self.assertEqual(restored.snapshot(600, now + 121)['total_tps'], 120)
            self.assertTrue(all(r['total_tps'] is None for r in restored.snapshot(600, now + 121)['rate_buckets']))
            self.assertEqual(restored.bytes_written, 0)
            history.close()
            history.close()
            restored.close()
            self.assertEqual(SAVE_INTERVAL, 900)

    def test_shutdown_flush_and_torn_tail_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'history'
            history = MeterHistory(folder)
            now = int(datetime.now().timestamp()) // 60 * 60
            history.account({'start': now, 'end': now + 60, 'minutes': {str(now): 1020},
                             'day_only': {}, 'available': True, 'partial': False})
            self.assertEqual(history.save_batches, 0)
            self.assertEqual(list(folder.iterdir()), [])
            history.close()
            self.assertFalse(history.worker.is_alive())
            journal = next(folder.glob('*.jsonl'))
            with journal.open('ab') as stream:
                stream.write(b'[1,')
            restored = MeterHistory(folder, start_writer=False)
            self.assertIsNotNone(restored.storage_error)
            self.assertEqual(restored.snapshot(600, now + 30)['total_tokens'], 1020)
            restored.account({'start': now, 'end': now + 60, 'minutes': {str(now): 1200},
                              'day_only': {}, 'available': True, 'partial': False})
            restored.close()
            self.assertTrue(journal.read_bytes().endswith(b'\n'))
            again = MeterHistory(folder, start_writer=False)
            self.assertEqual(again.snapshot(600, now + 30)['total_tokens'], 1200)
            again.close()

    def test_outages_do_not_dirty_unchanged_past_and_oversized_journals_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = MeterHistory(tmp, start_writer=False)
            now = int(datetime.now().timestamp()) // 60 * 60
            projection = {'start': now, 'end': now + 60, 'minutes': {str(now): 100},
                          'day_only': {}, 'available': True, 'partial': False}
            history.account(projection)
            history.flush()
            history.account(dict(projection, minutes={}, partial=True))
            self.assertFalse(history.dirty)
            journal = next(Path(tmp).glob('*.jsonl'))
            original = journal.read_bytes()
            journal.write_bytes(original * (2 * 1024 * 1024 // len(original) + 1))
            history.account(dict(projection, minutes={str(now): 120}))
            history.flush()
            self.assertLess(journal.stat().st_size, 1024)
            restored = MeterHistory(tmp, start_writer=False)
            self.assertEqual(restored.snapshot(600, now + 30)['total_tokens'], 120)
            self.assertFalse(list(Path(tmp).glob('.compact-*')))
            restored.close()
            history.close()

    def test_daily_totals_are_not_spread_into_minute_bars(self):
        history = MeterHistory()
        day = datetime(2026, 10, 6)
        begin = int(day.timestamp())
        end = begin + 3 * 86400
        projection = {'start': begin, 'end': end, 'minutes': {}, 'day_only': {'2026-10-06': 500},
                      'available': True, 'partial': False}
        history.account(projection)
        whole = history.snapshot(7 * 86400, end - 60)
        self.assertEqual(whole['total_tokens'], 500)
        self.assertEqual(whole['day_only_tokens'], 500)
        self.assertEqual(sum(b['tokens'] or 0 for b in whole['buckets']), 0)
        self.assertEqual(history.snapshot(600, begin + 300)['day_only_tokens'], 0)
        history.account(dict(projection, day_only={}, minutes={str(begin): 500}))
        self.assertEqual(history.snapshot(7 * 86400, end - 60)['total_tokens'], 500)
        self.assertEqual(history.snapshot(7 * 86400, end - 60)['day_only_tokens'], 0)

    def test_ranges_are_bounded_and_private(self):
        class PeerHandler(Handler):
            def do_GET(self):
                self.client_address = (self.server.peer, self.client_address[1])
                super().do_GET()
        server = ThreadingHTTPServer(('127.0.0.1', 0), PeerHandler)
        server.peer = '127.0.0.1'
        server.meter_history = MeterHistory()
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def call(query):
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request('GET', '/api/live/history?' + query, headers={'Host': 'localhost'})
            response = connection.getresponse()
            status, data = response.status, json.loads(response.read())
            connection.close()
            return status, data
        try:
            for period in RANGES:
                status, data = call('range=' + str(period))
                self.assertEqual(status, 200)
                self.assertEqual(len(data['buckets']), 120)
                self.assertLess(len(json.dumps(data)), 128 * 1024)
                self.assertNotIn('path', json.dumps(data))
            for query in ('range=1', 'range=600&range=3600', 'range=600&extra=1', '', 'range=no'):
                self.assertEqual(call(query)[0], 400)
            server.peer = '192.168.1.2'
            self.assertEqual(call('range=600')[0], 403)
            server.peer = '127.0.0.1'
            server.meter_history = None
            self.assertEqual(call('range=600')[0], 503)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
