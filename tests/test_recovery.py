"""Recupero dopo stalli e chiusure forzate: limite per blocco, cache precedente, corse senza FINE."""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import Campaign, logbook  # noqa: E402
from master.prepare import Preparer, create_message, load_chunks  # noqa: E402
from test_prepare import FakePrepClient, make_pdf  # noqa: E402


class SlowStream:
    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for _ in range(5):
            time.sleep(0.03)
            yield SimpleNamespace(type="ping")

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn", content=[])


class TestBlockTimeout(unittest.TestCase):
    def test_stalled_stream_raises(self) -> None:
        client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: SlowStream(**kw), create=lambda **kw: None))
        with self.assertRaises(TimeoutError):
            create_message(client, max_seconds=0.05, max_tokens=1)
        self.assertEqual(create_message(client, max_seconds=5, max_tokens=1).stop_reason, "end_turn")

    def test_preparer_marks_block_and_continues(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            root = tmp / "camp"
            shutil.copytree(ROOT / "campaigns" / "esempio", root)
            pdf = tmp / "m.pdf"
            make_pdf(pdf, [f"Pagina {i} con regole" for i in range(1, 4)])
            client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: SlowStream(**kw), create=lambda **kw: None))
            prep = Preparer(Campaign(root), client=client, cache_root=tmp / "cache", batch=False, block_timeout=0.05)
            report = prep.run(str(pdf), consolidate=False, tidy=False, progress=lambda s: None)
            self.assertTrue(any("non conclusa entro" in w for w in report.warnings))
            self.assertEqual(list((tmp / "cache").glob("*/*/blocco_*.json")), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLegacyRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.camps = self.tmp / "campaigns"
        shutil.copytree(ROOT / "campaigns" / "esempio", self.camps / "esempio")
        self.camp = Campaign.create(self.camps / "Vampire")
        self.pdf = self.tmp / "Manuale Base.pdf"
        long = "Regole di combattimento e luoghi del mondo " * 8
        make_pdf(self.pdf, ["", long, long, "", long, long])  # pagine 1 e 4 vuote -> scartate

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_blocks_are_imported_by_page_range(self) -> None:
        chunks = load_chunks(self.pdf, chars_per_chunk=600)  # confini nuovi: senza le pagine vuote
        self.assertEqual([(c.first_page, c.last_page) for c in chunks], [(2, 3), (5, 6)])
        legacy = self.camps / "Vampire" / "preparazione" / "Manuale_Base"
        legacy.mkdir(parents=True)
        good = {"riassunto": "Vecchio blocco valido.", "sezioni": [{"file": "meccanica/regole.md", "titolo": "Combattimento", "testo": "Danni."}], "glossario": []}
        (legacy / "blocco_001_p1-3.json").write_text(json.dumps(good), encoding="utf-8")  # copre 2-3 con una pagina in piu'
        (legacy / "blocco_002_p4-6.json").write_text('{"riassunto": "", "sezioni": [], "glossario": []}', encoding="utf-8")  # vuoto: non vale

        client = FakePrepClient()
        prep = Preparer(self.camp, client=client, cache_root=self.tmp / "cache", batch=False)
        import master.prepare as mod
        orig = mod.load_chunks
        mod.load_chunks = lambda path, **kw: orig(path, chars_per_chunk=600, **{k: v for k, v in kw.items() if k != "chars_per_chunk"})
        lines: list[str] = []
        try:
            report = prep.run(str(self.pdf), consolidate=False, tidy=False, progress=lines.append)
        finally:
            mod.load_chunks = orig
        self.assertTrue(any("recuperati 1 blocchi" in l for l in lines))
        self.assertEqual(report.chunks_from_cache, 1)
        self.assertEqual(len(client.calls), 1)  # solo il secondo blocco e' stato pagato
        self.assertTrue(self.camp.file("meccanica/regole.md").get_section("Combattimento").startswith("Danni."))


class TestUnfinishedRuns(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.log_dir = self.tmp / "log"
        self.log_dir.mkdir()

    def tearDown(self) -> None:
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_simple_master_handler", False):
                logging.getLogger().removeHandler(h)
                h.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_forced_close_is_flagged_once_and_diagnosed(self) -> None:
        today = dt.date.today().isoformat()
        killed = self.log_dir / f"{today}-gioco-menu.log"
        killed.write_text(
            f"{today} 15:50:37 INFO    master: AVVIO gioco | campagna=menu\n"
            f"{today} 16:03:10 INFO    master.preparazione: blocco 5 api -> claude-opus-5\n"
            f"{today} 16:03:12 INFO    httpx2: HTTP Request: POST https://api.anthropic.com/v1/messages \"HTTP/1.1 200 OK\"\n"
            f"{today} 16:54:06 INFO    master: ============================================================\n"
            f"{today} 16:54:06 INFO    master: AVVIO gioco | campagna=menu\n"
            f"{today} 16:54:07 INFO    master.ui: UI in ascolto\n",
            encoding="utf-8",
        )
        clean = self.log_dir / f"{today}-riordino-x.log"
        clean.write_text(f"{today} 10:00:00 INFO    master: AVVIO riordino\n{today} 10:00:01 INFO    master.main: FINE riordino\n", encoding="utf-8")

        import os
        import time as _time

        hour_ago = _time.time() - 3600  # registri senza pid: conta l'eta' del file
        os.utime(killed, (hour_ago, hour_ago))
        path = logbook.setup("gioco", "altra", log_dir=self.log_dir)
        text = killed.read_text(encoding="utf-8")
        # la corsa delle 15:50 e' morta durante una richiesta: interruzione vera, con la sua ultima attivita'
        self.assertEqual(text.count("INTERROTTA senza chiusura"), 1)
        self.assertIn(f"corsa avviata alle {today} 15:50:37 INTERROTTA", text)
        self.assertIn("ultima attivita': " + today + " 16:03:12 INFO httpx2", text)
        # quella delle 16:54 era a riposo sul menu: normale chiusura della finestra, nessun allarme
        self.assertIn(f"corsa avviata alle {today} 16:54:06: finestra chiusa a riposo", text)
        self.assertNotIn("INTERROTTA", clean.read_text(encoding="utf-8"))
        self.assertIn("si e' chiusa a forza", path.read_text(encoding="utf-8"))
        findings, _ = logbook.diagnose(self.log_dir)
        self.assertEqual(sum(1 for f in findings if f.file == killed.name and "CHIUSURA FORZATA" in f.text), 1)
        # un secondo avvio non le segnala di nuovo
        logbook.setup("gioco", "altra", log_dir=self.log_dir)
        after = killed.read_text(encoding="utf-8")
        self.assertEqual((after.count("INTERROTTA senza chiusura"), after.count("a riposo")), (1, 1))

    def test_idle_close_and_running_process_are_not_alarms(self) -> None:
        import os

        old = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        idle = self.log_dir / f"{old}-gioco-menu.log"
        idle.write_text(
            f"{old} 10:27:31 INFO    master: AVVIO gioco | pid=999999 | campagna=menu\n"
            f"{old} 10:57:31 INFO    master.ui: campagna chiusa: Vampire\n", encoding="utf-8")
        running = self.log_dir / f"{old}-sanifica-Vampire.log"
        running.write_text(
            f"{old} 11:21:34 INFO    master: AVVIO sanifica | pid={os.getpid()} | campagna=Vampire\n"
            f"{old} 11:25:49 INFO    httpx2: HTTP Request: POST https://api.anthropic.com/v1/messages \"HTTP/1.1 200 OK\"\n", encoding="utf-8")
        found = logbook.flag_unfinished_runs(self.log_dir)
        self.assertEqual(found, [])
        self.assertIn("finestra chiusa a riposo", idle.read_text(encoding="utf-8"))
        self.assertNotIn("INTERROTTA", idle.read_text(encoding="utf-8"))
        self.assertNotIn("INTERROTTA", running.read_text(encoding="utf-8"))  # processo vivo: non si tocca
        self.assertNotIn("a riposo", running.read_text(encoding="utf-8"))
        findings, _ = logbook.diagnose(self.log_dir)
        self.assertEqual(findings, [])
        self.assertTrue(logbook.pid_alive(os.getpid()))
        self.assertFalse(logbook.pid_alive(999999))


if __name__ == "__main__":
    unittest.main()
