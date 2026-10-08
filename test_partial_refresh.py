import hashlib
import builtins
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import collect_snapshot
from test_usage import row


class PartialRefreshTests(unittest.TestCase):
    def test_failure_recovery_and_new_host_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root/'config.ini'
            config.write_text('[source:a]\ntransport=local\n[source:b]\ntransport=ssh\n[source:c]\ntransport=ssh\n')
            fingerprint = hashlib.sha256(config.read_bytes()).hexdigest()
            def source(name, stamp, tokens):
                request = row(request_id=name, provider_id='_session', date='2026-01-01', input_tokens=tokens, cache_read_tokens=0,
                              cache_creation_tokens=0, output_tokens=0, session_key=name, total_cost_usd='0.10')
                return name, dict(collected_at=stamp, host_timezone='UTC', providers=[],
                                  proxy_request_logs=[request], usage_daily_rollups=[])
            previous = None
            def run(results):
                nonlocal previous
                def fetch(item):
                    result = results[item[0]]
                    if isinstance(result, Exception):
                        raise result
                    return result
                with patch('update.fetch', side_effect=fetch):
                    previous = collect_snapshot(config, fingerprint, previous)
                return previous
            offline = RuntimeError('SSH connection timed out')
            first = run({'a':source('a','old',10),'b':source('b','old',20),'c':offline})
            self.assertEqual(first['data']['sources']['c']['status'], 'unavailable')
            second = run({'a':source('a','new',30),'b':offline,'c':offline})
            self.assertEqual({r['host']:r['tokens'] for r in second['data']['rows']}, {'a':30,'b':20})
            self.assertEqual(sum(r['tokens'] for r in second['data']['hourly_rows']), 50)
            self.assertEqual(second['data']['sources']['b']['collected_at'], 'old')
            self.assertEqual(second['data']['sources']['b']['status'], 'stale')
            self.assertEqual(second['data']['sources']['a']['status'], 'fresh')
            all_failed = run({'a':offline,'b':offline,'c':offline})
            self.assertEqual(all_failed['data']['rows'], second['data']['rows'])
            recovered = run({'a':source('a','recovered',40),'b':source('b','recovered',50),'c':source('c','recovered',60)})
            self.assertEqual(recovered['data']['warnings'], [])
            self.assertEqual(sum(r['tokens'] for r in recovered['data']['rows']),150)
            self.assertEqual(set(recovered['source_snapshots']), {'a','b','c'})
            # Older versions stored aggregates only. Offline data must survive upgrade.
            previous = {'config_hash':fingerprint,'data':recovered['data']}
            legacy = run({'a':source('a','latest',70),'b':offline,'c':offline})
            self.assertEqual(sum(r['tokens'] for r in legacy['data']['rows']),180)
            self.assertEqual(sum(r['tokens'] for r in legacy['data']['hourly_rows']),180)
            self.assertTrue(any('deduplication' in w for w in legacy['data']['warnings']))
            self.assertEqual(set(root.iterdir()), {config})

    def test_first_run_all_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.ini'
            config.write_text('[source:a]\ntransport=ssh\n')
            with patch('update.fetch', side_effect=RuntimeError('offline')):
                data=collect_snapshot(config, 'new')['data']
            self.assertEqual(data['rows'], [])
            self.assertEqual(data['sources']['a']['status'], 'unavailable')
            self.assertTrue(data['warnings'])

    def test_config_hash_mismatch_discards_previous_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.ini'
            config.write_text('[source:a]\ntransport=ssh\n')
            previous = {'config_hash': 'different',
                        'data': {'sources': {'a': {'collected_at': 'old', 'timezone': 'UTC'}},
                                 'rows': [{'host': 'a', 'tokens': 100}]},
                        'source_snapshots': {'a': {'collected_at': 'old'}}}
            with patch('update.fetch', side_effect=RuntimeError('offline')):
                result = collect_snapshot(config, 'current', previous)
            self.assertEqual(result['source_snapshots'], {})
            self.assertEqual(result['data']['rows'], [])
            self.assertEqual(result['data']['sources']['a']['status'], 'unavailable')

    def test_refresh_does_not_create_cache_or_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.ini'
            config.write_text('[source:a]\ntransport=local\n')
            request = row(request_id='a', provider_id='_session', date='2026-01-01',
                          input_tokens=10, cache_read_tokens=0, cache_creation_tokens=0,
                          output_tokens=0, session_key='a', total_cost_usd='0.10')
            snapshot = ('a', {'collected_at': 'now', 'host_timezone': 'UTC', 'providers': [],
                              'proxy_request_logs': [request], 'usage_daily_rollups': []})
            original_open, original_io_open = builtins.open, io.open
            def read_only_open(opener):
                def guarded(file, mode='r', *args, **kwargs):
                    self.assertFalse(any(flag in mode for flag in 'wax+'), mode)
                    return opener(file, mode, *args, **kwargs)
                return guarded
            before = config.read_bytes(), config.stat().st_mtime_ns
            with patch('update.fetch', return_value=snapshot), \
                    patch('builtins.open', side_effect=read_only_open(original_open)), \
                    patch('io.open', side_effect=read_only_open(original_io_open)), \
                    patch('tempfile.TemporaryDirectory', side_effect=AssertionError('temporary export')), \
                    patch('update.build', side_effect=AssertionError('disk export')):
                first = collect_snapshot(config, 'current')
                second = collect_snapshot(config, 'current', first)
            self.assertEqual(second['data']['rows'][0]['tokens'], 10)
            self.assertEqual(set(root.iterdir()), {config})
            self.assertEqual((config.read_bytes(), config.stat().st_mtime_ns), before)
