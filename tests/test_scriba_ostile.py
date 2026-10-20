"""Lo scriba ostile: un modello finto che rispetta lo schema dei tool e prova a violare le regole.

I test del gioco usano copioni benevoli; qui il copione cerca ogni scappatoia che un modello
vero ha gia' trovato o potrebbe trovare: scrivere l'esperienza per percorso, assegnare punti
senza la riga `pe` del narratore, usare la via `creazione`, far avanzare un orologio che non
esiste, scrivere sulla vista della scheda, uscire dalla campagna, passare `presenti` come
frase invece che come elenco (su Vampiri la scena mostrava "J, o, n, a, t, h, a, n"),
indicizzare nel glossario un PNG mai scritto nel file (su Vampiri: 25 voci cosi'). Ogni
tentativo deve tornare un errore, non lasciare tracce nei file, e il turno deve restare valido.
E' l'idea del fake model "ostile ma schema-valido" di trpg-drama-engine.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from master import MasterEngine  # noqa: E402
from master.sheet import Sheet  # noqa: E402
from master.tools import ToolExecutor  # noqa: E402
from test_economy import NARRATION, Base, ScriptedClient, _b  # noqa: E402
from test_scribe import ROUTER  # noqa: E402


def tool(id_: str, name: str, args: dict | None = None, **kw):
    """Come `test_sheet.tool`, ma gli argomenti del tool vanno in `args`: molti tool hanno un parametro `name`."""
    return _b(type="tool_use", id=id_, name=name, input={**(args or {}), **kw})

NOTES_WITHOUT_XP = f"{NARRATION}\n\n<appunti>\n- diario: Kael parla con Mira\n- nuovo: Jolyne (png), amica di Kayla\n</appunti>"
SCENE_OK = {"luogo": "Lanterna Blu", "quando": "sera", "presenti": ["Mira Sol"], "appena_successo": "Kael ha chiesto della taglia.",
            "domanda_aperta": "Fidarsi di Mira?", "da_richiamare": []}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and "sessioni" not in p.parts}


# Ogni voce: (id, tool, argomenti, perche' va rifiutata)
HOSTILE = [
    ("h01", "update_sheet", {"changes": [{"path": "esperienza.registro.+", "value": {"punti": 50, "via": "narratore", "motivo": "x"}}]},
     "l'esperienza non si scrive per percorso"),
    ("h02", "update_sheet", {"changes": [{"path": "esperienza", "value": {"registro": []}}]}, "nemmeno per intero"),
    ("h03", "award_xp", {"amount": 5, "route": "storia", "reason": "me lo merito"}, "gli appunti non hanno la riga pe"),
    ("h04", "spend_xp", {"amount": 1, "reason": "x", "changes": [{"path": "tratti.caratteristiche.forza", "value": 9}]}, "idem per la spesa"),
    ("h05", "award_xp", {"amount": 5, "route": "creazione", "reason": "punti iniziali"}, "la via creazione e' del costruttore"),
    ("h06", "tick_clock", {"name": "Orologio che non esiste", "amount": 1}, "orologio inesistente"),
    ("h07", "set_clock", {"name": "Troppo", "segments": 20, "consequence": "x"}, "segmenti fuori misura"),
    ("h31", "set_clock", {"name": "Subito", "segments": 4, "consequence": "x", "deadline_turns": 0}, "scadenza a zero turni"),
    ("h32", "set_clock", {"name": "Mai", "segments": 4, "consequence": "x", "deadline_turns": "dieci"}, "scadenza non numerica"),
    ("h08", "append_file", {"name": "schede/kael.md", "text": "Forza 10"}, "la vista della scheda e' generata"),
    ("h09", "set_section", {"name": "schede/kael.md", "section": "Stato", "text": "immortale"}, "idem"),
    ("h10", "replace_text", {"name": "schede/kael.md", "old": "12", "new": "99"}, "idem"),
    ("h11", "append_file", {"name": "../fuori.md", "text": "x"}, "fuori dalla campagna"),
    ("h12", "append_file", {"name": "stato.json", "text": "{}"}, "i JSON non sono file di testo della campagna"),
    ("h13", "set_section", {"name": "meccanica/dadi.json", "section": "x", "text": "y"}, "idem"),
    ("h14", "update_sheet", {"changes": [{"path": "stato.punti_ferita.attuale", "value": 5}, {"path": "equipaggiamento.7.nome", "value": "x"}]},
     "una modifica sbagliata (indice fuori dall'elenco) annulla anche quella buona"),
    ("h15", "update_sheet", {"changes": [{"path": "stato.punti_ferita.attuale", "value": 5}], "pg": "Nessuno"}, "personaggio inesistente"),
    ("h16", "set_scene", {**SCENE_OK, "presenti": "Jonathan; Kayla Ruiz"}, "presenti come frase: iterata lettera per lettera"),
    ("h17", "set_scene", {**SCENE_OK, "da_richiamare": "la lettera sul marmo"}, "idem"),
    ("h18", "set_scene", {**SCENE_OK, "presenti": [{"nome": "Mira"}]}, "elenco di oggetti, non di testi"),
    ("h19", "set_scene", {**SCENE_OK, "appena_successo": ["a", "b"]}, "un testo, non un elenco"),
    ("h20", "set_scene", {k: v for k, v in SCENE_OK.items() if k != "domanda_aperta"}, "chiave obbligatoria mancante"),
    ("h21", "upsert_glossary", {"term": "Jolyne", "kind": "png", "description": "Amica di Kayla", "file": "ambientazione/npc.md"},
     "il PNG non e' scritto nel file"),
    ("h22", "upsert_glossary", {"term": "Rosa Ruiz", "kind": "png", "description": "Madre", "file": "ambientazione/inesistente.md"},
     "il file non esiste"),
    ("h23", "upsert_glossary", {"term": "", "kind": "png", "description": "x", "file": "ambientazione/npc.md"}, "termine vuoto"),
    ("h24", "read_file", {"name": "ambientazione/npc.md"}, "lo scriba non legge"),
    ("h27", "shift_relationship", {"name": "Mira Sol", "verso": "sale", "forza": "enorme", "reason": "x"}, "forza fuori vocabolario"),
    ("h28", "shift_relationship", {"name": "Mira Sol", "verso": "su", "forza": "lieve", "reason": "x"}, "verso fuori vocabolario"),
    ("h29", "shift_relationship", {"name": "Mira Sol", "verso": "sale", "forza": 20, "reason": "x"}, "un numero al posto della forza"),
    ("h30", "shift_relationship", {"name": "  ", "verso": "sale", "forza": "lieve", "reason": "x"}, "PNG senza nome"),
    ("h25", "save_sheet", {"name": "Kael", "summary": "x", "data": {"stato": {}}}, "lo scriba non crea schede"),
    ("h26", "list_files", {}, "lo scriba non legge"),
]


class TestScribaOstile(Base):
    def test_every_hostile_call_is_refused_and_leaves_no_trace(self) -> None:
        before = _snapshot(self.camp.root)
        hostile = [tool(id_, name, args) for id_, name, args, _ in HOSTILE]
        client = ScriptedClient([ROUTER, [_b(type="text", text=NOTES_WITHOUT_XP)], hostile, [_b(type="text", text="mi arrendo")]])
        engine = MasterEngine(self.camp, client=client)
        result = engine.play("Parlo con Mira.")
        # il turno resta valido: la narrazione torna ed e' salvata
        self.assertEqual(result.text, NARRATION)
        self.assertEqual(len(self.camp.read_turns("kael")), 1)
        # il secondo giro dello scriba riceve un errore per ogni tentativo
        retry = [c for c in client.calls if "Sei lo scriba" in str(c.get("system"))][1]
        results = {r["tool_use_id"]: r for r in retry["messages"][2]["content"]}  # [0] il turno, [1] i tentativi, [2] gli esiti
        for id_, name, _, why in HOSTILE:
            with self.subTest(id=id_, tool=name):
                self.assertTrue(results[id_].get("is_error"), f"{name} accettato: {why} -> {results[id_]['content']}")
        self.assertEqual(result.files_changed, [])
        self.assertEqual(_snapshot(self.camp.root), before, "qualche tentativo ha lasciato traccia")
        self.assertEqual(Sheet(self.camp, "kael").xp(), {"guadagnati": 0, "spesi": 0, "disponibili": 0})

    def test_the_same_writes_pass_when_they_follow_the_rules(self) -> None:
        """La controprova: con la riga pe e la sezione scritta prima, le stesse operazioni passano."""
        notes = (f"{NARRATION}\n\n<appunti>\n- nuovo: Jolyne (png), amica di Kayla\n- pe: 2, via storia, chiusa la storia del faro\n"
                 "- relazione: Jolyne sale (media) — Kael l'ha difesa\n</appunti>")
        good = [tool("g1", "set_section", {"name": "ambientazione/npc.md", "section": "Jolyne", "text": "Amica di Kayla, bracciale VIP giallo."}),
                tool("g2", "upsert_glossary", term="Jolyne", kind="png", description="Amica di Kayla", file="ambientazione/npc.md"),
                tool("g3", "award_xp", amount=2, route="storia", reason="Chiusa la storia del faro"),
                tool("g4", "set_scene", **{**SCENE_OK, "presenti": ["Jolyne", "Mira Sol"]}),
                tool("g5", "shift_relationship", {"name": "Jolyne", "verso": "sale", "forza": "media", "reason": "Kael l'ha difesa"})]
        client = ScriptedClient([ROUTER, [_b(type="text", text=notes)], good])
        engine = MasterEngine(self.camp, client=client)
        engine.play("Parlo con Jolyne.")
        self.assertEqual(len([c for c in client.calls if "Sei lo scriba" in str(c.get("system"))]), 1)  # nessun giro di correzione
        self.assertEqual(self.camp.glossary.get("Jolyne").file, "ambientazione/npc.md")
        self.assertEqual(Sheet(self.camp, "kael").xp()["disponibili"], 2)
        self.assertIn("Jolyne, Mira Sol", self.camp.file("ambientazione/scena.md").read())
        self.assertEqual(self.camp.relation("Jolyne"), 8)  # enum in ingresso, numero scritto dal codice
        self.assertTrue(engine.executor.xp_allowed)  # il permesso vale per quel giro, poi si richiude

    def test_glossary_entry_about_the_file_itself_needs_no_term_in_it(self) -> None:
        ex = ToolExecutor(self.camp)
        for kind in ("file", "fonte", "cronaca"):
            text, err = ex.execute("upsert_glossary", {"term": f"Indice {kind}", "kind": kind, "description": "x", "file": "ambientazione/mondo.md"})
            self.assertFalse(err, text)
        text, err = ex.execute("upsert_glossary", {"term": "Mira Sol", "kind": "png", "description": "x", "file": "ambientazione/npc.md"})
        self.assertFalse(err, text)  # il termine c'e': la voce passa (e la maiuscola non conta)


if __name__ == "__main__":
    unittest.main()
