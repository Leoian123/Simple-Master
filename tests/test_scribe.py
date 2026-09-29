"""Narrazione e scrittura sono due chiamate separate: il narratore narra, lo scriba trascrive."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import MasterEngine  # noqa: E402
from master.engine import MIN_TURNS_TO_CLOSE  # noqa: E402
from master.sheet import Sheet  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b, write  # noqa: E402
from test_sheet import tool  # noqa: E402

NOTES = "- diario: Kael ferito dalla guardia del molo\n- stato: punti ferita -3\n- nuovo: Guardia del molo (png), corrotta dalla Gilda"
WITH_NOTES = f"{NARRATION}\n\n<appunti>\n{NOTES}\n</appunti>"
ROUTER = [_b(type="text", text="nessuna")]  # l'instradatore delle regole: la prima chiamata di un turno di gioco
QUIET = NARRATION + "\n<appunti>nulla</appunti>"


def scribe_writes():
    return [write("s1", "- Kael ferito dalla guardia del molo."),
            tool("s2", "update_sheet", changes=[{"path": "stato.punti_ferita.attuale", "value": 9}]),
            tool("s3", "upsert_glossary", term="Guardia del molo", kind="png", description="Corrotta dalla Gilda", file="ambientazione/npc.md")]


def scribe_calls(client):
    return [c for c in client.calls if "Sei lo scriba" in str(c.get("system"))]


class TestScribe(Base):
    def test_narrator_narrates_and_a_separate_cheap_call_writes(self) -> None:
        client = ScriptedClient([ROUTER, [_b(type="text", text=WITH_NOTES)], scribe_writes()])
        engine = MasterEngine(self.camp, client=client)
        result = engine.play("Carico la guardia.")
        self.assertEqual(len(client.calls), 3)
        _, narrator, scribe = client.calls
        # il narratore non ha strumenti di scrittura e non gli si chiede di scrivere
        self.assertEqual({t["name"] for t in narrator["tools"]}, {"read_file", "lookup_glossary", "list_files"})
        self.assertIn("TU NON SCRIVI FILE", narrator["system"][0]["text"])
        for legacy in ("NARRA E SCRIVI NELLO STESSO MESSAGGIO", "PERSONAGGIO ATTIVO", "i turni qui sopra"):
            self.assertNotIn(legacy, narrator["system"][0]["text"])
        # lo scriba: altro modello, prompt suo, solo strumenti di scrittura, nessuna conversazione al seguito
        self.assertEqual((narrator["model"], scribe["model"]), ("claude-opus-5", "claude-haiku-4-5"))
        self.assertIn("Sei lo scriba", scribe["system"])
        self.assertFalse({t["name"] for t in scribe["tools"]} & {"read_file", "lookup_glossary", "list_files", "save_sheet"})
        self.assertEqual(len(scribe["messages"]), 1)
        body = scribe["messages"][0]["content"]
        for piece in ("APPUNTI DEL NARRATORE:\n- diario: Kael ferito", "NARRAZIONE:\nTi svegli", '"punti_ferita":{"attuale":12', "FILI APERTI ATTUALI"):
            self.assertIn(piece, body)
        # il giocatore vede la narrazione, non gli appunti; nel salvataggio lo stesso
        self.assertEqual(result.text, NARRATION)
        self.assertEqual(result.notes, NOTES)
        self.assertNotIn("appunti", self.camp.read_turns("kael")[0]["master"])
        # le scritture sono avvenute, e il costo del turno comprende lo scriba
        self.assertEqual(Sheet(self.camp, "kael").get("stato.punti_ferita.attuale"), 9)
        self.assertIn("ferito dalla guardia", self.camp.file("ambientazione/diario.md").read())
        self.assertIsNotNone(self.camp.glossary.get("Guardia del molo"))
        self.assertEqual(result.files_changed, ["ambientazione/diario.md", "schede/kael.json", "glossario.md"])
        self.assertGreater(result.scribe_cost_usd, 0)
        self.assertGreater(result.cost_usd, result.scribe_cost_usd)
        self.assertEqual(engine.messages, [])  # nessuna conversazione che resta

    def test_nothing_to_note_still_updates_the_scene(self) -> None:
        client = ScriptedClient([ROUTER, [_b(type="text", text=QUIET)], [_b(type="text", text="fatto")]])
        MasterEngine(self.camp, client=client).play("Aspetto.")
        scribe = scribe_calls(client)
        self.assertEqual(len(scribe), 1)  # la scena va aggiornata anche quando non succede nulla
        self.assertTrue(scribe[0]["messages"][0]["content"].endswith("APPUNTI DEL NARRATORE:\nnulla"))

    def test_missing_block_still_gets_transcribed_from_the_narration(self) -> None:
        client = ScriptedClient([ROUTER, [_b(type="text", text=NARRATION)], [_b(type="text", text="fatto")]])
        MasterEngine(self.camp, client=client).play("Aspetto.")
        self.assertIn("non li ha lasciati", scribe_calls(client)[0]["messages"][0]["content"])

    def test_scribe_failure_keeps_the_turn_and_the_notes(self) -> None:
        client = ScriptedClient([ROUTER, [_b(type="text", text=WITH_NOTES)]])
        create = client.messages.create

        def flaky(**kwargs):
            if "Sei lo scriba" in str(kwargs.get("system")) and not scribe_calls(client):
                client.calls.append(kwargs)
                raise RuntimeError("rete caduta")
            return create(**kwargs)

        client.messages.create = flaky
        engine = MasterEngine(self.camp, client=client)
        result = engine.play("Carico la guardia.")
        self.assertEqual(result.text, NARRATION)  # la narrazione, gia' pagata, arriva comunque
        self.assertEqual(len(self.camp.read_turns("kael")), 1)
        self.assertEqual(Sheet(self.camp, "kael").get("stato.punti_ferita.attuale"), 12)
        # al turno dopo gli appunti rimasti partono insieme ai nuovi
        client.script = [ROUTER, [_b(type="text", text=QUIET)], scribe_writes()]
        engine.play("Mi rialzo.")
        self.assertIn("APPUNTI RIMASTI DA UN TURNO PRECEDENTE:\n- diario: Kael ferito", scribe_calls(client)[-1]["messages"][0]["content"])
        self.assertEqual(Sheet(self.camp, "kael").get("stato.punti_ferita.attuale"), 9)

    def test_scribe_can_fix_a_wrong_path_once(self) -> None:
        bad = [tool("s1", "update_sheet", changes=[{"path": "stato.punti_ferita.attuale.x", "value": 9}])]
        good = [tool("s2", "update_sheet", changes=[{"path": "stato.punti_ferita.attuale", "value": 9}])]
        client = ScriptedClient([ROUTER, [_b(type="text", text=WITH_NOTES)], bad, good])
        MasterEngine(self.camp, client=client).play("Carico la guardia.")
        scribe = scribe_calls(client)
        self.assertEqual(len(scribe), 2)
        self.assertTrue(scribe[1]["messages"][-1]["content"][0]["is_error"])
        self.assertEqual(Sheet(self.camp, "kael").get("stato.punti_ferita.attuale"), 9)

    def test_session_closing_awards_experience_through_the_scribe(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        for i in range(MIN_TURNS_TO_CLOSE):
            client.script = [ROUTER, [_b(type="text", text=QUIET)], [_b(type="text", text="fatto")]]
            engine.play(f"Azione {i}.")
        closing = NARRATION + "\n<appunti>\n- pe: 1, via sessione, una sessione giocata\n</appunti>"
        client.script = [[_b(type="text", text=closing)],
                         [tool("x1", "award_xp", amount=1, route="sessione", reason="Una sessione giocata")]]
        result = engine.close_session(consolidate=False)
        brief = [c for c in client.calls if "Sei il master" in str(c.get("system"))][-1]["messages"][0]["content"]
        self.assertIn("riga \"pe\" per la via `sessione`", brief)  # i punti passano dagli appunti, non da award_xp
        self.assertTrue(result.text.endswith("*Esperienza di Kael: 1 disponibili (1 guadagnati, 0 spesi).*"))
        self.assertEqual(Sheet(self.camp, "kael").xp()["disponibili"], 1)
        self.assertEqual(self.camp.session_number, 2)

    def test_page_uses_the_scribe_and_its_model_setting(self) -> None:
        import shutil

        from master.conversation import ConversationEngine
        from master.ui import App

        camps = self.tmp / "campaigns"
        shutil.copytree(self.tmp / "camp", camps / "esempio")
        # nella pagina il livello veloce instrada prima le regole: e' la prima chiamata, minuscola
        client = ScriptedClient([ROUTER, [_b(type="text", text=WITH_NOTES)], scribe_writes()])
        app = App(camps, "esempio", client=client)
        app.update_settings({"model": "claude-opus-5", "model_scribe": "claude-sonnet-5"})
        out = app.play("Carico la guardia.")
        self.assertEqual(scribe_calls(client)[0]["model"], "claude-sonnet-5")
        self.assertEqual(out.text, NARRATION)
        with self.assertRaises(ValueError):
            app.update_settings({"model": "claude-opus-5", "model_scribe": "gpt"})
        # il costruttore di schede resta com'era: ciclo a conversazione, salva lui a conferma del giocatore
        self.assertIs(type(app.builder).__mro__[1], ConversationEngine)
        self.assertNotIsInstance(app.builder, MasterEngine)


if __name__ == "__main__":
    unittest.main()
