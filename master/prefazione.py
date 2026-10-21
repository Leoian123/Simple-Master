"""La prefazione delle regole: come si usano, scritta una volta e letta a ogni turno.

Un manuale smontato in sezioni dice le regole, non come orientarsi fra loro: quale fonte vince
se due dicono cose diverse, cosa va cercato nei file e non ricordato, cosa fare in gioco quando
una regola non si trova. AiChatTrpg lo risolve con una "Sezione 0" generata in testa al libro;
qui e' la sezione `## Come usare queste regole` in testa a `meccanica/regole.md`, ricavata con
una chiamata alla fine della preparazione (`extract_preface`) e copiata nel prompt di sistema
(blocco in cache: non costa a ogni turno). Il modello risponde in tre liste con uno schema
forzato; il codice le trascrive in Markdown e tiene nei rimandi solo le sezioni che esistono
davvero: un rimando inventato e' peggio di nessun rimando. La verifica rilegge i rimandi.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .campaign import MECHANICS_DIR, Campaign

log = logging.getLogger("master.prefazione")

RULES_FILE = f"{MECHANICS_DIR}/regole.md"
SECTION_TITLE = "Come usare queste regole"
HEADS = ("Precedenza", "Cerca nei file, non a memoria", "Se una regola non si trova")
MAX_ITEMS = 12
MIN_SECTIONS = 5  # sotto, le regole si leggono intere: una prefazione non serve
_LOOKUP_LINE = re.compile(r"^- (?P<topic>.+?): `(?P<file>[^`]+)` § (?P<section>.+?)\s*$")
_SECTION_RE = re.compile(r"(?m)^## (.+?)\s*$")


# --- lettura -------------------------------------------------------------------------------------

def leggi(campaign: Campaign) -> str:
    """Il corpo della prefazione, o "" se le regole non ce l'hanno."""
    f = campaign.file(RULES_FILE)
    return (f.get_section(SECTION_TITLE) or "").strip() if f.exists() else ""


def prompt_section(campaign: Campaign) -> str:
    body = leggi(campaign)
    return f"\n## Come usare le regole di questa campagna (dalla prefazione in `{RULES_FILE}`)\n{body}\n" if body else ""


def lookups(body: str) -> list[tuple[str, str, str]]:
    """(argomento, file, sezione) dei rimandi scritti nella prefazione."""
    out = []
    for line in body.splitlines():
        m = _LOOKUP_LINE.match(line.strip())
        if m:
            out.append((m.group("topic"), m.group("file"), m.group("section")))
    return out


def broken_lookups(campaign: Campaign) -> list[tuple[str, str, str]]:
    """I rimandi che non portano a una sezione esistente: la verifica li segnala."""
    existing = {f.name: {t.lower() for t in f.outline()} for f in campaign.files()}
    return [(topic, file, section) for topic, file, section in lookups(leggi(campaign))
            if file not in existing or section.strip().lower() not in existing[file]]


# --- scrittura: in testa alle regole --------------------------------------------------------------

def render(data: dict[str, Any]) -> str:
    """Le tre liste in Markdown, con un titolo fisso ciascuna: cosi' i rimandi si rileggono."""
    parts = [f"### {HEADS[0]}", "\n".join(f"{i + 1}. {x}" for i, x in enumerate(data.get("precedenza") or [])) or "(nessuna indicazione)",
             f"### {HEADS[1]}", "\n".join(f"- {c['argomento']}: `{c['file']}` § {c['sezione']}" for c in data.get("cercare") or []) or "(nessun rimando)",
             f"### {HEADS[2]}", "\n".join(f"- {x}" for x in data.get("riserva") or []) or "(nessuna indicazione)"]
    return "\n\n".join(parts).strip()


def scrivi(campaign: Campaign, body: str) -> None:
    """Sostituisce (o crea) la prefazione come prima sezione di `regole.md`: in testa, non in coda."""
    f = campaign.file(RULES_FILE)
    content = f.read() if f.exists() else "# Regole\n"
    sections = list(_SECTION_RE.finditer(content))
    block = f"## {SECTION_TITLE}\n\n{body.strip()}\n\n"
    for i, m in enumerate(sections):
        if m.group(1).strip().lower() == SECTION_TITLE.lower():
            end = sections[i + 1].start() if i + 1 < len(sections) else len(content)
            content = content[: m.start()] + block + content[end:]
            break
    else:
        at = sections[0].start() if sections else len(content.rstrip("\n") + "\n\n")
        content = content[:at].rstrip("\n") + "\n\n" + block + content[at:]
    f.write(content.rstrip("\n") + "\n")


# --- dal manuale alla prefazione: una chiamata ------------------------------------------------------

PREFACE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "precedenza": {"type": "array", "items": {"type": "string"},
                       "description": "In ordine, da chi vince a chi cede: quale fonte conta quando due dicono cose diverse (scheda, un file, una sezione, le regole generali)"},
        "cercare": {"type": "array", "description": "Cio' che il master deve rileggere nei file invece di ricordare: numeri, tabelle, costi, liste",
                    "items": {"type": "object",
                              "properties": {"argomento": {"type": "string"}, "file": {"type": "string", "description": "Un file dell'indice, esatto"},
                                             "sezione": {"type": "string", "description": "Un titolo di sezione di quel file, esatto"}},
                              "required": ["argomento", "file", "sezione"], "additionalProperties": False}},
        "riserva": {"type": "array", "items": {"type": "string"},
                    "description": "Cosa fare in gioco quando una regola non si trova nei file: la lettura di riserva, nei termini di questo gioco"},
    },
    "required": ["precedenza", "cercare", "riserva"],
    "additionalProperties": False,
}
PREFACE_PROMPT = """\
Scrivi la PREFAZIONE D'USO delle regole di un gioco di ruolo, per il master che le applichera' in gioco. Hai l'indice
dei file di regole (file e titoli di sezione), i tipi di tiro che il programma sa tirare e le tabelle che sa tirare.
Rispondi in tre liste, in italiano, ognuna di poche voci (al massimo dodici):
- precedenza: in ordine, quale fonte vince quando due dicono cose diverse (per esempio: la scheda del personaggio
  sulle regole generali; una regola specifica su una generale; il manuale su cio' che ricordi di altri giochi);
- cercare: cio' che va riletto nei file e non ricordato a memoria (numeri, costi, tabelle, elenchi), con file e
  titolo di sezione presi ESATTI dall'indice: non inventare file ne' sezioni;
- riserva: cosa fare quando una regola non si trova: quale tiro generico usare, quando decidere senza tiro, cosa dire
  al giocatore. Nei termini di questo gioco, non di un altro."""


def materials(campaign: Campaign) -> str:
    """Cio' che la chiamata vede: l'indice delle regole, i tiri e le tabelle che il programma gestisce."""
    from . import dice, tabelle

    # l'indice senza la prefazione stessa: cosi' l'impronta dei materiali non cambia per averla scritta
    index = []
    for f in campaign.files():
        if f.name.startswith(MECHANICS_DIR + "/"):
            titles = [t for t in f.outline() if t.strip().lower() != SECTION_TITLE.lower()]
            index.append(f"- {f.name}" + (f": {', '.join(titles[:60])}" if titles else ""))
    out = ["INDICE DELLE REGOLE:", "\n".join(index)]
    profile = dice.load_profile(campaign)
    if profile:
        out.append("\nTIPI DI TIRO CHE IL PROGRAMMA TIRA: " + ", ".join(f"{k.get('nome')} ({k.get('uso', '')})" for k in profile.get("tiri", [])))
    tables = tabelle.carica(campaign)
    if tables:
        out.append("TABELLE CHE IL PROGRAMMA TIRA: " + ", ".join(str(t.get("nome")) for t in tables))
    return "\n".join(out)


def clean(campaign: Campaign, data: Any) -> dict[str, Any]:
    """Tiene testi non vuoti, entro il tetto, e solo i rimandi che esistono davvero (titoli ricopiati esatti)."""
    if not isinstance(data, dict):
        return {"precedenza": [], "cercare": [], "riserva": []}
    existing = {f.name: {t.lower(): t for t in f.outline()} for f in campaign.files()}
    kept = []
    for c in (data.get("cercare") or [])[:MAX_ITEMS] if isinstance(data.get("cercare"), list) else []:
        if not isinstance(c, dict):
            continue
        file, section, topic = str(c.get("file", "")).strip(), str(c.get("sezione", "")).strip(), str(c.get("argomento", "")).strip()
        if topic and file in existing and section.lower() in existing[file]:
            kept.append({"argomento": topic, "file": file, "sezione": existing[file][section.lower()]})
        else:
            log.warning("prefazione: rimando scartato (%s -> %s § %s): non esiste", topic, file, section)
    texts = lambda key: [str(x).strip() for x in (data.get(key) or [])[:MAX_ITEMS] if str(x).strip()] if isinstance(data.get(key), list) else []
    return {"precedenza": texts("precedenza"), "cercare": kept, "riserva": texts("riserva")}


def extract_preface(campaign: Campaign, client: Any, model: str) -> dict[str, Any] | None:
    """Una chiamata sull'indice delle regole; scrive la sezione in testa a `regole.md` e torna i dati puliti."""
    if not campaign.file(RULES_FILE).exists() or not campaign.file(RULES_FILE).outline():
        log.info("prefazione: nessuna regola da prefare")
        return None
    from .models import cost_usd
    from .prepare import create_message, parse_json_object

    response = create_message(client, max_seconds=180, model=model, max_tokens=3000, system=PREFACE_PROMPT,
                              messages=[{"role": "user", "content": materials(campaign)}],
                              output_config={"format": {"type": "json_schema", "schema": PREFACE_SCHEMA}})
    usage = getattr(response, "usage", None)
    campaign.record_spend("prefazione", model, cost_usd(model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0))
    data = clean(campaign, parse_json_object("\n".join(b.text for b in response.content if b.type == "text")))
    scrivi(campaign, render(data))
    log.info("prefazione scritta: %d precedenze, %d rimandi, %d riserve", len(data["precedenza"]), len(data["cercare"]), len(data["riserva"]))
    return data
