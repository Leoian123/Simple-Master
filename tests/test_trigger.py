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


class TestTempo(Base):
    """Il tempo come fatto: una volta per sessione, ogni N turni. Niente AI, niente memoria: contatori."""

    RULE = {"trigger": [
        {"nome": "apertura", "ricorrente": "sessione",
         "quando": [{"contatore": "sessione", "op": ">=", "valore": 1}],
         "effetti": [{"avviso": "Inizio di sessione: le regole sul riposo"}]},
        {"nome": "marea", "ricorrente": True,
         "quando": [{"contatore": "turni", "op": "ogni", "valore": 2}],
         "effetti": [{"orologio": "La marea", "avanza": 1, "segmenti": 4, "conseguenza": "l'isola si copre"}]},
    ]}

    def test_shape(self) -> None:
        self.assertEqual(trigger.problems(self.RULE), [])
        bad = json.loads(json.dumps(self.RULE))
        bad["trigger"][0]["ricorrente"] = "ogni"
        bad["trigger"][1]["quando"][0]["valore"] = 0
        bad["trigger"].append({"nome": "x", "quando": [{"scheda": "stato.monete", "op": "ogni", "valore": 2}], "effetti": [{"avviso": "y"}]})
        self.assertEqual([p for p, _ in trigger.problems(bad)], ["trigger[0].ricorrente", "trigger[1].quando[0].valore", "trigger[2].quando[0].op"])

    def test_once_per_session_and_every_n_turns(self) -> None:
        (self.camp.root / "meccanica" / "trigger.json").write_text(json.dumps(self.RULE), encoding="utf-8")
        self.assertEqual(validate(self.camp).errors, [])
        slug = self.camp.active_sheet()["slug"]
        self.assertEqual(trigger.valuta(self.camp, "Salpo."), ["[TRIGGER apertura: Inizio di sessione: le regole sul riposo]"])  # sessione 1, turno 0
        self.assertEqual(trigger.valuta(self.camp, "Guardo."), [])  # stessa sessione: gia' scattato
        self.assertEqual(self.camp.triggers_fired(), {"apertura@1"})
        self.camp.append_turn(slug, "a", "b")
        self.assertEqual(trigger.valuta(self.camp, "Remo."), [])  # turno 1: non multiplo di 2
        self.camp.append_turn(slug, "a", "b")
        trigger.valuta(self.camp, "Remo.")  # turno 2
        self.assertEqual(self.camp.clocks()["La marea"]["pieni"], 1)
        self.camp.append_turn(slug, "a", "b")
        trigger.valuta(self.camp, "Remo.")  # turno 3
        self.assertEqual(self.camp.clocks()["La marea"]["pieni"], 1)
        self.camp.append_turn(slug, "a", "b")
        trigger.valuta(self.camp, "Remo.")  # turno 4
        self.assertEqual(self.camp.clocks()["La marea"]["pieni"], 2)
        self.camp.next_session()
        self.assertEqual(trigger.valuta(self.camp, "Riprendo."), ["[TRIGGER apertura: Inizio di sessione: le regole sul riposo]"])  # sessione 2
        self.assertEqual(self.camp.triggers_fired(), {"apertura@1", "apertura@2"})
        self.assertEqual(validate(self.camp).errors, [])


class TestTabella(Base):
    """La riga uscita da una tabella e' un fatto: vale nel turno in cui arriva, per quella tabella, e poi sparisce."""

    RULE = {"trigger": [
        {"nome": "il gorgo", "ricorrente": True,
         "quando": [{"tabella": "incontri in mare", "op": "contiene", "valore": "gorgo"}],
         "effetti": [{"avviso": "Il Gorgo: le regole sulle correnti"}, {"orologio": "La tempesta", "avanza": 1, "segmenti": 4, "conseguenza": "la nave affonda"}]},
        {"nome": "calma", "ricorrente": True,
         "quando": [{"tabella": "incontri in mare", "op": "==", "valore": "Mare calmo"}],
         "effetti": [{"avviso": "Mare calmo: una giornata senza prove"}]},
    ]}

    def test_shape(self) -> None:
        self.assertEqual(trigger.problems(self.RULE), [])
        bad = json.loads(json.dumps(self.RULE))
        bad["trigger"][0]["quando"][0]["op"] = ">="
        bad["trigger"][1]["quando"][0]["valore"] = 3
        self.assertEqual([p for p, _ in trigger.problems(bad)],
                         ["trigger[0].quando[0].op", "trigger[0].quando[0].valore", "trigger[1].quando[0].valore"])  # >= vuole anche un numero

    def test_an_effect_can_roll_a_table_and_the_rows_reach_the_narrator(self) -> None:
        from test_dice import fixed

        tables = {"tabelle": [
            {"nome": "incontri in mare", "uso": "", "dado": "1d6", "fonte": "",
             "righe": [{"da": 1, "a": 5, "risultato": "Mare calmo"}, {"da": 6, "a": 6, "risultato": "Il Gorgo", "tabella": "relitti"}]},
            {"nome": "relitti", "uso": "", "dado": "1d4", "fonte": "", "righe": [{"da": 1, "a": 4, "risultato": "Una chiglia spezzata"}]},
        ]}
        (self.camp.root / "meccanica" / "tabelle.json").write_text(json.dumps(tables), encoding="utf-8")
        rule = {"trigger": [{"nome": "nuova sessione in mare", "ricorrente": True,
                             "quando": [{"contatore": "sessione", "op": ">=", "valore": 1}],
                             "effetti": [{"tabella": "incontri in mare"}]}]}
        (self.camp.root / "meccanica" / "trigger.json").write_text(json.dumps(rule), encoding="utf-8")
        self.assertEqual(trigger.problems(rule), [])
        self.assertEqual(validate(self.camp).errors, [])
        with fixed(6, 2):
            notes = trigger.valuta(self.camp, "Salpo.")
        self.assertEqual(notes, ["[TRIGGER nuova sessione in mare: tabella incontri in mare -> Il Gorgo]",
                                 "[TRIGGER nuova sessione in mare: tabella relitti -> Una chiglia spezzata]"])
        # una tabella che la campagna non ha: la regola scatta senza riga, e la verifica lo dice prima
        rule["trigger"][0]["effetti"] = [{"tabella": "ignota"}]
        (self.camp.root / "meccanica" / "trigger.json").write_text(json.dumps(rule), encoding="utf-8")
        self.assertEqual(trigger.valuta(self.camp, "Salpo."), [])
        self.assertEqual([f.title for f in validate(self.camp).warnings if "tabella" in f.title], ["tabella sconosciuta nell'effetto"])
        self.assertEqual(validate(self.camp).errors, [])

    def test_fires_only_in_the_turn_the_row_arrives_and_only_for_its_table(self) -> None:
        (self.camp.root / "meccanica" / "trigger.json").write_text(json.dumps(self.RULE), encoding="utf-8")
        self.assertEqual(validate(self.camp).errors, [])
        self.assertEqual(trigger.valuta(self.camp, "Salpo."), [])
        self.assertEqual(trigger.valuta(self.camp, "[Tabella] tesori — 1d6: 6 -> 6: Il Gorgo"), [])  # altra tabella
        self.assertEqual(trigger.valuta(self.camp, "[Tabella] incontri in mare — 1d6: 6 -> 6: Il Gorgo"),
                         ["[TRIGGER il gorgo: Il Gorgo: le regole sulle correnti]"])
        self.assertEqual(self.camp.clocks()["La tempesta"]["pieni"], 1)
        self.assertEqual(trigger.valuta(self.camp, "[Tabella] Incontri in mare — 1d6: 2 -> 2: Mare calmo"), ["[TRIGGER calma: Mare calmo: una giornata senza prove]"])
        self.assertEqual(trigger.valuta(self.camp, "Guardo l'orizzonte."), [])
        # dal tavolo: il messaggio della tabella e' il turno, e la testa del messaggio porta il trigger al narratore
        client = ScriptedClient([QUIET, DONE])
        MasterEngine(self.camp, client=client).play("[Tabella] incontri in mare — 1d6: 4 +2 -> 6: Il Gorgo")
        self.assertIn("[TRIGGER il gorgo:", narrator_calls(client)[-1]["messages"][-1]["content"])
        self.assertEqual(self.camp.clocks()["La tempesta"]["pieni"], 2)


class TestEstrazione(Base):
    """Dal manuale ai trigger: il modello propone in forma piatta, il codice ritraduce e setaccia."""

    def sheet(self):
        from master.sheet import Sheet

        info = self.camp.active_sheet()
        return Sheet(self.camp, info["slug"])

    def test_sheet_paths_are_the_leaves_without_the_ledger(self) -> None:
        paths = trigger.sheet_paths({"nome": "Kael", "stato": {"monete": 3, "punti_ferita": {"attuale": 8, "massimo": 10}},
                                     "esperienza": [{"punti": 1}], "tratti": ["x"], "ok": True})
        self.assertEqual(paths, ["nome", "stato.monete", "stato.punti_ferita.attuale", "stato.punti_ferita.massimo"])

    def test_rules_with_thresholds_pick_sections_that_bind_a_number_to_a_consequence(self) -> None:
        self.camp.file("meccanica/regole.md").set_section("Ferite", "Quando i punti ferita scendono a 0 il personaggio cade a terra.")
        self.camp.file("meccanica/regole.md").set_section("Tono", "Scene brevi e cupe.")
        text = trigger.rules_with_thresholds(self.camp)
        self.assertIn("## Ferite (meccanica/regole.md)", text)
        self.assertNotIn("## Tono", text)

    def test_sections_are_ranked_by_threshold_density_not_by_file_order(self) -> None:
        self.camp.file("meccanica/creature.md").set_section(
            "Animali", "Il lupo ha 3 dadi di attacco. Se il branco e' numeroso, quando attacca di notte, il master decide 1 cosa.")
        self.camp.file("meccanica/regole.md").set_section(
            "Fame", "Quando la Fame raggiunge 4 ogni tentazione richiede una prova con almeno 3 dadi; a 5 il personaggio scende in frenesia se supera 2 fallimenti, e torna a 0 quando si nutre.")
        self.camp.file("meccanica/regole.md").set_section("Volonta'", "La Volonta' scende di 1 ogni volta che si cede; a zero il personaggio crolla.")
        ranked = trigger.threshold_sections(self.camp)
        self.assertEqual([(t, s >= trigger.MIN_SCORE) for s, _, t, _ in ranked][:2], [("Fame", True), ("Volonta'", True)])
        self.assertNotIn("Animali", [t for _, _, t, _ in ranked])  # "se" e "quando" vicino a un numero non bastano
        self.assertGreater(ranked[0][0], ranked[1][0])
        self.assertTrue(trigger.rules_with_thresholds(self.camp).startswith("## Fame (meccanica/regole.md)"))
        self.assertEqual(trigger.threshold_score("Scene brevi e cupe, 3 atti."), 0)

    def test_a_second_run_does_not_duplicate_rules_under_another_name(self) -> None:
        """Collaudo del 2026-10-22: la seconda corsa riproponeva 'ferite_sotto_cinque' come 'ferite sotto cinque'."""
        self.camp.file("meccanica/regole.md").set_section("Ferite", "Quando i punti ferita scendono sotto 5 ogni prova perde un dado.")
        (self.camp.root / "meccanica" / "trigger.json").write_text(json.dumps({"trigger": [RULES["trigger"][0]]}), encoding="utf-8")  # 'ferito': attuale <= 4
        eff = lambda text: {"tipo": "avviso", "testo": text, "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}
        answer = {"trigger": [
            {"nome": "Ferito", "ricorrente": "ogni turno", "fonte": "Ferite", "quando": [{"tipo": "scheda", "soggetto": "stato.monete", "op": "<", "valore": "1"}], "effetti": [eff("x")]},  # stesso nome
            {"nome": "ferite_basse", "ricorrente": "ogni turno", "fonte": "Ferite", "quando": [{"tipo": "scheda", "soggetto": "stato.punti_ferita.attuale", "op": "<=", "valore": "4"}], "effetti": [eff("y")]},  # stesse condizioni
            {"nome": "senza un soldo", "ricorrente": "una volta", "fonte": "Ferite", "quando": [{"tipo": "scheda", "soggetto": "stato.monete", "op": "<=", "valore": "0"}], "effetti": [eff("z")]},
        ]}
        client = ScriptedClient([[_b(type="text", text=json.dumps(answer))]])
        new = trigger.extract_triggers(self.camp, client, "m", self.sheet())
        self.assertEqual([t["nome"] for t in new], ["senza un soldo"])
        self.assertEqual(new[0]["ricorrente"], False)
        self.assertIn("REGOLE GIA' SCRITTE (non riproporle, nemmeno con un altro nome): ferito", client.calls[0]["messages"][0]["content"])
        self.assertEqual([t["nome"] for t in trigger.carica(self.camp)], ["ferito", "senza un soldo"])

    def test_flat_answer_becomes_the_file_shape(self) -> None:
        rule = trigger._to_rule({"nome": "Ferito", "ricorrente": True, "fonte": "Ferite",
                                 "quando": [{"tipo": "scheda", "soggetto": "stato.punti_ferita.attuale", "op": "<=", "valore": "4"}],
                                 "effetti": [{"tipo": "avviso", "testo": "Malus", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None},
                                             {"tipo": "orologio", "testo": "La Gilda", "avanza": None, "segmenti": 4, "conseguenza": "arrivano", "verso": None, "forza": None},
                                             {"tipo": "relazione", "testo": "Mira Sol", "avanza": None, "segmenti": None, "conseguenza": None, "verso": "scende", "forza": "lieve"}]})
        self.assertEqual(rule["nome"], "ferito")
        self.assertEqual(rule["quando"], [{"scheda": "stato.punti_ferita.attuale", "op": "<=", "valore": 4}])
        self.assertEqual(rule["effetti"], [{"avviso": "Malus"}, {"orologio": "La Gilda", "avanza": 1, "segmenti": 4, "conseguenza": "arrivano"},
                                           {"relazione": "Mira Sol", "verso": "scende", "forza": "lieve"}])
        self.assertEqual(trigger.problems({"trigger": [rule]}), [])

    def test_extraction_keeps_what_passes_and_leaves_hand_written_rules_alone(self) -> None:
        self.camp.file("meccanica/regole.md").set_section("Ferite", "Quando i punti ferita scendono sotto 5 ogni prova ha un malus.")
        (self.camp.root / "meccanica" / "trigger.json").write_text(json.dumps({"trigger": [RULES["trigger"][0]]}), encoding="utf-8")
        sheet = self.sheet()
        answer = {"trigger": [
            {"nome": "ferito", "ricorrente": True, "fonte": "Ferite",  # nome gia' usato a mano: resta quella a mano
             "quando": [{"tipo": "scheda", "soggetto": "stato.punti_ferita.attuale", "op": "<", "valore": "5"}],
             "effetti": [{"tipo": "avviso", "testo": "doppione", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}]},
            {"nome": "senza un soldo", "ricorrente": True, "fonte": "Ferite",
             "quando": [{"tipo": "scheda", "soggetto": "stato.monete", "op": "<=", "valore": "0"}],
             "effetti": [{"tipo": "avviso", "testo": "Senza monete non paga il pedaggio", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}]},
            {"nome": "inventato", "ricorrente": True, "fonte": "Ferite",  # percorso che la scheda non ha
             "quando": [{"tipo": "scheda", "soggetto": "stato.sanita", "op": "<", "valore": "3"}],
             "effetti": [{"tipo": "avviso", "testo": "x", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}]},
            {"nome": "storto", "ricorrente": False, "fonte": "Ferite",  # stadio con un operatore numerico
             "quando": [{"tipo": "stadio", "soggetto": "Mira Sol", "op": ">", "valore": "Ostile"}],
             "effetti": [{"tipo": "avviso", "testo": "x", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}]},
        ]}
        client = ScriptedClient([[_b(type="text", text=json.dumps(answer))]])
        new = trigger.extract_triggers(self.camp, client, "claude-sonnet-5", sheet)
        self.assertEqual([t["nome"] for t in new], ["senza un soldo"])
        saved = json.loads((self.camp.root / "meccanica" / "trigger.json").read_text(encoding="utf-8"))
        self.assertEqual([t["nome"] for t in saved["trigger"]], ["ferito", "senza un soldo"])
        self.assertEqual(saved["trigger"][0], RULES["trigger"][0])  # quella a mano intatta
        self.assertEqual(saved["trigger"][1]["quando"], [{"scheda": "stato.monete", "op": "<=", "valore": 0}])
        self.assertEqual(validate(self.camp).errors, [])
        call = client.calls[0]
        self.assertEqual(call["output_config"]["format"]["schema"], trigger.TRIGGERS_SCHEMA)
        self.assertIn("stato.monete", call["messages"][0]["content"])
        self.assertIn("## Ferite", call["messages"][0]["content"])
        # senza scheda: le condizioni `scheda` si scartano, non si verificano a vuoto
        (self.camp.root / "meccanica" / "trigger.json").unlink()
        client = ScriptedClient([[_b(type="text", text=json.dumps(answer))]])
        self.assertEqual([t["nome"] for t in trigger.extract_triggers(self.camp, client, "m", None)], [])
        self.assertIn("non usare condizioni `scheda`", client.calls[0]["messages"][0]["content"])
        # senza sezioni con soglie: nessuna chiamata
        self.camp.file("meccanica/regole.md").write("# Regole\n\n## Tono\n\nCupo.\n")
        client = ScriptedClient([])
        self.assertEqual(trigger.extract_triggers(self.camp, client, "m", sheet), [])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
