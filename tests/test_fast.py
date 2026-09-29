"""Il livello veloce: una chiamata per turno su un contesto composto dal codice, senza conversazione."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import MasterEngine  # noqa: E402
from master.context import compose  # noqa: E402
from master.glossary import GlossaryEntry  # noqa: E402
from master.memory import NPC_MEMORY, SCENE, write_scene  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402

QUIET = NARRATION + "\n<appunti>nulla</appunti>"


def scene_tool(**over):
    scene = {"luogo": "Lanterna Blu", "quando": "sera", "presenti": ["Mira Sol"], "appena_successo": "Kael ha chiesto della taglia.",
             "domanda_aperta": "Fidarsi di Mira?", "da_richiamare": ["Mira tamburella sul bancone quando mente"]}
    scene.update(over)
    return _b(type="tool_use", id="sc", name="set_scene", input=scene)


def narrator_calls(client):
    return [c for c in client.calls if "Sei il master" in str(c.get("system"))]


class TestContext(Base):
    def test_context_is_a_set_of_documents_joined_by_the_glossary(self) -> None:
        camp = self.camp
        write_scene(camp, {"luogo": "Lanterna Blu", "quando": "sera", "presenti": ["Mira Sol"], "appena_successo": "Kael e' entrato.",
                           "domanda_aperta": "Cosa chiedere?", "da_richiamare": ["L'insegna cigola"]})
        camp.file(NPC_MEMORY).write("# Memoria dei PNG\n\n## Mira Sol\n\n- **Sa:** Kael cerca chi ha messo la taglia.\n- **Crede:** Che Kael lavori per la Gilda.\n")
        camp.file("ambientazione/avventura.md").set_section("Fili aperti di Kael", "- La taglia viene dalla Gilda delle Maree.")
        camp.file("meccanica/regole.md").set_section("Combattimento", "Introduzione.\n\n### Disarmare\n\nProva di Agilita' contro 15: l'arma cade.\n\n### Spingere\n\nAltro.")
        camp.append_turn("kael", "Entro alla Lanterna Blu.", "La sala e' piena di fumo. Mira Sol alza gli occhi.")
        ctx = compose(camp, "Provo a disarmare il tizio che minaccia Mira Sol.", "kael")
        text = ctx.text
        self.assertNotIn('"agilita":3', text)  # i tratti stanno nel prompt di sistema, in cache: qui solo cio' che cambia
        for piece in ('"punti_ferita":{"attuale":12,"massimo":12}', "## Scena in corso", "L'insegna cigola", "La taglia viene dalla Gilda",
                      "### Mira Sol (png)", "**Crede:** Che Kael lavori per la Gilda.",  # la memoria del PNG, non la sua scheda
                      "### Disarmare (meccanica/regole.md)\nProva di Agilita' contro 15",  # una sottosezione, trovata per titolo
                      "GIOCATORE: Entro alla Lanterna Blu.\nNARRATORE: La sala e' piena di fumo."):
            self.assertIn(piece, text)
        self.assertNotIn("### Spingere", text)  # solo il blocco che serve
        self.assertEqual((ctx.entities[0], ctx.rules, ctx.exchanges), ("Mira Sol", ["Disarmare"], 1))

    def test_implicit_actions_go_through_the_router(self) -> None:
        self.camp.file("meccanica/regole.md").set_section("Cadute", "Ogni 3 metri di caduta: 1d6 danni.")
        asked = {}

        def router(titles, action, scene):
            asked.update(titles=titles, action=action)
            return ["cadute", "Titolo inventato"]

        ctx = compose(self.camp, "Salto dal tetto sul carro.", "kael", router=router)
        self.assertIn("Cadute", asked["titles"])
        self.assertIn("### Cadute (meccanica/regole.md)\nOgni 3 metri", ctx.text)
        self.assertEqual(ctx.rules, ["Cadute (instradata)"])
        # se il giocatore nomina una regola, quella viene prima; l'instradatore completa senza doppioni
        named = compose(self.camp, "Temo le cadute: scendo con la corda.", "kael", router=router)
        self.assertEqual(named.rules, ["Cadute"])
        self.assertEqual(named.text.count("### Cadute"), 1)
        # le istruzioni del tavolo non sono azioni: niente ricerca e niente instradatore
        asked.clear()
        self.assertEqual(compose(self.camp, "", "kael", router=router).rules, [])
        self.assertEqual(asked, {})
        # e se cade, il turno va avanti lo stesso
        def broken(*a):
            raise RuntimeError("rete")
        self.assertEqual(compose(self.camp, "Salto dal tetto.", "kael", router=broken).rules, [])


class TestFastTurn(Base):
    def test_one_stateless_call_per_turn(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client, scribe=True, memory=True, fast=True)
        for i in range(4):
            client.script = [[_b(type="text", text="nessuna")], [_b(type="text", text=QUIET)], [scene_tool()]]
            engine.play(f"Azione {i}.")
        calls = narrator_calls(client)
        self.assertEqual(len(calls), 4)  # una chiamata del narratore per turno
        self.assertTrue(all(len(c["messages"]) == 1 for c in calls))  # nessuna conversazione che cresce
        self.assertTrue(all(len(c["system"]) == 2 and all(b["cache_control"] for b in c["system"]) for c in calls))  # tutto il prefisso in cache
        self.assertEqual(calls[0]["system"], calls[3]["system"])  # identico da un turno all'altro: si legge a un decimo del prezzo
        stable = calls[0]["system"][1]["text"]
        self.assertIn('"agilita":3', stable)  # la parte della scheda che cambia di rado
        self.assertNotIn('"punti_ferita"', stable)  # lo stato no: invaliderebbe la cache a ogni ferita
        self.assertNotIn('"esperienza"', stable)
        self.assertNotIn("GLOSSARIO", str(calls[0]["system"]))  # l'indice del glossario non viaggia piu' a ogni turno
        self.assertIn("una chiamata", calls[0]["system"][0]["text"])
        self.assertFalse(any("cache_control" in str(m) for m in calls[3]["messages"]))  # il contesto cambia: non si scrive in cache
        last = calls[3]["messages"][0]["content"]
        self.assertEqual(last.count("NARRATORE:"), 3)  # solo gli ultimi tre scambi, testuali
        self.assertTrue(last.rstrip().endswith("GIOCATORE: Azione 3."))
        sizes = [len(c["messages"][0]["content"]) for c in calls[2:]]
        self.assertLess(abs(sizes[1] - sizes[0]), 200)  # costo piatto: il contesto non cresce con la sessione
        self.assertEqual(engine.messages, [])
        self.assertEqual(len(self.camp.read_turns("kael")), 4)

    def test_scribe_keeps_the_scene_document_alive_every_turn(self) -> None:
        client = ScriptedClient([[_b(type="text", text="nessuna")], [_b(type="text", text=QUIET)], [scene_tool()]])
        engine = MasterEngine(self.camp, client=client, scribe=True, memory=True, fast=True)
        result = engine.play("Chiedo a Mira della taglia.")
        scribe = next(c for c in client.calls if "Sei lo scriba" in str(c.get("system")))
        self.assertIn("chiama SEMPRE `set_scene`", scribe["system"])  # anche con appunti "nulla"
        self.assertIn("set_scene", {t["name"] for t in scribe["tools"]})
        self.assertIn("SCENA PRIMA DI QUESTO TURNO:\n(nessuna: e' la prima)", scribe["messages"][0]["content"])
        self.assertIn("Mira tamburella sul bancone", self.camp.file(SCENE).read())
        self.assertIn("ambientazione/scena.md", result.files_changed)
        # il turno dopo riparte da quella scena
        client.script = [[_b(type="text", text="nessuna")], [_b(type="text", text=QUIET)], [scene_tool()]]
        engine.play("La fisso negli occhi.")
        self.assertIn("Mira tamburella sul bancone", narrator_calls(client)[-1]["messages"][0]["content"])

    def test_reserve_read_still_works_and_is_logged(self) -> None:
        read = _b(type="tool_use", id="r1", name="read_file", input={"name": "ambientazione/mondo.md", "section": "Faro Spento"})
        client = ScriptedClient([[_b(type="text", text="nessuna")], [read], [_b(type="text", text=QUIET)], [scene_tool()]])
        engine = MasterEngine(self.camp, client=client, scribe=True, memory=True, fast=True)
        with self.assertLogs("master.gioco", level="INFO") as logs:
            result = engine.play("Vado al Faro Spento.")
        self.assertEqual(len(narrator_calls(client)), 2)
        self.assertEqual(result.files_read, ["ambientazione/mondo.md"])
        self.assertTrue(any("LETTURA DI RISERVA" in line for line in logs.output))
        self.assertTrue(any("contesto:" in line for line in logs.output))

    def test_table_briefs_and_rolls_are_not_routed(self) -> None:
        client = ScriptedClient([[_b(type="text", text=QUIET)], [scene_tool()]])
        engine = MasterEngine(self.camp, client=client, scribe=True, memory=True, fast=True)
        engine.prelude("tono cupo")
        self.assertFalse(any("Scegli le sezioni" in str(c.get("system")) for c in client.calls))
        brief = narrator_calls(client)[0]["messages"][0]["content"]
        self.assertIn("## Personaggio del giocatore: Kael", brief)
        self.assertIn("ROTTURA", brief)
        self.assertNotIn("GIOCATORE: [APERTURA", brief)
        # l'instradamento finisce nel registro delle spese, fuori dalla media per turno
        self.camp.record_spend("instradamento", "claude-haiku-4-5", 0.002)
        self.assertIn("instradamento", self.camp.spend_summary()["per_processo"])


if __name__ == "__main__":
    unittest.main()


class TestSheetCacheLayers(Base):
    def test_state_changes_do_not_touch_the_cached_sheet_but_growth_does(self) -> None:
        from master.sheet import Sheet
        from master.tools import ToolExecutor

        engine = MasterEngine(self.camp, client=ScriptedClient([]), scribe=True, memory=True, fast=True)
        before = engine._system()
        ex = ToolExecutor(self.camp)
        ex.execute("update_sheet", {"changes": [{"path": "stato.punti_ferita.attuale", "value": 5}]})
        ex.execute("award_xp", {"amount": 10, "route": "storia", "reason": "Fine della storia"})
        self.assertEqual(engine._system(), before)  # ferite ed esperienza: la cache regge
        ex.execute("spend_xp", {"amount": 10, "reason": "Mente +1 -> +2", "changes": [{"path": "tratti.caratteristiche.mente", "value": 2}]})
        after = engine._system()
        self.assertEqual(after[0], before[0])  # il primo blocco non si invalida mai
        self.assertNotEqual(after[1], before[1])  # il personaggio e' cresciuto: si riscrive solo il blocco della scheda
        self.assertIn('"mente":2', after[1]["text"])
        # lo scriba riceve solo cio' che di solito aggiorna, e i nomi del resto
        working = Sheet(self.camp, "kael").working_json()
        self.assertIn('"punti_ferita"', working)
        self.assertIn('"equipaggiamento"', working)
        self.assertIn('"altri_gruppi_della_scheda"', working)
        self.assertNotIn("Cacciatore di taglie", working)


class TestOpeningWithoutWastedCalls(Base):
    def test_fast_opening_gets_its_sources_and_is_told_not_to_read(self) -> None:
        """2026-09-19: l'apertura della sessione 2 ha speso una chiamata in piu' per rileggere cio' che aveva davanti."""
        from master.models import Settings

        self.camp.file("ambientazione/diario.md").set_section("Sessione 1 — cronaca", "- [t3] Kael ha mentito alla guardia del molo.")
        self.camp.file("ambientazione/avventura.md").set_section("Fili aperti di Kael", "- La Gilda cerca un certo Doran.")
        self.camp.next_session()
        client = ScriptedClient([[_b(type="text", text=QUIET)], [scene_tool()]])
        engine = MasterEngine(self.camp, client=client, scribe=True, memory=True, fast=True)
        engine.prelude()
        brief = narrator_calls(client)[0]["messages"][0]["content"]
        self.assertIn("APERTURA DELLA SESSIONE 2", brief)
        self.assertIn("NON leggere nulla", brief)
        self.assertNotIn("Leggi in un solo giro", brief)
        self.assertIn("## Ultima parte del diario", brief)
        self.assertIn("[t3] Kael ha mentito alla guardia del molo.", brief)
        self.assertIn("La Gilda cerca un certo Doran.", brief)
        self.assertEqual(len(narrator_calls(client)), 1)
        # la qualita' prima del risparmio: il ragionamento resta quello pieno del modello finche' non lo cambia il giocatore
        self.assertEqual(Settings().effort_play, "")
