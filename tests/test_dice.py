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
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
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
        self.assertEqual(out["roll_text"], "[Tiro] Scassinare — 1d20: 12 +3 = 15 -> totale 15 contro difficolta' 15: RIUSCITO (margine 0).")
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
