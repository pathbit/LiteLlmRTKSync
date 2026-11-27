"""Exercise the status command against a local administrative API fixture."""

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from litellm_rtksync.cli import main


class StatusCommandTests(unittest.TestCase):
    def run_status(self, *, key_status=200, health_status=200, rpm_limit=50):
        responses = {
            "/health/liveliness": (health_status, {}),
            "/key/list": (key_status, {"keys": [
                {"key_alias": "example", "team_id": "demo", "rpm_limit": rpm_limit},
            ]}),
            "/team/list": (200, [{"team_id": "demo", "team_alias": "Demo", "rpm_limit": 60}]),
            "/model/info": (200, {"data": []}),
        }

        class GatewayHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = responses.get(urlsplit(self.path).path, (404, {}))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *_):
                pass

        stdout, stderr = StringIO(), StringIO()
        with TemporaryDirectory() as data, ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler) as proxy:
            thread = Thread(target=proxy.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:
                env = {
                    "DATA_DIR": data,
                    "LITELLM_URL": f"http://127.0.0.1:{proxy.server_port}",
                    "LITELLM_MASTER_KEY": "synthetic-key",
                }
                with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["litellmrtksync", "--status"]):
                    with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as result:
                        main()
            finally:
                proxy.shutdown()
                thread.join()
            self.assertEqual(list(Path(data).iterdir()), [])

        return result.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_a_rejected_key_is_an_operational_error_without_a_traceback(self):
        code, output, error = self.run_status(key_status=401)
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("master key recusada", error)
        self.assertNotIn("Traceback", error)
        self.assertNotIn("synthetic-key", error)

    def test_an_unavailable_proxy_remains_an_operational_error(self):
        code, output, error = self.run_status(health_status=503)
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("inacessível", error)

    def test_an_inconsistent_limit_keeps_its_own_exit_code(self):
        code, output, error = self.run_status(rpm_limit=100)
        self.assertEqual(code, 1)
        self.assertIn("[INCOERENTE]", output)
        self.assertEqual(error, "")

    def test_consistent_limits_pass(self):
        code, output, error = self.run_status()
        self.assertEqual(code, 0)
        self.assertIn("Nenhuma incoerência", output)
        self.assertEqual(error, "")
