"""Le relazioni dei PNG: il numero lo conta il codice, lo scriba muove per enum, il narratore legge l'etichetta."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine, relazioni  # noqa: E402
from master.context import compose  # noqa: E402
from master.tools import ToolExecutor  # noqa: E402
from master.validate import validate  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402
from test_scribe import ROUTER, scribe_calls  # noqa: E402

TRE = {"stadi": [{"nome": "Ostile", "da": -100, "a": -21}, {"nome": "Neutrale", "da": -20, "a": 20}, {"nome": "Amico", "da": 21, "a": 100}]}


class TestScala(unittest.TestCase):
    def test_default_scale_tiles_the_whole_range_with_a_closed_vocabulary(self) -> None:
        self.assertEqual(relazioni.stadi_problems(relazioni.DEFAULT_STADI), [])
        names = {relazioni.stadio(v) for v in range(-100, 101)}
        self.assertEqual(names, {s["nome"] for s in relazioni.DEFAULT_STADI})
        self.assertEqual((relazioni.stadio(-100), relazioni.stadio(0), relazioni.stadio(100)), ("Nemico", "Neutrale", "Devoto"))
        self.assertEqual(relazioni.stadio(500), "Devoto")  # clampato
        self.assertIn("Nemico (-100..-81) · Ostile (-80..-41)", relazioni.scala())

    def test_a_scale_must_tile_minus_100_to_100(self) -> None:
        good = TRE["stadi"]
        self.assertEqual(relazioni.stadi_problems(good), [])
        for broken, where in (
            ([{**good[0], "da": -90}, good[1], good[2]], "[0].da"),
            ([{**good[0], "a": -25}, good[1], good[2]], "[1].da"),
            ([good[0], {**good[1], "da": -30}, good[2]], "[1].da"),
            ([good[0], good[1], {**good[2], "a": 90}], "[2].a"),
            ([good[0], {**good[1], "nome": "Ostile"}, good[2]], ""),
            ([good[0], {**good[1], "da": "x"}, good[2]], "[1].da"),
            ([good[0], "x", good[2]], "[1]"),
            ([], ""),
            ("x", ""),
        ):
            with self.subTest(where=where):
                self.assertIn(where, [p for p, _ in relazioni.stadi_problems(broken)])

    def test_step_is_an_enum_in_and_a_number_out(self) -> None:
        self.assertEqual((relazioni.passo("sale", "lieve"), relazioni.passo("scende", "forte")), (3, -15))
        for verso, forza in (("su", "lieve"), ("sale", "enorme"), ("sale", 5), ("", "lieve")):
            with self.assertRaises(ValueError):
                relazioni.passo(verso, forza)


class TestCampaignData(Base):
    def test_shift_clamps_and_finds_the_name_without_case(self) -> None:
        self.assertIsNone(self.camp.relation("Mira Sol"))
        moved = self.camp.shift_relation("Mira Sol", "scende", "forte", "ha mentito")
        self.assertEqual((moved["prima"], moved["relazione"], moved["stadio_prima"], moved["stadio"]), (0, -15, "Neutrale", "Diffidente"))
        self.camp.shift_relation("mira sol", "scende", "forte")
        self.assertEqual(list(self.camp.relations()), ["Mira Sol"])  # una sola voce, con il nome originale
        for _ in range(10):
            self.camp.shift_relation("Mira Sol", "scende", "forte")
        self.assertEqual(self.camp.relation("Mira Sol"), -100)
        self.assertEqual(self.camp.relations_line(), "Mira Sol: Nemico (-100)")
        self.assertEqual(json.loads((self.camp.root / "stato.json").read_text(encoding="utf-8"))["png"], {"Mira Sol": {"relazione": -100}})
        with self.assertRaises(ValueError):
            self.camp.shift_relation(" ", "sale", "lieve")

    def test_manual_scale_overrides_the_default_only_if_valid(self) -> None:
        path = self.camp.root / "meccanica" / "relazioni.json"
        path.write_text(json.dumps(TRE), encoding="utf-8")
        self.camp.shift_relation("Mira Sol", "scende", "forte")
        self.assertEqual(self.camp.shift_relation("Mira Sol", "scende", "media")["stadio"], "Ostile")
        self.assertEqual(validate(self.camp).errors, [])
        broken = {"stadi": [{**TRE["stadi"][0], "a": -30}, *TRE["stadi"][1:]]}
        path.write_text(json.dumps(broken), encoding="utf-8")
        self.assertEqual(relazioni.carica(self.camp), relazioni.DEFAULT_STADI)  # un file sbagliato si ignora intero
        self.assertEqual(self.camp.relations_line(), "Mira Sol: Diffidente (-23)")
        self.assertEqual([f.path for f in validate(self.camp).errors], ["meccanica/relazioni.json:stadi[1].da"])


class TestTurno(Base):
    def test_the_tool_moves_by_enum_and_refuses_the_rest(self) -> None:
        ex = ToolExecutor(self.camp)
        text, err = ex.execute("shift_relationship", {"name": "Mira Sol", "verso": "sale", "forza": "media", "reason": "aiuto"})
        self.assertFalse(err, text)
        self.assertEqual(text, "Relazione di Mira Sol: Neutrale -> Neutrale (0 -> 8)")
        self.assertEqual(ex.files_changed, ["stato.json"])
        for bad in ({"name": "Mira Sol", "verso": "sale", "forza": 8, "reason": "x"},
                    {"name": "Mira Sol", "verso": "molto", "forza": "lieve", "reason": "x"},
                    {"name": "", "verso": "sale", "forza": "lieve", "reason": "x"}):
            self.assertTrue(ex.execute("shift_relationship", bad)[1])
        self.assertEqual(self.camp.relation("Mira Sol"), 8)

    def test_notes_line_becomes_a_shift_and_the_next_turn_shows_the_label(self) -> None:
        notes = f"{NARRATION}\n<appunti>\n- relazione: Mira Sol scende (forte) — Kael l'ha minacciata\n</appunti>"
        shift = [_b(type="tool_use", id="r1", name="shift_relationship",
                    input={"name": "Mira Sol", "verso": "scende", "forza": "forte", "reason": "minacciata"})]
        client = ScriptedClient([ROUTER, [_b(type="text", text=notes)], shift])
        engine = MasterEngine(self.camp, client=client)
        engine.play("Minaccio Mira Sol.")
        scribe = scribe_calls(client)[0]
        self.assertIn("shift_relationship", {t["name"] for t in scribe["tools"]})
        self.assertIn("RELAZIONI DEI PNG: (nessuna contata)", scribe["messages"][0]["content"])
        self.assertIn("## Relazioni", client.calls[1]["system"][0]["text"])  # il narratore sa cosa scrivere negli appunti
        self.assertNotIn("shift_relationship", {t["name"] for t in client.calls[1]["tools"]})  # ma non ha il tool
        self.assertEqual(self.camp.relation("Mira Sol"), -15)
        # al turno dopo il narratore vede l'etichetta, non il numero; lo scriba vede entrambi
        text = compose(self.camp, "Parlo con Mira Sol", "kael").text
        self.assertIn("### Mira Sol (png · Diffidente)", text)
        self.assertNotIn("-15", text)
        client.script = [ROUTER, [_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], [_b(type="text", text="fatto")]]
        engine.play("Parlo con Mira Sol.")
        self.assertIn("RELAZIONI DEI PNG: Mira Sol: Diffidente (-15)", scribe_calls(client)[-1]["messages"][0]["content"])

    def test_consolidation_moves_a_medium_step_only_when_asked(self) -> None:
        from master.memory import Consolidator

        for i in range(2):
            self.camp.append_turn("kael", f"azione {i}", f"narrazione {i}")
        data = {"eventi": [{"turno": 1, "testo": "Kael aiuta Mira."}], "fili_aperti": "- x",
                "png": [{"nome": "Mira Sol", "sa": "a", "crede": "", "vuole": "b", "relazione": "sale"},
                        {"nome": "Guardia", "sa": "a", "crede": "", "vuole": "b", "relazione": "invariata"}],
                "scena": {"luogo": "x", "quando": "y", "presenti": [], "appena_successo": "z", "domanda_aperta": "w", "da_richiamare": []},
                "incoerenze": []}
        client = ScriptedClient([[_b(type="text", text=json.dumps(data))]])
        report = Consolidator(self.camp, client).run()
        self.assertEqual(self.camp.relation("Mira Sol"), 8)
        self.assertIsNone(self.camp.relation("Guardia"))
        self.assertIn("stato.json", report.files_changed)
        schema = client.calls[0]["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["png"]["items"]["properties"]["relazione"]["enum"], ["sale", "scende", "invariata"])


if __name__ == "__main__":
    unittest.main()
