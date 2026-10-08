import csv
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_usage import row
from update import aggregate, build


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(1791396000, tz)


def sources():
    requests = [
        row(request_id='timed', provider_id='provider', data_source='proxy',
            date='2026-10-06', total_cost_usd='0.10', session_key='session',
            duration_ms=1000, first_token_ms=100, session_title='Example'),
        row(request_id='native', provider_id='_session', date='2026-10-07',
            total_cost_usd='0.20', session_key='session', native_response_ms=500),
        row(request_id='untimed', provider_id='_session', date='2026-10-07',
            total_cost_usd='0.30'),
    ]
    rollup = row(provider_id='_session', date='2026-10-01', request_count=3,
                 total_cost_usd='0.40')
    data = dict(collected_at='2026-10-07T18:00:00Z', host_timezone='UTC',
                providers=[dict(id='provider', app_type='codex', name='Provider')],
                proxy_request_logs=requests, usage_daily_rollups=[rollup])
    # Copied requests are deduplicated; matching rollups retain their original grain.
    return [('one', data), ('two', data)]


class MemoryAggregation(unittest.TestCase):
    def test_computation_never_touches_the_filesystem(self):
        with patch('builtins.open', side_effect=AssertionError('unexpected I/O')), \
                patch.object(Path, 'open', side_effect=AssertionError('unexpected I/O')), \
                patch.object(Path, 'mkdir', side_effect=AssertionError('unexpected I/O')), \
                patch('update.write_csv', side_effect=AssertionError('unexpected export')), \
                patch('update.render', side_effect=AssertionError('unexpected plot')):
            result = aggregate(sources())
        self.assertEqual(result['summary']['cross_host_duplicates_removed'], 3)
        self.assertEqual(result['summary']['requests'], 9)
        self.assertEqual(result['summary']['total_tokens'], 5 * 1120)
        self.assertEqual(result['summary']['total_cost_usd'], '1.40')
        self.assertEqual(len(result['responses']), 3)
        self.assertEqual(len(result['samples']), 1)
        self.assertTrue(all(isinstance(item['hours'], dict)
                            for item in result['session_daily']))

    def test_explicit_exports_preserve_the_in_memory_results(self):
        names = {'daily_usage.csv': 'daily', 'hourly_usage.csv': 'hourly',
                 'session_daily_usage.csv': 'session_daily', 'request_tps.csv': 'samples',
                 'response_speed.csv': 'responses'}
        with patch('update.datetime', FixedDatetime), tempfile.TemporaryDirectory() as directory:
            expected = aggregate(sources())
            returned = build(sources(), Path(directory), render_figures=False)
            self.assertEqual(returned, expected['summary'])
            self.assertEqual(json.loads((Path(directory) / 'summary.json').read_text()),
                             expected['summary'])
            for filename, key in names.items():
                with self.subTest(filename=filename):
                    with (Path(directory) / filename).open(newline='') as stream:
                        actual = list(csv.DictReader(stream))
                    prepared = []
                    for original in expected[key]:
                        record = dict(original)
                        if key == 'session_daily':
                            record['hours'] = json.dumps(record['hours'], sort_keys=True)
                        prepared.append({name: '' if value is None else str(value)
                                         for name, value in record.items()})
                    self.assertEqual(actual, prepared)

    def test_conflicting_request_identity_still_fails_explicitly(self):
        collected = sources()
        modified = dict(collected[1][1])
        modified['proxy_request_logs'] = [dict(request, output_tokens=21)
                                          for request in modified['proxy_request_logs']]
        with self.assertRaisesRegex(ValueError, 'Conflicting cross-host request ID'):
            aggregate([collected[0], ('two', modified)])


if __name__ == '__main__':
    unittest.main()
