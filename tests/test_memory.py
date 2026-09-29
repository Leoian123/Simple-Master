"""Il livello lento: il giocato grezzo (copiato dal codice a ogni turno) diventa memoria, a comando o a fine sessione."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import MasterEngine  # noqa: E402
from master.engine import MIN_TURNS_TO_CLOSE  # noqa: E402
from master.memory import NPC_MEMORY, SCENE, Consolidator  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b, write  # noqa: E402

QUIET = NARRATION + "\n<appunti>nulla</appunti>"
ROUTER = [_b(type="text", text="nessuna")]  # l'instradatore delle regole: la prima chiamata di un turno di gioco


def play_quietly(engine, client, n: int) -> None:
    """n turni di gioco: instradatore, narratore senza appunti, scriba che aggiorna la scena."""
    for i in range(n):
        client.script = [ROUTER, [_b(type="text", text=QUIET)], [_b(type="text", text="fatto")]]
        engine.play(f"Azione {i}.")


def narrator_calls(client) -> list[dict]:
    return [c for c in client.calls if "Sei il master" in str(c.get("system"))]


def memory_json(first: int, **extra):
    data = {
        "eventi": [{"turno": first, "testo": "Kael dichiara alla guardia di chiamarsi Doran e mostra un lasciapassare falso."},
                   {"turno": first + 1, "testo": "La guardia del molo lo lascia passare ma annota il nome Doran sul registro."},
                   {"turno": 999, "testo": "Evento di un turno che non esiste."}],
        "fili_aperti": "- La Gilda cerca un certo Doran.\n- Mira Sol non sa ancora che Kael e' in citta'.",
        "png": [{"nome": "Guardia del molo", "sa": "Un forestiero e' entrato al tramonto.", "crede": "Che si chiami Doran.", "vuole": "Riferire alla Gilda."}],
        "scena": {"luogo": "Molo di Velmora", "quando": "tramonto", "presenti": ["Guardia del molo"], "appena_successo": "Kael ha passato il controllo.",
                  "domanda_aperta": "Andare alla Lanterna Blu o seguire la guardia?", "da_richiamare": ["Il lasciapassare falso ha un sigillo sbavato"]},
        "incoerenze": [],
    }
    data.update(extra)
    return [_b(type="text", text=json.dumps(data))]


class TestSlowMemory(Base):
    def _play(self, engine, client, n):
        play_quietly(engine, client, n)

    def test_raw_play_becomes_event_units_with_their_turn(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        self._play(engine, client, 2)
        client.script = [memory_json(1)]
        report = engine.consolidate()
        call = client.calls[-1]
        self.assertEqual(call["model"], "claude-sonnet-5")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        body = call["messages"][0]["content"]
        for piece in ("[t1] GIOCATORE: Azione 0.", "[t2] NARRATORE: Ti svegli", "FILI APERTI ATTUALI", '"punti_ferita"'):
            self.assertIn(piece, body)
        self.assertNotIn("<appunti>", body)  # il grezzo e' cio' che ha letto il giocatore
        self.assertEqual((report.turns, report.events, report.npcs), (2, 2, ["Guardia del molo"]))
        diary = self.camp.file("ambientazione/diario.md")
        chronicle = diary.get_section("Sessione 1 — cronaca")
        self.assertIn("- [t1] Kael dichiara alla guardia di chiamarsi Doran", chronicle)
        self.assertNotIn("non esiste", chronicle)  # un evento senza turno d'origine valido non entra
        self.assertIn("**Crede:** Che si chiami Doran.", self.camp.file(NPC_MEMORY).get_section("Guardia del molo"))
        self.assertIn("La Gilda cerca un certo Doran", self.camp.file("ambientazione/avventura.md").get_section("Fili aperti di Kael"))
        self.assertIn("sigillo sbavato", self.camp.file(SCENE).read())
        self.assertGreater(report.cost_usd, 0)
        # cio' che e' consolidato non si ripaga
        calls = len(client.calls)
        self.assertEqual(engine.consolidate().turns, 0)
        self.assertEqual(len(client.calls), calls)
        # i turni nuovi si aggiungono alla stessa cronaca, numerati di seguito
        self._play(engine, client, 1)
        client.script = [memory_json(3)]
        engine.consolidate()
        self.assertIn("[t3] GIOCATORE", client.calls[-1]["messages"][0]["content"])
        self.assertNotIn("[t1] GIOCATORE", client.calls[-1]["messages"][0]["content"])
        chronicle = diary.get_section("Sessione 1 — cronaca")
        self.assertIn("[t1]", chronicle)
        self.assertIn("[t3]", chronicle)

    def test_scribe_notes_are_provisional_and_replaced(self) -> None:
        client = ScriptedClient([ROUTER, [_b(type="text", text=NARRATION + "\n<appunti>\n- diario: Kael passa il molo\n</appunti>")],
                                 [write("s1", "## Turno\n- Kael passa il molo.")]])
        engine = MasterEngine(self.camp, client=client)
        engine.play("Passo il controllo.")
        diary = self.camp.file("ambientazione/diario.md")
        self.assertIn("Kael passa il molo", diary.get_section("Appunti"))
        self.assertNotIn("## Turno", diary.read())  # lo scriba non apre sezioni sue nel diario
        client.script = [memory_json(1)]
        engine.consolidate()
        self.assertIsNone(diary.get_section("Appunti"))  # una sola copia dei fatti: la cronaca
        self.assertIn("[t1]", diary.get_section("Sessione 1 — cronaca"))

    def test_failure_leaves_the_raw_play_pending(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        self._play(engine, client, 2)
        client.script = [[_b(type="text", text="non e' json")]]
        with self.assertRaises(ValueError):
            engine.consolidate()
        self.assertEqual(self.camp.consolidated("kael"), 0)
        self.assertEqual(len(Consolidator(self.camp, client).pending("kael")[1]), 2)

    def test_closing_a_session_consolidates_and_next_opening_gets_the_scene(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        self._play(engine, client, MIN_TURNS_TO_CLOSE)
        closing = NARRATION + "\n<appunti>\n- pe: 1, via sessione, una sessione giocata\n</appunti>"
        client.script = [[_b(type="text", text=closing)],
                         [_b(type="tool_use", id="x1", name="award_xp", input={"amount": 1, "route": "sessione", "reason": "Una sessione giocata"})],
                         memory_json(1, incoerenze=[{"descrizione": "Nel diario Mira e' al Faro, nel giocato e' alla Lanterna Blu", "correzione": "Mira e' alla Lanterna Blu"}])]
        result = engine.close_session()
        brief = narrator_calls(client)[-1]["messages"][-1]["content"]
        self.assertIn("Niente riepilogo e niente fili aperti", brief)  # il riepilogo non lo paga due volte
        self.assertIn("Memoria consolidata: 4 turni", result.text)
        self.assertIn("1 incoerenze sanate", result.text)
        self.assertIn("Mira e' alla Lanterna Blu", self.camp.file("ambientazione/avventura.md").get_section("Incoerenze sanate"))
        self.assertEqual((self.camp.session_number, self.camp.consolidated("kael")), (2, 4))
        self.assertEqual(len(self.camp.all_turns("kael")), 4)  # l'archivio resta la fonte
        # la sessione dopo si apre con la scena lasciata dalla memoria, senza doverla leggere
        client.script = [[_b(type="text", text=QUIET)]]
        engine.prelude()
        opening = narrator_calls(client)[-1]["messages"][-1]["content"]
        for piece in ("APERTURA DELLA SESSIONE 2", "Molo di Velmora", "sigillo sbavato", "La Gilda cerca un certo Doran"):
            self.assertIn(piece, opening)

    def test_a_redone_start_is_not_campaign_history(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        self._play(engine, client, 1)
        self.assertIsNone(engine.close_session())  # troppo poco per una sessione: si rifa' da capo
        self.assertEqual(self.camp.all_turns("kael"), [])
        self.assertEqual(len(list((self.camp.root / "sessioni" / "archivio").glob("*-scartata.jsonl"))), 1)

    def test_page_offers_consolidation_on_demand(self) -> None:
        from master.ui import App

        camps = self.tmp / "campaigns"
        shutil.copytree(self.tmp / "camp", camps / "esempio")
        client = ScriptedClient([])
        app = App(camps, "esempio", client=client)
        for i in range(3):
            client.script = [ROUTER, [_b(type="text", text=QUIET)], [_b(type="text", text="fatto")]]
            app.play(f"Azione {i}.")
        self.assertEqual(app.state()["memory_pending"], 3)
        app.update_settings({"model": "claude-opus-5", "model_memory": "claude-opus-5"})
        client.script = [memory_json(1)]
        out = app.consolidate()
        self.assertEqual(client.calls[-1]["model"], "claude-opus-5")
        self.assertTrue(out["summary"].startswith("Memoria consolidata: 3 turni"))
        self.assertEqual(app.state()["memory_pending"], 0)


if __name__ == "__main__":
    unittest.main()


class TestTruncatedConsolidation(Base):
    def test_truncated_answer_is_an_error_that_is_paid_once_and_retried(self) -> None:
        """2026-09-18, prima sessione reale: risposta ferma a 8000 token, JSON a meta', sessione chiusa male."""
        from types import SimpleNamespace

        from master.memory import MAX_OUTPUT_TOKENS

        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        play_quietly(engine, client, 3)
        seen = {}

        def truncated(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(stop_reason="max_tokens", content=[_b(type="text", text='{"eventi": [{"turno": 1, "testo": "Kael')],
                                   usage=SimpleNamespace(input_tokens=14000, output_tokens=8000))

        client.messages.create = truncated
        with self.assertRaises(ValueError) as err:
            engine.consolidate()
        self.assertIn("troncata", str(err.exception))
        self.assertGreaterEqual(seen["max_tokens"], 20_000)
        self.assertEqual(MAX_OUTPUT_TOKENS, seen["max_tokens"])
        self.assertEqual(seen["output_config"]["effort"], "low")  # trascrivere non richiede ragionamento profondo
        self.assertEqual(self.camp.consolidated("kael"), 0)  # il giocato resta in attesa
        self.assertGreater(self.camp.spend_summary()["per_processo"]["memoria"], 0)  # ma la spesa c'e' stata e si vede

    def test_turns_consolidated_after_closing_belong_to_the_closed_session(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        play_quietly(engine, client, MIN_TURNS_TO_CLOSE)
        client.script = [[_b(type="text", text=QUIET)]]
        engine.close_session(consolidate=False)  # come fa la pagina: chiude subito, consolida a parte
        self.assertEqual((self.camp.session_number, self.camp.consolidated("kael")), (2, 0))
        client.script = [memory_json(1)]
        engine.consolidate()
        self.assertIsNotNone(self.camp.file("ambientazione/diario.md").get_section("Sessione 1 — cronaca"))
