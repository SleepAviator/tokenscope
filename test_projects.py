import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from collect import enrich_session_titles, enrich_session_providers, project_metadata
from test_usage import row
from update import build
from app import public_data


class Projects(unittest.TestCase):
    def test_path_identity_and_redaction(self):
        self.assertIsNone(project_metadata('relative/folder'))
        self.assertEqual(project_metadata('C:\\Work\\Example')[0], project_metadata('c:/work/example/')[0])
        self.assertEqual(project_metadata('/one/Example')[1], 'Example')
        self.assertNotEqual(project_metadata('/one/Example')[0], project_metadata('/two/Example')[0])
        self.assertEqual(project_metadata(r'\\fictional-private-host\share')[1], 'Filesystem root')
        self.assertNotIn('fictional-private-host', project_metadata(r'\\fictional-private-host\share')[1])

    def test_cloud_relative_identity(self):
        variants = [
            '/Users/alice/Library/CloudStorage/Dropbox/Research/Example',
            '/Users/bob/Dropbox/Research/Example/',
            'D:\\Dropbox\\Research\\Example',
            'C:\\Users\\bob\\Dropbox (Personal)\\Research\\Example',
        ]
        expected = project_metadata(variants[0])
        self.assertTrue(all(project_metadata(path) == expected for path in variants))
        self.assertNotEqual(expected[0], project_metadata('/Users/bob/Dropbox/Personal/Example')[0])
        self.assertNotEqual(expected[0], project_metadata('/Users/bob/OneDrive/Research/Example')[0])
        self.assertNotEqual(expected[0], project_metadata('/Users/bob/NotDropbox/Research/Example')[0])
        self.assertNotEqual(expected[0], project_metadata('/Users/bob/Dropbox/Research/example')[0])
        self.assertNotIn('Research', expected[0])
        self.assertNotEqual(project_metadata('/Users/a/Dropbox/../Example')[0],
                            project_metadata('/Users/b/Dropbox/../Example')[0])
        self.assertNotEqual(project_metadata('/srv/app-one/box/Example')[0],
                            project_metadata('/srv/app-two/box/Example')[0])
        self.assertEqual(project_metadata('/Users/a/Library/CloudStorage/GoogleDrive-private@example.test')[1],
                         'Google Drive')

    def test_other_cloud_roots(self):
        for variants in [
            ['/Users/a/Library/CloudStorage/OneDrive-Personal/Code/Example',
             'C:\\Users\\b\\OneDrive - Fictional Org\\Code\\Example'],
            ['/Users/a/Library/CloudStorage/GoogleDrive-example@example.test/My Drive/Code/Example',
             'G:\\My Drive\\Code\\Example', '/home/b/Google Drive/Code/Example'],
            ['/Users/a/Library/Mobile Documents/com~apple~CloudDocs/Code/Example',
             'C:\\Users\\b\\iCloudDrive\\Code\\Example'],
            ['/Users/a/Library/CloudStorage/Box-Box/Code/Example', 'C:\\Users\\b\\Box\\Code\\Example'],
        ]:
            self.assertEqual(len({project_metadata(path) for path in variants}), 1)

    def test_codex_state_and_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            db = sqlite3.connect(home/'state_5.sqlite')
            db.execute('CREATE TABLE threads(id TEXT,cwd TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?)', ('state', '/private/fictional/project'))
            db.commit(); db.close()
            rows = [row(session_id='state')]
            enrich_session_titles(rows, str(home), str(home/'claude'), [])
            self.assertEqual(rows[0]['project_name'], 'project')
            sid = '00000000-0000-0000-0000-000000000001'
            (home/('rollout-'+sid+'.jsonl')).write_text(json.dumps({'type':'session_meta','payload':{'id':sid,'cwd':'/private/fictional/header'}}))
            rows.append(row(session_id=sid))
            enrich_session_providers(rows, [home])
            self.assertEqual(rows[1]['project_name'], 'header')
            self.assertNotIn('/private/fictional', json.dumps(rows))

    def test_codex_state_header_conflict_is_unassigned(self):
        sid = '00000000-0000-0000-0000-000000000002'
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            db = sqlite3.connect(home/'state_5.sqlite')
            db.execute('CREATE TABLE threads(id TEXT,cwd TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?)', (sid, '/fictional/state-project'))
            db.commit(); db.close()
            header = {'type':'session_meta','payload':{'id':sid,'cwd':'/fictional/header-project'}}
            (home/('rollout-'+sid+'.jsonl')).write_text(json.dumps(header))
            rows = [row(session_id=sid)]
            enrich_session_titles(rows, str(home), str(home/'claude'), [])
            self.assertEqual(rows[0]['project_name'], 'state-project')
            enrich_session_providers(rows, [home])
            self.assertNotIn('project_key', rows[0])
            self.assertNotIn('project_name', rows[0])

    def test_claude_exact_matches_require_folder_agreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); projects = root/'projects'/'encoded'; projects.mkdir(parents=True)
            desktop = root/'desktop'; desktop.mkdir()
            for sid, cwd, mid in [('a','/Users/a/Dropbox/Code/shared','same'),('b','D:\\Dropbox\\Code\\shared','same'),
                                  ('c','/fictional/other','conflict'),('d','/fictional/shared','conflict'),
                                  ('e',None,'missing'),('f','/fictional/shared','missing')]:
                (projects/(sid+'.jsonl')).write_text(json.dumps({'type':'assistant','sessionId':sid,'cwd':cwd,'message':{'id':mid}}))
            (desktop/'session.json').write_text(json.dumps({'cliSessionId':'desktop','cwd':'/fictional/worktree','originCwd':'/fictional/original'}))
            rows = [row(app_type='claude-desktop',request_id='session:'+mid,session_id='proxy-'+mid)
                    for mid in ('same','conflict','missing','absent')]
            rows.append(row(app_type='claude',session_id='desktop'))
            enrich_session_titles(rows,str(root/'codex'),str(root/'projects'),[desktop])
            self.assertEqual(rows[0]['project_name'],'shared')
            self.assertEqual(rows[0]['session_id'],'proxy-same')
            self.assertTrue(all('project_key' not in r for r in rows[1:4]))
            self.assertEqual(rows[4]['project_name'],'original')
            self.assertNotIn('/fictional/',json.dumps(rows))

    def test_export_and_legacy_coverage(self):
        requests = [row(request_id='one',provider_id='_session',session_key='s',project_key='p',
                        project_name='Fictional project',total_cost_usd='0.1'),
                    row(request_id='two',provider_id='_session',session_key='s',total_cost_usd='0.2')]
        data = dict(collected_at='2026-01-01',host_timezone='UTC',providers=[],proxy_request_logs=requests,usage_daily_rollups=[])
        with tempfile.TemporaryDirectory() as tmp:
            build([('fictional',data)],Path(tmp),render_figures=False)
            exported=public_data(Path(tmp))
        self.assertEqual(sum(r['tokens'] for r in exported['session_rows']),sum(r['tokens'] for r in exported['rows']))
        self.assertEqual({r['project_key'] for r in exported['session_rows']},{'p',''})
        self.assertEqual({r['project_name'] for r in exported['session_rows']},{'Fictional project',''})


if __name__ == '__main__':
    unittest.main()
