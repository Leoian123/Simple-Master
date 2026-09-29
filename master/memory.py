"""Il livello lento della narrazione: consolidare la memoria dal giocato grezzo.

Due livelli. Quello veloce gioca i turni e lascia appunti (narratore + scriba). Quello lento,
a comando o alla chiusura di una sessione, rilegge il giocato GREZZO, che il programma copia
gia' a ogni turno in `sessioni/<pg>.jsonl` (e' il "watchdog": codice, nessuna chiamata), e lo
converte in memoria affidabile:

    - cronaca: unita' di evento brevi con il turno d'origine ([t12]), non un riassunto in prosa.
      I riassunti perdono proprio cio' che il giocatore ha detto e fatto; le unita' con la fonte no.
      Sostituiscono nel diario gli appunti provvisori dello scriba: una sola copia dei fatti.
    - fili aperti aggiornati;
    - memoria dei PNG: cosa sa, cosa crede (anche il falso), cosa vuole;
    - documento di scena: dove siamo rimasti, chi c'e', cosa richiamare alla ripresa;
    - incoerenze tra il giocato e i file, con la correzione (il giocato fa fede).

Una chiamata sola con uscita strutturata; le scritture le fa il codice. Cio' che e' gia'
consolidato non si ripaga: `stato.json` ricorda fino a che turno si e' arrivati.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .campaign import DIARY, SETTING_DIR, Campaign
from .logbook import describe_error, usage_line
from .models import cost_usd
from .sheet import Sheet

log = logging.getLogger("master.memoria")

NOTES_SECTION = "Appunti"  # le righe provvisorie dello scriba, in attesa del consolidamento
NPC_MEMORY = f"{SETTING_DIR}/png-memoria.md"
SCENE = f"{SETTING_DIR}/scena.md"
ADVENTURE = f"{SETTING_DIR}/avventura.md"
MAX_TURN_CHARS = 6_000
MAX_OUTPUT_TOKENS = 24_000   # a 8000 la prima sessione reale (9 turni) si e' troncata: il ragionamento conta come uscita
DEFAULT_MEMORY_EFFORT = "low"  # e' un lavoro di trascrizione ordinata, non di invenzione
DEFAULT_MEMORY_MODEL = "claude-sonnet-5"

MEMORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "eventi": {
            "type": "array",
            "description": "Unita' di evento, in ordine. Una frase ciascuna, autosufficiente, con i nomi per esteso.",
            "items": {
                "type": "object",
                "properties": {"turno": {"type": "integer", "description": "Il numero del turno da cui viene"},
                               "testo": {"type": "string"}},
                "required": ["turno", "testo"],
                "additionalProperties": False,
            },
        },
        "fili_aperti": {"type": "string", "description": "Il testo intero e aggiornato dei fili aperti, 3-8 righe con '- '. Segreti del narratore compresi."},
        "png": {
            "type": "array",
            "description": "Solo i PNG comparsi o toccati in questi turni",
            "items": {
                "type": "object",
                "properties": {"nome": {"type": "string"}, "sa": {"type": "string", "description": "Cosa sa del personaggio e dei fatti"},
                               "crede": {"type": "string", "description": "Cosa crede, anche se falso (bugie a cui ha creduto); vuoto se nulla"},
                               "vuole": {"type": "string", "description": "Cosa vuole adesso e cosa fara' se nessuno lo ferma"}},
                "required": ["nome", "sa", "crede", "vuole"],
                "additionalProperties": False,
            },
        },
        "scena": {
            "type": "object",
            "properties": {"luogo": {"type": "string"}, "quando": {"type": "string"},
                           "presenti": {"type": "array", "items": {"type": "string"}},
                           "appena_successo": {"type": "string", "description": "2-4 frasi"},
                           "domanda_aperta": {"type": "string", "description": "Cio' che il giocatore stava per decidere"},
                           "da_richiamare": {"type": "array", "items": {"type": "string"}, "description": "Dettagli concreti, frasi dette, oggetti: cio' che da' continuita' alla prosa"}},
            "required": ["luogo", "quando", "presenti", "appena_successo", "domanda_aperta", "da_richiamare"],
            "additionalProperties": False,
        },
        "incoerenze": {
            "type": "array",
            "description": "Contraddizioni tra il giocato e i documenti forniti. Vuoto se non ce ne sono.",
            "items": {
                "type": "object",
                "properties": {"descrizione": {"type": "string"}, "correzione": {"type": "string", "description": "Come va inteso d'ora in poi: il giocato fa fede"}},
                "required": ["descrizione", "correzione"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["eventi", "fili_aperti", "png", "scena", "incoerenze"],
    "additionalProperties": False,
}

MEMORY_PROMPT = """\
Sei la memoria lenta di una campagna di gioco di ruolo. Ricevi il GIOCATO GREZZO (i turni come sono stati
scritti, numerati) e i documenti attuali della campagna. Converti il giocato in memoria affidabile.
- Lavora solo su cio' che e' scritto: non inventare, non abbellire, non anticipare la trama.
- Gli eventi sono fatti, non riassunti d'atmosfera: chi ha fatto cosa a chi, dove, con quale esito, cosa e'
  stato detto o promesso, tiri ed esiti che hanno avuto conseguenze. Cio' che il GIOCATORE ha dichiarato e
  fatto va conservato con precisione: e' la prima cosa che i riassunti perdono. Uno o due eventi per turno, di
  una frase sola; nessuno per i turni in cui non accade nulla.
- Le istruzioni del tavolo (aperture e chiusure tra parentesi quadre) non sono eventi.
- Fili aperti: parti dal testo attuale, togli cio' che si e' risolto, aggiorna cio' che si e' mosso, aggiungi
  cio' che e' nato. I segreti restano segreti.
- PNG: separa cio' che sanno da cio' che credono. Una bugia creduta resta creduta finche' qualcosa la smentisce.
- Incoerenze: segnala solo contraddizioni vere tra il giocato e i documenti (o dentro il giocato), con la
  lettura da tenere d'ora in poi. Il giocato fa fede sui documenti.
Italiano asciutto, frasi brevi."""


@dataclass
class MemoryReport:
    turns: int = 0
    events: int = 0
    npcs: list[str] = field(default_factory=list)
    inconsistencies: list[dict[str, str]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    files_changed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.turns:
            return "Memoria gia' aggiornata: nessun turno nuovo da consolidare."
        text = f"Memoria consolidata: {self.turns} turni -> {self.events} eventi"
        if self.npcs:
            text += f", PNG: {', '.join(self.npcs)}"
        if self.inconsistencies:
            text += f", {len(self.inconsistencies)} incoerenze sanate"
        return text + f" (costo ≈ ${self.cost_usd:.3f})."


def write_scene(campaign: Campaign, scene: dict[str, Any], note: str = "") -> None:
    """`ambientazione/scena.md`: dove si e', chi c'e', cosa e' appena successo, cosa richiamare."""
    present = ", ".join(map(str, scene.get("presenti") or [])) or "nessun altro"
    recall = "\n".join(f"- {d}" for d in scene.get("da_richiamare") or [])
    campaign.file(SCENE).write(
        "# Scena in corso\n\n" + (f"*Dove si e' rimasti, {note}.*\n\n" if note else "")
        + f"## Dove e quando\n\n{scene.get('luogo', '')} — {scene.get('quando', '')}\n\n## Presenti\n\n{present}\n\n"
        f"## Appena successo\n\n{scene.get('appena_successo', '')}\n\n## Domanda aperta\n\n{scene.get('domanda_aperta', '')}\n"
        + (f"\n## Da richiamare\n\n{recall}\n" if recall else ""))


class Consolidator:
    def __init__(self, campaign: Campaign, client: Any, model: str | None = None, effort: str | None = None):
        self.campaign = campaign
        self.client = client
        self.model = model or DEFAULT_MEMORY_MODEL
        self.effort = effort or DEFAULT_MEMORY_EFFORT

    # --- cosa c'e' da consolidare ---------------------------------------------------------------

    def pending(self, pg: str) -> tuple[int, list[dict]]:
        turns = self.campaign.all_turns(pg)
        done = min(self.campaign.consolidated(pg), len(turns))
        return done, turns[done:]

    def _documents(self, name: str, slug: str) -> str:
        camp = self.campaign
        parts = []
        adventure = camp.file(ADVENTURE)
        threads = adventure.get_section(f"Fili aperti di {name}") if adventure.exists() else None
        parts.append("FILI APERTI ATTUALI:\n" + (threads.strip() if threads else "(nessuno)"))
        memory = camp.file(NPC_MEMORY)
        if memory.exists():
            parts.append("MEMORIA DEI PNG ATTUALE:\n" + memory.read().strip())
        diary = camp.file(DIARY)
        if diary.exists():
            parts.append("DIARIO (ultima parte):\n" + diary.read().strip()[-3000:])
        sheet = Sheet(camp, slug)
        if sheet.exists:
            parts.append("SCHEDA DEL PERSONAGGIO (JSON):\n" + sheet.prompt_json())
        clocks = camp.clocks_line()
        if clocks:
            parts.append("OROLOGI: " + clocks)
        return "\n\n".join(parts)

    # --- consolidamento -------------------------------------------------------------------------

    def run(self, progress=lambda text: None) -> MemoryReport:
        report = MemoryReport()
        active = self.campaign.active_sheet()
        if active is None:
            raise ValueError("Nessun personaggio attivo: non c'e' un giocato da consolidare")
        name, slug = active["name"], active["slug"]
        done, turns = self.pending(slug)
        if not turns:
            return report
        raw = "\n\n".join(f"[t{done + i + 1}] GIOCATORE: {t['player'][:MAX_TURN_CHARS]}\n[t{done + i + 1}] NARRATORE: {t['master'][:MAX_TURN_CHARS]}"
                          for i, t in enumerate(turns))
        content = (f"PERSONAGGIO: {name} | sessione {self.campaign.session_number}\n\n{self._documents(name, slug)}\n\n"
                   f"GIOCATO GREZZO (turni da t{done + 1} a t{done + len(turns)}):\n\n{raw}")
        params: dict[str, Any] = dict(model=self.model, max_tokens=MAX_OUTPUT_TOKENS, system=MEMORY_PROMPT,
                                      messages=[{"role": "user", "content": content}],
                                      output_config={"format": {"type": "json_schema", "schema": MEMORY_SCHEMA}})
        from .models import effective_effort
        from .prepare import create_message, parse_json_object

        if effective_effort(self.model, self.effort):
            params["output_config"]["effort"] = self.effort

        progress(f"Rileggo {len(turns)} turni di giocato…")
        log.info("consolidamento: %d turni (da t%d) -> %s", len(turns), done + 1, self.model)
        try:
            response = create_message(self.client, max_seconds=600, **params)
        except Exception as exc:
            log.error("consolidamento INTERROTTO alla chiamata: %s | il giocato resta da consolidare", describe_error(exc), exc_info=True)
            raise
        log.info("consolidamento <- %s", usage_line(response))
        usage = getattr(response, "usage", None)
        report.input_tokens = getattr(usage, "input_tokens", 0) or 0
        report.output_tokens = getattr(usage, "output_tokens", 0) or 0
        report.cost_usd = round(cost_usd(self.model, report.input_tokens, report.output_tokens), 4)
        # la spesa si registra subito: anche una risposta inutilizzabile e' stata pagata
        self.campaign.record_spend("memoria", self.model, report.cost_usd, turni=len(turns))
        if getattr(response, "stop_reason", None) == "max_tokens":
            log.error("consolidamento INTERROTTO: risposta troncata a %d token | il giocato resta da consolidare", report.output_tokens)
            raise ValueError(f"La risposta del consolidamento si e' troncata a {report.output_tokens} token: il giocato resta da consolidare")
        data = parse_json_object("\n".join(b.text for b in response.content if b.type == "text"))
        if not isinstance(data.get("eventi"), list) or not isinstance(data.get("scena"), dict):
            raise ValueError("Il consolidamento non ha restituito una memoria valida: il giocato resta da consolidare")
        self._apply(data, name, slug, done, len(turns), report)
        self.campaign.set_consolidated(slug, done + len(turns))
        report.turns = len(turns)
        log.info("consolidamento FINITO | %s | file=%s", report.summary(), ", ".join(report.files_changed))
        return report

    def _apply(self, data: dict[str, Any], name: str, slug: str, done: int, count: int, report: MemoryReport) -> None:
        camp = self.campaign
        touched = report.files_changed

        # cronaca: le unita' di evento prendono il posto degli appunti provvisori dello scriba
        events = [e for e in data["eventi"] if isinstance(e, dict) and str(e.get("testo", "")).strip()
                  and done < int(e.get("turno", 0)) <= done + count]
        diary = camp.file(DIARY)
        still_open = bool(camp.read_turns(slug))
        title = f"Sessione {camp.session_number if still_open else max(1, camp.session_number - 1)} — cronaca"
        old = (diary.get_section(title) or "").strip() if diary.exists() else ""
        lines = ([old] if old else []) + [f"- [t{int(e['turno'])}] {str(e['testo']).strip()}" for e in events]
        if lines:
            diary.set_section(title, "\n".join(lines))
        if diary.exists() and diary.get_section(NOTES_SECTION) is not None:
            diary.remove_section(NOTES_SECTION)
        touched.append(DIARY)
        report.events = len(events)

        threads = str(data.get("fili_aperti") or "").strip()
        if threads:
            camp.file(ADVENTURE).set_section(f"Fili aperti di {name}", threads)
            touched.append(ADVENTURE)

        memory = camp.file(NPC_MEMORY)
        for npc in data.get("png") or []:
            who = str(npc.get("nome", "")).strip()
            if not who:
                continue
            if not memory.exists():
                memory.write("# Memoria dei PNG\n\n*Cosa sa, cosa crede e cosa vuole chi ha incontrato il personaggio. La aggiorna il consolidamento.*\n")
            body = [f"- **Sa:** {npc.get('sa', '').strip()}"]
            if str(npc.get("crede", "")).strip():
                body.append(f"- **Crede:** {npc['crede'].strip()}")
            body.append(f"- **Vuole:** {npc.get('vuole', '').strip()}")
            memory.set_section(who, "\n".join(body))
            report.npcs.append(who)
        if report.npcs:
            touched.append(NPC_MEMORY)

        write_scene(camp, data["scena"], f"aggiornato al turno t{done + count}")
        touched.append(SCENE)

        fixes = [i for i in data.get("incoerenze") or [] if str(i.get("descrizione", "")).strip()]
        if fixes:
            text = "\n".join(f"- {i['descrizione'].strip()} -> {str(i.get('correzione', '')).strip()}" for i in fixes)
            adventure = camp.file(ADVENTURE)
            old_fixes = (adventure.get_section("Incoerenze sanate") or "").strip() if adventure.exists() else ""
            adventure.set_section("Incoerenze sanate", (old_fixes + "\n" if old_fixes else "") + text)
            report.inconsistencies = fixes
            if ADVENTURE not in touched:
                touched.append(ADVENTURE)
