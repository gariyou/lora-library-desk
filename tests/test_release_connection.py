import ast
import importlib.util
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_server_test', ROOT / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
connector = server.forge_connector


class LoopbackURLTests(unittest.TestCase):
    def test_custom_ports_and_ipv6(self):
        for value, expected in [(' http://localhost:18860/ ', 'http://localhost:18860'),
                                ('http://[::1]:8877', 'http://[::1]:8877'),
                                ('http://127.0.0.1:1', 'http://127.0.0.1:1')]:
            self.assertEqual(connector.normalize_forge_url(value), expected)

    def test_nonlocal_urls_credentials_and_invalid_ports_rejected(self):
        for value in [None, '', 'https://localhost:7860', 'http://example.com:7860',
                      'http://127.0.0.1.evil.test:7860', 'http://user:pass@localhost:7860',
                      'http://localhost:0', 'http://localhost:65536', 'http://localhost:abc',
                      'http://localhost:7860/api', 'http://localhost:7860?x=1', 'http://localhost:7860#x']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                connector.normalize_forge_url(value)


class DownloadCookieTests(unittest.TestCase):
    def test_cookie_sent_initially_but_dropped_on_redirect(self):
        class Response(io.BytesIO):
            headers = {'Content-Type':'application/octet-stream'}
            def geturl(self): return 'https://civitai.com/model.safetensors'
        def opener(request, **kwargs):
            self.assertEqual(request.get_header('Cookie'), 'session=fixture')
            redirect = urllib.request.HTTPRedirectHandler()
            for target in ['https://civitai.com/other', 'https://cdn.example.test/model.safetensors']:
                new = redirect.redirect_request(request, None, 302, 'Found', {}, target)
                self.assertIsNone(new.get_header('Cookie'))
            return Response(b'fixture model bytes')
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DATA_ROOT', Path(temp)), patch.object(server.urllib.request, 'urlopen', side_effect=opener):
            path = server.download_remote_model_to_temp_file('https://civitai.com/model.safetensors', cookie_header='session=fixture')
            self.assertEqual(path.read_bytes(), b'fixture model bytes')

    def test_cookie_never_sent_to_non_civitai_or_plain_http(self):
        with patch.object(server.urllib.request, 'urlopen') as opener:
            for url in ['https://example.com/a', 'http://civitai.com/a', 'https://user@civitai.com/a']:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    server.download_remote_model_to_temp_file(url, cookie_header='session=fixture')
            opener.assert_not_called()


class ConnectionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'connection.json'
        patcher = patch.object(connector, 'CONNECTION_CONFIG_PATH', self.config)
        patcher.start(); self.addCleanup(patcher.stop)
        class ForgeFixture(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps({'connected': True, 'busy': False, 'route': self.path}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(payload)
            def log_message(self, *_): pass
        self.forge = ThreadingHTTPServer(('127.0.0.1', 0), ForgeFixture)
        self.desk = ThreadingHTTPServer(('127.0.0.1', 0), server.build_handler(ROOT / 'web'))
        for instance in (self.forge, self.desk):
            threading.Thread(target=instance.serve_forever, daemon=True).start()
            self.addCleanup(instance.server_close); self.addCleanup(instance.shutdown)
        self.base = f'http://127.0.0.1:{self.desk.server_port}'

    def request(self, route, data=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        req = urllib.request.Request(self.base + route, data=None if data is None else json.dumps(data).encode(), headers=headers)
        return json.load(urllib.request.urlopen(req, timeout=5))

    def test_config_persists_and_actual_requests_use_new_port(self):
        self.assertEqual(self.request('/api/lora/forge/connection')['forge_url'], 'http://127.0.0.1:7860')
        url = f'http://127.0.0.1:{self.forge.server_port}'
        self.request('/api/lora/forge/connection', {'forge_url': url + '/'})
        self.assertEqual(json.loads(self.config.read_text())['forge_url'], url)
        self.assertEqual(self.request('/api/lora/forge/connection')['forge_url'], url)
        status = self.request('/api/lora/forge/status')
        self.assertTrue(status['connected'])
        self.assertEqual(status['route'], '/library-desk/status')
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.request('/api/lora/forge/connection', {'forge_url': 'http://example.com'})
        self.assertEqual(rejected.exception.code, 400)
        self.assertEqual(connector.get_forge_url(), url)

    def test_external_origin_and_corrupt_file_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.request('/api/lora/forge/connection', {'forge_url': 'http://localhost:8877'}, 'https://example.com')
        self.assertEqual(rejected.exception.code, 403)
        self.assertFalse(self.config.exists())
        self.config.write_text('[]')
        with self.assertRaises(ValueError): connector.get_forge_url()


class ForgeOriginTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((ROOT / 'forge_bridge/scripts/library_desk_bridge.py').read_text(encoding='utf-8'))
        selected = ast.Module(body=[node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in {'library_url', 'check_local'}], type_ignores=[])
        class HTTPError(Exception):
            def __init__(self, code, detail): self.code = code
        self.error = HTTPError
        self.opts = SimpleNamespace(data={'library_desk_url': 'http://localhost:18878'})
        self.namespace = {'shared': SimpleNamespace(opts=self.opts), 'local': connector._local,
                          'urlparse': urlparse, 'HTTPException': HTTPError}
        exec(compile(selected, '<forge connection checks>', 'exec'), self.namespace)

    def request(self, origin, host='127.0.0.1', host_header='127.0.0.1:18860'):
        return SimpleNamespace(client=SimpleNamespace(host=host), headers={'origin': origin, 'host': host_header}, url=SimpleNamespace(port=18860))

    def test_configured_ports_and_aliases_allowed(self):
        for origin in ['', 'http://localhost:18860', 'http://127.0.0.1:18878', 'http://[::1]:18860']:
            self.namespace['check_local'](self.request(origin))
        self.assertEqual(self.namespace['library_url'](), 'http://localhost:18878')

    def test_external_origins_and_peers_rejected(self):
        for origin, host in [('http://localhost:7860', '127.0.0.1'), ('https://example.com', '127.0.0.1'),
                             ('http://user@localhost:18860', '127.0.0.1'), ('', '192.0.2.1')]:
            with self.subTest(origin=origin, host=host), self.assertRaises(self.error):
                self.namespace['check_local'](self.request(origin, host))
        # DNS rebinding: same-origin GET carries no Origin but a foreign Host.
        for host_header in ['evil.example:18860', '', '192.0.2.1:18860']:
            with self.subTest(host_header=host_header), self.assertRaises(self.error):
                self.namespace['check_local'](self.request('', host_header=host_header))
        self.opts.data['library_desk_url'] = 'http://example.com:18878'
        with self.assertRaises(ValueError): self.namespace['library_url']()
