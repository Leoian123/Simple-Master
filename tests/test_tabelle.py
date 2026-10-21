"""Le tabelle casuali come dati: la forma, il tiro dal marcatore, l'estrazione che scarta cio' che non quadra."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine, dice, tabelle  # noqa: E402
from master.validate import validate  # noqa: E402
from test_dice import fixed  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402

INCONTRI = {"nome": "incontri nella palude", "uso": "ogni ora di marcia", "dado": "1d6", "fonte": "x",
            "righe": [{"da": 1, "a": 3, "risultato": "Nulla"}, {"da": 4, "a": 5, "risultato": "Un lupo"}, {"da": 6, "a": 6, "risultato": "Un orso"}]}
TESORI = {"nome": "tesori", "uso": "", "dado": "2d6+1", "fonte": "",
          "righe": [{"da": 3, "a": 7, "risultato": "Monete"}, {"da": 8, "a": 13, "risultato": "Una gemma"}]}


class TestForma(unittest.TestCase):
    def test_valid_tables_tile_their_die(self) -> None:
        self.assertEqual(tabelle.problems({"tabelle": [INCONTRI, TESORI]}), [])
        self.assertEqual(tabelle.dice_range("2d6+1"), (3, 13))
        self.assertEqual(tabelle.dice_range("1d100"), (1, 100))
        self.assertIsNone(tabelle.dice_range("banana"))

    def test_problems_name_the_path(self) -> None:
        rows = INCONTRI["righe"]
        for broken, where in (
            ({**INCONTRI, "nome": ""}, "tabelle[0].nome"),
            ({**INCONTRI, "dado": "1d8"}, "tabelle[0].righe[2].a"),
            ({**INCONTRI, "righe": [{**rows[0], "da": 2}, rows[1], rows[2]]}, "tabelle[0].righe[0].da"),
            ({**INCONTRI, "righe": [rows[0], {**rows[1], "da": 3}, rows[2]]}, "tabelle[0].righe[1].da"),
            ({**INCONTRI, "righe": [rows[0], {**rows[1], "a": 4}, rows[2]]}, "tabelle[0].righe[2].da"),
            ({**INCONTRI, "righe": [rows[0], rows[1], {**rows[2], "risultato": " "}]}, "tabelle[0].righe[2].risultato"),
            ({**INCONTRI, "righe": []}, "tabelle[0].righe"),
            ({**INCONTRI, "righe": [rows[0], "x", rows[2]]}, "tabelle[0].righe[1]"),
            ("x", "tabelle[0]"),
        ):
            with self.subTest(where=where):
                self.assertIn(where, [p for p, _ in tabelle.problems({"tabelle": [broken]})])
        self.assertIn("tabelle[1].nome", [p for p, _ in tabelle.problems({"tabelle": [INCONTRI, {**TESORI, "nome": "Incontri nella palude"}]})])


class TestTiro(unittest.TestCase):
    def test_roll_finds_the_row_and_formats_the_outcome(self) -> None:
        with fixed(5):
            r = tabelle.tira("incontri nella palude", [INCONTRI, TESORI])
        self.assertEqual((r["value"], r["result"]), (5, "Un lupo"))
        self.assertEqual(r["text"], "[Tabella] incontri nella palude — 1d6: 5 -> 5: Un lupo")
        with fixed(4, 6):
            r = tabelle.tira("Tesori", [INCONTRI, TESORI])  # 4+6+1 = 11
        self.assertEqual((r["value"], r["result"]), (11, "Una gemma"))
        self.assertIn("2d6+1: [4, 6] -> 11", r["text"])
        with fixed(1):
            self.assertEqual(tabelle.tira("[[tabella: incontri nella palude]]", [INCONTRI])["result"], "Nulla")
        with self.assertRaises(ValueError):
            tabelle.tira("inesistente", [INCONTRI])
        self.assertEqual(tabelle.find_marker("Buio.\n[[tabella: incontri nella palude]]"), "incontri nella palude")
        self.assertIsNone(tabelle.find_marker("Nessuna tabella."))

    def test_modifier_in_the_marker_shifts_the_roll_and_stops_at_the_ends(self) -> None:
        self.assertEqual(tabelle.parse("incontri | +2"), ("incontri", 2))
        self.assertEqual(tabelle.parse("[[tabella: incontri | - 3 | +1]]"), ("incontri", -2))
        self.assertEqual(tabelle.parse("incontri"), ("incontri", 0))
        with self.assertRaises(ValueError):
            tabelle.parse("incontri | di notte")
        with fixed(4):
            r = tabelle.tira("incontri nella palude | +2", [INCONTRI])
        self.assertEqual((r["value"], r["modifier"], r["result"]), (6, 2, "Un orso"))
        self.assertEqual(r["text"], "[Tabella] incontri nella palude — 1d6: 4 +2 -> 6: Un orso")
        with fixed(5):
            r = tabelle.tira("incontri nella palude | +4", [INCONTRI])
        self.assertEqual(r["value"], 6)
        self.assertIn("5 +4 (fermato a 6) -> 6: Un orso", r["text"])
        with fixed(2):
            self.assertEqual(tabelle.tira("incontri nella palude | -5", [INCONTRI])["value"], 1)

    def test_outcome_reads_the_row_back_from_the_message(self) -> None:
        self.assertEqual(tabelle.outcome("[Tabella] incontri nella palude — 1d6: 4 +2 -> 6: Un orso"), ("incontri nella palude", "Un orso"))
        self.assertEqual(tabelle.outcome("[Tabella] tesori — 2d6+1: [4, 6] -> 11: Una gemma"), ("tesori", "Una gemma"))
        self.assertIsNone(tabelle.outcome("[Tiro] x — 1d6: 4 -> totale 4."))
        self.assertIsNone(tabelle.outcome("Apro la porta."))
        with fixed(3, 6):
            text = tabelle.tira("tesori", [TESORI])["text"]
        self.assertEqual(tabelle.outcome(text), ("tesori", "Una gemma"))

    def test_prompt_lists_the_tables(self) -> None:
        text = tabelle.prompt_section([INCONTRI, TESORI])
        self.assertIn("## Tabelle: le tira il programma", text)
        self.assertIn("- `[[tabella: incontri nella palude]]` (1d6) — ogni ora di marcia", text)
        self.assertIn("- `[[tabella: tesori]]` (2d6+1)", text)
        self.assertIn("`[[tabella: <nome> | +10]]`", text)
        self.assertEqual(tabelle.prompt_section([]), "")


class TestInCampagna(Base):
    def write(self, tables) -> None:
        (self.camp.root / "meccanica" / "tabelle.json").write_text(json.dumps({"tabelle": tables}), encoding="utf-8")

    def test_tables_enter_the_cached_prompt_and_the_page_rolls_them(self) -> None:
        engine = MasterEngine(self.camp, client=ScriptedClient([]))
        self.assertNotIn("## Tabelle", engine.system_blocks()[0]["text"])
        self.write([INCONTRI])
        self.assertIn("[[tabella: incontri nella palude]]", engine.system_blocks()[0]["text"])
        self.assertEqual(validate(self.camp).errors, [])
        from master.ui import PAGE, App

        camps = self.tmp / "campaigns"
        shutil.copytree(self.tmp / "camp", camps / "esempio")
        client = ScriptedClient([[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], [_b(type="text", text="fatto")]])
        app = App(camps, "esempio", client=client)
        self.assertEqual(app.state()["tables"], ["incontri nella palude"])
        with fixed(6):
            out = app.table("incontri nella palude")
        self.assertEqual(out["roll_text"], "[Tabella] incontri nella palude — 1d6: 6 -> 6: Un orso")
        self.assertTrue(client.calls[0]["messages"][-1]["content"].rstrip().endswith(out["roll_text"]))  # l'esito va al narratore
        self.assertFalse(any("Scegli le sezioni" in str(c.get("system")) for c in client.calls))  # e non si instrada
        self.assertIn("TABLE_RE", PAGE)
        self.assertIn("/api/table", PAGE)

    def test_a_broken_file_is_ignored_whole_and_reported(self) -> None:
        self.write([{**INCONTRI, "dado": "1d8"}])
        self.assertEqual(tabelle.carica(self.camp), [])
        self.assertEqual([f.path for f in validate(self.camp).errors], ["meccanica/tabelle.json:tabelle[0].righe[2].a"])

    def test_extraction_keeps_only_tables_that_add_up(self) -> None:
        self.camp.file("meccanica/creature.md").set_section(
            "Incontri casuali", "Tira 1d6 ogni ora.\n\n| 1-3 | Nulla |\n| 4-5 | Un lupo |\n| 6 | Un orso |")
        found = tabelle.sections_with_tables(self.camp)
        self.assertEqual([(f, t) for f, t, _ in found], [("meccanica/creature.md", "Incontri casuali")])
        broken = {**TESORI, "righe": [{"da": 3, "a": 7, "risultato": "Monete"}]}  # non copre 2d6+1
        client = ScriptedClient([[_b(type="text", text=json.dumps({"tabelle": [INCONTRI, broken, INCONTRI]}))]])
        kept = tabelle.extract_tables(self.camp, client, "claude-sonnet-5")
        self.assertEqual([t["nome"] for t in kept], ["incontri nella palude"])  # la rotta scartata, il doppione pure
        self.assertEqual(client.calls[0]["output_config"]["format"]["schema"], tabelle.TABLES_SCHEMA)
        self.assertIn("Incontri casuali", client.calls[0]["messages"][0]["content"])
        self.assertEqual([t["nome"] for t in tabelle.carica(self.camp)], ["incontri nella palude"])
        self.assertEqual(validate(self.camp).errors, [])
        # senza sezioni con la forma di una tabella: nessuna chiamata
        self.camp.file("meccanica/creature.md").remove_section("Incontri casuali")
        client = ScriptedClient([])
        self.assertEqual(tabelle.extract_tables(self.camp, client, "m"), [])
        self.assertEqual(client.calls, [])


class TestAlias(unittest.TestCase):
    def test_marker_resolves_an_alias_and_the_prompt_shows_it(self) -> None:
        from test_dice import POOL

        profile = {"tiri": [{**POOL["tiri"][0], "speciali": None, "alias": ["test", "Prova di abilita'"]}]}
        with fixed(6, 6, 2):
            self.assertEqual(dice.roll("x | test | dadi 3 | difficolta 2", profile)["value"], 2)
        with fixed(6, 6, 2):
            self.assertEqual(dice.roll("x | prova di abilita' | dadi 3 | difficolta 2", profile)["value"], 2)
        self.assertIn("(anche: test, Prova di abilita')", dice.prompt_section(profile))


if __name__ == "__main__":
    unittest.main()
