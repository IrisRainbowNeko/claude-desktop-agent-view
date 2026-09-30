import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPRedirectHandler

import server


DESKTOP = "local_3695370d-42ec-4704-9280-095b7c9fe5ae"
CLI = "90e4cd6e-bd4e-4ee1-a0c6-6d7d7c8dfd39"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class SidebarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.record = self.root / "account" / "org" / f"{DESKTOP}.json"
        self.record.parent.mkdir(parents=True)
        self.record.write_text(json.dumps({"cliSessionId": CLI}))

    def tearDown(self):
        self.temp.cleanup()

    def test_resolves_current_transcript(self):
        self.assertEqual(server.desktop_transcript_id(DESKTOP, [self.root]), CLI)

    def test_rejects_paths_and_non_uuid_ids(self):
        for sid in ["../../etc/passwd", "local_bad", "", DESKTOP + "/../x"]:
            with self.assertRaises(ValueError):
                server.desktop_transcript_id(sid, [self.root])

    def test_missing_corrupt_invalid_and_ambiguous_records_fail_closed(self):
        for text in ["{}", "not json", '{"cliSessionId":"javascript:evil"}']:
            self.record.write_text(text)
            self.assertIsNone(server.desktop_transcript_id(DESKTOP, [self.root]))
        self.record.write_text(json.dumps({"cliSessionId": CLI}))
        other = self.root / "account2" / "org" / self.record.name
        other.parent.mkdir(parents=True)
        other.write_text(json.dumps({"cliSessionId": "ef0f06b6-6812-42a2-9d25-eb6140d40bab"}))
        self.assertIsNone(server.desktop_transcript_id(DESKTOP, [self.root]))
        self.assertIsNone(server.desktop_transcript_id("local_00000000-0000-0000-0000-000000000000", [self.root]))

    def test_http_binds_embedded_pane_and_preserves_normal_dashboard(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        opener = build_opener(NoRedirect)
        try:
            with patch("autopreview.desktop_session_dirs", return_value=[self.root]):
                with opener.open(base + "/?desktop_session=" + DESKTOP) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn((f'window.__AGENT_VIEW_DESKTOP_SESSION__="{CLI}"').encode(), response.read())
                    self.assertIn("frame-ancestors app://localhost", response.headers["Content-Security-Policy"])
                with self.assertRaises(HTTPError) as bad:
                    opener.open(base + "/?desktop_session=bad")
                self.assertEqual(bad.exception.code, 400)
                bad.exception.close()
                with opener.open(base + "/") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIsNone(response.headers.get("Content-Security-Policy"))
                    self.assertIn(b"Agent View", response.read())
                with opener.open(base + "/?embedded=1") as response:
                    self.assertIn("frame-ancestors app://localhost", response.headers["Content-Security-Policy"])
                    self.assertNotIn("unsafe-eval", response.headers["Content-Security-Policy"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
