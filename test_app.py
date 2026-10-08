import json
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.client import HTTPConnection
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer

from app import Collector, Handler, disabled_live_snapshot, interval_value, main, next_boundary


class DashboardTests(unittest.TestCase):
    def test_live_api_loopback_and_disabled(self):
        class PeerHandler(Handler):
            def do_GET(self):
                self.client_address = (self.server.test_peer, self.client_address[1])
                super().do_GET()

        server = ThreadingHTTPServer(('127.0.0.1', 0), PeerHandler)
        server.test_peer = '127.0.0.1'
        server.live_meter = None
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def call(host='localhost'):
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request('GET', '/api/live', headers={'Host': host})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        try:
            self.assertEqual(call(), (200, disabled_live_snapshot()))
            snapshot = {'enabled': True, 'total_tps': 120, 'average_tps': 40,
                        'contributing_sessions': 3}
            server.live_meter = Mock()
            server.live_meter.snapshot.return_value = snapshot
            self.assertEqual(call(), (200, snapshot))
            for peer in ('::1', '::ffff:127.0.0.1'):
                server.test_peer = peer
                self.assertEqual(call()[0], 200)
            for peer in ('192.168.1.2', '100.64.0.1', '::ffff:192.168.1.2'):
                server.test_peer = peer
                # A forged local Host does not turn a remote connection into loopback.
                self.assertEqual(call()[0], 403, peer)
            server.test_peer = '127.0.0.1'
            self.assertEqual(call('evil.example')[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_live_meter_lifecycle_and_default_disabled(self):
        for enabled, startup_failure in ((False, False), (True, False), (True, True)):
            with self.subTest(enabled=enabled, startup_failure=startup_failure), \
                    tempfile.TemporaryDirectory() as tmp:
                args = ['app.py', '--config', str(Path(tmp) / 'config.ini')]
                if enabled:
                    args.append('--live-meter')
                with patch('app.sys.argv', args), patch('app.Collector') as constructor, \
                        patch('app.LiveMeter') as meter_constructor, \
                        patch('app.ThreadingHTTPServer') as server_constructor:
                    collector = constructor.return_value
                    server = server_constructor.return_value
                    server.server_port = 8765
                    server.serve_forever.side_effect = KeyboardInterrupt
                    meter = meter_constructor.return_value
                    if startup_failure:
                        meter.start.side_effect = OSError('probe startup failed')
                        with self.assertRaisesRegex(OSError, 'probe startup failed'):
                            main()
                    else:
                        main()
                    collector.thread.start.assert_called_once_with()
                    collector.close.assert_called_once_with()
                    server.server_close.assert_called_once_with()
                    if enabled:
                        meter.start.assert_called_once_with()
                        meter.close.assert_called_once_with()
                        self.assertIs(server.live_meter, meter)
                    else:
                        meter_constructor.assert_not_called()
                        self.assertIsNone(server.live_meter)

    def test_bind_failure_does_not_start_workers(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch('app.sys.argv', ['app.py', '--live-meter', '--config', str(Path(tmp) / 'config.ini')]), \
                patch('app.Collector') as collector_constructor, \
                patch('app.LiveMeter') as meter_constructor, \
                patch('app.ThreadingHTTPServer', side_effect=OSError('address in use')):
            with self.assertRaisesRegex(OSError, 'address in use'):
                main()
            collector_constructor.return_value.thread.start.assert_not_called()
            meter_constructor.assert_not_called()

    def test_tailscale_host_range(self):
        handler = object.__new__(Handler)
        for host in ('localhost:8765', '192.168.1.2:8765', '100.64.0.1:8765', '100.127.255.254:8765'):
            handler.headers = {'Host': host}
            self.assertTrue(handler.valid_host(), host)
        for host in ('100.63.255.254:8765', '100.128.0.1:8765', '8.8.8.8:8765', 'evil.example:8765'):
            handler.headers = {'Host': host}
            self.assertFalse(handler.valid_host(), host)

    def test_boundaries(self):
        now = datetime(2026, 9, 19, 12, 3, 42).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_boundary(now, 300)).strftime('%H:%M:%S'), '12:05:00')
        self.assertEqual(datetime.fromtimestamp(next_boundary(now, 5)).strftime('%H:%M:%S'), '12:03:45')
        self.assertEqual(next_boundary(next_boundary(now, 300), 300)-next_boundary(now, 300), 300)

    def test_interval_validation(self):
        for v in (4, 601, 5.5, True, '5', None):
            with self.assertRaises(ValueError):
                interval_value(v)
        for v in (5, 300, 600):
            self.assertEqual(interval_value(v), v)

    def test_http_controls_and_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'config.ini'
            config.write_text('[source:test]\ntransport=local\n')
            collector = Collector(config, 300, Path(tmp)/'cache.json')
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.collector = collector
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            def call(method, path, body=None, headers=None):
                conn = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                conn.request(method, path, json.dumps(body) if body is not None else None, headers or {})
                response = conn.getresponse(); result = (response.status, response.read()); conn.close(); return result
            try:
                self.assertEqual(call('GET','/config.ini')[0],404)
                self.assertEqual(call('GET','/../config.ini')[0],404)
                self.assertEqual(call('GET','/api/status',headers={'Host':'evil.example'})[0],403)
                self.assertEqual(call('GET','/api/status',headers={'Host':'100.64.0.1:8765'})[0],200)
                tailscale_headers = {'Host':'100.64.0.1:8765', 'Origin':'http://100.64.0.1:8765'}
                self.assertEqual(call('POST','/api/interval',{'seconds':300},tailscale_headers)[0],403)
                tailscale_headers['X-Usage-CSRF'] = collector.csrf
                self.assertEqual(call('POST','/api/interval',{'seconds':300},tailscale_headers)[0],200)
                tailscale_headers['Origin'] = 'http://100.64.0.2:8765'
                self.assertEqual(call('POST','/api/interval',{'seconds':300},tailscale_headers)[0],403)
                self.assertEqual(call('POST','/api/interval',{'seconds':5})[0],403)
                headers={'X-Usage-CSRF':collector.csrf}
                self.assertEqual(call('POST','/api/interval',{'seconds':5},headers)[0],200)
                self.assertEqual(collector.interval,5)
                self.assertEqual(call('POST','/api/interval',{'seconds':601},headers)[0],400)
                headers['Origin']='https://evil.example'
                self.assertEqual(call('POST','/api/interval',{'seconds':300},headers)[0],403)
            finally:
                server.shutdown();server.server_close();thread.join()
                collector.close()

    def test_no_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.ini';config.write_text('[source:test]\ntransport=local\n')
            collector=Collector(config,300,Path(tmp)/'cache.json')
            process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO())
            process.poll.return_value = None
            with patch('app.subprocess.Popen', return_value=process) as popen, \
                    patch.object(collector, '_receive'), patch('app.stop_process') as stop:
                try:
                    self.assertTrue(collector.refresh())
                    self.assertFalse(collector.refresh())
                    self.assertEqual(popen.call_count,1)
                    self.assertEqual(process.stdin.getvalue(), b'refresh\n')
                    self.assertTrue(collector.status()['refreshing'])
                    self.assertEqual(collector.status()['storage'], 'memory')
                finally:
                    collector.close()
                stop.assert_called_once_with(process)
            self.assertFalse(any(reader.is_alive() for reader in collector.readers))
            self.assertEqual(set(Path(tmp).iterdir()), {config})

    def test_failed_refresh_keeps_last_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.ini';config.write_text('[source:test]\ntransport=local\n')
            collector=Collector(config,300,Path(tmp)/'cache.json')
            old={'generated_at':'previous', 'rows':[]}
            collector.data=old
            process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO())
            process.poll.return_value = 1
            with patch('app.subprocess.Popen', return_value=process), patch('app.stop_process'):
                collector.thread.start()
                deadline=time.monotonic()+3
                while collector.error is None and time.monotonic()<deadline:
                    time.sleep(.02)
                collector.close()
            self.assertIs(collector.data,old)
            self.assertIn('Previous data retained',collector.error)
            self.assertEqual(set(Path(tmp).iterdir()), {config})


if __name__ == '__main__':
    unittest.main()
