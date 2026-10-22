"""Le tabelle casuali del manuale come dati: le tira il programma, non il narratore.

Un manuale ha tabelle in cui un tiro sceglie una riga per intervallo (incontri, tesori, effetti
collaterali, "1d100: 01-05 ..."). Lasciate in prosa, il narratore le legge e sceglie lui la
riga: una scelta, non un tiro. Qui diventano `meccanica/tabelle.json`:

    {"tabelle": [{"nome": "incontri nella palude", "uso": "ogni ora di marcia", "dado": "1d100",
                  "righe": [{"da": 1, "a": 30, "risultato": "Nulla"}, {"da": 31, "a": 100, "risultato": "..."}],
                  "fonte": "meccanica/creature.md: Incontri casuali"}]}

Il narratore chiude la narrazione con `[[tabella: <nome>]]`, la pagina lo mostra come pulsante,
il programma tira il dado della tabella, trova la riga e l'esito torna al narratore come messaggio
del giocatore: `[Tabella] incontri nella palude — 1d100: 37 -> ...`. Le righe devono coprire tutto
l'intervallo del dado senza buchi ne' sovrapposizioni (`problems`, la regola condivisa con la
verifica); un file che non la rispetta si ignora intero. La preparazione le ricava con una chiamata
(`extract_tables`) dalle sezioni che hanno la forma di una tabella; il modello trascrive, il codice
controlla e scarta cio' che non quadra. Idea presa dalle "famiglie di parametri" di AiChatTrpg.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import dice, testi
from .campaign import MECHANICS_DIR, SETTING_DIR, Campaign

log = logging.getLogger("master.tabelle")

FILE_NAME = "tabelle.json"
MARK_RE = re.compile(r"\[\[\s*tabella\s*:(.*?)\]\]", re.S | re.I)
MAX_TABLES, MAX_ROWS = 60, 200
MAX_CHAIN = 3  # una riga che manda a un'altra tabella: al massimo tre tabelle in fila, poi ci si ferma
# una riga di tabella: `01-05 ...`, `| 1-3 | ...` o `| 6 | ...`; non un passo numerato `1. ...`
_RANGE_LINE = re.compile(r"(?m)^\s*(?:\|\s*\d{1,3}(?:\s*[-–]\s*\d{1,3})?\s*\||\d{1,3}\s*[-–]\s*\d{1,3}\b)")
_DICE_WORD = re.compile(r"\b\d{0,2}d\d{1,3}\b", re.I)
MIN_RANGE_LINES = 3
SOURCE_LIMIT = 40_000


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def find_marker(text: str) -> str | None:
    found = MARK_RE.search(text or "")
    return found.group(1).strip() if found else None


def dice_range(notation: str) -> tuple[int, int] | None:
    """(minimo, massimo) di una notazione NdS+K, o None se non e' una notazione."""
    try:
        p = dice.parse(f"x | {notation}") if notation else None
    except ValueError:
        return None
    if not p or not p.get("notation"):
        return None
    found = dice._NOTATION_RE.match(p["notation"])
    count, sides = int(found.group(1) or 1), int(found.group(2))
    bonus = int((found.group(3) or "0").replace(" ", ""))
    return count + bonus, count * sides + bonus


# --- la forma --------------------------------------------------------------------------------------

def problems(data: Any) -> list[tuple[str, str]]:
    """(percorso, problema) per ogni punto in cui il file non ha la forma giusta."""
    if not isinstance(data, dict) or "tabelle" not in data:
        return [("", "il file deve contenere {\"tabelle\": [...]}")]
    tables = data["tabelle"]
    if not isinstance(tables, list):
        return [("tabelle", "dev'essere un elenco")]
    out: list[tuple[str, str]] = []
    if len(tables) > MAX_TABLES:
        out.append(("tabelle", f"al massimo {MAX_TABLES} tabelle"))
    names: set[str] = set()
    links: list[tuple[str, str]] = []  # (percorso, tabella a cui una riga manda): si controllano a nomi raccolti
    for i, t in enumerate(tables):
        at = f"tabelle[{i}]"
        if not isinstance(t, dict):
            out.append((at, "una tabella e' {nome, uso, dado, righe}"))
            continue
        name = str(t.get("nome") or "").strip().lower()
        bad_name = testi.problema_nome(t.get("nome"))
        if bad_name:
            out.append((f"{at}.nome", bad_name))
        elif name in names:
            out.append((f"{at}.nome", f"'{name}' e' gia' usato da un'altra tabella"))
        names.add(name)
        if t.get("uso") not in (None, "") and testi.problema_riga(t.get("uso")):
            out.append((f"{at}.uso", testi.problema_riga(t.get("uso"))))
        if "modificatore_da" in t and t["modificatore_da"] is not None and (not isinstance(t["modificatore_da"], str) or not t["modificatore_da"].strip()):
            out.append((f"{at}.modificatore_da", "un percorso della scheda (es. `stato.fortuna`), o niente"))
        span = dice_range(str(t.get("dado") or ""))
        if span is None:
            out.append((f"{at}.dado", "una notazione come 1d100, 2d6, 1d20+1"))
        rows = t.get("righe")
        if not isinstance(rows, list) or not rows:
            out.append((f"{at}.righe", "almeno una riga {da, a, risultato}"))
            continue
        if len(rows) > MAX_ROWS:
            out.append((f"{at}.righe", f"al massimo {MAX_ROWS} righe"))
        clean: list[tuple[int, dict[str, Any]]] = []
        for j, r in enumerate(rows):
            where = f"{at}.righe[{j}]"
            if not isinstance(r, dict):
                out.append((where, "una riga e' {da, a, risultato}"))
                continue
            if not _is_int(r.get("da")) or not _is_int(r.get("a")) or r["a"] < r["da"]:
                out.append((f"{where}.da", "`da` e `a` sono interi, con `a` non minore di `da`"))
                continue
            bad_text = testi.problema_riga(r.get("risultato"))
            if bad_text:
                out.append((f"{where}.risultato", bad_text))
            if "tabella" in r and r["tabella"] is not None:
                target = str(r["tabella"]).strip().lower() if isinstance(r["tabella"], str) else ""
                if not target:
                    out.append((f"{where}.tabella", "il nome di un'altra tabella di questo file, o niente"))
                elif target == name:
                    out.append((f"{where}.tabella", "una riga non puo' rimandare alla propria tabella"))
                else:
                    links.append((f"{where}.tabella", target))
            clean.append((j, r))
        if span is None or len(clean) != len(rows):
            continue
        ordered = sorted(clean, key=lambda x: x[1]["da"])
        lo, hi = span
        if ordered[0][1]["da"] != lo:
            out.append((f"{at}.righe[{ordered[0][0]}].da", f"la prima riga comincia a {lo}, il minimo di {t.get('dado')}"))
        if ordered[-1][1]["a"] != hi:
            out.append((f"{at}.righe[{ordered[-1][0]}].a", f"l'ultima riga finisce a {hi}, il massimo di {t.get('dado')}"))
        for (_, r), (j, s) in zip(ordered, ordered[1:]):
            if s["da"] != r["a"] + 1:
                out.append((f"{at}.righe[{j}].da", f"deve valere {r['a'] + 1}: le righe si toccano senza buchi ne' sovrapposizioni"))
    for where, target in links:
        if target not in names:
            out.append((where, f"'{target}' non e' una tabella di questo file"))
    return out


def sheet_path_problems(data: Any, sheet: Any) -> list[tuple[str, str]]:
    """I `modificatore_da` che nella scheda attiva non esistono: il tiro andrebbe senza modificatore, con una nota."""
    out = []
    if not isinstance(data, dict) or not isinstance(data.get("tabelle"), list) or sheet is None:
        return out
    for i, t in enumerate(data["tabelle"]):
        path = t.get("modificatore_da") if isinstance(t, dict) else None
        if isinstance(path, str) and path.strip():
            try:
                sheet.get(path)
            except ValueError:
                out.append((f"tabelle[{i}].modificatore_da", f"`{path}` non esiste nella scheda attiva: il modificatore non verrebbe letto"))
    return out


def carica(campaign: Campaign) -> list[dict[str, Any]]:
    path = campaign.root / MECHANICS_DIR / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [] if problems(data) else list(data["tabelle"])


def salva(campaign: Campaign, tables: list[dict[str, Any]]) -> None:
    path = campaign.root / MECHANICS_DIR / FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tabelle": tables}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def trova(tables: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Per nome esatto, o per l'unico nome che lo contiene (`nomi.risolvi`); due candidati -> ValueError che li nomina."""
    from . import nomi

    chosen, near = nomi.risolvi(name, [str(t.get("nome", "")) for t in tables])
    if chosen is None and len(near) > 1:
        raise ValueError(f"'{name}' puo' essere {' o '.join(repr(n) for n in near)}: scrivi il nome intero")
    return next((t for t in tables if str(t.get("nome", "")) == chosen), None) if chosen else None


# --- il tiro --------------------------------------------------------------------------------------

_MODIFIER = re.compile(r"^[+-]\s*\d+$")
OUTCOME_RE = re.compile(r"^\[Tabella\] (?P<name>.+?) — .+? -> (?P<value>\d+): (?P<result>.*)$")


def parse(spec: str) -> tuple[str, int]:
    """`<nome> | +2` -> (nome, modificatore): il manuale dice "tira 1d100 +10 di notte", il narratore lo scrive nel marcatore."""
    text = str(spec).strip().strip("[]")
    if text.lower().startswith("tabella:"):
        text = text[8:]
    parts = [p.strip() for p in text.split("|")]
    name, modifier = parts[0], 0
    for part in parts[1:]:
        if _MODIFIER.match(part):
            modifier += int(part.replace(" ", ""))
        elif part:
            raise ValueError(f"'{part}' non e' un modificatore (+N o -N): il marcatore e' [[tabella: <nome> | +N]]")
    return name, modifier


def tira(spec: str, tables: list[dict[str, Any]], sheet: Any = None) -> dict[str, Any]:
    """`<nome tabella> | +N`: tira il dado della tabella, somma il modificatore (quello del marcatore piu' quello che
    la tabella legge dalla scheda, `modificatore_da`), trova la riga; se la riga manda a un'altra tabella, tira anche
    quella (una riga di `[Tabella]` per ciascuna). Tirare non costa chiamate."""
    name, modifier = parse(spec)
    first = _tira_una(name, modifier, tables, sheet)
    chain = [first]
    seen = {first["name"].lower()}
    while chain[-1].get("next") and len(chain) < MAX_CHAIN and chain[-1]["next"].lower() not in seen:
        step = _tira_una(chain[-1]["next"], 0, tables, sheet)
        seen.add(step["name"].lower())
        chain.append(step)
    out = {**first, "chain": chain[1:], "text": "\n".join(step["text"] for step in chain)}
    out.pop("next", None)
    return out


def _from_sheet(table: dict[str, Any], sheet: Any) -> tuple[int, str]:
    """(modificatore, nota) letti dalla scheda per `modificatore_da`; (0, nota) se il percorso non da' un numero."""
    path = str(table.get("modificatore_da") or "").strip()
    if not path:
        return 0, ""
    if sheet is None or not getattr(sheet, "exists", False):
        return 0, f" ({path}: nessuna scheda, non letto)"
    try:
        value = sheet.get(path)
    except ValueError:
        return 0, f" ({path}: non nella scheda, non letto)"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0, f" ({path}: non e' un numero, non letto)"
    return int(value), f" (da {path})"


def _tira_una(name: str, modifier: int, tables: list[dict[str, Any]], sheet: Any = None) -> dict[str, Any]:
    table = trova(tables, name)
    if table is None:
        known = [t.get("nome") for t in tables]
        raise ValueError(f"'{name}' non e' una tabella di questa campagna ({known or 'nessuna tabella'})")
    from_sheet, note = _from_sheet(table, sheet)
    modifier += from_sheet
    rolled = dice.roll(f"{table['nome']} | {table['dado']}")
    lo, hi = dice_range(str(table["dado"])) or (rolled["value"], rolled["value"])
    value = max(lo, min(hi, rolled["value"] + modifier))  # fuori scala si ferma al capo: la tabella non ha righe oltre
    row = next((r for r in table["righe"] if int(r["da"]) <= value <= int(r["a"])), None)
    result = str(row["risultato"]).strip() if row else "(nessuna riga per questo valore: la tabella ha un buco)"
    faces = rolled["rolls"] if len(rolled["rolls"]) > 1 else rolled["rolls"][0]
    shown = (f"{faces} {modifier:+d}" if modifier else f"{faces}") + note
    if modifier and value != rolled["value"] + modifier:
        shown += f" (fermato a {value})"
    text = f"[Tabella] {table['nome']} — {table['dado']}: {shown} -> {value}: {result}"
    return {"name": table["nome"], "value": value, "rolls": rolled["rolls"], "modifier": modifier, "result": result, "text": text,
            "next": str(row.get("tabella") or "").strip() if row else ""}


def outcomes(spoken: str) -> list[tuple[str, str]]:
    """(nome tabella, riga uscita) per ogni riga `[Tabella] ...` del messaggio: i fatti che i trigger leggono."""
    out = []
    for line in str(spoken).strip().splitlines():
        found = OUTCOME_RE.match(line.strip())
        if found:
            out.append((found.group("name").strip(), found.group("result").strip()))
    return out


def outcome(spoken: str) -> tuple[str, str] | None:
    """La prima tabella uscita, o None."""
    found = outcomes(spoken)
    return found[0] if found else None


def promemoria(tables: list[dict[str, Any]], title: str) -> str | None:
    """La riga da appendere a una sezione di regole che porta nel contesto una tabella che il programma tira:
    letta in prosa, il narratore sceglierebbe la riga; con il promemoria chiude con il marcatore."""
    wanted = str(title or "").strip().lower()
    if not wanted:
        return None
    for t in tables:
        name = str(t.get("nome", "")).strip().lower()
        source = str(t.get("fonte", "")).strip().lower()
        if name and (name in wanted or wanted in name or (source and wanted in source)):
            return f"> Questa tabella la tira il programma: non scegliere la riga, chiudi con `[[tabella: {t['nome']}]]`."
    return None


# --- cio' che il narratore deve sapere ---------------------------------------------------------------

def prompt_section(tables: list[dict[str, Any]]) -> str:
    if not tables:
        return ""
    lines = ["\n## Tabelle: le tira il programma",
             "Quando le regole mandano a una tabella casuale non scegliere tu la riga: chiudi la narrazione con un marcatore "
             "su una riga sua (uno per messaggio, in alternativa al tiro), il giocatore preme il pulsante, il programma tira e "
             "ti arriva `[Tabella] ...` con il dado e la riga uscita: narra quella. Se le regole danno un modificatore al tiro "
             "(\"+10 di notte\") scrivilo nel marcatore: `[[tabella: <nome> | +10]]`."]
    for t in tables:
        lines.append(f"- `[[tabella: {t['nome']}]]` ({t.get('dado')})" + (f" — {t['uso']}" if t.get("uso") else ""))
    return "\n".join(lines) + "\n"


# --- dal manuale alle tabelle: una chiamata, poi restano su disco -------------------------------------

_ROW = {
    "type": "object",
    "properties": {"da": {"type": "integer"}, "a": {"type": "integer"}, "risultato": {"type": "string", "description": "Il testo della riga, breve, com'e' nel manuale"},
                   "tabella": {"type": ["string", "null"], "description": "Se la riga manda a un'altra tabella ('tira sulla tabella dei tesori'), il suo nome esatto; altrimenti null"}},
    "required": ["da", "a", "risultato", "tabella"],
    "additionalProperties": False,
}
TABLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tabelle": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "Il nome della tabella, minuscolo, come la chiama il manuale"},
                    "uso": {"type": "string", "description": "Quando si tira, in una frase"},
                    "dado": {"type": "string", "description": "La notazione del dado che la tabella usa: 1d100, 2d6, 1d20..."},
                    "righe": {"type": "array", "items": _ROW, "description": "Tutte le righe, con gli intervalli del manuale: devono coprire tutto il dado senza buchi"},
                    "fonte": {"type": "string", "description": "File e sezione da cui viene"},
                },
                "required": ["nome", "uso", "dado", "righe", "fonte"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tabelle"],
    "additionalProperties": False,
}
TABLES_PROMPT = """\
Trascrivi le TABELLE CASUALI di un gioco di ruolo: quelle in cui un tiro di dado sceglie una riga per intervallo
(incontri, tesori, effetti, eventi: "1d100: 01-05 ..."). Solo cio' che le sezioni qui sotto contengono, senza
inventare righe ne' completare a memoria. Gli intervalli vanno riportati esatti e devono coprire tutto il dado:
se nel manuale una tabella ha buchi o sovrapposizioni, riportala com'e' (il programma la scartera'). Lascia fuori
le tabelle che non si tirano (listini, tabelle di modificatori): quelle il master le legge. Se una riga manda a
un'altra tabella ("tira sui tesori"), trascrivi anche quella e scrivi il suo nome nel campo `tabella` della riga."""


def setaccia(tables: list[Any]) -> list[dict[str, Any]]:
    """Le tabelle che passano `problems` prese INSIEME: un rimando a una sorella vale solo se la sorella resta.

    Si toglie una tabella alla volta, quella che `problems` indica, finche' il resto e' pulito; con il controllo
    una per una (collaudo del 2026-10-22) 'incontri' cadeva perche' 'relitti' non era nel suo stesso probe."""
    kept = [t for t in tables if isinstance(t, dict)][:MAX_TABLES]
    while kept:
        bad = problems({"tabelle": kept})
        if not bad:
            break
        guilty = sorted({int(m.group(1)) for p, _ in bad for m in [re.match(r"tabelle\[(\d+)\]", p)] if m}, reverse=True)
        if not guilty:  # un problema del file intero, non di una tabella: nulla da tenere
            log.warning("tabelle scartate tutte: %s", "; ".join(f"{p}: {m}" for p, m in bad[:3]))
            return []
        for i in guilty:
            reasons = [f"{p}: {m}" for p, m in bad if p.startswith(f"tabelle[{i}]")]
            log.warning("tabella scartata '%s': %s", kept[i].get("nome"), "; ".join(reasons[:3]))
            kept.pop(i)
    return kept


GROUP_CHARS = 12_000  # sezioni per chiamata: l'uscita (le tabelle trascritte) pesa quanto l'ingresso


def _groups(found: list[tuple[str, str, str]], limit: int) -> list[list[tuple[str, str, str]]]:
    """Le sezioni a gruppi che stanno nel limite di caratteri, nell'ordine in cui arrivano."""
    groups: list[list[tuple[str, str, str]]] = [[]]
    size = 0
    for row in found:
        if groups[-1] and size + len(row[2]) > limit:
            groups.append([])
            size = 0
        groups[-1].append(row)
        size += len(row[2])
    return [g for g in groups if g]


def sections_with_tables(campaign: Campaign) -> list[tuple[str, str, str]]:
    """(file, titolo, corpo) delle sezioni che hanno la forma di una tabella casuale, fino a un tetto di caratteri."""
    out, size = [], 0
    for f in campaign.files():
        if not (f.name.startswith(MECHANICS_DIR + "/") or f.name.startswith(SETTING_DIR + "/")):
            continue
        for title, body in f.sections().items():
            if not title or len(_RANGE_LINE.findall(body)) < MIN_RANGE_LINES or not _DICE_WORD.search(body):
                continue
            if size + len(body) > SOURCE_LIMIT:
                return out
            out.append((f.name, title, body.strip()))
            size += len(body)
    return out


def extract_tables(campaign: Campaign, client: Any, model: str) -> list[dict[str, Any]]:
    """Una chiamata sulle sezioni che sembrano tabelle; salva solo le tabelle che passano i controlli."""
    found = sections_with_tables(campaign)
    if not found:
        log.info("tabelle: nessuna sezione con la forma di una tabella casuale")
        return []
    from .models import cost_usd
    from .prepare import create_message, parse_json_object

    # a gruppi: un manuale vero (Cyberpunk RED, 19 sezioni, 40k caratteri) in una chiamata sola supera i token di
    # uscita e il JSON arriva tagliato; ogni gruppo sta nel suo budget, poi le tabelle si setacciano insieme
    tables: list[Any] = []
    for group in _groups(found, GROUP_CHARS):
        content = "\n\n".join(f"## {title} ({name})\n{body}" for name, title, body in group)
        response = create_message(client, max_seconds=300, model=model, max_tokens=16000, system=TABLES_PROMPT,
                                  messages=[{"role": "user", "content": content}],
                                  output_config={"format": {"type": "json_schema", "schema": TABLES_SCHEMA}})
        usage = getattr(response, "usage", None)
        campaign.record_spend("tabelle", model, cost_usd(model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0))
        if getattr(response, "stop_reason", "") == "max_tokens":
            log.warning("tabelle: risposta tagliata per il gruppo '%s'...: le sue tabelle si perdono", group[0][1])
            continue
        try:
            data = parse_json_object("\n".join(b.text for b in response.content if b.type == "text"))
        except ValueError as exc:
            log.warning("tabelle: JSON illeggibile per il gruppo '%s'...: %s", group[0][1], exc)
            continue
        part = data.get("tabelle") if isinstance(data, dict) else None
        tables += part if isinstance(part, list) else []
    kept = setaccia(tables)
    salva(campaign, kept)
    log.info("tabelle salvate: %s", ", ".join(t["nome"] for t in kept) or "nessuna")
    return kept
