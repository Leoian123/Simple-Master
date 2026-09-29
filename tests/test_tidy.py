"""Test del riordino glossario senza rete."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import Campaign, GlossaryEntry  # noqa: E402
from master.tidy import GlossaryTidier  # noqa: E402


def _block(**kw):
    return SimpleNamespace(**kw)


class FakeTidyClient:
    def __init__(self, data: dict | None = None, raw: str | None = None) -> None:
        self.calls: list[dict] = []
        self.data = data
        self.raw = raw
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.raw if self.raw is not None else json.dumps(self.data, ensure_ascii=False)
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=20, output_tokens=10),
                               content=[_block(type="text", text=text)])


class TestTidy(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "camp"
        shutil.copytree(ROOT / "campaigns" / "esempio", self.root)
        self.camp = Campaign(self.root)
        # un file nuovo senza voce, una voce verso un file sparito, una voce sospetta
        self.camp.file("meccanica/oggetti.md").write("# Oggetti\n\n## Spada del Faro\n\nLama che brilla nella nebbia.\n")
        self.camp.glossary.upsert(GlossaryEntry("Vecchia rovina", "luogo", "Non esiste piu'", "rovine.md"))
        self.camp.glossary.upsert(GlossaryEntry("Fantasma", "png", "Mai scritto nel file", "ambientazione/npc.md"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sync_files_adds_updates_removes_flags(self) -> None:
        report = GlossaryTidier(self.camp, client=FakeTidyClient()).sync_files()
        g = self.camp.glossary
        self.assertIn("meccanica/oggetti.md", report.files_added)
        self.assertEqual(g.get("Oggetti").kind, "file")
        self.assertIn("Spada del Faro", g.get("Oggetti").description)
        self.assertEqual(report.removed_missing, ["Vecchia rovina"])
        self.assertIsNone(g.get("Vecchia rovina"))
        self.assertIn("Fantasma", report.suspicious)
        self.assertNotIn("Kael", report.suspicious)
        # i file gia' indicizzati da voci di contenuto non ricevono una voce "file" doppia
        self.assertFalse(any(e.kind == "file" and e.file == "ambientazione/npc.md" for e in g.entries()))
        # seconda passata: niente cambia
        report2 = GlossaryTidier(self.camp, client=FakeTidyClient()).sync_files()
        self.assertEqual((report2.files_added, report2.files_updated, report2.removed_missing), ([], [], []))

    def test_organize_removes_and_sorts_keeping_descriptions(self) -> None:
        client = FakeTidyClient({
            "mantieni": [
                {"term": "kael", "kind": "pg"},
                {"term": "Regole", "kind": "regola"},
                {"term": "Velmora", "kind": "luogo"},
                {"term": "Inventato", "kind": "altro"},
            ],
            "rimuovi": [{"term": "Fantasma", "motivo": "sospetta, nessuna sezione"}],
        })
        tidier = GlossaryTidier(self.camp, client=client, effort="low")
        report = tidier.run(progress=lambda s: None)

        g = self.camp.glossary
        self.assertIsNone(g.get("Fantasma"))
        self.assertEqual(report.removed_by_api, [("Fantasma", "sospetta, nessuna sezione")])
        self.assertIsNone(g.get("Inventato"))
        # voci non nominate dall'API restano
        self.assertIsNotNone(g.get("Mira Sol"))
        self.assertIsNotNone(g.get("Oggetti"))
        # descrizione originale conservata, ordine per tipo poi termine
        self.assertEqual(g.get("Kael").description, "Il personaggio del giocatore, cacciatore di taglie")
        kinds = [e.kind for e in g.entries()]
        self.assertEqual(kinds[0], "regola")
        self.assertLess(kinds.index("file"), kinds.index("luogo"))
        self.assertTrue(g.text().startswith("# Glossario"))
        self.assertEqual(report.kept, len(g.entries()))
        # richiesta: schema JSON, effort basso, voci sospette nel messaggio
        call = client.calls[0]
        self.assertEqual(call["extra_body"]["output_config"]["effort"], "low")
        self.assertEqual(call["extra_body"]["output_config"]["format"]["type"], "json_schema")
        self.assertIn("Fantasma", call["messages"][0]["content"].split("## Voci sospette")[1])

    def test_organize_refuses_mass_deletion(self) -> None:
        terms = [e.term for e in self.camp.glossary.entries()]
        client = FakeTidyClient({"mantieni": [], "rimuovi": [{"term": t, "motivo": "x"} for t in terms]})
        before = self.camp.glossary.text()
        report = GlossaryTidier(self.camp, client=client).organize()
        self.assertEqual(self.camp.glossary.text(), before)
        self.assertTrue(report.warnings)

    def test_organize_invalid_json_keeps_glossary(self) -> None:
        before = self.camp.glossary.text()
        report = GlossaryTidier(self.camp, client=FakeTidyClient(raw="boh")).organize()
        self.assertEqual(self.camp.glossary.text(), before)
        self.assertTrue(report.warnings)


if __name__ == "__main__":
    unittest.main()
