"""La prefazione d'uso delle regole: scritta in testa, rimandi solo a sezioni vere, letta dal prompt in cache."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine, prefazione  # noqa: E402
from master.context import rule_titles  # noqa: E402
from master.validate import validate  # noqa: E402
from test_economy import Base, ScriptedClient, _b  # noqa: E402

DATA = {"precedenza": ["La scheda del personaggio sulle regole generali", "Una regola specifica su una generale"],
        "cercare": [{"argomento": "Costi dell'esperienza", "file": "meccanica/regole.md", "sezione": "Esperienza"}],
        "riserva": ["Un tiro di d20 contro 15 quando l'esito e' incerto"]}


class TestRender(unittest.TestCase):
    def test_three_fixed_heads_and_lookup_lines(self) -> None:
        body = prefazione.render(DATA)
        self.assertIn("### Precedenza\n\n1. La scheda del personaggio sulle regole generali\n2. Una regola", body)
        self.assertIn("### Cerca nei file, non a memoria\n\n- Costi dell'esperienza: `meccanica/regole.md` § Esperienza", body)
        self.assertIn("### Se una regola non si trova\n\n- Un tiro di d20", body)
        self.assertEqual(prefazione.lookups(body), [("Costi dell'esperienza", "meccanica/regole.md", "Esperienza")])
        self.assertIn("(nessun rimando)", prefazione.render({"precedenza": [], "cercare": [], "riserva": []}))


class TestInCampagna(Base):
    def test_written_at_the_head_and_replaced_in_place(self) -> None:
        rules = self.camp.file(prefazione.RULES_FILE)
        before = rules.outline()
        prefazione.scrivi(self.camp, prefazione.render(DATA))
        self.assertEqual(rules.outline(), [prefazione.SECTION_TITLE] + before)
        self.assertTrue(rules.read().startswith("# Regole\n\n## Come usare queste regole\n\n### Precedenza"))
        prefazione.scrivi(self.camp, "### Precedenza\n\n1. Solo questa")
        self.assertEqual(rules.outline(), [prefazione.SECTION_TITLE] + before)  # sostituita, non aggiunta
        self.assertEqual(prefazione.leggi(self.camp), "### Precedenza\n\n1. Solo questa")
        self.assertNotIn("Costi dell'esperienza", rules.read())

    def test_prompt_block_carries_it_and_the_router_does_not_offer_it(self) -> None:
        engine = MasterEngine(self.camp, client=ScriptedClient([]))
        self.assertNotIn("Come usare le regole", engine.system_blocks()[0]["text"])
        prefazione.scrivi(self.camp, prefazione.render(DATA))
        text = engine.system_blocks()[0]["text"]
        self.assertIn("## Come usare le regole di questa campagna", text)
        self.assertIn("- Costi dell'esperienza: `meccanica/regole.md` § Esperienza", text)
        self.assertNotIn(prefazione.SECTION_TITLE, [t for _, t in rule_titles(self.camp)])
        self.assertIn("Esperienza", [t for _, t in rule_titles(self.camp)])

    def test_validator_recommends_a_preface_and_warns_on_broken_lookups(self) -> None:
        self.assertNotIn("regole senza prefazione d'uso", [f.title for f in validate(self.camp).recommendations])  # tre sezioni: si leggono intere
        rules = self.camp.file(prefazione.RULES_FILE)
        for i in range(prefazione.MIN_SECTIONS - len(rules.outline())):
            rules.set_section(f"Regola {i}", "Un testo.")
        self.assertIn("regole senza prefazione d'uso", [f.title for f in validate(self.camp).recommendations])
        broken = {**DATA, "cercare": DATA["cercare"] + [{"argomento": "Veleni", "file": "meccanica/regole.md", "sezione": "Veleni"}]}
        prefazione.scrivi(self.camp, prefazione.render(broken))
        report = validate(self.camp)
        self.assertNotIn("regole senza prefazione d'uso", [f.title for f in report.recommendations])
        self.assertEqual([f.title for f in report.warnings if "rimando" in f.title], ["rimando a una sezione che non esiste"])
        self.assertEqual(prefazione.broken_lookups(self.camp), [("Veleni", "meccanica/regole.md", "Veleni")])
        self.assertEqual(report.errors, [])

    def test_extraction_keeps_only_real_lookups_and_writes_the_section(self) -> None:
        answer = {**DATA, "cercare": DATA["cercare"] + [
            {"argomento": "Veleni", "file": "meccanica/veleni.md", "sezione": "Tabella"},  # file inventato
            {"argomento": "Tono", "file": "meccanica/regole.md", "sezione": "tono"},  # titolo in minuscolo: si ricopia esatto
        ]}
        client = ScriptedClient([[_b(type="text", text=json.dumps(answer))]])
        data = prefazione.extract_preface(self.camp, client, "claude-sonnet-5")
        self.assertEqual([c["sezione"] for c in data["cercare"]], ["Esperienza", "Tono"])
        call = client.calls[0]
        self.assertEqual(call["output_config"]["format"]["schema"], prefazione.PREFACE_SCHEMA)
        self.assertIn("INDICE DELLE REGOLE", call["messages"][0]["content"])
        self.assertIn("meccanica/regole.md", call["messages"][0]["content"])
        self.assertEqual(prefazione.broken_lookups(self.camp), [])
        self.assertEqual(self.camp.file(prefazione.RULES_FILE).outline()[0], prefazione.SECTION_TITLE)
        self.assertIn("prefazione", json.dumps(self.camp.spend_summary()))
        # senza regole: nessuna chiamata
        self.camp.file(prefazione.RULES_FILE).write("# Regole\n")
        client = ScriptedClient([])
        self.assertIsNone(prefazione.extract_preface(self.camp, client, "m"))
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
