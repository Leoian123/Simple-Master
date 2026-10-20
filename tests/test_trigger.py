"""I trigger: regole come dati, valutate dal codice in testa a ogni turno, senza chiamate e senza giudizi."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine, trigger  # noqa: E402
from master.validate import validate  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402
from test_scribe import ROUTER  # noqa: E402

QUIET = [_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")]
DONE = [_b(type="text", text="fatto")]
RULES = {"trigger": [
    {"nome": "ferito", "ricorrente": True,
     "quando": [{"scheda": "stato.punti_ferita.attuale", "op": "<=", "valore": 4}],
     "effetti": [{"avviso": "Sotto 5 punti ferita: malus a ogni prova, secondo le regole"}]},
    {"nome": "la Gilda si muove",
     "quando": [{"scheda": "stato.monete", "op": ">=", "valore": 12}, {"contatore": "turni", "op": ">=", "valore": 1}],
     "effetti": [{"orologio": "La Gilda", "avanza": 2, "segmenti": 4, "conseguenza": "i contrabbandieri lo cercano"},
                 {"relazione": "Mira Sol", "verso": "scende", "forza": "lieve"}]},
    {"nome": "dopo un fallimento", "ricorrente": True,
     "quando": [{"esito": "fallito", "op": "==", "valore": "fallito"}],
     "effetti": [{"avviso": "Un fallimento lascia un segno: nomina la conseguenza"}]},
    {"nome": "ostile", "ricorrente": True,
     "quando": [{"stadio": "Mira Sol", "op": "==", "valore": "Diffidente"}, {"orologio": "La Gilda", "op": ">=", "valore": 2}],
     "effetti": [{"avviso": "Mira e' diffidente e la Gilda si muove"}]},
]}


def narrator_calls(client):
    return [c for c in client.calls if "Sei il master" in str(c.get("system"))]


class TestForma(unittest.TestCase):
    def test_valid_rules_have_no_problems(self) -> None:
        self.assertEqual(trigger.problems(RULES), [])
        self.assertEqual(trigger.problems({"trigger": []}), [])

    def test_problems_name_the_path(self) -> None:
        r = json.loads(json.dumps(RULES))
        r["trigger"][0]["quando"][0]["op"] = "~"
        r["trigger"][1]["effetti"][0]["avanza"] = "due"
        r["trigger"][2]["quando"][0]["op"] = ">"
        r["trigger"][3]["nome"] = "ferito"
        r["trigger"][1]["effetti"][1]["forza"] = "enorme"
        r["trigger"].append({"nome": "x", "quando": [{"scheda": "a", "op": "==", "valore": 1, "extra": 2}], "effetti": [{"avviso": "y", "forza": 1}]})
        paths = [p for p, _ in trigger.problems(r)]
        for expected in ("trigger[0].quando[0].op", "trigger[1].effetti[0].avanza", "trigger[2].quando[0].op", "trigger[3].nome",
                         "trigger[1].effetti[1].forza", "trigger[4].quando[0]", "trigger[4].effetti[0]"):
            self.assertIn(expected, paths)
        self.assertEqual(trigger.problems({"trigger": [{"nome": "a", "quando": [{"scheda": "x", "orologio": "y", "op": "==", "valore": 1}], "effetti": [{"avviso": "z"}]}]})[0][0],
                         "trigger[0].quando[0]")

    def test_roll_outcome_is_read_from_the_turn_message(self) -> None:
        self.assertEqual(trigger.roll_outcome("[Tiro] x — 1d20: 3 -> totale 3 contro difficolta' 15: FALLITO MALAMENTE (mancano 12). Guida: ..."), "fallito malamente")
        self.assertIsNone(trigger.roll_outcome("Apro la porta."))
        self.assertIsNone(trigger.roll_outcome("[Tiro] x — 2d6: 3, 4 -> totale 7."))


class TestInGioco(Base):
    def write(self, rules) -> None:
        (self.camp.root / "meccanica" / "trigger.json").write_text(json.dumps(rules), encoding="utf-8")

    def test_rules_fire_in_the_turn_head_once_or_every_turn(self) -> None:
        self.write(RULES)
        self.assertEqual(validate(self.camp).errors, [])
        client = ScriptedClient([ROUTER, QUIET, DONE])
        engine = MasterEngine(self.camp, client=client)
        engine.play("Guardo il porto.")  # turno 1: monete 12 ma turni giocati 0 -> la Gilda non si muove
        head = narrator_calls(client)[-1]["messages"][-1]["content"]
        self.assertNotIn("TRIGGER", head)
        self.assertEqual(self.camp.clocks(), {})
        client.script = [ROUTER, QUIET, DONE]
        engine.play("Conto le monete.")  # turno 2: la regola una tantum scatta: orologio nuovo a 2/4, Mira scende
        self.assertEqual(self.camp.clocks()["La Gilda"]["pieni"], 2)
        self.assertEqual(self.camp.relation("Mira Sol"), -3)
        self.assertEqual(self.camp.triggers_fired(), {"la gilda si muove"})
        head = narrator_calls(client)[-1]["messages"][-1]["content"]
        self.assertIn("[Orologi: La Gilda 2/4]", head)
        self.assertNotIn("TRIGGER", head)  # orologio e relazione non fanno rumore: si vedono dove si vedono
        client.script = [ROUTER, QUIET, DONE]
        engine.play("Aspetto.")  # turno 3: la una tantum non riscatta (l'orologio resterebbe a 4)
        self.assertEqual(self.camp.clocks()["La Gilda"]["pieni"], 2)
        # una ferita: la regola ricorrente scatta a ogni turno finche' vale, e la cache del prompt non si tocca
        from master.sheet import Sheet

        sheet = Sheet(self.camp, "kael")
        sheet.set("stato.punti_ferita.attuale", 3)
        sheet.save()
        system_before = engine.system_blocks()
        for _ in range(2):
            client.script = [ROUTER, QUIET, DONE]
            engine.play("Mi trascino.")
            head = narrator_calls(client)[-1]["messages"][-1]["content"]
            self.assertIn("[TRIGGER ferito: Sotto 5 punti ferita: malus a ogni prova, secondo le regole]", head)
        self.assertEqual(engine.system_blocks(), system_before)
        # l'esito di un tiro: solo nel turno in cui arriva
        client.script = [[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], DONE]
        engine.play("[Tiro] Scasso — 1d20: 2 +0 = 2 -> totale 2 contro difficolta' 15: FALLITO MALAMENTE (mancano 13).")
        self.assertNotIn("dopo un fallimento", narrator_calls(client)[-1]["messages"][-1]["content"])  # 'fallito malamente' != 'fallito'
        client.script = [[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], DONE]
        engine.play("[Tiro] Scasso — 1d20: 9 +0 = 9 -> totale 9 contro difficolta' 15: FALLITO (mancano 6).")
        self.assertIn("[TRIGGER dopo un fallimento:", narrator_calls(client)[-1]["messages"][-1]["content"])
        # stadio + orologio insieme (AND): Mira a -3 e' Neutrale, non Diffidente
        self.assertNotIn("TRIGGER ostile", narrator_calls(client)[-1]["messages"][-1]["content"])
        self.camp.shift_relation("Mira Sol", "scende", "forte")
        client.script = [ROUTER, QUIET, DONE]
        engine.play("Torno da Mira.")
        self.assertIn("[TRIGGER ostile: Mira e' diffidente e la Gilda si muove]", narrator_calls(client)[-1]["messages"][-1]["content"])

    def test_a_broken_file_is_ignored_whole_and_reported(self) -> None:
        bad = json.loads(json.dumps(RULES))
        bad["trigger"][0]["quando"][0]["valore"] = "quattro"
        self.write(bad)
        self.assertEqual(trigger.carica(self.camp), [])
        self.assertEqual(trigger.valuta(self.camp, ""), [])
        self.assertEqual([f.path for f in validate(self.camp).errors], ["meccanica/trigger.json:trigger[0].quando[0].valore"])
        good = json.loads(json.dumps(RULES))
        good["trigger"][0]["quando"][0]["scheda"] = "stato.sete"
        self.write(good)
        self.assertEqual(len(trigger.carica(self.camp)), 4)  # la forma e' giusta: il file vale
        self.assertEqual([f.title for f in validate(self.camp).errors], ["percorso assente dalla scheda"])
        self.assertEqual(trigger.valuta(self.camp, ""), [])  # e la regola non scatta mai, senza errori

    def test_without_the_file_nothing_changes(self) -> None:
        client = ScriptedClient([ROUTER, QUIET, DONE])
        MasterEngine(self.camp, client=client).play("Guardo.")
        self.assertNotIn("TRIGGER", narrator_calls(client)[-1]["messages"][-1]["content"])
        self.assertEqual(validate(self.camp).errors, [])


if __name__ == "__main__":
    unittest.main()
