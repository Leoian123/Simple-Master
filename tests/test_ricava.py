"""`--ricava`: le estrazioni a una chiamata dalla riga di comando, le stesse dei pulsanti del tavolo."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402
from master import prefazione, tabelle  # noqa: E402
from master.models import estimate_usd  # noqa: E402
from test_economy import Base, ScriptedClient, _b  # noqa: E402
from test_tabelle import INCONTRI  # noqa: E402


class TestRicava(Base):
    def test_each_item_runs_its_extraction_and_reports_a_line(self) -> None:
        self.camp.file("meccanica/creature.md").set_section(
            "Incontri casuali", "Tira 1d6 ogni ora.\n\n| 1-3 | Nulla |\n| 4-5 | Un lupo |\n| 6 | Un orso |")
        self.camp.file("meccanica/regole.md").set_section("Fame", "Quando la Fame raggiunge 4 serve una prova; a 5 scende in frenesia.")
        answers = [
            [_b(type="text", text=json.dumps({"tabelle": [INCONTRI]}))],
            [_b(type="text", text=json.dumps({"precedenza": ["La scheda"], "cercare": [], "riserva": ["Un d20"]}))],
            [_b(type="text", text=json.dumps({"trigger": [
                {"nome": "fame alta", "ricorrente": True, "fonte": "Fame",
                 "quando": [{"tipo": "scheda", "soggetto": "stato.monete", "op": ">=", "valore": "4"}],
                 "effetti": [{"tipo": "avviso", "testo": "Serve una prova", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}]}]}))],
        ]
        client = ScriptedClient(answers)
        lines = main.ricava(self.camp, ["tabelle", "prefazione", "trigger"], "claude-sonnet-5", client=client)
        self.assertEqual(lines, ["tabelle: incontri nella palude", f"prefazione: scritta in testa a {prefazione.RULES_FILE} (0 rimandi)", "trigger: fame alta"])
        self.assertEqual(len(client.calls), 3)
        self.assertEqual([t["nome"] for t in tabelle.carica(self.camp)], ["incontri nella palude"])
        self.assertTrue(prefazione.leggi(self.camp))
        self.assertIn("fame alta", (self.camp.root / "meccanica" / "trigger.json").read_text(encoding="utf-8"))

    def test_without_a_sheet_triggers_are_refused_and_nothing_is_called(self) -> None:
        for s in self.camp.sheets():
            (self.camp.root / "schede" / f"{s['slug']}.json").unlink(missing_ok=True)
            (self.camp.root / "schede" / f"{s['slug']}.md").unlink(missing_ok=True)
        client = ScriptedClient([])
        lines = main.ricava(self.camp, ["trigger"], "m", client=client)
        self.assertIn("serve un personaggio", lines[0])
        self.assertEqual(client.calls, [])

    def test_cli_flag_is_parsed_with_its_choices(self) -> None:
        parser = main.build_parser() if hasattr(main, "build_parser") else None
        if parser is None:
            self.skipTest("nessun build_parser esposto")
        args = parser.parse_args(["--campaign", "x", "--ricava", "dadi", "trigger"])
        self.assertEqual(args.ricava, ["dadi", "trigger"])

    def test_anteprima_reads_the_disk_and_calls_nothing(self) -> None:
        self.camp.file("meccanica/creature.md").set_section(
            "Incontri casuali", "Tira 1d6 ogni ora.\n\n| 1-3 | Nulla |\n| 4-5 | Un lupo |\n| 6 | Un orso |")
        self.camp.file("meccanica/regole.md").set_section("Fame", "Quando la Fame raggiunge 4 serve una prova; a 5 scende in frenesia.")
        lines = main.anteprima(self.camp, ["dadi", "tabelle", "prefazione", "trigger"], "claude-sonnet-5")
        self.assertTrue(lines[0].startswith("Gia' su disco: dadi: d20 | tabelle: 0 | prefazione: assente | trigger: 0"), lines[0])
        self.assertIn("tabelle: 1 sezioni con la forma di una tabella (Incontri casuali)", lines[2])
        self.assertIn("trigger: 1 sezioni con soglie (Fame [", lines[4])
        for line in lines[1:]:
            self.assertRegex(line, r"circa \$\d+\.\d\d$")
        self.assertEqual(main.build_parser().parse_args(["--campaign", "x", "--ricava", "dadi", "--anteprima"]).anteprima, True)

    def test_estimate_grows_with_the_text_and_is_informative_only(self) -> None:
        small, big = estimate_usd("claude-sonnet-5", 1_000), estimate_usd("claude-sonnet-5", 100_000)
        self.assertLess(small, big)
        self.assertLess(small, 0.05)
        self.assertLess(big, 1.0)


if __name__ == "__main__":
    unittest.main()
