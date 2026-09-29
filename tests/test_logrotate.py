"""Rotazione, archiviazione e cancellazione ordinata dei registri."""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import logbook  # noqa: E402

TODAY = dt.date(2026, 9, 17)


def make_log(log_dir: Path, day: dt.date, process: str, campaign: str = "camp", text: str = "riga\n", suffix: str = "") -> Path:
    path = log_dir / f"{day.isoformat()}-{process}-{campaign}.log{suffix}"
    path.write_text(text, encoding="utf-8")
    return path


class TestRotation(unittest.TestCase):
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

    def names(self, sub: str = "") -> list[str]:
        base = self.log_dir / sub if sub else self.log_dir
        return sorted(p.name for p in base.rglob("*") if p.is_file())

    def test_parse_name(self) -> None:
        p = self.log_dir / "2026-09-17-gioco-la-mia-campagna.log.2"
        self.assertEqual(logbook.parse_name(p), (TODAY, "gioco", "la-mia-campagna"))
        self.assertIsNone(logbook.parse_name(self.log_dir / "note.txt"))

    def test_rotate_archives_old_and_deletes_expired(self) -> None:
        make_log(self.log_dir, TODAY, "gioco")
        make_log(self.log_dir, TODAY - dt.timedelta(days=3), "gioco")
        old = make_log(self.log_dir, TODAY - dt.timedelta(days=10), "preparazione")
        expired_dir = self.log_dir / "archivio" / "2026-07"
        expired_dir.mkdir(parents=True)
        (expired_dir / "2026-07-01-gioco-camp.log").write_text("x", encoding="utf-8")

        report = logbook.rotate(self.log_dir, keep_days=7, archive_days=30, max_files=40, today=TODAY)

        self.assertEqual(report.archived, [old.name])
        self.assertEqual(report.deleted, ["2026-07-01-gioco-camp.log"])
        self.assertEqual(report.kept, 2)
        self.assertEqual(self.names("archivio"), [old.name])
        self.assertTrue((self.log_dir / "archivio" / "2026-09" / old.name).exists())
        self.assertFalse(expired_dir.exists())  # cartella vuota rimossa

    def test_rotate_caps_file_count_but_never_today(self) -> None:
        for i in range(6):
            make_log(self.log_dir, TODAY - dt.timedelta(days=i), "gioco", campaign=f"c{i}")
        make_log(self.log_dir, TODAY, "riordino")
        report = logbook.rotate(self.log_dir, keep_days=30, max_files=3, today=TODAY)
        remaining = [p.name for p in self.log_dir.iterdir() if p.is_file()]
        self.assertEqual(len(remaining), 3)
        self.assertTrue(all(n.startswith(("2026-09-17", "2026-09-16")) for n in remaining))
        self.assertEqual(len(report.archived), 4)

    def test_dismiss_by_name_and_by_age(self) -> None:
        a = make_log(self.log_dir, TODAY - dt.timedelta(days=1), "gioco")
        b = make_log(self.log_dir, TODAY - dt.timedelta(days=2), "preparazione")
        c = make_log(self.log_dir, TODAY - dt.timedelta(days=5), "riordino")
        today_file = make_log(self.log_dir, TODAY, "gioco")

        report = logbook.dismiss(files=[a.name, today_file.name], log_dir=self.log_dir, today=TODAY)
        self.assertEqual(report.archived, [a.name])  # quello di oggi resta
        report = logbook.dismiss(older_than_days=3, log_dir=self.log_dir, today=TODAY)
        self.assertEqual(report.archived, [c.name])
        self.assertEqual([p.name for p in self.log_dir.iterdir() if p.is_file()], sorted([b.name, today_file.name]))

    def test_purge_archive_then_everything(self) -> None:
        make_log(self.log_dir, TODAY, "gioco")
        make_log(self.log_dir, TODAY - dt.timedelta(days=1), "gioco")
        # "piu' vecchio di 1 giorno" non prende il file di ieri; 0 giorni = tutto tranne oggi
        self.assertEqual(logbook.dismiss(older_than_days=1, log_dir=self.log_dir, today=TODAY).archived, [])
        logbook.dismiss(older_than_days=0, log_dir=self.log_dir, today=TODAY)
        self.assertEqual(len(self.names("archivio")), 1)

        report = logbook.purge(log_dir=self.log_dir, today=TODAY)
        self.assertEqual(len(report.deleted), 1)
        self.assertFalse((self.log_dir / "archivio").exists())

        make_log(self.log_dir, TODAY - dt.timedelta(days=2), "riordino")
        report = logbook.purge(log_dir=self.log_dir, everything=True, today=TODAY)
        self.assertEqual(len(report.deleted), 1)
        self.assertEqual(self.names(), [f"{TODAY.isoformat()}-gioco-camp.log"])

    def test_listing_counts_errors_and_interruptions(self) -> None:
        make_log(self.log_dir, TODAY, "preparazione", text=(
            "2026-09-17 10:00:00 INFO    master: AVVIO\n"
            "2026-09-17 10:00:01 ERROR   master.preparazione: preparazione INTERROTTA al blocco 3/9: ConnectionError\n"
        ))
        make_log(self.log_dir, TODAY, "gioco", text="2026-09-17 10:00:00 INFO    master: ok\n")
        infos = logbook.scan(self.log_dir)
        prep = next(i for i in infos if i.process == "preparazione")
        self.assertEqual((prep.errors, prep.interruptions, prep.lines), (1, 1, 2))
        self.assertIn("INTERROTTA al blocco 3/9", prep.last)
        out = logbook.listing(self.log_dir)
        self.assertIn("1 interruz., 1 errori", out)
        self.assertIn("ultima riga:", out)
        self.assertIn("2 file", out)

    def test_setup_rotates_and_uses_size_rotation(self) -> None:
        old_day = dt.date.today() - dt.timedelta(days=10)  # setup() usa la data reale
        old = make_log(self.log_dir, old_day, "gioco")
        expired = make_log(self.log_dir, dt.date.today() - dt.timedelta(days=60), "gioco", campaign="vecchia")
        path = logbook.setup("test", "camp", log_dir=self.log_dir)
        self.assertFalse(old.exists())
        self.assertTrue((self.log_dir / "archivio" / old_day.strftime("%Y-%m") / old.name).exists())
        self.assertFalse(expired.exists())  # archiviato e subito scaduto (oltre 30 giorni)
        self.assertEqual(self.names("archivio"), [old.name])
        self.assertIn("rotazione registri: archiviati 2, cancellati 1", path.read_text(encoding="utf-8"))
        handler = next(h for h in logging.getLogger().handlers if getattr(h, "_simple_master_handler", False))
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.assertEqual(handler.backupCount, logbook.BACKUPS)


if __name__ == "__main__":
    unittest.main()
