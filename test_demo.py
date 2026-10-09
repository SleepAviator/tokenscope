import json
import unittest
from collections import defaultdict
from pathlib import Path
from build_demo import synthetic_data


class SyntheticDemo(unittest.TestCase):
    def test_published_demo_matches_only_synthetic_fixture(self):
        source = (Path(__file__).parent / 'docs' / 'demo-data.js').read_text()
        prefix = 'window.TOKEN_SCOPE_DEMO = '
        self.assertTrue(source.startswith(prefix))
        self.assertEqual(json.loads(source[len(prefix):].rstrip().removesuffix(';')),
                         synthetic_data())

    def test_published_client_assets_match_source(self):
        root = Path(__file__).parent
        for name in ('web.js', 'web.css', 'session_usage.js', 'response_speed.js', 'i18n.js'):
            with self.subTest(asset=name):
                self.assertEqual((root / name).read_bytes(), (root / 'docs' / name).read_bytes())

    def test_public_links_use_current_repository(self):
        root = Path(__file__).parent
        for name in ('README.md', 'README.zh-CN.md', 'docs/index.html'):
            with self.subTest(page=name):
                text = (root / name).read_text()
                self.assertNotIn('Crear12/tokenscope', text)
                self.assertNotIn('crear12.github.io/tokenscope', text)
                self.assertIn('github.com/SleepAviator/tokenscope', text)

    def test_private_history_folders_are_ignored(self):
        patterns = set((Path(__file__).parent / '.gitignore').read_text().splitlines())
        self.assertTrue({'*.ini', 'output/', 'meter-history/', 'daily-usage/',
                         '*.db*', '*.sqlite*', '*.jsonl', '.env', '*.key'} <= patterns)

    def test_deterministic_complete_and_fictional(self):
        data = synthetic_data()
        self.assertEqual(data, synthetic_data())
        self.assertEqual(len(data['rows']), 270)
        self.assertEqual(len({r['date'] for r in data['rows']}), 90)
        self.assertEqual({r['model'] for r in data['rows']}, {'Atlas Code', 'Cedar Think', 'Orbit Local'})
        self.assertTrue(all(r['tokens'] > 0 and float(r['cost_usd']) >= 0 for r in data['rows']))
        by_day_model = defaultdict(lambda: [0, 0.0])
        for row in data['hourly_rows']:
            key = row['date'], row['host'], row['model']
            by_day_model[key][0] += row['tokens']
            by_day_model[key][1] += float(row['cost_usd'])
            self.assertIn(row['hour'], {'09', '14'})
        for row in data['rows']:
            tokens, cost = by_day_model[row['date'], row['host'], row['model']]
            self.assertEqual(tokens, row['tokens'])
            self.assertAlmostEqual(cost, float(row['cost_usd']))
        self.assertEqual(len(data['response_rows']),sum(r['requests'] for r in data['rows']))
        self.assertEqual(data['host_date'],'2026-03-31')
        self.assertTrue(any(r['tps'] is None for r in data['response_rows']))
        self.assertTrue(any(r['first_token_ms'] is None for r in data['response_rows']))
        for row in data['response_rows']:
            if row['tps'] is not None:
                self.assertAlmostEqual(row['tps'],row['output_tokens']*1000/row['duration_ms'])
            if row['first_token_ms'] is not None:
                self.assertGreater(row['first_token_ms'],0)

    def test_monthly_leaders_change(self):
        data = synthetic_data()
        for month, expected in enumerate(('Atlas Code', 'Cedar Think', 'Orbit Local'), 1):
            totals = {}
            for row in data['rows']:
                if int(row['date'][5:7]) == month:
                    totals[row['model']] = totals.get(row['model'], 0) + row['tokens']
            self.assertEqual(max(totals, key=totals.get), expected)

    def test_session_titles_and_components_are_synthetic(self):
        data = synthetic_data()
        titles = {f'{name} · iteration {i+1}' for i in range(9)
                  for name in ('Build a sample dashboard', 'Review a fictional API', 'Explore a demo dataset')}
        for row in data['session_rows']:
            self.assertIn(row['session_title'], titles)
            self.assertIn(row['host'], {'workstation', 'lab'})
            self.assertEqual(row['tokens'], sum(row[k] for k in
                             ('fresh_input_tokens', 'cache_read_tokens', 'cache_creation_tokens', 'output_tokens')))
