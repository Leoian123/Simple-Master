"""I modi di gioco: una lente nel prompt in cache, scelta dal giocatore, che non tocca regole e dadi."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine, modi  # noqa: E402
from master.validate import validate  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402
from test_scribe import ROUTER  # noqa: E402

CUPO = {"modi": [{"nome": "Cupo", "descrizione": "x", "istruzioni": "Tono cupo.", "png": "Diffidenti."},
                 {"nome": "lieve", "descrizione": "y", "istruzioni": "Tono lieve."}]}


class TestModi(unittest.TestCase):
    def test_defaults_are_valid_and_classico_is_the_fallback(self) -> None:
        self.assertEqual(modi.modi_problems(modi.DEFAULT_MODI), [])
        self.assertEqual([m["nome"] for m in modi.DEFAULT_MODI], ["quieto", "classico", "spietato"])
        self.assertEqual(modi.DEFAULT, "classico")
        text = modi.testo(modi.DEFAULT_MODI[2])
        self.assertTrue(text.startswith("\n## Modo di gioco: spietato\n"))
        self.assertIn("I PNG in questo modo:", text)
        self.assertIn("Le regole, le difficolta' previste dal manuale, i dadi e la scheda non cambiano", text)

    def test_problems(self) -> None:
        good = CUPO["modi"]
        for broken, where in (
            ([], ""), ("x", ""), ([good[0], "x"], "[1]"),
            ([{**good[0], "nome": ""}, good[1]], "[0].nome"),
            ([good[0], {**good[1], "nome": "cupo"}], "[1].nome"),
            ([{**good[0], "istruzioni": "  "}, good[1]], "[0].istruzioni"),
            ([{**good[0], "png": 3}, good[1]], "[0].png"),
        ):
            with self.subTest(where=where):
                self.assertIn(where, [p for p, _ in modi.modi_problems(broken)])
        self.assertEqual(modi.modi_problems(good), [])


class TestInCampagna(Base):
    def test_mode_is_a_cached_block_that_changes_only_when_chosen(self) -> None:
        engine = MasterEngine(self.camp, client=ScriptedClient([]))
        first = engine.system_blocks()[0]["text"]
        self.assertIn("## Modo di gioco: classico", first)
        self.assertEqual(engine.system_blocks()[0]["text"], first)  # byte-identico fra due turni
        self.assertEqual(self.camp.set_mode("Spietato"), "spietato")
        self.assertEqual(json.loads((self.camp.root / "stato.json").read_text(encoding="utf-8"))["modo"], "spietato")
        second = engine.system_blocks()[0]["text"]
        self.assertIn("## Modo di gioco: spietato", second)
        self.assertNotIn("classico", second.split("## Modo di gioco")[1])
        self.assertEqual(first.split("## Modo di gioco")[0], second.split("## Modo di gioco")[0])  # prima della lente nulla cambia
        with self.assertRaises(ValueError):
            self.camp.set_mode("eroico")
        # la lente non tocca i dadi: la sezione dei dadi e' la stessa
        self.assertEqual(engine.dice_text(), MasterEngine(self.camp, client=ScriptedClient([])).dice_text())

    def test_campaign_modes_replace_the_defaults_only_if_valid(self) -> None:
        path = self.camp.root / "meccanica" / "modi.json"
        path.write_text(json.dumps(CUPO), encoding="utf-8")
        self.assertEqual(modi.nomi(self.camp), ["cupo", "lieve"])
        self.assertEqual(modi.attivo(self.camp)["nome"], "Cupo")  # niente `classico`: il primo dell'elenco
        self.camp.set_mode("cupo")
        self.assertEqual(validate(self.camp).errors, [])
        path.write_text(json.dumps({"modi": [{"nome": "solo"}]}), encoding="utf-8")
        self.assertEqual(modi.carica(self.camp), modi.DEFAULT_MODI)
        errors = validate(self.camp)
        self.assertIn("meccanica/modi.json:modi[0].descrizione", [f.path for f in errors.errors])
        self.assertIn("modo di gioco sconosciuto", [f.title for f in errors.errors])  # `cupo` non esiste piu'
        self.assertEqual(modi.attivo(self.camp)["nome"], "classico")

    def test_page_exposes_and_sets_the_mode(self) -> None:
        import shutil

        from master.ui import App

        camps = self.tmp / "campaigns"
        shutil.copytree(self.tmp / "camp", camps / "esempio")
        client = ScriptedClient([])
        app = App(camps, "esempio", client=client)
        state = app.state()
        self.assertEqual((state["mode"], [m["name"] for m in state["modes"]]), ("classico", ["quieto", "classico", "spietato"]))
        self.assertEqual(app.set_mode("quieto"), {"mode": "quieto"})
        self.assertEqual(app.state()["mode"], "quieto")
        client.script = [ROUTER, [_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], [_b(type="text", text="fatto")]]
        app.play("Guardo.")
        narrator = [c for c in client.calls if "Sei il master" in str(c.get("system"))][-1]
        self.assertIn("## Modo di gioco: quieto", narrator["system"][0]["text"])
        with self.assertRaises(ValueError):
            app.set_mode("x")
        from master.ui import PAGE

        self.assertIn('id="mode"', PAGE)


if __name__ == "__main__":
    unittest.main()
