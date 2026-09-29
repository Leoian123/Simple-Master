"""Il registro deve dire dove e perche' un processo si ferma."""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import Campaign, MasterEngine, logbook  # noqa: E402
from master.prepare import Preparer  # noqa: E402
from test_master import FakeClient, conversation  # noqa: E402
from test_prepare import make_pdf  # noqa: E402


class FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        raise self.exc


class FakeAPIError(Exception):
    status_code = 529
    request_id = "req_test123"


class TestLogbook(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "camp"
        shutil.copytree(ROOT / "campaigns" / "esempio", self.root)
        self.camp = Campaign(self.root)
        self.log_path = logbook.setup("test", "camp", log_dir=self.tmp / "log")

    def tearDown(self) -> None:
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_simple_master_handler", False):
                logging.getLogger().removeHandler(h)
                h.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def text(self) -> str:
        return self.log_path.read_text(encoding="utf-8")

    def test_setup_writes_header(self) -> None:
        self.assertTrue(self.log_path.name.endswith("-test-camp.log"))
        self.assertRegex(self.text(), r"AVVIO test \| pid=\d+ \| campagna=camp \| python")

    def test_turn_is_traced(self) -> None:
        conversation(self.camp, client=FakeClient()).play("Entro alla Lanterna Blu.")  # il ciclo a conversazione, per intero
        t = self.text()
        self.assertIn("master.scheda: turno 1 | giocatore: Entro alla Lanterna Blu.", t)
        self.assertIn("turno 1 api #1 -> claude-opus-5", t)
        self.assertIn("turno 1 api #1 <- stop=tool_use in=10 out=5", t)
        self.assertIn('tool read_file({"name": "ambientazione/npc.md"', t)
        self.assertIn("WARNING master.scheda: turno 1 tool read_file", t)  # manca.md -> errore segnalato
        self.assertRegex(t, r"turno 1 fine \| chiamate=3 token in/out=30/15 cache letti/scritti=0/0 costo~\$\d\.\d+ \| uscita: narrazione \d+ car, tool \d+ car \| letti=ambientazione/npc\.md")

    def test_api_failure_says_where(self) -> None:
        engine = MasterEngine(self.camp, client=FailingClient(FakeAPIError("overloaded")))
        with self.assertRaises(FakeAPIError):
            engine.play("Ciao.")
        t = self.text()
        self.assertIn("turno 1 INTERROTTO alla chiamata api #1: FakeAPIError http=529 request-id=req_test123 overloaded", t)
        self.assertIn("Traceback", t)

    def test_preparation_failure_says_which_chunk(self) -> None:
        pdf = self.tmp / "m.pdf"
        make_pdf(pdf, ["Regole di prova pagina uno", "Pagina due"])
        prep = Preparer(self.camp, client=FailingClient(ConnectionError("rete giu")), cache_root=self.tmp / "cache")
        with self.assertRaises(ConnectionError):
            prep.run(str(pdf), progress=lambda s: None)
        t = self.text()
        self.assertIn("master.preparazione: preparazione: fonte=", t)
        self.assertIn("m.pdf -> 1 blocchi", t)
        self.assertIn("preparazione INTERROTTA al blocco 1/1 (pagine 1-2): ConnectionError rete giu", t)
        self.assertIn("i blocchi gia' fatti sono in cache", t)

    def test_describe_error_and_short(self) -> None:
        self.assertEqual(logbook.describe_error(ValueError("x\ny")), "ValueError x y")
        self.assertEqual(logbook.short("a" * 10, 8), "aaaaa...")
        self.assertEqual(logbook.short({"k": "v"}), '{"k": "v"}')


if __name__ == "__main__":
    unittest.main()
