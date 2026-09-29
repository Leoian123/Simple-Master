"""Impostazioni di modello ed effort: persistenza, validazione, propagazione ai motori."""

from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master.models import Settings, effective_effort  # noqa: E402
from master.ui import App, make_handler  # noqa: E402
from test_master import FakeClient  # noqa: E402


class TestSettingsModel(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_then_file_wins_and_persists(self) -> None:
        path = self.tmp / "impostazioni.json"
        s = Settings.load(path, {"model": "claude-sonnet-5", "effort_play": "medium"})
        self.assertEqual((s.model, s.effort_play, s.effort_tidy), ("claude-sonnet-5", "medium", "low"))
        s.update({"model": "claude-opus-5", "effort_play": "max", "effort_prepare": ""})
        self.assertTrue(path.exists())
        again = Settings.load(path, {"model": "claude-sonnet-5"})
        self.assertEqual((again.model, again.effort_play, again.effort_prepare), ("claude-opus-5", "max", ""))

    def test_validation(self) -> None:
        s = Settings()
        with self.assertRaises(ValueError):
            s.update({"model": "gpt-9"}, save=False)
        with self.assertRaises(ValueError):
            s.update({"effort_play": "enorme"}, save=False)

    def test_haiku_ignores_effort(self) -> None:
        self.assertIsNone(effective_effort("claude-haiku-4-5", "max"))
        self.assertEqual(effective_effort("claude-opus-5", "max"), "max")
        self.assertIsNone(effective_effort("claude-opus-5", ""))


class TestSettingsUI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        camps = self.tmp / "campaigns"
        shutil.copytree(ROOT / "campaigns" / "esempio", camps / "esempio")
        self.client = FakeClient()
        self.app = App(camps, "esempio", client=self.client, effort="medium")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method: str, path: str, body: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        resp = conn.getresponse()
        out = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, out

    def test_settings_reach_engines_and_requests(self) -> None:
        status, s = self.call("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(s["current"]["model"], "claude-opus-5")
        self.assertEqual(s["current"]["effort_play"], "medium")
        self.assertTrue(any(m["id"] == "claude-haiku-4-5" for m in s["models"]))
        self.assertTrue((self.tmp / "impostazioni.json").exists() is False)

        status, cur = self.call("POST", "/api/settings", {"model": "claude-sonnet-5", "effort_play": "max", "effort_sheet": "low"})
        self.assertEqual(status, 200)
        self.assertEqual((self.app.engine.model, self.app.engine.effort), ("claude-sonnet-5", "max"))
        self.assertEqual((self.app.builder.model, self.app.builder.effort), ("claude-sonnet-5", "low"))
        self.assertTrue((self.tmp / "impostazioni.json").exists())
        # la richiesta API successiva usa i nuovi valori
        self.call("POST", "/api/play", {"text": "Guardo la nebbia."})
        first = [c for c in self.client.calls if "Sei il master" in str(c.get("system"))][0]  # la chiamata del narratore
        self.assertEqual(first["model"], "claude-sonnet-5")
        self.assertEqual(first["extra_body"]["output_config"]["effort"], "max")
        # cambiare campagna conserva le impostazioni
        self.call("POST", "/api/campaign", {"name": "esempio"})
        self.assertEqual((self.app.engine.model, self.app.engine.effort), ("claude-sonnet-5", "max"))
        # haiku: nessun effort inviato
        self.call("POST", "/api/settings", {"model": "claude-haiku-4-5", "effort_play": "max"})
        self.assertIsNone(self.app.engine.effort)
        status, err = self.call("POST", "/api/settings", {"model": "sconosciuto"})
        self.assertEqual(status, 400)
        status, st = self.call("GET", "/api/state")
        self.assertEqual(st["settings"]["model"], "claude-haiku-4-5")


if __name__ == "__main__":
    unittest.main()
