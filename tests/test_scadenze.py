"""Orologi con scadenza e fili aperti con l'eta': il tempo passa anche se nessuno lo conta."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine, fili  # noqa: E402
from master.engine import OPENING_MARK  # noqa: E402
from master.tools import ToolExecutor  # noqa: E402
from master.validate import validate  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402
from test_scribe import ROUTER, scribe_calls  # noqa: E402

QUIET = [_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")]
DONE = [_b(type="text", text="fatto")]


def narrator_calls(client):
    return [c for c in client.calls if "Sei il master" in str(c.get("system"))]


class TestOrologiConScadenza(Base):
    def test_a_clock_with_a_deadline_fills_itself_once_and_the_others_do_not(self) -> None:
        for i in range(3):
            self.camp.append_turn("kael", f"a{i}", f"n{i}")  # tre turni gia' giocati quando l'orologio nasce
        clock = self.camp.set_clock("Guardia", 6, "arrivano", deadline=2)
        self.assertEqual((clock["scadenza_turni"], clock["creato_turno"]), (2, 3))
        self.camp.set_clock("Eterno", 4, "mai")
        self.assertEqual(self.camp.clocks_line(), "Guardia 0/6 (entro 2 turni); Eterno 0/4")
        self.assertEqual(self.camp.expire_clocks(), [])
        self.camp.append_turn("kael", "a3", "n3")
        self.assertEqual(self.camp.clocks_line(), "Guardia 0/6 (entro 1 turni); Eterno 0/4")
        self.camp.append_turn("kael", "a4", "n4")
        self.assertEqual(self.camp.expire_clocks(), ["Guardia"])
        self.assertEqual(self.camp.expire_clocks(), [])  # una volta sola
        self.assertEqual(self.camp.clocks()["Guardia"]["pieni"], 6)
        self.assertEqual(self.camp.pop_full_clocks(), ["Guardia: arrivano (il tempo e' passato)"])
        self.assertEqual(list(self.camp.clocks()), ["Eterno"])  # senza scadenza nulla cambia (regressione)
        self.camp.set_clock("Guardia2", 6, "x", deadline=5)
        self.camp.set_clock("Guardia2", 8, "y")  # aggiornare i segmenti non azzera la scadenza
        self.assertEqual((self.camp.clocks()["Guardia2"]["scadenza_turni"], self.camp.clocks()["Guardia2"]["segmenti"]), (5, 8))
        with self.assertRaises(ValueError):
            self.camp.set_clock("Zero", 4, "x", deadline=0)

    def test_the_narrator_gets_the_expired_clock_in_the_turn_head(self) -> None:
        notes = NARRATION + '\n<appunti>\n- orologio: nuovo "Guardia", 4 segmenti, entro 2 turni, conseguenza: bussano\n</appunti>'
        set_clock = [_b(type="tool_use", id="c1", name="set_clock",
                        input={"name": "Guardia", "segments": 4, "consequence": "bussano", "deadline_turns": 2})]
        client = ScriptedClient([ROUTER, [_b(type="text", text=notes)], set_clock])
        engine = MasterEngine(self.camp, client=client)
        engine.play("Mi nascondo.")  # turno 1: nasce l'orologio
        self.assertEqual(self.camp.clocks()["Guardia"]["creato_turno"], 0)  # lo scriba scrive prima che il turno sia salvato
        client.script = [ROUTER, QUIET, DONE]
        engine.play("Aspetto.")  # turno 2
        self.assertIn("[Orologi: Guardia 0/4 (entro 1 turni)]", narrator_calls(client)[-1]["messages"][-1]["content"])
        client.script = [ROUTER, QUIET, DONE]
        engine.play("Aspetto ancora.")  # turno 3: la scadenza e' passata senza che nessuno lo toccasse
        sent = narrator_calls(client)[-1]["messages"][-1]["content"]
        self.assertIn("[OROLOGIO PIENO: Guardia: bussano (il tempo e' passato). La conseguenza accade in questa scena.]", sent)
        self.assertEqual(self.camp.clocks(), {})
        client.script = [ROUTER, QUIET, DONE]
        engine.play("Apro.")
        self.assertNotIn("OROLOGIO", narrator_calls(client)[-1]["messages"][-1]["content"])

    def test_tool_and_validator(self) -> None:
        ex = ToolExecutor(self.camp)
        text, err = ex.execute("set_clock", {"name": "Indagine", "segments": 6, "consequence": "x", "deadline_turns": 10})
        self.assertFalse(err, text)
        self.assertEqual(text, "Orologio 'Indagine': 0/6, scade fra 10 turni")
        self.assertTrue(ex.execute("set_clock", {"name": "Zero", "segments": 6, "consequence": "x", "deadline_turns": 0})[1])
        self.assertEqual(validate(self.camp).errors, [])
        state = json.loads((self.camp.root / "stato.json").read_text(encoding="utf-8"))
        del state["orologi"]["Indagine"]["creato_turno"]
        (self.camp.root / "stato.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual([f.title for f in validate(self.camp).errors], ["scadenza senza turno di partenza"])


class TestFili(unittest.TestCase):
    def test_tags_are_kept_for_old_lines_and_given_to_new_ones(self) -> None:
        old = "- [s1] La guardia cerca Kael\n- [s2] Mira sa del faro"
        new = "- Mira sa del faro\n- Un debito con la Gilda\n\n- [s1] La guardia cerca Kael"
        self.assertEqual(fili.tag_lines(old, new, 3), "- [s2] Mira sa del faro\n- [s3] Un debito con la Gilda\n\n- [s1] La guardia cerca Kael")
        self.assertEqual(fili.tag_lines("", "La prima riga senza trattino", 1), "- [s1] La prima riga senza trattino")
        self.assertEqual(fili.stale("- [s1] a\n- [s2] b\n- [s3] c\n- d", session=3), ["a"])
        self.assertEqual(fili.stale("- [s1] a", session=3, after=3), [])
        self.assertEqual(fili.ages("- [s1] a\n- b", 3), [(2, "a"), (None, "b")])


class TestFiliInGioco(Base):
    def test_scribe_and_consolidation_tag_lines_and_stale_ones_reach_the_opening(self) -> None:
        ex = ToolExecutor(self.camp)
        ex.execute("set_section", {"name": "ambientazione/avventura.md", "section": "Fili aperti di Kael", "text": "- La guardia cerca Kael\n- Mira sa del faro"})
        self.assertEqual(self.camp.open_threads("Kael"), "- [s1] La guardia cerca Kael\n- [s1] Mira sa del faro")
        self.assertEqual(self.camp.stale_threads("Kael"), [])
        # due sessioni dopo: un filo e' stato toccato dal consolidamento, l'altro no
        self.camp.next_session()
        self.camp.next_session()
        from master.memory import Consolidator

        self.camp.append_turn("kael", "a", "n")
        data = {"eventi": [{"turno": 1, "testo": "x"}], "fili_aperti": "- Mira sa del faro e lo ha detto a Kael\n- La guardia cerca Kael",
                "png": [], "scena": {"luogo": "x", "quando": "y", "presenti": [], "appena_successo": "z", "domanda_aperta": "w", "da_richiamare": []},
                "incoerenze": []}
        client = ScriptedClient([[_b(type="text", text=json.dumps(data))]])
        Consolidator(self.camp, client).run()
        self.assertEqual(self.camp.open_threads("Kael"), "- [s3] Mira sa del faro e lo ha detto a Kael\n- [s1] La guardia cerca Kael")
        self.assertEqual(self.camp.stale_threads("Kael"), ["La guardia cerca Kael"])
        self.assertIn("fili stantii", [f.title for f in validate(self.camp).recommendations])
        # il consolidamento li riceve fra i documenti; l'apertura della sessione li consegna al narratore
        self.assertIn("FILI STANTII (fermi da almeno 2 sessioni", client.calls[0]["messages"][0]["content"])
        self.camp.archive_turns("kael")
        client = ScriptedClient([[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], DONE])
        engine = MasterEngine(self.camp, client=client)
        engine.prelude()
        brief = narrator_calls(client)[0]["messages"][-1]["content"]
        self.assertIn("[FILI STANTII, fermi da almeno 2 sessioni: La guardia cerca Kael]", brief)
        self.assertIn("chiudilo con un fatto accaduto nel frattempo, o rilancialo", brief)
        self.assertTrue(self.camp.read_turns("kael")[0]["player"].startswith(OPENING_MARK))
        # la sezione nel contesto del turno mostra le eta', il narratore le legge e basta
        from master.context import compose

        self.assertIn("- [s1] La guardia cerca Kael", compose(self.camp, "", "kael").text)

    def test_no_stale_note_when_nothing_is_old(self) -> None:
        self.camp.next_session()
        client = ScriptedClient([[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], DONE])
        MasterEngine(self.camp, client=client).prelude()
        self.assertNotIn("FILI STANTII", narrator_calls(client)[0]["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
