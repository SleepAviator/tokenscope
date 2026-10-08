import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from live_meter import LiveMeter, project_sources, validate_packet


def sample(key='response', session='session', tokens=300, start=95, end=100, **extra):
    return dict(event_key=key, session_key=session, runtime='codex', provider='OpenAI',
                model='Example', output_tokens=tokens, start=start, end=end, basis='native_window', **extra)


def packet(samples=None, **extra):
    return dict(version=1, sampled_at=100, samples=samples or [], pending_sessions=[],
                coverage={'status': 'complete', 'issues': []}, **extra)


def frame(samples=None, received=200, **extra):
    return dict(packet=packet(samples), received_at=received, error=None, **extra)


class WindowTests(unittest.TestCase):
    def test_concurrent_total_and_average(self):
        states = {name: frame([sample(name, name, tokens=tps*5)])
                  for name, tps in [('a',60),('b',40),('c',20)]}
        result = project_sources(states, 200)
        self.assertEqual(result['total_tps'], 120)
        self.assertEqual(result['average_tps'], 40)
        self.assertEqual(result['contributing_sessions'], 3)
        self.assertEqual(result['status'], 'complete')

    def test_long_batch_uniform_allocation_and_clock_offset(self):
        states = {'host': frame([sample(tokens=500, start=90)])}
        self.assertEqual(project_sources(states, 200)['total_tps'], 50)
        self.assertEqual(project_sources(states, 201)['total_tps'], 40)
        # The source clock can be hours away; relative ages give the same rate.
        shifted = copy.deepcopy(states)
        shifted['host']['packet']['sampled_at'] += 3600
        for value in shifted['host']['packet']['samples']:
            value['start'] += 3600; value['end'] += 3600
        self.assertEqual(project_sources(shifted, 200)['total_tps'], 50)

    def test_expiry_idle_and_unknown(self):
        states = {'host': frame([sample()])}
        self.assertEqual(project_sources(states, 202)['total_tps'], 36)
        expired = project_sources(states, 203)
        self.assertIsNone(expired['total_tps'])
        self.assertEqual(expired['sources'][0]['status'], 'stale')
        self.assertEqual(expired['status'], 'unavailable')
        idle = project_sources({'host': frame()}, 200)
        self.assertEqual(idle['total_tps'], 0)
        self.assertIsNone(idle['average_tps'])
        self.assertEqual(idle['contributing_sessions'], 0)
        initial = project_sources({'host': {'packet':None,'received_at':None,'error':None}}, 200)
        self.assertIsNone(initial['total_tps'])
        self.assertEqual(initial['status'], 'initializing')

    def test_overlapping_responses_same_session_and_expired_window(self):
        states = {'host': frame([sample('r1',tokens=250),sample('r2',tokens=100,start=98),
                                 sample('old',tokens=500,start=80,end=90)])}
        result = project_sources(states, 200)
        self.assertEqual(result['total_tps'], 70)
        self.assertEqual(result['contributing_sessions'], 1)
        self.assertEqual(result['average_tps'], 70)

    def test_copied_logs_and_native_proxy_duplicates(self):
        a = sample()
        proxy = dict(a, basis='proxy_elapsed', session_key=None)
        result = project_sources({'a':frame([proxy]),'b':frame([a]),'c':frame([a])}, 200)
        self.assertEqual(result['total_tps'], 60)
        self.assertEqual(result['average_tps'], 60)
        self.assertEqual(result['sessions'][0]['source'], 'b')
        self.assertEqual(sum(s['total_tps'] for s in result['sources']), 60)

    def test_conflicting_copies_excluded_and_unattributed_output(self):
        result = project_sources({'a':frame([sample()]),'b':frame([sample(tokens=301)])}, 200)
        self.assertIsNone(result['total_tps'])
        self.assertEqual(result['status'], 'partial')

    def test_conflicting_session_identity_and_missing_timing_are_unknown(self):
        result = project_sources({'a':frame([sample()]),'b':frame([sample(session='other')])}, 200)
        self.assertIsNone(result['total_tps'])
        self.assertIsNone(result['average_tps'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sessions'], [])
        states = {'a':frame()}
        states['a']['packet']['coverage'] = {'status':'partial','issues':['Timing unavailable']}
        result = project_sources(states, 200)
        self.assertIsNone(result['total_tps'])
        self.assertIsNone(result['sources'][0]['total_tps'])
        self.assertTrue(result['issues'])
        result = project_sources({'a':frame([sample(),sample('unassigned',None,100)])}, 200)
        self.assertEqual(result['total_tps'], 80)
        self.assertIsNone(result['average_tps'])
        self.assertEqual(result['contributing_sessions'], 1)
        self.assertEqual(result['status'], 'partial')

    def test_partial_machine_and_pending_session(self):
        states = {'a':frame([sample()]),'offline':{'packet':None,'received_at':None,'error':'Disconnected'}}
        states['a']['packet']['pending_sessions'] = [dict(session_key='new',runtime='codex',provider='OpenAI',
                                                        model='Example',status='awaiting_usage')]
        result = project_sources(states, 200)
        self.assertEqual(result['total_tps'], 60)
        self.assertEqual(result['average_tps'], 60)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['pending_sessions'][0]['source'], 'a')

    def test_projection_validation_and_allowlist(self):
        source = packet([sample()]);source['prompt'] = 'private'
        source['samples'][0]['path'] = '/private/raw-log'
        self.assertNotIn('private', json.dumps(validate_packet(source)))
        for changed in (dict(start=100),dict(end=float('nan')),dict(output_tokens=True),dict(output_tokens=-1),dict(end=102)):
            bad = packet([dict(sample(),**changed)])
            with self.assertRaises(ValueError): validate_packet(bad)
        with self.assertRaises(ValueError): validate_packet(dict(source,version=2))


class WorkerTests(unittest.TestCase):
    def test_local_worker_one_second_and_shutdown(self):
        class FakeProbe:
            def __init__(self, cfg): self.closed=False
            def snapshot(self): return dict(packet(),sampled_at=time.time())
            def close(self): self.closed=True
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.ini';config.write_text('[source:local]\ntransport=local\n')
            with patch('live_probe.Probe', FakeProbe):
                meter=LiveMeter(config);meter.start()
                try:
                    deadline=time.monotonic()+3
                    while meter.snapshot()['status']=='initializing' and time.monotonic()<deadline: time.sleep(.02)
                    first=meter.states['local']['received_at']
                    time.sleep(1.1)
                    second=meter.states['local']['received_at']
                    self.assertGreaterEqual(second-first,.8)
                    self.assertLess(second-first,1.3)
                    self.assertEqual(meter.snapshot()['total_tps'],0)
                finally: meter.close()
                self.assertFalse(any(t.is_alive() for t in meter.threads))

    def test_reader_disconnect_reconnect_and_process_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.ini';config.write_text('[source:remote]\ntransport=ssh\nhost=example\npython=python\n')
            meter=LiveMeter(config)
            fixture="import sys,json,time;sys.stdin.buffer.read(int(sys.stdin.buffer.readline()));print(json.dumps({'version':1,'sampled_at':time.time(),'samples':[],'pending_sessions':[],'coverage':{'status':'complete','issues':[]}}),flush=True)"
            launched=[]
            real_popen=subprocess.Popen
            def popen(*args,**kwargs):
                process=real_popen(*args,**kwargs);launched.append(process);return process
            with patch.object(meter,'_command',return_value=[__import__('sys').executable,'-u','-c',fixture]), patch('live_meter.subprocess.Popen',side_effect=popen):
                meter.start()
                try:
                    deadline=time.monotonic()+5
                    while len(launched)<2 and time.monotonic()<deadline:time.sleep(.03)
                    self.assertGreaterEqual(len(launched),2)
                finally:meter.close()
            self.assertTrue(all(p.poll() is not None for p in launched))
            self.assertFalse(meter.processes)


if __name__=='__main__':
    unittest.main()
