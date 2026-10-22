"""Dadi tirati dal codice secondo il profilo ricavato dal manuale; orologi contati dal codice."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import MasterEngine, dice  # noqa: E402
from master.sheet import Sheet  # noqa: E402
from master.tools import ToolExecutor  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402

POOL = {
    "sistema": "Gioco a riserve", "note": "", "tiri": [{
        "nome": "riserva", "uso": "", "tipo": "successi", "dado": 10, "dadi_fissi": None, "successo_da": 6, "vantaggio": False,
        "nome_zero_successi": "fallimento totale", "coppie": {"faccia": 10, "successi_extra": 2, "nome": "critico"},
        "speciali": {"nome": "fame", "quanti_da": "stato.fame", "sostituiscono": True},
        "eventi": [{"nome": "critico caotico", "faccia": 10, "quando": "critico", "solo_speciali": True, "effetto": ""},
                   {"nome": "fallimento bestiale", "faccia": 1, "quando": "fallito", "solo_speciali": True, "effetto": ""}]}]}


def fixed(*values):
    """I prossimi dadi, in ordine."""
    seq = iter(values)
    return mock.patch.object(dice, "_die", lambda sides: next(seq))


class TestDiceEngine(unittest.TestCase):
    def test_the_engine_knows_no_game(self) -> None:
        source = (ROOT / "master" / "dice.py").read_text(encoding="utf-8").split('"""', 2)[2]
        for word in ("fame", "bestiale", "caotico", "d20", "vampir"):
            self.assertFalse(word in source.lower(), word)  # le regole stanno nel profilo, non nel codice

    def test_no_module_that_talks_to_the_model_names_a_real_game(self) -> None:
        """Gli esempi nei prompt, negli schemi e nelle docstring sono di un gioco inventato: un manuale vero
        li rileggerebbe come la risposta attesa. Le misure prese su un manuale vero restano, ma senza nominarlo."""
        words = ("vampir", "fame", "frenesia", "bestia", "sangue", "ventrue", "clan ", "jonathan", "caotico")  # 'umanita'' e' di molti giochi
        for name in ("dice", "trigger", "tabelle", "prefazione", "engine", "tools", "docs", "character", "sheet"):
            source = (ROOT / "master" / f"{name}.py").read_text(encoding="utf-8").lower()
            for word in words:
                self.assertFalse(word in source, f"{name}.py nomina '{word}'")

    def test_pool_with_special_dice_from_the_sheet(self) -> None:
        sheet = SimpleNamespace(get=lambda path: {"stato.fame": 2}[path])
        with fixed(7, 3, 10, 10, 2):  # tre normali, poi due dadi fame
            r = dice.roll("Intelligenza + Investigare | riserva | dadi 5 | difficolta 3", POOL, sheet)
        self.assertEqual((r["value"], r["success"], r["special_rolls"]), (5, True, [10, 2]))  # 7, 10, 10 + coppia
        self.assertIn("5d10: 7, 3, 10 | fame: 10, 2", r["text"])
        self.assertIn("CRITICO. CRITICO CAOTICO", r["text"])
        with fixed(2, 3, 4, 1, 5):
            r = dice.roll("x | riserva | dadi 5 | difficolta 2", POOL, sheet)
        self.assertEqual(r["notes"], ["FALLIMENTO BESTIALE", "FALLIMENTO TOTALE"])
        self.assertIn("FALLITO (mancano 2)", r["text"])
        with fixed(6, 1, 6):  # riuscito: l'1 sul dado speciale non conta
            r = dice.roll("x | riserva | dadi 3 | difficolta 2 | fame 1", POOL, sheet)
        self.assertEqual((r["success"], r["notes"]), (True, []))
        with fixed(10, 10):  # coppia sui dadi normali: critico pulito
            r = dice.roll("x | riserva | dadi 2 | fame 0", POOL, sheet)
        self.assertEqual((r["value"], r["notes"]), (4, ["CRITICO"]))

    def test_sum_systems_and_plain_notation(self) -> None:
        d20 = {"tiri": [{"nome": "d20", "tipo": "somma", "dado": 20, "dadi_fissi": 1, "vantaggio": True,
                         "eventi": [{"nome": "colpo critico", "faccia": 20, "quando": "sempre", "solo_speciali": False, "effetto": ""}]}]}
        with fixed(20):
            r = dice.roll("Attacco | d20 | bonus +5 | difficolta 15", d20)
        self.assertEqual((r["value"], r["success"], r["notes"]), (25, True, ["COLPO CRITICO"]))
        with fixed(4, 17):
            r = dice.roll("Attacco | d20 | bonus +2 | vantaggio | difficolta 15", d20)
        self.assertEqual(r["value"], 19)
        self.assertIn("4 e 17, tengo 17 (vantaggio)", r["text"])
        with fixed(3, 4):  # senza profilo: i dadi per esteso
            r = dice.roll("Danno | 2d6+1")
        self.assertEqual((r["value"], r["success"]), (8, None))
        with fixed(6, 2, 9):
            r = dice.roll("Furtivita' | 3d10 | successi 6+ | difficolta 2")
        self.assertEqual((r["value"], r["success"]), (2, True))
        self.assertEqual(dice.roll("2d6")["name"], "Tiro libero")
        for bad in ("Attacco | riserva | dadi 3", "x | 500d6", "x | banana"):
            with self.assertRaises(ValueError):
                dice.roll(bad)

    def test_marker_and_prompt_come_from_the_profile(self) -> None:
        self.assertEqual(dice.find_marker("Buio.\n[[tiro: Forza + Rissa | riserva | dadi 4 | difficolta 2]]"),
                         "Forza + Rissa | riserva | dadi 4 | difficolta 2")
        self.assertIsNone(dice.find_marker("Nessun tiro qui."))
        text = dice.prompt_section(POOL)
        for piece in ("`riserva`", "d10, successo da 6", "`stato.fame`", "[[tiro: <nome> | riserva | dadi <N> | difficolta <D>]]"):
            self.assertIn(piece, text)
        self.assertIn("non ha ancora un profilo", dice.prompt_section(None))


LADDER = [{"nome": "fallito", "da_margine": None, "a_margine": -1, "riuscito": False, "guida": "non riesce"},
          {"nome": "vittoria costosa", "da_margine": 0, "a_margine": 0, "riuscito": True, "guida": "riesce, ma a un prezzo"},
          {"nome": "riuscito", "da_margine": 1, "a_margine": None, "riuscito": True, "guida": "riesce"}]


class TestEsiti(unittest.TestCase):
    """Con una difficolta', l'esito ha il nome della fascia e una guida: dal manuale, o predefinite."""

    def test_default_bands_for_pools_and_sums(self) -> None:
        with fixed(6, 6, 2):
            r = dice.roll("x | 3d10 | successi 6+ | difficolta 2")
        self.assertEqual((r["esito"], r["success"]), ("riuscito di misura", True))
        self.assertIn("RIUSCITO DI MISURA (margine 0). Guida: Riesce appena", r["text"])
        with fixed(6, 6, 6, 6, 6):
            self.assertEqual(dice.roll("x | 5d10 | successi 6+ | difficolta 2")["esito"], "riuscito bene")
        with fixed(2, 2, 2):
            r = dice.roll("x | 3d10 | successi 6+ | difficolta 2")
        self.assertEqual((r["esito"], r["success"]), ("fallito", False))
        self.assertIn("FALLITO (mancano 2)", r["text"])
        with fixed(2):
            self.assertEqual(dice.roll("x | 1d20 | difficolta 15")["esito"], "fallito malamente")
        with fixed(20):
            self.assertEqual(dice.roll("x | 1d20+5 | difficolta 15")["esito"], "riuscito bene")
        self.assertEqual(dice.roll("x | 2d6")["esito"], "")  # senza difficolta' niente fascia

    def test_manual_bands_override_the_defaults_and_decide_success(self) -> None:
        pool = {"tiri": [{**POOL["tiri"][0], "esiti": LADDER}]}
        sheet = SimpleNamespace(get=lambda path: 0)
        with fixed(6, 6, 2):
            r = dice.roll("x | riserva | dadi 3 | difficolta 2 | fame 0", pool, sheet)
        self.assertEqual((r["esito"], r["guida"], r["success"]), ("vittoria costosa", "riesce, ma a un prezzo", True))
        self.assertIn("VITTORIA COSTOSA (margine 0). Guida: riesce, ma a un prezzo", r["text"])
        strict = {"tiri": [{**POOL["tiri"][0], "esiti": [{**LADDER[0], "a_margine": 0}, {**LADDER[2], "da_margine": 1}]}]}
        with fixed(6, 6, 2):  # qui il margine 0 e' un fallimento: lo decide la fascia, non la soglia
            r = dice.roll("x | riserva | dadi 3 | difficolta 2 | fame 0", strict, sheet)
        self.assertEqual((r["esito"], r["success"]), ("fallito", False))
        self.assertIn("FALLITO (margine 0)", r["text"])
        with fixed(2, 2, 2):  # zero successi: il nome del manuale resta in coda
            r = dice.roll("x | riserva | dadi 3 | difficolta 1 | fame 0", pool, sheet)
        self.assertEqual(r["notes"], ["FALLIMENTO TOTALE"])

    def test_bands_must_tile_the_margins(self) -> None:
        self.assertEqual(dice.esiti_problems([]), [])
        self.assertEqual(dice.esiti_problems(LADDER), [])
        for broken, where in (
            ([{**LADDER[0]}, {**LADDER[1], "da_margine": 1}, LADDER[2]], "[1].da_margine"),          # buco
            ([LADDER[0], {**LADDER[1], "a_margine": 2}, LADDER[2]], "[2].da_margine"),               # sovrapposizione
            ([{**LADDER[0], "da_margine": -5}, LADDER[1], LADDER[2]], "[0].da_margine"),            # non aperta in basso
            ([LADDER[0], LADDER[1], {**LADDER[2], "a_margine": 9}], "[2].a_margine"),               # non aperta in alto
            ([LADDER[0], {**LADDER[1], "nome": "fallito"}, LADDER[2]], ""),                          # nome doppio
            ([LADDER[0], {**LADDER[1], "riuscito": "si"}, LADDER[2]], "[1].riuscito"),              # non booleano
            ([LADDER[0], {**LADDER[1], "riuscito": True}, {**LADDER[2], "riuscito": False}], "[2].riuscito"),  # fallita sopra una riuscita
            ("x", ""),
        ):
            with self.subTest(where=where):
                self.assertIn(where, [p for p, _ in dice.esiti_problems(broken)])

    def test_prompt_lists_the_bands_of_each_kind(self) -> None:
        text = dice.prompt_section({"tiri": [{**POOL["tiri"][0], "esiti": LADDER}, {"nome": "d20", "tipo": "somma", "dado": 20, "dadi_fissi": 1}]})
        self.assertIn("esiti per margine: fallito (fino a -1) · vittoria costosa (0) · riuscito (da 1)", text)
        self.assertIn("esiti per margine: fallito malamente (fino a -10) · fallito (-9..-1) · riuscito di misura (0..4)", text)
        self.assertIn("narra quella, non reinterpretare il margine", text)
        self.assertIn("esiti", json.dumps(dice.PROFILE_SCHEMA))  # il manuale le puo' dare


D100 = {"tiri": [{"nome": "percentuale", "tipo": "sotto", "dado": 100, "dadi_fissi": 1,
                  "soglie": [{"nome": "estremo", "frazione": 0.2, "guida": "da manuale: estremo"}, {"nome": "difficile", "frazione": 0.5, "guida": ""}],
                  "fallimento_critico": {"da": 96, "sotto_valore": 50, "altrimenti_da": 100}, "successo_critico_fino_a": 1,
                  "eventi": [{"nome": "colpo di fortuna", "faccia": 7, "quando": "riuscito", "solo_speciali": False, "effetto": ""}]}]}


class TestSotto(unittest.TestCase):
    """Roll-under: si riesce con un totale fino al valore; i gradi sono frazioni del valore."""

    def test_grades_fumble_and_crit(self) -> None:
        for face, esito, won in ((1, "successo critico", True), (10, "estremo", True), (12, "estremo", True), (13, "difficile", True),
                                 (30, "difficile", True), (31, "riuscito", True), (60, "riuscito", True), (61, "fallito", False),
                                 (99, "fallito", False), (100, "fallimento critico", False)):
            with self.subTest(face=face), fixed(face):
                r = dice.roll("Ascoltare | percentuale | valore 60", D100)
                self.assertEqual((r["esito"], r["success"], r["value"]), (esito, won, face))
        with fixed(96):  # con valore sotto 50 il fallimento critico comincia a 96
            self.assertEqual(dice.roll("x | percentuale | valore 40", D100)["esito"], "fallimento critico")
        with fixed(25):
            r = dice.roll("Ascoltare | percentuale | valore 60", D100)
        self.assertEqual(r["text"], "[Tiro] Ascoltare — 1d100: 25 -> totale 25 contro valore 60: DIFFICILE (margine 35). Guida: Riesce come previsto dalle regole.")
        with fixed(10):
            self.assertIn("Guida: da manuale: estremo", dice.roll("x | percentuale | valore 60", D100)["text"])
        with fixed(70):
            self.assertIn("FALLITO (mancano 10)", dice.roll("x | percentuale | valore 60", D100)["text"])
        with fixed(7):
            r = dice.roll("x | percentuale | valore 60 | bonus -20", D100)  # il bonus modifica il valore
        self.assertEqual((r["target"], r["esito"], r["notes"]), (40, "estremo", ["COLPO DI FORTUNA"]))
        self.assertIn("(valore 60 -20 = 40)", r["text"])
        with self.assertRaises(ValueError):
            dice.roll("x | percentuale | difficolta 60", D100)  # serve `valore`

    def test_prompt_shows_the_shape_and_the_scale(self) -> None:
        text = dice.prompt_section(D100)
        self.assertIn("[[tiro: <nome> | percentuale | valore <V>]]", text)
        self.assertIn("esiti sul totale: successo critico (fino a 1) · estremo (fino a 0.2 del valore) · difficile (fino a 0.5 del valore) · riuscito (fino al valore) · fallito (oltre) · fallimento critico (da 96 con valore sotto 50, altrimenti da 100)", text)


FITD = {"tiri": [{"nome": "azione", "tipo": "somma", "dado": 6, "dadi_fissi": None, "tieni": {"quanti": 1, "quale": "alto"}, "esiti_su": "dado",
                  "coppie": {"faccia": 6, "successi_extra": 0, "nome": "critico"},
                  "esiti": [{"nome": "fallito", "da_margine": None, "a_margine": 3, "riuscito": False, "guida": "va male"},
                            {"nome": "con conseguenze", "da_margine": 4, "a_margine": 5, "riuscito": True, "guida": "riesce a un prezzo"},
                            {"nome": "pieno", "da_margine": 6, "a_margine": None, "riuscito": True, "guida": "riesce"}]},
                 {"nome": "disperato", "tipo": "somma", "dado": 6, "dadi_fissi": 2, "tieni": {"quanti": 1, "quale": "basso"}, "esiti_su": "dado",
                  "esiti": [{"nome": "fallito", "da_margine": None, "a_margine": 3, "riuscito": False, "guida": ""},
                            {"nome": "riuscito", "da_margine": 4, "a_margine": None, "riuscito": True, "guida": ""}]},
                 {"nome": "esplosivo", "tipo": "somma", "dado": 6, "dadi_fissi": 1, "esplode_su": 6}]}


class TestTieniEsplode(unittest.TestCase):
    def test_highest_die_decides_without_a_difficulty(self) -> None:
        with fixed(3, 5):
            r = dice.roll("Scavalcare | azione | dadi 2", FITD)
        self.assertEqual((r["value"], r["esito"], r["success"], r["guida"]), (5, "con conseguenze", True, "riesce a un prezzo"))
        self.assertEqual(r["text"], "[Tiro] Scavalcare — 2d6: 3, 5 -> tengo 5 (i piu' alti) -> totale 5: CON CONSEGUENZE. Guida: riesce a un prezzo")
        with fixed(6, 6, 1):
            r = dice.roll("x | azione | dadi 3", FITD)
        self.assertEqual((r["esito"], r["notes"]), ("pieno", ["CRITICO"]))
        with fixed(2, 1):
            self.assertEqual(dice.roll("x | azione | dadi 2", FITD)["esito"], "fallito")
        with fixed(5, 2):  # il piu' basso decide
            self.assertEqual(dice.roll("x | disperato", FITD)["esito"], "fallito")
        with fixed(4):  # un dado solo: si tiene quello
            self.assertEqual(dice.roll("x | azione | dadi 1", FITD)["esito"], "con conseguenze")
        text = dice.prompt_section(FITD)
        self.assertIn("[[tiro: <nome> | azione | dadi <N>]]", text)
        self.assertIn("si tengono i 1 piu' alti e decide il dado tenuto, senza difficolta'", text)
        self.assertIn("esiti sul dado tenuto: fallito (fino a 3) · con conseguenze (4..5) · pieno (da 6)", text)

    def test_exploding_dice_in_sums_and_pools(self) -> None:
        with fixed(6, 6, 2):
            r = dice.roll("x | esplosivo | difficolta 10", FITD)
        self.assertEqual((r["value"], r["success"]), (14, True))
        self.assertIn("1d6: 6+6+2=14", r["text"])
        self.assertIn("una faccia da 6 in su si ritira e si somma", dice.prompt_section(FITD))
        pool = {"tiri": [{**POOL["tiri"][0], "speciali": None, "esplode_su": 10}]}
        with fixed(10, 3, 7, 4):  # il 10 aggiunge un dado (4): tre dadi tirati, quattro contati
            r = dice.roll("x | riserva | dadi 3 | difficolta 2", pool)
        self.assertEqual((r["value"], r["rolls"]), (2, [10, 3, 7, 4]))  # riescono il 10 e il 7; il 4 esploso no
        self.assertIn("| esplosi: 4", r["text"])
        with fixed(*([6] * 12), 3):  # il tetto: al massimo 10 esplosioni per dado
            self.assertEqual(dice.roll("x | esplosivo", FITD)["value"], 6 * 11)

    def test_keep_rules_are_validated_in_code(self) -> None:
        for bad in ({"quanti": 0, "quale": "alto"}, {"quanti": 1, "quale": "medio"}, "x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError), fixed(3, 4):
                dice.roll("x | azione | dadi 2", {"tiri": [{**FITD["tiri"][0], "tieni": bad}]})


class TestCampioni(unittest.TestCase):
    """I campioni del profilo si eseguono a dadi fissi: zero chiamate, e il profilo o torna o si dice."""

    def profile(self, **over):
        kind = {**POOL["tiri"][0], "esempi": [
            {"marcatore": "x | riserva | dadi 3 | difficolta 2 | fame 0", "dadi": [7, 7, 2], "atteso": {"valore": 2, "esito": "riuscito di misura", "riuscito": True}},
            {"marcatore": "x | riserva | dadi 2 | difficolta 1 | fame 0", "dadi": [2, 3], "atteso": {"valore": 0, "esito": "fallito", "riuscito": False}}]}
        kind.update(over)
        return {"tiri": [kind, {**D100["tiri"][0], "esempi": [{"marcatore": "x | percentuale | valore 60", "dadi": [25], "atteso": {"valore": 25, "esito": "difficile", "riuscito": None}}]}]}

    def test_samples_that_match_have_no_problems(self) -> None:
        self.assertEqual(dice.verifica_esempi(self.profile()), [])
        self.assertEqual(dice.verifica_esempi({"tiri": [{**POOL["tiri"][0]}]}), [])  # senza campioni nulla da dire

    def test_samples_name_what_does_not_match(self) -> None:
        bad = self.profile(esempi=[
            {"marcatore": "x | riserva | dadi 3 | difficolta 2 | fame 0", "dadi": [7, 7, 2], "atteso": {"valore": 3, "esito": "riuscito bene", "riuscito": False}},
            {"marcatore": "x | banana", "dadi": [1], "atteso": {"valore": None, "esito": None, "riuscito": None}},
            {"marcatore": "x | riserva | dadi 3 | difficolta 2 | fame 0", "dadi": [7], "atteso": {"valore": None, "esito": None, "riuscito": None}},
            "x"])
        paths = [p for p, _ in dice.verifica_esempi(bad)]
        self.assertEqual(paths, ["tiri[0].esempi[0].atteso.valore", "tiri[0].esempi[0].atteso.esito", "tiri[0].esempi[0].atteso.riuscito",
                                 "tiri[0].esempi[1].marcatore", "tiri[0].esempi[2].marcatore", "tiri[0].esempi[3]"])
        self.assertIn("piu' di 1 dadi", dict(dice.verifica_esempi(bad))["tiri[0].esempi[2].marcatore"])
        self.assertIs(dice._die.__name__, "_die")  # il dado vero torna al suo posto


class TestCampioniNellEstrazione(Base):
    def test_extraction_retries_once_when_samples_fail_and_flags_what_still_fails(self) -> None:
        dice.profile_path(self.camp).unlink()
        bad = {"sistema": "x", "note": "", "tiri": [{**POOL["tiri"][0], "speciali": None, "esempi": [
            {"marcatore": "x | riserva | dadi 3 | difficolta 2 | fame 0", "dadi": [7, 7, 2], "atteso": {"valore": 5, "esito": None, "riuscito": None}}]}]}
        good = {"sistema": "x", "note": "", "tiri": [{**POOL["tiri"][0], "speciali": None, "esempi": [
            {"marcatore": "x | riserva | dadi 3 | difficolta 2 | fame 0", "dadi": [7, 7, 2], "atteso": {"valore": 2, "esito": None, "riuscito": True}}]}]}
        client = ScriptedClient([[_b(type="text", text=json.dumps(bad))], [_b(type="text", text=json.dumps(good))]])
        profile = dice.extract_profile(self.camp, client, "claude-sonnet-5")
        self.assertEqual(len(client.calls), 2)
        complaint = client.calls[1]["messages"][-1]["content"]
        self.assertIn("tiri[0].esempi[0].atteso.valore: atteso 5, il programma da' 2", complaint)
        self.assertIn("LA TUA RISPOSTA PRECEDENTE:", complaint)  # il primo profilo resta sotto gli occhi, nello stesso messaggio
        self.assertIn('"valore": 5', complaint)
        self.assertEqual(client.calls[1]["tool_choice"], {"type": "tool", "name": dice.PROFILE_TOOL})
        self.assertNotIn("campioni_falliti", profile)
        self.assertEqual(dice.load_profile(self.camp)["tiri"][0]["esempi"][0]["atteso"]["valore"], 2)
        # se non torna nemmeno la seconda volta: si salva comunque, segnando, e la verifica lo dice
        dice.profile_path(self.camp).unlink()
        client = ScriptedClient([[_b(type="text", text=json.dumps(bad))], [_b(type="text", text=json.dumps(bad))]])
        profile = dice.extract_profile(self.camp, client, "claude-sonnet-5")
        self.assertEqual(profile["campioni_falliti"], ["tiri[0].esempi[0].atteso.valore: atteso 5, il programma da' 2"])
        from master.validate import validate

        errors = validate(self.camp).errors
        self.assertEqual([(f.title, f.path) for f in errors], [("campione del profilo che non torna", "meccanica/dadi.json:tiri[0].esempi[0].atteso.valore")])


class TestProfile(Base):
    def test_example_campaign_rolls_by_its_own_rules(self) -> None:
        profile = dice.load_profile(self.camp)
        self.assertEqual([k["nome"] for k in profile["tiri"]], ["d20"])
        engine = MasterEngine(self.camp, client=ScriptedClient([]))
        prompt = engine.system_blocks()[0]["text"]
        self.assertIn("[[tiro: <nome> | d20 | bonus <+K> | difficolta <D>]]", prompt)
        self.assertNotIn("riserva", prompt.split("## Dadi")[1].split("Se nessun tipo")[0])

    def test_profile_is_extracted_from_the_prepared_rules(self) -> None:
        dice.profile_path(self.camp).unlink()
        self.assertIsNone(dice.load_profile(self.camp))
        client = ScriptedClient([[_b(type="text", text=json.dumps(POOL))]])
        profile = dice.extract_profile(self.camp, client, "claude-sonnet-5", sheet_json='{"stato":{"fame":2}}')
        call = client.calls[0]
        # il profilo arriva come input di un tool forzato: lo schema e' troppo ricco per la grammatica dell'output strutturato
        self.assertEqual(call["tool_choice"], {"type": "tool", "name": dice.PROFILE_TOOL})
        self.assertEqual(call["tools"][0]["input_schema"], dice.PROFILE_SCHEMA)
        self.assertNotIn("output_config", call)
        self.assertIn("d20 + modificatore contro una difficolta'", call["messages"][0]["content"])  # le regole estratte
        self.assertIn('"fame":2', call["messages"][0]["content"])  # i percorsi della scheda
        self.assertIn("SOLO cio' che le regole", call["system"])
        self.assertEqual(dice.load_profile(self.camp)["tiri"][0]["nome"], "riserva")
        self.assertIn("fonte", profile)
        # una campagna senza regole sui tiri: nessuna chiamata
        empty = self.tmp / "vuota"
        from master import Campaign
        self.assertIsNone(dice.extract_profile(Campaign.create(empty), ScriptedClient([]), "m"))

    def test_roll_from_the_page_goes_to_the_narrator_as_the_player(self) -> None:
        from master.ui import App

        camps = self.tmp / "campaigns"
        shutil.copytree(self.tmp / "camp", camps / "esempio")
        client = ScriptedClient([[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")]])
        app = App(camps, "esempio", client=client)
        self.assertEqual(app.state()["dice"], ["d20"])
        with fixed(12):
            out = app.roll("Scassinare | d20 | bonus +3 | difficolta 15")
        self.assertTrue(out["roll_text"].startswith(
            "[Tiro] Scassinare — 1d20: 12 +3 = 15 -> totale 15 contro difficolta' 15: RIUSCITO DI MISURA (margine 0). Guida: Riesce appena"))
        self.assertTrue(out["roll_success"])
        self.assertTrue(client.calls[0]["messages"][-1]["content"].rstrip().endswith(out["roll_text"]))  # l'esito, in coda al contesto
        self.assertEqual(app.engine.history(1)[0]["player"], out["roll_text"])
        self.assertEqual(sum(1 for c in client.calls if "Sei il master" in str(c.get("system"))), 1)  # tirare non costa chiamate
        self.assertFalse(any("Scegli le sezioni" in str(c.get("system")) for c in client.calls))  # e un esito non si instrada
        with self.assertRaises(ValueError):
            app.roll("x | banana")


class TestClocks(Base):
    def test_clocks_are_counted_by_code_and_delivered_once(self) -> None:
        ex = ToolExecutor(self.camp)
        text, err = ex.execute("set_clock", {"name": "Indagine dello Sceriffo", "segments": 4, "consequence": "Lo Sceriffo bussa alla porta"})
        self.assertFalse(err, text)
        self.assertIn("0/4", text)
        ex.execute("tick_clock", {"name": "indagine dello sceriffo", "amount": 3})
        self.assertEqual(self.camp.clocks_line(), "Indagine dello Sceriffo 3/4")
        self.assertTrue(ex.execute("tick_clock", {"name": "Inesistente"})[1])
        # il narratore li riceve in testa al messaggio del turno, non nel prompt (che resta in cache)
        router = [_b(type="text", text="nessuna")]
        quiet = [_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")]
        client = ScriptedClient([router, [_b(type="text", text=NARRATION + "\n<appunti>\n- orologio: \"Indagine dello Sceriffo\" +1\n</appunti>")],
                                 [_b(type="tool_use", id="c1", name="tick_clock", input={"name": "Indagine dello Sceriffo", "amount": 1})]])
        engine = MasterEngine(self.camp, client=client)

        def narrator() -> dict:
            return [c for c in client.calls if "Sei il master" in str(c.get("system"))][-1]

        engine.play("Esco dal retro.")
        sent = narrator()["messages"][-1]["content"]
        self.assertIn("[FINE DEL CONTESTO. Qui sotto, il turno a cui rispondere.]\n\n[Orologi: Indagine dello Sceriffo 3/4]\nGIOCATORE: Esco dal retro.", sent)
        self.assertEqual(self.camp.read_turns("kael")[0]["player"], "Esco dal retro.")  # nel salvataggio solo le parole del giocatore
        scribe = [c for c in client.calls if "Sei lo scriba" in str(c.get("system"))][-1]
        self.assertIn("OROLOGI ATTIVI: Indagine dello Sceriffo 3/4", scribe["messages"][0]["content"])
        self.assertNotIn("Sceriffo", json.dumps(narrator()["system"], ensure_ascii=False))
        # pieno: la conseguenza arriva al turno dopo, una volta sola, e l'orologio sparisce
        client.script = [router, quiet, [_b(type="text", text="fatto")]]
        engine.play("Torno a casa.")
        self.assertIn("[OROLOGIO PIENO: Indagine dello Sceriffo: Lo Sceriffo bussa alla porta.", narrator()["messages"][-1]["content"])
        self.assertEqual(self.camp.clocks(), {})
        client.script = [router, quiet, [_b(type="text", text="fatto")]]
        engine.play("Apro.")
        self.assertNotIn("Orologi", narrator()["messages"][-1]["content"])
        self.assertNotIn("OROLOGIO", narrator()["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
