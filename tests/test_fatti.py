"""I fatti da non perdere: nel consolidamento per intero, nel contesto quando il turno li tocca, all'apertura tutti."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine, fatti  # noqa: E402
from master.context import compose  # noqa: E402
from master.memory import Consolidator  # noqa: E402
from master.validate import validate  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402

PROMISE = "Kael ha promesso a Mira Sol di non nominare mai il Faro Spento davanti alla Gilda"
DEBT = "Kael deve dodici monete al taverniere della Lanterna Blu"


def memory_json(**over):
    data = {"eventi": [{"turno": 1, "testo": "x"}], "fili_aperti": "- un filo", "png": [],
            "scena": {"luogo": "x", "quando": "y", "presenti": [], "appena_successo": "z", "domanda_aperta": "w", "da_richiamare": []},
            "da_non_perdere": [], "incoerenze": []}
    data.update(over)
    return [_b(type="text", text=json.dumps(data))]


class TestFile(Base):
    def test_read_append_dedup_and_cap(self) -> None:
        self.assertEqual(fatti.leggi(self.camp), [])
        self.assertEqual(fatti.aggiungi(self.camp, [PROMISE, DEBT, "un terzo fatto"]), [PROMISE, DEBT])  # tetto 2
        self.assertEqual(fatti.leggi(self.camp), [PROMISE, DEBT])
        self.assertTrue(self.camp.file(fatti.FILE).read().startswith("# Da non perdere"))
        self.assertEqual(fatti.aggiungi(self.camp, [DEBT + ".", "  ", 3, "Nuovo"]), ["Nuovo"])  # doppione e scarti ignorati
        self.assertEqual(len(fatti.leggi(self.camp)), 3)

    def test_relevance_needs_a_name_or_two_full_words(self) -> None:
        facts = [PROMISE, DEBT, "Il personaggio ha una cicatrice sulla mano sinistra"]
        self.assertEqual(fatti.pertinenti(facts, ["Parlo con Mira Sol del porto"]), [PROMISE])
        self.assertEqual(fatti.pertinenti(facts, ["pago le monete al taverniere"]), [DEBT])
        self.assertEqual(fatti.pertinenti(facts, ["cammino sulla spiaggia"]), [])
        self.assertEqual(fatti.pertinenti(facts, ["una cicatrice"]), [])  # una parola sola non basta
        self.assertEqual(fatti.pertinenti(facts, ["la cicatrice sulla mano sinistra"]), ["Il personaggio ha una cicatrice sulla mano sinistra"])


class TestInGioco(Base):
    def test_context_gets_only_the_facts_the_turn_touches(self) -> None:
        self.assertNotIn("Da non perdere", compose(self.camp, "Parlo con Mira Sol", "kael").text)
        fatti.aggiungi(self.camp, [PROMISE, DEBT])
        ctx = compose(self.camp, "Parlo con Mira Sol", "kael")
        self.assertIn("## Da non perdere (fatti stabiliti, non si contraddicono)\n- " + PROMISE, ctx.text)
        self.assertNotIn(DEBT, ctx.text)
        self.assertEqual(ctx.facts, [PROMISE])
        self.assertEqual(compose(self.camp, "Guardo il cielo", "kael").facts, [])

    def test_consolidation_reads_them_whole_and_adds_at_most_two(self) -> None:
        fatti.aggiungi(self.camp, [PROMISE])
        self.camp.append_turn("kael", "a", "n")
        client = ScriptedClient([memory_json(da_non_perdere=[DEBT, "Secondo", "Terzo", PROMISE])])
        report = Consolidator(self.camp, client).run()
        body = client.calls[0]["messages"][0]["content"]
        self.assertIn("DA NON PERDERE (fatti stabiliti):\n- " + PROMISE, body)
        self.assertIn("Da non perdere", client.calls[0]["system"])
        self.assertIn("da_non_perdere", client.calls[0]["output_config"]["format"]["schema"]["required"])
        self.assertEqual(fatti.leggi(self.camp), [PROMISE, DEBT, "Secondo"])
        self.assertIn(fatti.FILE, report.files_changed)
        # senza proposte il file non si tocca
        self.camp.append_turn("kael", "b", "m")
        report = Consolidator(self.camp, ScriptedClient([memory_json()])).run()
        self.assertNotIn(fatti.FILE, report.files_changed)

    def test_session_opening_carries_them_all(self) -> None:
        fatti.aggiungi(self.camp, [PROMISE, DEBT])
        self.camp.next_session()
        client = ScriptedClient([[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], [_b(type="text", text="fatto")]])
        MasterEngine(self.camp, client=client).prelude()
        brief = [c for c in client.calls if "Sei il master" in str(c.get("system"))][0]["messages"][-1]["content"]
        self.assertIn(f"[DA NON PERDERE, fatti stabiliti: {PROMISE} | {DEBT}]", brief)

    def test_too_many_facts_is_a_recommendation(self) -> None:
        self.camp.append_turn("kael", "a", "n")
        self.assertNotIn("fatti da non perdere troppi", [f.title for f in validate(self.camp).recommendations])
        self.camp.file(fatti.FILE).write("# Da non perdere\n\n" + "\n".join(f"- fatto numero {i}" for i in range(31)))
        self.assertIn("fatti da non perdere troppi", [f.title for f in validate(self.camp).recommendations])


if __name__ == "__main__":
    unittest.main()
