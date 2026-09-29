"""Economia dei turni e preludio: meno chiamate, cache che non si invalida, prima scena dal background.

Il ciclo a conversazione (quello del costruttore di schede) si prova su ConversationEngine; il gioco su MasterEngine."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import Campaign, MasterEngine  # noqa: E402
from master.conversation import SNAPSHOT_TURNS, ConversationEngine  # noqa: E402
from master.engine import PRELUDE_MARK  # noqa: E402

NARRATION = "Ti svegli sul pavimento del seminterrato. L'odore di ferro ti riempie la gola prima ancora che tu apra gli occhi. Cosa fai?"


def _b(**kw):
    return SimpleNamespace(**kw)


class ScriptedClient:
    """Risponde con una sequenza di messaggi preparati; poi solo testo."""

    def __init__(self, script: list[list]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.script.pop(0) if self.script else [_b(type="text", text="Va bene. E poi?" + " " * 80)]
        stop = "tool_use" if any(getattr(b, "type", "") == "tool_use" for b in content) else "end_turn"
        return SimpleNamespace(stop_reason=stop, content=content,
                               usage=SimpleNamespace(input_tokens=5, output_tokens=50, cache_read_input_tokens=1000, cache_creation_input_tokens=100))


def write(id_: str, text: str = "## Preludio\n\nRisveglio nel seminterrato."):
    return _b(type="tool_use", id=id_, name="append_file", input={"name": "ambientazione/diario.md", "text": text})


def read(id_: str, name: str, section: str | None = None):
    return _b(type="tool_use", id=id_, name="read_file", input={"name": name, **({"section": section} if section else {})})


def conversation(camp, **kw) -> ConversationEngine:
    """Il ciclo a conversazione con tutti gli strumenti: come lo usa il costruttore di schede."""
    return ConversationEngine(camp, prompt="Sei il master di prova.", **kw)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "campaigns" / "esempio", self.tmp / "camp", ignore=shutil.ignore_patterns("sessioni", "preparazione", "stato.json"))
        self.camp = Campaign(self.tmp / "camp")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestOneCallTurns(Base):
    def test_narration_with_writes_closes_the_turn_in_one_call(self) -> None:
        client = ScriptedClient([[_b(type="text", text=NARRATION), write("w1")]])
        engine = conversation(self.camp, client=client)
        result = engine.play("Apro gli occhi.")
        self.assertEqual(len(client.calls), 1)  # niente giro in piu' solo per dire "fatto"
        self.assertEqual(result.text, NARRATION)
        self.assertEqual(result.stop_reason, "end_turn")
        self.assertIn("Risveglio nel seminterrato.", self.camp.file("ambientazione/diario.md").read())
        self.assertEqual(len(self.camp.read_turns("kael")), 1)
        # gli esiti delle scritture aprono il messaggio del turno dopo, prima delle parole del giocatore
        engine.play("Mi alzo.")
        last_user = client.calls[1]["messages"][-1]["content"]
        self.assertEqual([b["type"] for b in last_user], ["tool_result", "text"])
        self.assertEqual(last_user[0]["tool_use_id"], "w1")
        self.assertEqual(last_user[1]["text"], "Mi alzo.")

    def test_reads_or_failed_writes_keep_the_loop_going(self) -> None:
        # testo + lettura: serve un altro giro per usare cio' che si e' letto
        client = ScriptedClient([[_b(type="text", text=NARRATION), read("r1", "ambientazione/npc.md", "Mira Sol")]])
        conversation(self.camp, client=client).play("Cerco Mira.")
        self.assertEqual(len(client.calls), 2)
        # scrittura fallita: il master deve poterla correggere nello stesso turno
        bad = _b(type="tool_use", id="w2", name="replace_text", input={"name": "schede/kael.md", "old": "non esiste", "new": "x"})
        client = ScriptedClient([[_b(type="text", text=NARRATION), bad]])
        conversation(self.camp, client=client).play("Bevo.")
        self.assertEqual(len(client.calls), 2)


class TestCacheStability(Base):
    def test_system_prompt_is_frozen_and_refreshed_every_few_turns(self) -> None:
        upsert = _b(type="tool_use", id="g1", name="upsert_glossary",
                    input={"term": "Seminterrato", "kind": "luogo", "description": "Dove tutto e' cominciato", "file": "ambientazione/mondo.md"})
        client = ScriptedClient([[_b(type="text", text=NARRATION), upsert]])
        engine = conversation(self.camp, client=client)
        for i in range(SNAPSHOT_TURNS + 1):
            engine.play(f"Azione {i}.")
        first = client.calls[0]["system"]
        self.assertTrue(all(c["system"] == first for c in client.calls[:SNAPSHOT_TURNS]))  # fermo: la cache regge
        self.assertNotIn("Seminterrato", first[1]["text"])
        self.assertIn("Seminterrato", client.calls[SNAPSHOT_TURNS]["system"][1]["text"])  # poi si rinnova

    def test_history_is_halved_not_shaved(self) -> None:
        engine = conversation(self.camp, client=ScriptedClient([]), max_history=10)
        sizes = []
        for i in range(12):
            engine.play(f"Azione {i}.")
            sizes.append(len(engine.messages))
        drops = sum(1 for a, b in zip(sizes, sizes[1:]) if b < a)
        # tagli rari: ognuno invalida tutta la cache della conversazione. Togliendo un messaggio
        # alla volta sarebbero stati 7 su 12 turni; dimezzando, uno ogni tre turni circa
        self.assertLessEqual(drops, 4)
        self.assertGreaterEqual(min(sizes[3:]), 4)
        self.assertLessEqual(max(sizes), 12)
        self.assertEqual(engine.messages[0]["role"], "user")

    def test_big_reads_from_past_turns_become_one_line(self) -> None:
        self.camp.file("meccanica/regole.md").set_section("Frenesia", "Regola lunga. " * 200)
        client = ScriptedClient([[read("r1", "meccanica/regole.md", "Frenesia")]])
        engine = conversation(self.camp, client=client)
        engine.play("Sento la Bestia.")
        in_turn = [b for m in client.calls[1]["messages"] if isinstance(m["content"], list) for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertGreater(len(in_turn[0]["content"]), 2000)  # nel turno in cui serve, il testo c'e' tutto
        engine.play("Resisto.")
        later = [b for m in client.calls[2]["messages"] if isinstance(m["content"], list) for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertLess(len(later[0]["content"]), 200)
        self.assertIn("read_file meccanica/regole.md § Frenesia", later[0]["content"])
        self.assertIn("rileggilo", later[0]["content"])
        # con Fable lo storico non si tocca
        fable = conversation(self.camp, client=ScriptedClient([[read("r2", "meccanica/regole.md", "Frenesia")]]), model="claude-fable-5-1")
        fable.campaign.archive_turns("kael")
        fable.play("Sento la Bestia.")
        fable.play("Resisto.")
        kept = [b for m in fable.messages if isinstance(m["content"], list) for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertGreater(len(kept[0]["content"]), 2000)


class TestPrelude(Base):
    def test_prelude_builds_the_first_scene_from_the_background(self) -> None:
        threads = _b(type="tool_use", id="a1", name="set_section",
                     input={"name": "ambientazione/avventura.md", "section": "Fili aperti di Kael", "text": "Chi ha messo la taglia sa che e' arrivato."})
        notes = NARRATION + "\n<appunti>\n- diario: Preludio, risveglio nel seminterrato\n- segreto: chi ha messo la taglia sa che e' arrivato\n</appunti>"
        client = ScriptedClient([[_b(type="text", text=notes)], [write("w1"), threads]])
        engine = MasterEngine(self.camp, client=client)
        result = engine.prelude("tono cupo, niente combattimento subito")
        self.assertEqual(result.text, NARRATION)  # gli appunti non arrivano al giocatore
        narrator = [c for c in client.calls if "Sei il master" in str(c.get("system"))]
        self.assertEqual(len(narrator), 1)
        brief = narrator[0]["messages"][-1]["content"]
        for piece in ("prima scena di Kael", "ROTTURA", "PUNTO D'INGRESSO", "MOTORE", "Fili aperti di Kael",
                      "non una scena da copiare", "tono cupo, niente combattimento subito"):
            self.assertIn(piece, brief)
        # la scheda con il background e' gia' nel prompt: non serve leggerla
        self.assertIn("SCHEDA DI KAEL", narrator[0]["system"][1]["text"])
        self.assertIn("Mira Sol, Lanterna Blu", narrator[0]["system"][1]["text"])
        self.assertIn("Nessuna scena nasce dal nulla", narrator[0]["system"][0]["text"])
        # le scritture le fa lo scriba, dagli appunti
        self.assertIn("Risveglio nel seminterrato.", self.camp.file("ambientazione/diario.md").read())
        # nel salvataggio resta il segno del preludio, non le istruzioni del tavolo
        turns = self.camp.read_turns("kael")
        self.assertTrue(turns[0]["player"].startswith(PRELUDE_MARK))
        self.assertNotIn("ROTTURA", turns[0]["player"])
        self.assertIn("Fili aperti di Kael", self.camp.file("ambientazione/avventura.md").outline())
        # a campagna iniziata il preludio non si ripete
        with self.assertRaises(ValueError):
            engine.prelude()

    def test_prelude_needs_a_character(self) -> None:
        empty = Campaign.create(self.tmp / "vuota")
        with self.assertRaises(ValueError):
            MasterEngine(empty, client=ScriptedClient([])).prelude()

    def test_prelude_endpoint(self) -> None:
        from master.ui import App

        camps = self.tmp / "campaigns"
        shutil.copytree(self.tmp / "camp", camps / "esempio")
        client = ScriptedClient([[_b(type="text", text=NARRATION), write("w1")]])
        app = App(camps, "esempio", client=client)
        result = app.prelude("")
        self.assertEqual(result.text, NARRATION)
        self.assertEqual(app.history("play")["total"], 1)


if __name__ == "__main__":
    unittest.main()


class TestSpendCounter(Base):
    def test_average_per_turn_comes_from_the_ledger(self) -> None:
        client = ScriptedClient([])
        engine = MasterEngine(self.camp, client=client)
        for i in range(3):
            client.script = [[_b(type="text", text="nessuna")], [_b(type="text", text=NARRATION + "\n<appunti>\n- diario: x\n</appunti>")],
                             [write(f"s{i}", "- x")]]
            engine.play(f"Azione {i}.")
        s = self.camp.spend_summary()
        self.assertEqual((s["turni_sessione"], s["turni_campagna"]), (3, 3))
        self.assertGreater(s["media_turno_sessione"], 0)
        # narrazione + scriba; l'instradatore entra nei totali, non nella media
        self.assertAlmostEqual(s["media_turno_sessione"] * 3, s["sessione"] - s["per_processo"]["instradamento"], places=3)
        self.assertEqual(set(s["per_processo"]), {"gioco", "scriba", "instradamento"})
        self.assertAlmostEqual(s["ultimo_turno"], s["media_turno_sessione"], places=3)
        self.assertEqual(s["oggi"], s["campagna"])
        # le altre spese entrano nei totali ma non nella media per turno
        self.camp.record_spend("memoria", "claude-sonnet-5", 0.08)
        after = self.camp.spend_summary()
        self.assertEqual(after["media_turno_sessione"], s["media_turno_sessione"])
        self.assertAlmostEqual(after["campagna"], s["campagna"] + 0.08, places=4)
        # la sessione dopo riparte da zero, la campagna no
        self.camp.next_session()
        new = self.camp.spend_summary()
        self.assertEqual((new["turni_sessione"], new["sessione"], new["turni_campagna"]), (0, 0, 3))
        # e non e' giocato: il consolidamento non lo legge
        self.assertEqual(len(self.camp.all_turns("kael")), 3)
