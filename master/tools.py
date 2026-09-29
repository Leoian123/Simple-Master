"""Gli strumenti che l'API puo' chiamare per leggere e modificare la campagna.

Ogni tool e' piccolo e mirato: l'AI chiede *cosa* leggere o *cosa* cambiare,
il codice Python esegue. Cosi' l'AI non riscrive mai interi file.
"""

from __future__ import annotations

import json
from typing import Any

from .campaign import SHEETS_DIR, Campaign
from .glossary import GlossaryEntry
from .sheet import XP_ROUTES, Sheet, slugify

WHOLE_FILE_LIMIT = 24_000  # caratteri: oltre, read_file senza sezione restituisce l'elenco delle sezioni

TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": (
            "Legge un file della campagna. Usalo dopo aver consultato il glossario "
            "per aprire SOLO i file necessari a questo turno. Puoi indicare una "
            "sezione (titolo `## ...`) per leggere solo quella."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome del file con cartella, es. ambientazione/npc.md"},
                "section": {"type": "string", "description": "Titolo di sezione opzionale"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "append_file",
        "description": (
            "Aggiunge testo in coda a un file (lo crea se non esiste). "
            "Usalo per il diario (ambientazione/diario.md): una voce breve per ogni evento rilevante."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "text": {"type": "string", "description": "Testo Markdown da aggiungere"},
            },
            "required": ["name", "text"],
        },
    },
    {
        "name": "set_section",
        "description": (
            "Crea o sostituisce per intero una sezione `## Titolo` di un file. "
            "Usalo per aggiornare un PNG, un luogo, i fili aperti. Non per la scheda del personaggio: quella e' update_sheet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "section": {"type": "string", "description": "Titolo della sezione (senza ##)"},
                "text": {"type": "string", "description": "Nuovo corpo completo della sezione"},
            },
            "required": ["name", "section", "text"],
        },
    },
    {
        "name": "replace_text",
        "description": (
            "Sostituisce la prima occorrenza esatta di un frammento con un altro. "
            "Usalo per piccole modifiche puntuali in un file di testo (es. lo stato di un PNG)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["name", "old", "new"],
        },
    },
    {
        "name": "upsert_glossary",
        "description": (
            "Aggiunge o aggiorna una voce del glossario. Chiamalo ogni volta che "
            "introduci un'entita' nuova (PNG, luogo, oggetto, fazione) o quando la sua "
            "descrizione in una frase cambia. Il campo file indica dove stanno i dettagli."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "kind": {"type": "string", "description": "pg, png, luogo, oggetto, fazione, regola, evento..."},
                "description": {"type": "string", "description": "Una frase sola"},
                "file": {"type": "string", "description": "File con i dettagli, es. ambientazione/npc.md"},
            },
            "required": ["term", "kind", "description", "file"],
        },
    },
    {
        "name": "lookup_glossary",
        "description": (
            "Cerca nel glossario completo e ritorna le voci (termine, tipo, descrizione, file) che "
            "corrispondono. Usalo quando il glossario nel prompt e' in forma di indice compatto e ti "
            "serve la descrizione di un termine o il file giusto, prima di aprire file interi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Termine o parole chiave, es. 'Frenesia' o 'clan Ventrue'"}},
            "required": ["query"],
        },
    },
    {
        "name": "list_files",
        "description": "Elenca i file della campagna con le loro sezioni. Raramente necessario: il glossario basta.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_CHANGES = {
    "type": "array",
    "description": "Modifiche ai dati della scheda, applicate tutte insieme",
    "items": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Percorso con i punti, es. 'stato.fame', 'stato.salute.attuale', 'tratti.abilita.rissa', 'equipaggiamento.+' per aggiungere a un elenco"},
            "value": {"description": "Nuovo valore JSON (numero, testo, elenco, oggetto). null cancella la voce"},
        },
        "required": ["path", "value"],
    },
}
_PG = {"type": "string", "description": "Personaggio, solo se diverso da quello attivo"}

SHEET_TOOLS: list[dict[str, Any]] = [
    {
        "name": "update_sheet",
        "description": (
            "Cambia i dati della scheda del personaggio (il JSON in PERSONAGGIO ATTIVO) per percorso: danni, "
            "risorse, condizioni, equipaggiamento. Una sola chiamata con tutte le modifiche del turno. "
            "Non tocca l'esperienza: per quella ci sono award_xp e spend_xp."
        ),
        "input_schema": {"type": "object", "properties": {"changes": _CHANGES, "pg": _PG}, "required": ["changes"]},
    },
    {
        "name": "award_xp",
        "description": (
            "Assegna punti esperienza al personaggio e li scrive nel registro della scheda. Il conto lo tiene il "
            "codice: non sommare a mente, il risultato ti dice guadagnati, spesi e disponibili. Vie: "
            + "; ".join(f"`{k}` = {v}" for k, v in XP_ROUTES.items() if k != "creazione") + "."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "minimum": 1},
                "route": {"type": "string", "enum": [k for k in XP_ROUTES if k != "creazione"]},
                "reason": {"type": "string", "description": "Perche', in una riga (per `regola`: quale regola)"},
                "pg": _PG,
            },
            "required": ["amount", "route", "reason"],
        },
    },
    {
        "name": "spend_xp",
        "description": (
            "Spende punti esperienza per far crescere un tratto: controlla che bastino, applica le modifiche alla "
            "scheda e registra la spesa, tutto insieme. Il costo lo calcoli tu dalle regole in meccanica/ (leggile "
            "se non le hai davanti); se i punti non bastano la spesa viene rifiutata."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "description": "Cosa si compra e il calcolo del costo, es. 'Fermezza 2->3: 5 x 3'"},
                "changes": _CHANGES,
                "pg": _PG,
            },
            "required": ["amount", "reason", "changes"],
        },
    },
]
TOOLS += SHEET_TOOLS

CLOCK_TOOLS: list[dict[str, Any]] = [
    {
        "name": "set_clock",
        "description": (
            "Crea o aggiorna un orologio: una minaccia o un progetto che avanza a segmenti (es. 'La guardia "
            "indaga', 6 segmenti). `consequence` e' cio' che accade quando si riempie. segments 0 lo toglie."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "segments": {"type": "integer", "minimum": 0, "maximum": 12, "description": "4 breve, 6 normale, 8 lungo; 0 = togli"},
                "consequence": {"type": "string", "description": "Cosa succede quando e' pieno, in una frase"},
                "filled": {"type": "integer", "minimum": 0, "description": "Segmenti gia' pieni (di solito 0)"},
            },
            "required": ["name", "segments"],
        },
    },
    {
        "name": "tick_clock",
        "description": "Fa avanzare (o arretrare, con un numero negativo) un orologio esistente. Il conto lo tiene il codice.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "amount": {"type": "integer", "description": "Di solito 1; 2-3 per un fatto grave"}},
            "required": ["name"],
        },
    },
]
TOOLS += CLOCK_TOOLS

# Solo per lo scriba del livello veloce: la scena e' il documento da cui riparte il turno dopo.
SCENE_TOOL: dict[str, Any] = {
    "name": "set_scene",
    "description": "Riscrive il documento della scena in corso, com'e' ALLA FINE di questo turno. Va chiamato a ogni turno.",
    "input_schema": {
        "type": "object",
        "properties": {
            "luogo": {"type": "string"},
            "quando": {"type": "string", "description": "Giorno/notte e ora, come risulta dalla narrazione"},
            "presenti": {"type": "array", "items": {"type": "string"}, "description": "I PNG in scena, con il nome usato nella narrazione"},
            "appena_successo": {"type": "string", "description": "2-4 frasi: i fatti di questo turno, comprese le azioni del giocatore"},
            "domanda_aperta": {"type": "string", "description": "Cio' che il giocatore deve decidere adesso"},
            "da_richiamare": {"type": "array", "items": {"type": "string"}, "description": "Fino a 6 dettagli concreti da tenere vivi: frasi dette, oggetti, promesse. Tieni quelli ancora utili, togli i superati"},
        },
        "required": ["luogo", "quando", "presenti", "appena_successo", "domanda_aperta", "da_richiamare"],
    },
}

SAVE_SHEET_TOOL: dict[str, Any] = {
    "name": "save_sheet",
    "description": (
        "Salva la scheda completa di un personaggio giocante come dati in schede/<nome>.json (la vista leggibile "
        "schede/<nome>.md viene generata da sola) e la registra nel glossario. Chiamalo solo dopo la conferma del giocatore."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Nome del personaggio"},
            "summary": {"type": "string", "description": "Una frase per il glossario, es. 'Ladra elfa di Velmora'"},
            "data": {"type": "object", "description": "I dati della scheda, secondo le convenzioni indicate nel prompt"},
            "starting_xp": {"type": "integer", "minimum": 0, "description": "Punti esperienza concessi dalla creazione e NON ancora spesi (di solito 0)"},
        },
        "required": ["name", "summary", "data"],
    },
}

# Il costruttore di schede legge, elenca e salva: non tocca diario o mondo.
BUILDER_TOOLS: list[dict[str, Any]] = [t for t in TOOLS if t["name"] in {"read_file", "list_files", "lookup_glossary"}] + [SAVE_SHEET_TOOL]


class ToolExecutor:
    """Esegue le chiamate tool sulla campagna e tiene traccia di cosa e' stato toccato."""

    def __init__(self, campaign: Campaign):
        self.campaign = campaign
        self.files_read: list[str] = []
        self.files_changed: list[str] = []

    def reset(self) -> None:
        self.files_read.clear()
        self.files_changed.clear()

    def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Ritorna (testo_risultato, is_error)."""
        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            return f"Tool sconosciuto: {name}", True
        try:
            return handler(**args), False
        except (ValueError, FileNotFoundError, TypeError) as exc:
            return f"Errore: {exc}", True

    # --- handler ---------------------------------------------------------

    def _touch(self, seen: list[str], name: str) -> None:
        if name not in seen:
            seen.append(name)

    def _do_read_file(self, name: str, section: str | None = None) -> str:
        f = self.campaign.file(name)
        if section:
            body = f.get_section(section)
            if body is None:
                close = [t for t in f.outline() if section.lower() in t.lower() or t.lower() in section.lower()]
                if close:
                    # un titolo quasi giusto ("Fame" per "La Fame") non vale un giro di API in piu':
                    # si restituiscono le sezioni piu' vicine, le piu' brevi di nome per prime
                    parts, size = [], 0
                    for t in sorted(close, key=len)[:3]:
                        text = f.get_section(t) or ""
                        if parts and size + len(text) > 12_000:
                            break
                        parts.append(f"## {t}\n\n{text}")
                        size += len(text)
                    self._touch(self.files_read, f.name)
                    return f"(Sezione '{section}' non esiste in {f.name}: ecco le piu' vicine.)\n\n" + "\n\n".join(parts)
                hint = f.outline()[:40]
                raise ValueError(f"Sezione '{section}' non trovata in {f.name}. Sezioni simili o disponibili: {hint}")
        else:
            body = f.read()
            if len(body) > WHOLE_FILE_LIMIT:
                # un file di manuale letto intero sono decine di migliaia di token a ogni chiamata del turno
                titles = f.outline()
                self._touch(self.files_read, f.name)
                return (f"{f.name} e' grande ({len(body):,} caratteri, {len(titles)} sezioni): non lo leggo intero. "
                        "Richiama read_file indicando `section`. Sezioni:\n" + "\n".join(f"- {t}" for t in titles)).replace(",", ".", 1)
        self._touch(self.files_read, f.name)
        return body

    def _not_a_sheet_view(self, name: str) -> None:
        """La vista .md di una scheda con dati JSON e' generata: scriverci sopra andrebbe perso."""
        clean = name.strip().replace(chr(92), "/")
        if clean.startswith(SHEETS_DIR + "/") and clean.endswith(".md") and Sheet(self.campaign, clean[len(SHEETS_DIR) + 1:-3]).exists:
            raise ValueError(f"{clean} e' la vista generata dalla scheda: i dati si cambiano con update_sheet "
                             "(percorsi come 'stato.fame'), l'esperienza con award_xp / spend_xp")

    def _sheet(self, pg: str | None = None) -> Sheet:
        slug = slugify(pg) if pg else self.campaign.active_pg
        if not slug:
            raise ValueError("Nessun personaggio attivo: indica `pg`")
        sheet = Sheet(self.campaign, slug)
        if not sheet.exists:
            raise ValueError(f"{SHEETS_DIR}/{sheet.slug}.json non esiste: questa scheda e' ancora solo testo. "
                             f"Aggiornala con set_section / replace_text su {sheet.view_name}")
        return sheet

    def _sheet_saved(self, sheet: Sheet) -> None:
        sheet.save()
        self._touch(self.files_changed, f"{SHEETS_DIR}/{sheet.slug}.json")

    def _do_append_file(self, name: str, text: str) -> str:
        self._not_a_sheet_view(name)
        f = self.campaign.file(name)
        f.append(text)
        self._touch(self.files_changed, f.name)
        return f"Aggiunto a {f.name}"

    def _do_set_section(self, name: str, section: str, text: str) -> str:
        self._not_a_sheet_view(name)
        f = self.campaign.file(name)
        what = f.set_section(section, text)
        self._touch(self.files_changed, f.name)
        return f"Sezione '{section}' {what} in {f.name}"

    def _do_replace_text(self, name: str, old: str, new: str) -> str:
        self._not_a_sheet_view(name)
        f = self.campaign.file(name)
        if not f.replace(old, new):
            raise ValueError(f"Frammento non trovato in {f.name}: {old!r}")
        self._touch(self.files_changed, f.name)
        return f"Sostituito in {f.name}"

    def _do_upsert_glossary(self, term: str, kind: str, description: str, file: str) -> str:
        self.campaign.resolve(file)  # valida il nome
        what = self.campaign.glossary.upsert(GlossaryEntry(term, kind, description, file))
        self._touch(self.files_changed, self.campaign.glossary.file.name)
        return f"Glossario: '{term}' {what}"

    def _do_list_files(self) -> str:
        return self.campaign.index() or "(nessun file)"

    def _do_lookup_glossary(self, query: str) -> str:
        found = self.campaign.glossary.lookup(query)
        if not found:
            return f"Nessuna voce per '{query}'. Prova con un'altra parola o con list_files."
        return "\n".join(f"- {e.term} | {e.kind} | {e.description} | {e.file}" for e in found)

    def _do_update_sheet(self, changes: list[dict[str, Any]], pg: str | None = None) -> str:
        sheet = self._sheet(pg)
        done = sheet.apply(changes)
        self._sheet_saved(sheet)
        return f"Scheda di {sheet.name} aggiornata: " + "; ".join(done)

    @staticmethod
    def _xp_line(xp: dict[str, int]) -> str:
        return f"Esperienza ora: disponibili {xp['disponibili']} (guadagnati {xp['guadagnati']}, spesi {xp['spesi']})"

    def _do_award_xp(self, amount: int, route: str, reason: str, pg: str | None = None) -> str:
        sheet = self._sheet(pg)
        xp = sheet.award(amount, route, reason)
        self._sheet_saved(sheet)
        return f"+{int(amount)} a {sheet.name} (via {route}, sessione {self.campaign.session_number}). {self._xp_line(xp)}"

    def _do_spend_xp(self, amount: int, reason: str, changes: list[dict[str, Any]], pg: str | None = None) -> str:
        sheet = self._sheet(pg)
        xp, done = sheet.spend(amount, reason, changes)
        self._sheet_saved(sheet)
        return f"-{int(amount)} a {sheet.name}: " + "; ".join(done) + f". {self._xp_line(xp)}"

    def _do_set_scene(self, **scene: Any) -> str:
        from .memory import SCENE, write_scene

        write_scene(self.campaign, scene, "aggiornato dallo scriba a fine turno")
        self._touch(self.files_changed, SCENE)
        return "Scena aggiornata"

    def _do_set_clock(self, name: str, segments: int, consequence: str = "", filled: int = 0) -> str:
        clock = self.campaign.set_clock(name, segments, consequence, filled)
        self._touch(self.files_changed, "stato.json")
        return f"Orologio '{name}': {clock['pieni']}/{clock['segmenti']}" if clock else f"Orologio '{name}' tolto"

    def _do_tick_clock(self, name: str, amount: int = 1) -> str:
        clock = self.campaign.tick_clock(name, amount)
        self._touch(self.files_changed, "stato.json")
        full = clock["pieni"] >= clock["segmenti"]
        return (f"Orologio '{clock['nome']}': {clock['pieni']}/{clock['segmenti']}"
                + (" - PIENO: la conseguenza verra' consegnata al narratore al prossimo turno" if full else ""))

    def _do_save_sheet(self, name: str, summary: str, data: dict[str, Any], starting_xp: int = 0) -> str:
        sheet = Sheet(self.campaign, name)
        sheet.replace(data, name=name)
        if starting_xp and not sheet.xp()["guadagnati"]:
            sheet.award(starting_xp, "creazione", "Punti della creazione non ancora spesi")
        self._sheet_saved(sheet)
        self.campaign.glossary.upsert(GlossaryEntry(name.strip(), "pg", summary.strip(), sheet.view_name))
        self._touch(self.files_changed, sheet.view_name)
        self._touch(self.files_changed, self.campaign.glossary.file.name)
        return f"Scheda salvata in {SHEETS_DIR}/{sheet.slug}.json (vista {sheet.view_name}) e registrata nel glossario"


def tool_result_block(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def dumps(obj: Any) -> str:  # pragma: no cover - utilita' per il debug
    return json.dumps(obj, ensure_ascii=False, indent=2)
