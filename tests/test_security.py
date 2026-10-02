import base64
import hashlib
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

TOKEN = "fixture-token-0123456789abcdef"


class SecurityHTTPTests(unittest.TestCase):
    def start(self, **options):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.build_handler(ROOT / "web", **options))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_port

    def request(self, port, path, *, method="GET", headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        # skip_host lets the test send a forged Host header like a rebinding page would.
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        headers = {"Host": f"127.0.0.1:{port}", **(headers or {})}
        if body is not None:
            headers.setdefault("Content-Type", "application/json")
            headers["Content-Length"] = str(len(body))
        for name, value in headers.items():
            if value is not None:
                conn.putheader(name, value)
        conn.endheaders(body)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def test_dns_rebinding_host_is_rejected(self):
        port = self.start()
        for host in (f"evil.example:{port}", "rebind.attacker.test"):
            response, _ = self.request(port, "/api/lora/forge/connection", headers={"Host": host})
            self.assertEqual(response.status, 403, host)
            response, _ = self.request(port, "/lora", headers={"Host": host})
            self.assertEqual(response.status, 403, host)
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"192.168.1.20:{port}"):
            response, _ = self.request(port, "/health", headers={"Host": host})
            self.assertEqual(response.status, 200, host)

    def test_origin_matching_forged_host_is_rejected(self):
        port = self.start()
        response, _ = self.request(
            port,
            "/api/lora/forge/connection",
            method="POST",
            headers={"Host": f"evil.example:{port}", "Origin": f"http://evil.example:{port}"},
            body=b"{}",
        )
        self.assertEqual(response.status, 403)

    def test_allowed_host_option(self):
        port = self.start(allowed_hosts=["MyPC.local"])
        response, _ = self.request(port, "/health", headers={"Host": f"mypc.local:{port}"})
        self.assertEqual(response.status, 200)

    def test_only_known_extension_ids_are_allowed(self):
        port = self.start(extension_ids=["forkedextensionid"])
        allowed = [server.LIBRARY_DESK_EXTENSION_ID, "forkedextensionid"]
        for extension_id in allowed + ["otherextension"]:
            response, _ = self.request(
                port, "/api/lora/pending-download", method="OPTIONS",
                headers={"Origin": f"chrome-extension://{extension_id}"},
            )
            expected = 204 if extension_id in allowed else 403
            self.assertEqual(response.status, expected, extension_id)

    def test_cross_site_subresource_without_origin_is_rejected(self):
        port = self.start()
        response, _ = self.request(port, "/api/lora/encyclopedia-queue", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status, 403)
        response, _ = self.request(port, "/api/lora/forge/connection", headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(response.status, 200)

    def test_lan_clients_need_token_but_loopback_does_not(self):
        port = self.start(lan_token=TOKEN)
        # Requests from this PC (loopback) work as before.
        response, _ = self.request(port, "/api/lora/forge/connection")
        self.assertEqual(response.status, 200)
        with patch.object(server.Handler, "_client_is_loopback", return_value=False):
            response, _ = self.request(port, "/api/lora/forge/connection")
            self.assertEqual(response.status, 401)
            response, body = self.request(port, "/lora")
            self.assertEqual(response.status, 401)
            self.assertIn("token", body.decode("utf-8"))
            response, _ = self.request(port, "/manifest.webmanifest")
            self.assertEqual(response.status, 200)
            response, _ = self.request(port, "/lora?token=wrong")
            self.assertEqual(response.status, 401)

            response, _ = self.request(port, f"/lora?view=grid&token={TOKEN}")
            self.assertEqual(response.status, 303)
            self.assertEqual(response.getheader("Location"), "/lora?view=grid")
            cookie = response.getheader("Set-Cookie")
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            cookie_value = cookie.split(";", 1)[0]

            response, _ = self.request(port, "/api/lora/forge/connection", headers={"Cookie": cookie_value})
            self.assertEqual(response.status, 200)
            response, _ = self.request(
                port, "/api/lora/forge/connection", headers={server.LAN_TOKEN_HEADER: TOKEN}
            )
            self.assertEqual(response.status, 200)


class SecurityHelperTests(unittest.TestCase):
    def test_manifest_key_matches_server_extension_id(self):
        manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
        extension_id = "".join(chr(ord("a") + int(c, 16)) for c in digest)
        self.assertEqual(extension_id, server.LIBRARY_DESK_EXTENSION_ID)

    def test_lan_token_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data" / "lan-token.txt"
            first = server.load_or_create_lan_token(path)
            self.assertGreaterEqual(len(first), 24)
            self.assertEqual(server.load_or_create_lan_token(path), first)

    def test_loopback_bind_detection(self):
        self.assertTrue(server.is_loopback_bind_host("127.0.0.1"))
        self.assertTrue(server.is_loopback_bind_host("localhost"))
        self.assertFalse(server.is_loopback_bind_host("0.0.0.0"))
        self.assertFalse(server.is_loopback_bind_host("192.168.1.5"))

    def test_extension_ids_from_environment(self):
        with patch.dict(server.os.environ, {server.EXTENSION_IDS_ENV: "AbC, def"}):
            ids = server.configured_extension_ids()
        self.assertIn("abc", ids)
        self.assertIn("def", ids)
        self.assertIn(server.LIBRARY_DESK_EXTENSION_ID, ids)


if __name__ == "__main__":
    unittest.main()
