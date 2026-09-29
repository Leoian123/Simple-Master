"""Scheda come dati ed esperienza contata dal codice: niente conversioni, niente conti a memoria."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import MasterEngine  # noqa: E402
from master.engine import CLOSING_MARK, MIN_TURNS_TO_CLOSE, OPENING_MARK  # noqa: E402
from master.sheet import Sheet  # noqa: E402
from master.tools import ToolExecutor  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b, write  # noqa: E402


def tool(id_: str, name: str, **args):
    return _b(type="tool_use", id=id_, name=name, input=args)


class TestSheetData(Base):
    def test_master_gets_numbers_not_sentences(self) -> None:
        engine = MasterEngine(self.camp, client=ScriptedClient([]))
        block = engine.system_blocks()[-1]["text"]
        data = json.loads(block[block.index("```json\n") + 8:block.rindex("\n```")])
        self.assertEqual(data["tratti"]["caratteristiche"]["agilita"], 3)
        self.assertEqual(data["stato"]["punti_ferita"], {"attuale": 12, "massimo": 12})
        self.assertEqual(data["esperienza"], {"guadagnati": 0, "spesi": 0, "disponibili": 0, "ultimi_movimenti": []})
        self.assertIn("update_sheet", block)

    def test_update_by_path_and_view_follows(self) -> None:
        ex = ToolExecutor(self.camp)
        text, err = ex.execute("update_sheet", {"changes": [
            {"path": "stato.punti_ferita.attuale", "value": 9},
            {"path": "stato.condizioni.+", "value": "Ferito al braccio"},
            {"path": "equipaggiamento.+", "value": {"nome": "Chiave del faro"}},
            {"path": "stato.monete", "value": 7}]})
        self.assertFalse(err, text)
        self.assertIn("stato.punti_ferita.attuale: 12 -> 9", text)
        self.assertEqual(ex.files_changed, ["schede/kael.json"])
        sheet = Sheet(self.camp, "kael")
        self.assertEqual(sheet.get("stato.punti_ferita.attuale"), 9)
        self.assertEqual(sheet.get("stato.condizioni"), ["Ferito al braccio"])
        view = self.camp.file("schede/kael.md")
        self.assertIn("**Punti ferita:** 9 / 12", view.get_section("Stato"))
        self.assertIn("Chiave del faro", view.get_section("Equipaggiamento"))
        # cancellare una voce; e le modifiche sono tutte o nessuna
        ex.execute("update_sheet", {"changes": [{"path": "stato.condizioni.0", "value": None}]})
        self.assertEqual(Sheet(self.camp, "kael").get("stato.condizioni"), [])
        text, err = ex.execute("update_sheet", {"changes": [{"path": "stato.monete", "value": 0}, {"path": "stato.monete.x", "value": 1}]})
        self.assertTrue(err)
        self.assertEqual(Sheet(self.camp, "kael").get("stato.monete"), 7)

    def test_generated_view_is_not_edited_by_hand(self) -> None:
        ex = ToolExecutor(self.camp)
        for name, args in (("replace_text", {"name": "schede/kael.md", "old": "12 / 12", "new": "9 / 12"}),
                           ("set_section", {"name": "schede/kael.md", "section": "Stato", "text": "x"}),
                           ("append_file", {"name": "schede/kael.md", "text": "x"})):
            text, err = ex.execute(name, args)
            self.assertTrue(err)
            self.assertIn("update_sheet", text)
        # una scheda ancora solo testo si aggiorna come prima
        (self.camp.root / "schede" / "vecchia.md").write_text("# Vecchia\n\n## Stato\n\nPF: 5/5.\n", encoding="utf-8")
        text, err = ex.execute("replace_text", {"name": "schede/vecchia.md", "old": "5/5", "new": "3/5"})
        self.assertFalse(err, text)
        text, err = ex.execute("update_sheet", {"pg": "vecchia", "changes": [{"path": "stato.pf", "value": 3}]})
        self.assertTrue(err)
        self.assertIn("solo testo", text)

    def test_resaving_a_sheet_keeps_the_experience_ledger(self) -> None:
        ex = ToolExecutor(self.camp)
        ex.execute("award_xp", {"amount": 4, "route": "storia", "reason": "Chiusa la storia del faro"})
        text, err = ex.execute("save_sheet", {"name": "Kael", "summary": "Cacciatore di taglie", "starting_xp": 10,
                                               "data": {"stato": {"punti_ferita": {"attuale": 14, "massimo": 14}}, "esperienza": {"registro": []}}})
        self.assertFalse(err, text)
        sheet = Sheet(self.camp, "kael")
        self.assertEqual(sheet.get("stato.punti_ferita.massimo"), 14)
        self.assertEqual(sheet.xp(), {"guadagnati": 4, "spesi": 0, "disponibili": 4})  # ne' azzerato ne' gonfiato


class TestExperience(Base):
    def test_counter_is_computed_from_the_ledger(self) -> None:
        ex = ToolExecutor(self.camp)
        text, err = ex.execute("award_xp", {"amount": 1, "route": "sessione", "reason": "Sessione giocata"})
        self.assertFalse(err, text)
        self.assertIn("disponibili 1 (guadagnati 1, spesi 0)", text)
        text, err = ex.execute("award_xp", {"amount": 5, "route": "regola", "reason": "Regola X del manuale"})
        self.assertIn("disponibili 6", text)
        rows = Sheet(self.camp, "kael").data["esperienza"]["registro"]
        self.assertEqual([(r["via"], r["punti"], r["sessione"]) for r in rows], [("sessione", 1, 1), ("regola", 5, 1)])
        self.assertNotIn("disponibili", Sheet(self.camp, "kael").data["esperienza"])  # nessun totale salvato: si calcola
        self.assertIn("sessione 1 · regola · +5 — Regola X del manuale", self.camp.file("schede/kael.md").get_section("Esperienza"))

    def test_routes_are_guarded(self) -> None:
        ex = ToolExecutor(self.camp)
        ex.execute("award_xp", {"amount": 1, "route": "sessione", "reason": "Sessione giocata"})
        text, err = ex.execute("award_xp", {"amount": 1, "route": "sessione", "reason": "Di nuovo"})
        self.assertTrue(err)
        self.assertIn("gia' stati assegnati", text)
        for bad in ({"amount": 1, "route": "simpatia", "reason": "x"}, {"amount": 0, "route": "storia", "reason": "x"},
                    {"amount": 2, "route": "storia", "reason": " "}):
            self.assertTrue(ex.execute("award_xp", bad)[1])
        # l'esperienza non si scrive a mano
        text, err = ex.execute("update_sheet", {"changes": [{"path": "esperienza.registro", "value": []}]})
        self.assertTrue(err)
        self.assertEqual(Sheet(self.camp, "kael").xp()["disponibili"], 1)
        # alla sessione dopo la via si riapre
        self.camp.next_session()
        self.assertFalse(ex.execute("award_xp", {"amount": 1, "route": "sessione", "reason": "Sessione 2"})[1])

    def test_spending_raises_the_trait_or_nothing(self) -> None:
        ex = ToolExecutor(self.camp)
        ex.execute("award_xp", {"amount": 6, "route": "storia", "reason": "Fine della prima storia"})
        up = [{"path": "tratti.caratteristiche.mente", "value": 2}]
        text, err = ex.execute("spend_xp", {"amount": 10, "reason": "Mente 1->2", "changes": up})
        self.assertTrue(err)
        self.assertIn("ne servono 10, disponibili 6", text)
        self.assertEqual(Sheet(self.camp, "kael").get("tratti.caratteristiche.mente"), 1)
        text, err = ex.execute("spend_xp", {"amount": 5, "reason": "Mente 1->2: 5 x 1", "changes": up})
        self.assertFalse(err, text)
        self.assertIn("disponibili 1 (guadagnati 6, spesi 5)", text)
        sheet = Sheet(self.camp, "kael")
        self.assertEqual(sheet.get("tratti.caratteristiche.mente"), 2)
        self.assertEqual(sheet.xp(), {"guadagnati": 6, "spesi": 5, "disponibili": 1})


class TestSessionRoute(Base):
    def _closing(self):
        return [_b(type="text", text=NARRATION), write("d1", "## Sessione 1 — riepilogo\n\nKael ha trovato Mira."),
                tool("x1", "award_xp", amount=1, route="sessione", reason="Una sessione giocata")]

    def test_closing_a_session_awards_experience_and_advances(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        engine.prelude()
        self.assertFalse(engine.can_close())  # il preludio da solo non e' una sessione
        for i in range(MIN_TURNS_TO_CLOSE):
            engine.play(f"Azione {i}.")
        self.assertTrue(engine.can_close())
        client.script = [self._closing()]
        calls = len(client.calls)
        result = engine.close_session()
        self.assertEqual(len(client.calls), calls + 1)  # una chiamata sola
        brief = client.calls[-1]["messages"][-1]["content"]
        brief = brief if isinstance(brief, str) else brief[-1]["text"]
        for piece in ("CHIUSURA DELLA SESSIONE 1", "award_xp", "`sessione`", "Fili aperti di Kael"):
            self.assertIn(piece, brief)
        # il totale lo aggiunge il codice, dal registro
        self.assertEqual(result.text, NARRATION + "\n\n*Esperienza di Kael: 1 disponibili (1 guadagnati, 0 spesi).*")
        self.assertIn("1 punto esperienza", brief)  # la regola del manuale viaggia gia' nel messaggio
        self.assertEqual(Sheet(self.camp, "kael").xp()["disponibili"], 1)
        self.assertEqual(self.camp.session_number, 2)
        self.assertEqual(self.camp.turn_count("kael"), 0)  # archiviata, chiusura compresa
        archived = list((self.camp.root / "sessioni" / "archivio").glob("kael-*.jsonl"))
        self.assertEqual(len(archived), 1)
        self.assertIn(CLOSING_MARK, archived[0].read_text(encoding="utf-8"))
        # la sessione dopo non rinasce dal background: riparte da diario e fili aperti
        engine.prelude("riprendiamo con calma")
        opening = client.calls[-1]["messages"][-1]["content"]
        opening = opening if isinstance(opening, str) else opening[-1]["text"]
        self.assertIn("APERTURA DELLA SESSIONE 2", opening)
        self.assertNotIn("ROTTURA", opening)
        self.assertTrue(self.camp.read_turns("kael")[0]["player"].startswith(OPENING_MARK))
        self.assertIn("Sessione in corso: 2", client.calls[-1]["system"][-1]["text"])

    def test_a_few_turns_are_a_restart_not_a_session(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        engine.prelude()
        engine.play("Mi guardo attorno.")
        calls = len(client.calls)
        self.assertIsNone(engine.close_session())
        self.assertEqual(len(client.calls), calls)  # nessuna chiamata, nessun punto
        self.assertEqual(self.camp.session_number, 1)
        self.assertEqual(self.camp.turn_count("kael"), 0)
        self.assertEqual(Sheet(self.camp, "kael").xp()["guadagnati"], 0)

    def test_page_shows_counter_and_closes_from_the_button(self) -> None:
        import shutil

        from master.ui import App

        camps = self.tmp / "campaigns"
        shutil.copytree(self.tmp / "camp", camps / "esempio")
        client = ScriptedClient([])
        app = App(camps, "esempio", client=client)
        state = app.state()
        self.assertEqual((state["xp"]["disponibili"], state["session"], state["can_close"]), (0, 1, False))
        for i in range(MIN_TURNS_TO_CLOSE):
            app.play(f"Azione {i}.")
        self.assertTrue(app.state()["can_close"])
        client.script = [self._closing()]
        out = app.new_session("play")
        self.assertTrue(out["closing"]["text"].endswith("1 disponibili (1 guadagnati, 0 spesi).*"))
        self.assertEqual((out["xp"]["disponibili"], out["session"]), (1, 2))
        self.assertEqual(app.state()["xp"]["guadagnati"], 1)


if __name__ == "__main__":
    unittest.main()
