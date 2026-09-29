"""Diagnosi automatica: mostra solo le interruzioni non ancora esaminate."""

from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import logbook  # noqa: E402

LINES = (
    "2026-09-17 10:00:00 INFO    master: AVVIO preparazione\n"
    "2026-09-17 10:00:01 INFO    master.preparazione: blocco 3/9 (pagine 61-90): estrazione\n"
    "2026-09-17 10:00:02 INFO    master.preparazione: blocco 3 api -> claude-opus-5 effort=-\n"
    "2026-09-17 10:00:09 ERROR   master.preparazione: preparazione INTERROTTA al blocco 3/9 (pagine 61-90): RateLimitError http=429\n"
    "Traceback (most recent call last):\n"
    "  File \"x\", line 1\n"
    "2026-09-17 10:00:10 INFO    master.main: FINE preparazione\n"
)


class TestDiagnose(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.log_dir = self.tmp / "log"
        self.log_dir.mkdir()
        today = dt.date.today().isoformat()
        self.today_file = self.log_dir / f"{today}-preparazione-mia.log"
        self.today_file.write_text(LINES, encoding="utf-8")
        old_day = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        self.old_file = self.log_dir / f"{old_day}-gioco-mia.log"
        self.old_file.write_text(
            "2026-09-15 09:00:00 ERROR   master.gioco: turno 4 INTERROTTO alla chiamata api #2: APIConnectionError\n",
            encoding="utf-8",
        )
        (self.log_dir / f"{today}-riordino-mia.log").write_text("2026-09-17 11:00:00 INFO    master: ok\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_diagnose_finds_unexamined_with_context(self) -> None:
        findings, scanned = logbook.diagnose(self.log_dir)
        self.assertEqual(scanned, 3)
        self.assertEqual([(f.file, f.line_no) for f in findings], [(self.today_file.name, 4), (self.old_file.name, 1)])
        self.assertTrue(any("blocco 3 api ->" in c for c in findings[0].context))
        text = logbook.diagnosis_text(self.log_dir)
        self.assertIn("2 interruzioni/errori non ancora esaminati in 2 file", text)
        self.assertIn("r.4: 2026-09-17 10:00:09 ERROR", text)
        self.assertIn("--logs examined", text)

    def test_examined_hides_findings_and_archives_old(self) -> None:
        marked = logbook.mark_examined(files=[self.today_file.name, self.old_file.name], log_dir=self.log_dir)
        self.assertEqual(sorted(marked), sorted([self.today_file.name, self.old_file.name]))
        self.assertEqual(logbook.diagnosis_text(self.log_dir), "")
        self.assertTrue(self.today_file.exists())  # oggi resta in uso
        self.assertFalse(self.old_file.exists())  # il vecchio va in archivio
        self.assertTrue(any(p.name == self.old_file.name for p in (self.log_dir / "archivio").rglob("*")))
        # nuove righe dopo il segno tornano in diagnosi
        with open(self.today_file, "a", encoding="utf-8") as fh:
            fh.write("2026-09-17 12:00:00 ERROR   master.gioco: turno 1 INTERROTTO alla chiamata api #1: KeyError\n")
        findings, _ = logbook.diagnose(self.log_dir)
        self.assertEqual([(f.file, f.line_no) for f in findings], [(self.today_file.name, 8)])

    def test_examined_all_and_state_cleanup(self) -> None:
        logbook.mark_examined(everything=True, log_dir=self.log_dir)
        state = logbook._examined(self.log_dir)
        self.assertIn(self.today_file.name, state)
        self.assertNotIn(self.old_file.name, state)  # archiviato: la voce sparisce
        self.assertEqual(logbook.diagnosis_text(self.log_dir), "")


if __name__ == "__main__":
    unittest.main()
