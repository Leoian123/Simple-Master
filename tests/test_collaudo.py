"""Collaudo a secco della preparazione v2: un manuale inventato entra, una campagna in dati esce, e si gioca.

Nessuna chiamata vera: il modello e' un copione. Quello che si prova e' il filo intero, che nessun test
di modulo copre: dal testo del manuale ai file (regole, glossario, dadi, tabelle, prefazione, trigger), dalla
verifica muta al turno in cui il narratore chiede una tabella, il programma la tira, e una soglia della
scheda arriva al narratore come trigger. Il gioco e' inventato apposta: il codice non ne conosce nessuno.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import Campaign, MasterEngine, dice, prefazione, tabelle, trigger  # noqa: E402
from master.prepare import Preparer  # noqa: E402
from master.validate import validate  # noqa: E402
from test_dice import fixed  # noqa: E402
from test_economy import NARRATION, ScriptedClient, _b  # noqa: E402
from test_mutazioni import PROFILO  # noqa: E402
from test_prepare import FakePrepClient  # noqa: E402
from test_scribe import ROUTER  # noqa: E402

MANUALE = """\
CRONACHE DELLA MAREA - regolamento

Prove. Quando l'esito e' incerto il giocatore tira una riserva di d10 pari al tratto: ogni 6 o piu' e' un successo,
la difficolta' dice quanti ne servono (facile 1, media 2, dura 3). Due 10 valgono un successo in piu'.

Ferite. Quando i punti ferita scendono sotto 5 ogni prova perde un dado; a 0 il personaggio cade e la marea decide.

Incontri in mare. Ogni giorno di navigazione tira 1d6:
| 1-3 | Mare calmo |
| 4-5 | Una vela all'orizzonte |
| 6 | Il Gorgo |

La Marea. Il mondo e' un arcipelago che la marea scopre e copre; le rotte cambiano con la luna.
"""

SEZIONI = [
    {"file": "meccanica/regole.md", "titolo": "Prove",
     "testo": "Riserva di d10 pari al tratto; ogni 6 o piu' e' un successo, la difficolta' dice quanti ne servono (facile 1, media 2, dura 3). Due 10 valgono un successo in piu'."},
    {"file": "meccanica/regole.md", "titolo": "Ferite",
     "testo": "Quando i punti ferita scendono sotto 5 ogni prova perde un dado; a 0 il personaggio cade e la marea decide."},
    {"file": "meccanica/regole.md", "titolo": "Incontri in mare",
     "testo": "Ogni giorno di navigazione tira 1d6:\n\n| 1-3 | Mare calmo |\n| 4-5 | Una vela all'orizzonte |\n| 6 | Il Gorgo |"},
    {"file": "ambientazione/mondo.md", "titolo": "La Marea", "testo": "Un arcipelago che la marea scopre e copre; le rotte cambiano con la luna."},
]
GLOSSARIO = [
    {"term": "Prove", "kind": "regola", "description": "Riserve di d10, successi dal 6", "file": "meccanica/regole.md"},
    {"term": "Ferite", "kind": "regola", "description": "Sotto 5 punti ferita si perde un dado", "file": "meccanica/regole.md"},
    {"term": "Incontri in mare", "kind": "regola", "description": "Tabella giornaliera a 1d6", "file": "meccanica/regole.md"},
    {"term": "La Marea", "kind": "luogo", "description": "L'arcipelago che cambia con la luna", "file": "ambientazione/mondo.md"},
]
TABELLE = {"tabelle": [{"nome": "incontri in mare", "uso": "ogni giorno di navigazione", "dado": "1d6", "fonte": "meccanica/regole.md: Incontri in mare",
                        "righe": [{"da": 1, "a": 3, "risultato": "Mare calmo"}, {"da": 4, "a": 5, "risultato": "Una vela all'orizzonte"},
                                  {"da": 6, "a": 6, "risultato": "Il Gorgo"}]}]}
PREFAZIONE = {"precedenza": ["La scheda del personaggio sulle regole generali", "Una regola specifica su una generale"],
              "cercare": [{"argomento": "Le difficolta' delle prove", "file": "meccanica/regole.md", "sezione": "Prove"},
                          {"argomento": "Gli incontri", "file": "meccanica/regole.md", "sezione": "Incontri in mare"},
                          {"argomento": "Veleni", "file": "meccanica/veleni.md", "sezione": "Tabella"}],  # inventato: va scartato
              "riserva": ["Se una regola manca, una prova a difficolta' media"]}
TRIGGER = {"trigger": [
    {"nome": "ferito", "ricorrente": True, "fonte": "Ferite",
     "quando": [{"tipo": "scheda", "soggetto": "stato.punti_ferita.attuale", "op": "<", "valore": "5"}],
     "effetti": [{"tipo": "avviso", "testo": "Sotto 5 punti ferita ogni prova perde un dado", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}]},
    {"nome": "in pericolo", "ricorrente": True, "fonte": "Ferite",  # un giudizio travestito da percorso: la scheda non ce l'ha
     "quando": [{"tipo": "scheda", "soggetto": "stato.pericolo", "op": "==", "valore": "si"}],
     "effetti": [{"tipo": "avviso", "testo": "x", "avanza": None, "segmenti": None, "conseguenza": None, "verso": None, "forza": None}]},
]}


def profilo_marea() -> dict:
    """Il profilo del gioco inventato: riserva di d10 (i dadi speciali contati da un percorso che la scheda ha) e la prova a d20."""
    kinds = json.loads(json.dumps([k for k in PROFILO["tiri"] if k["nome"] in ("riserva", "prova")]))
    kinds[0]["speciali"] = {"nome": "marea", "quanti_da": "stato.monete", "sostituiscono": True}
    kinds[0]["esempi"] = [{**e, "marcatore": e["marcatore"].replace("fame", "marea")} for e in kinds[0]["esempi"]]
    return {"sistema": "Cronache della Marea", "note": PROFILO["note"], "tiri": kinds}


class ClientMarea(FakePrepClient):
    """Il copione del manuale inventato: estrazione, dadi, tabelle, prefazione, trigger."""

    def __init__(self) -> None:
        super().__init__()
        self.meta = {"titolo": "Cronache della Marea", "gioco": "Cronache della Marea", "edizione": None, "anno": None}
        self.dice_calls: list[dict] = []
        self.table_calls: list[dict] = []
        self.preface = PREFAZIONE
        self.triggers = TRIGGER

    def _create(self, **kwargs):
        usage = SimpleNamespace(input_tokens=100, output_tokens=50)
        system = str(kwargs.get("system"))
        answer = None
        if "PROFILO DEI DADI" in system:
            self.dice_calls.append(kwargs)
            answer = profilo_marea()
        elif "TABELLE CASUALI" in system:
            self.table_calls.append(kwargs)
            answer = TABELLE
        if answer is not None:
            return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[_b(type="text", text=json.dumps(answer, ensure_ascii=False))])
        user_text = kwargs["messages"][0]["content"]
        if isinstance(user_text, list):
            user_text = " ".join(c.get("text", "") for c in user_text)
        if "Blocco:" in user_text:  # l'estrazione: tutto il manuale sta in un blocco
            self.calls.append(kwargs)
            data = {"riassunto": "Regole e mondo delle Cronache della Marea.", "sezioni": SEZIONI, "glossario": GLOSSARIO}
            return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[_b(type="text", text=json.dumps(data, ensure_ascii=False))])
        return super()._create(**kwargs)


class TestCollaudo(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "campaigns" / "esempio", self.tmp / "camp", ignore=shutil.ignore_patterns("sessioni", "preparazione", "stato.json"))
        self.camp = Campaign(self.tmp / "camp")
        for name in ("meccanica/regole.md", "meccanica/creazione-pg.md", "ambientazione/mondo.md"):
            if self.camp.file(name).exists():
                self.camp.file(name).write(f"# {name.split('/')[-1][:-3].capitalize()}\n")
        self.manual = self.tmp / "marea.txt"
        self.manual.write_text(MANUALE, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_from_the_manual_to_a_campaign_in_data_to_a_played_turn(self) -> None:
        client = ClientMarea()
        report = Preparer(self.camp, client=client, cache_root=self.tmp / "cache").run(
            str(self.manual), consolidate=False, tidy=False, progress=lambda s: None)

        # 1. i file: regole in sezioni, glossario, e i quattro dati che il programma gestisce
        self.assertEqual(self.camp.file("meccanica/regole.md").outline(), [prefazione.SECTION_TITLE, "Prove", "Ferite", "Incontri in mare"])
        self.assertEqual(self.camp.glossary.get("Incontri in mare").kind, "regola")
        self.assertEqual([k["nome"] for k in dice.load_profile(self.camp)["tiri"]], ["riserva", "prova"])
        self.assertEqual([t["nome"] for t in tabelle.carica(self.camp)], ["incontri in mare"])
        self.assertEqual([c for _, _, c in prefazione.lookups(prefazione.leggi(self.camp))], ["Prove", "Incontri in mare"])  # il rimando inventato e' sparito
        self.assertEqual([t["nome"] for t in trigger.carica(self.camp)], ["ferito"])  # 'in pericolo' legge un percorso che la scheda non ha
        self.assertEqual((len(client.dice_calls), len(client.table_calls), len(client.preface_calls), len(client.trigger_calls)), (1, 1, 1, 1))
        self.assertIn("Dati che il programma gestisce: dadi: riserva, prova | tabelle: 1 | prefazione: 2 rimandi | trigger: 1", report.summary())
        self.assertIn("stato.punti_ferita.attuale", client.trigger_calls[0]["messages"][0]["content"])

        # 2. la verifica: muta sugli errori, e non chiede piu' di ricavare nulla
        found = validate(self.camp)
        self.assertEqual(found.errors, [])
        self.assertEqual([f.title for f in found.recommendations if "ricavat" in f.title or "prefazione" in f.title], [])

        # 3. lo stesso manuale di nuovo: nessuna chiamata, nessun doppione
        before = (len(client.calls), len(client.dice_calls), len(client.table_calls), len(client.preface_calls), len(client.trigger_calls))
        Preparer(self.camp, client=client, cache_root=self.tmp / "cache").run(str(self.manual), consolidate=False, tidy=False, progress=lambda s: None)
        self.assertEqual((len(client.calls), len(client.dice_calls), len(client.table_calls), len(client.preface_calls), len(client.trigger_calls)), before)
        self.assertEqual(self.camp.file("meccanica/regole.md").outline().count("Prove"), 1)

        # 4. il turno: il prompt in cache porta tabelle e prefazione; il narratore chiede la tabella, il programma la tira
        narrate = [_b(type="text", text=NARRATION + "\n[[tabella: incontri in mare]]\n<appunti>nulla</appunti>")]
        done = [_b(type="text", text="fatto")]
        play = ScriptedClient([ROUTER, narrate, done])
        engine = MasterEngine(self.camp, client=play)
        system = engine.system_blocks()[0]["text"]
        self.assertIn("- `[[tabella: incontri in mare]]` (1d6) — ogni giorno di navigazione", system)
        self.assertIn("- Le difficolta' delle prove: `meccanica/regole.md` § Prove", system)
        self.assertIn("`riserva`", system)
        turn = engine.play("Salpo all'alba.")
        self.assertEqual(tabelle.find_marker(turn.text), "incontri in mare")
        with fixed(6):
            rolled = tabelle.tira("incontri in mare", tabelle.carica(self.camp))
        self.assertEqual(rolled["text"], "[Tabella] incontri in mare — 1d6: 6 -> 6: Il Gorgo")
        play.script = [[_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], done]
        engine.play(rolled["text"])
        narrator = [c for c in play.calls if "Sei il master" in str(c.get("system"))]
        self.assertTrue(narrator[-1]["messages"][-1]["content"].rstrip().endswith(rolled["text"]))
        self.assertFalse(any("Scegli le sezioni" in str(c.get("system")) for c in play.calls[len(play.calls) - 2:]))  # l'esito non si instrada

        # 5. la soglia della scheda arriva al narratore come trigger, dal contatore e non dalla memoria
        from master.sheet import Sheet

        sheet = Sheet(self.camp, self.camp.active_sheet()["slug"])
        sheet.set("stato.punti_ferita.attuale", 3)
        sheet.save()
        play.script = [ROUTER, [_b(type="text", text=NARRATION + "\n<appunti>nulla</appunti>")], done]
        engine.play("Mi trascino a prua.")
        narrator = [c for c in play.calls if "Sei il master" in str(c.get("system"))]
        self.assertIn("[TRIGGER ferito: Sotto 5 punti ferita ogni prova perde un dado]", narrator[-1]["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
