"""Esploratore file: elenco cartelle e manuali, finestra nativa simulata."""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import explorer  # noqa: E402
from master.ui import App, make_handler  # noqa: E402


class TestListDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "Manuali").mkdir()
        (self.tmp / "Manuali" / "regole.pdf").write_bytes(b"%PDF-1.4 fake")
        (self.tmp / "Manuali" / "note.md").write_text("x", encoding="utf-8")
        (self.tmp / "Manuali" / "foto.png").write_bytes(b"png")
        (self.tmp / ".nascosta").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roots_and_listing(self) -> None:
        r = explorer.list_dir(None)
        self.assertEqual(r["path"], "")
        self.assertTrue(r["roots"])
        d = explorer.list_dir(str(self.tmp))
        self.assertEqual([x["name"] for x in d["dirs"]], ["Manuali"])
        self.assertEqual(d["files"], [])
        sub = explorer.list_dir(str(self.tmp / "Manuali"))
        self.assertEqual([f["name"] for f in sub["files"]], ["note.md", "regole.pdf"])  # png escluso, ordine alfabetico
        self.assertEqual(Path(sub["parent"]), self.tmp.resolve())
        with self.assertRaises(FileNotFoundError):
            explorer.list_dir(str(self.tmp / "manca"))

    def test_native_pick_uses_powershell(self) -> None:
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="C:\\Manuali\\regole.pdf\n", stderr="")

        original, explorer.subprocess.run = explorer.subprocess.run, fake_run
        orig_plat, explorer.sys.platform = explorer.sys.platform, "win32"
        try:
            self.assertEqual(explorer.native_pick("C:\\Manuali"), "C:\\Manuali\\regole.pdf")
            self.assertEqual(calls[0][0][0], "powershell")
            self.assertIn("-STA", calls[0][0])
            self.assertEqual(calls[0][1]["env"]["SM_INITIAL_DIR"], "C:\\Manuali")
            explorer.subprocess.run = lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            self.assertIsNone(explorer.native_pick())  # annullata
            explorer.sys.platform = "linux"
            with self.assertRaises(RuntimeError):
                explorer.native_pick()
        finally:
            explorer.subprocess.run = original
            explorer.sys.platform = orig_plat


class TestExplorerEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        camps = self.tmp / "campaigns"
        shutil.copytree(ROOT / "campaigns" / "esempio", camps / "esempio")
        (self.tmp / "m.pdf").write_bytes(b"x")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(camps, None)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        out = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, out

    def test_fs_endpoint(self) -> None:
        status, d = self.get("/api/fs?path=" + quote(str(self.tmp)))
        self.assertEqual(status, 200)
        self.assertEqual([f["name"] for f in d["files"]], ["m.pdf"])
        self.assertEqual([x["name"] for x in d["dirs"]], ["campaigns"])
        status, err = self.get("/api/fs?path=" + quote(str(self.tmp / "no")))
        self.assertEqual(status, 400)
        status, root = self.get("/api/fs")
        self.assertEqual((status, root["path"]), (200, ""))


if __name__ == "__main__":
    unittest.main()
