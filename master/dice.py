"""I dadi li tira il codice, secondo la meccanica estratta dal manuale.

Questo modulo non conosce nessun gioco. Sa tirare dadi in due modi (sommare, o contare i
successi) e applicare cio' che un PROFILO gli dice: che dado si usa, da che valore e' un
successo, quanto valgono le coppie di una faccia, se esistono dadi speciali presi da un valore
della scheda e quali facce fanno scattare un evento con un nome. Il profilo sta in
`meccanica/dadi.json` ed e' ricavato dalle regole che la preparazione ha estratto dal manuale
(`extract_profile`, una chiamata sola, poi resta su disco): Vampiri diventa riserve di d10 con
dadi Fame, un gioco a d20 diventa d20 + bonus contro una soglia, senza toccare il codice.

Flusso: il narratore chiude la narrazione con un marcatore, la pagina lo mostra come pulsante,
`roll` tira, e l'esito torna al narratore come messaggio del giocatore. Tirare non costa chiamate.

    [[tiro: <nome> | <tipo di tiro del profilo> | dadi N | bonus +K | difficolta D]]

Senza profilo resta la forma esplicita, in cui il narratore dice i dadi per esteso:

    [[tiro: <nome> | 2d6+1 | difficolta 8]]      [[tiro: <nome> | 6d10 | successi 6+ | difficolta 3]]
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from typing import Any

from .campaign import MECHANICS_DIR, Campaign

log = logging.getLogger("master.dadi")

PROFILE_NAME = "dadi.json"
MARK_RE = re.compile(r"\[\[\s*tiro\s*:(.*?)\]\]", re.S | re.I)
_NOTATION_RE = re.compile(r"^\s*(\d{0,3})\s*d\s*(\d{1,4})\s*([+-]\s*\d+)?\s*$", re.I)
MAX_DICE = 100
WHEN = ("sempre", "riuscito", "fallito", "critico")  # quando una faccia fa scattare un evento


def find_marker(text: str) -> str | None:
    found = MARK_RE.search(text or "")
    return found.group(1).strip() if found else None


# --- profilo -------------------------------------------------------------------------------

def profile_path(campaign: Campaign):
    return campaign.root / MECHANICS_DIR / PROFILE_NAME


def load_profile(campaign: Campaign) -> dict[str, Any] | None:
    try:
        data = json.loads(profile_path(campaign).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("tiri"), list) and data["tiri"] else None


def save_profile(campaign: Campaign, profile: dict[str, Any]) -> None:
    path = profile_path(campaign)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _kind(profile: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if not profile:
        return None
    wanted = name.strip().lower()
    return next((k for k in profile["tiri"] if str(k.get("nome", "")).lower() == wanted), None)


# --- marcatore -----------------------------------------------------------------------------

def _number(text: str) -> int | None:
    found = re.search(r"[+-]?\s*\d+", text)
    return int(found.group().replace(" ", "")) if found else None


def parse(spec: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    parts = [p.strip() for p in str(spec).strip().strip("[]").split("|") if p.strip()]
    if parts and parts[0].lower().startswith("tiro:"):
        parts[0] = parts[0][5:].strip()
    if len(parts) == 1:  # "/tira 2d6+1": solo i dadi, o solo il tipo di tiro
        parts = ["Tiro libero", parts[0]]
    if len(parts) < 2:
        raise ValueError("Tiro non valido: servono un nome e i dadi, es. 'Attacco | 2d6+1 | difficolta 8'")
    out: dict[str, Any] = {"name": parts[0][:120], "kind": None, "notation": None, "dice": None, "bonus": 0,
                           "target": None, "count_at": None, "special": None, "advantage": 0}
    kind = _kind(profile, parts[1])
    if kind is not None:
        out["kind"] = kind
    elif _NOTATION_RE.match(parts[1]):
        out["notation"] = parts[1]
    else:
        known = [k.get("nome") for k in (profile or {}).get("tiri", [])]
        raise ValueError(f"'{parts[1]}' non e' un tipo di tiro di questa campagna ({known or 'nessun profilo'}) ne' dadi come 2d6+1")
    for opt in parts[2:]:
        low = opt.lower().replace("à", "a")
        number = _number(low)
        if low.startswith("vantagg"):
            out["advantage"] = 1
        elif low.startswith("svantagg"):
            out["advantage"] = -1
        elif number is None:
            continue
        elif low.startswith("diff"):
            out["target"] = number
        elif low.startswith("dadi"):
            out["dice"] = number
        elif low.startswith("bonus") or low.startswith("mod"):
            out["bonus"] = number
        elif low.startswith("succ"):
            out["count_at"] = abs(number)
        elif low.startswith("special") or (kind and low.startswith(str((kind.get("speciali") or {}).get("nome", "\0")).lower())):
            out["special"] = abs(number)
    return out


# --- tiro ----------------------------------------------------------------------------------

def _die(sides: int) -> int:
    return secrets.randbelow(sides) + 1


def _check(count: int, sides: int) -> None:
    if not 1 <= count <= MAX_DICE or not 2 <= sides <= 1000:
        raise ValueError(f"Dadi non validi: {count}d{sides}")


def _events(rules: list[dict[str, Any]], faces: list[int], won: bool | None, critical: bool, only_special: bool) -> list[str]:
    out = []
    for e in rules or []:
        if bool(e.get("solo_speciali")) != only_special or e.get("faccia") not in faces:
            continue
        when = e.get("quando", "sempre")
        if when == "sempre" or (when == "riuscito" and won) or (when == "fallito" and won is False) or (when == "critico" and critical and won is not False):
            out.append(str(e.get("nome", "evento")).upper() + (f" ({e['effetto']})" if e.get("effetto") else ""))
    return out


def _roll_successes(p: dict[str, Any], kind: dict[str, Any], special_from_sheet: int | None) -> dict[str, Any]:
    sides = int(kind.get("dado") or 10)
    count = p["dice"] if p["dice"] is not None else kind.get("dadi_fissi")
    if not count:
        raise ValueError("Manca il numero di dadi: aggiungi 'dadi N' al tiro")
    _check(int(count), sides)
    count = int(count)
    threshold = int(p["count_at"] or kind.get("successo_da") or sides)
    special_rule = kind.get("speciali") or {}
    wanted = p["special"] if p["special"] is not None else (special_from_sheet if special_rule else 0)
    special_n = min(int(wanted or 0), count) if special_rule.get("sostituiscono", True) else int(wanted or 0)
    normal_n = count - special_n if special_rule.get("sostituiscono", True) else count
    normal = [_die(sides) for _ in range(normal_n)]
    special = [_die(sides) for _ in range(special_n)]
    rolls = normal + special
    successes = sum(1 for r in rolls if r >= threshold)
    pair = kind.get("coppie") or {}
    pairs = rolls.count(pair["faccia"]) // 2 if pair.get("faccia") else 0
    successes += pairs * int(pair.get("successi_extra") or 0)
    target = p["target"]
    won = None if target is None else successes >= target
    critical = pairs > 0
    notes = []
    if critical and won is not False and pair.get("nome"):
        notes.append(str(pair["nome"]).upper())
    notes += _events(kind.get("eventi"), rolls, won, critical, only_special=False)
    notes += _events(kind.get("eventi"), special, won, critical, only_special=True)
    if successes == 0 and kind.get("nome_zero_successi"):
        notes.append(str(kind["nome_zero_successi"]).upper())
    label = str(special_rule.get("nome") or "speciali")
    detail = f"{count}d{sides}: {', '.join(map(str, normal)) or '-'}" + (f" | {label}: {', '.join(map(str, special))}" if special else "")
    return {"rolls": rolls, "special_rolls": special, "value": successes, "unit": "successi", "detail": detail, "notes": notes, "won": won}


def _roll_sum(p: dict[str, Any], kind: dict[str, Any] | None) -> dict[str, Any]:
    if kind is not None:
        sides, count, bonus = int(kind.get("dado") or 20), int(p["dice"] or kind.get("dadi_fissi") or 1), int(p["bonus"])
    else:
        found = _NOTATION_RE.match(p["notation"])
        count, sides = int(found.group(1) or 1), int(found.group(2))
        bonus = int((found.group(3) or "0").replace(" ", "")) + int(p["bonus"])
    _check(count, sides)
    rolls = [_die(sides) for _ in range(count)]
    shown = ", ".join(map(str, rolls))
    if p["advantage"] and count == 1:  # si tira due volte e si tiene il migliore (o il peggiore)
        other = _die(sides)
        kept = max(rolls[0], other) if p["advantage"] > 0 else min(rolls[0], other)
        shown = f"{rolls[0]} e {other}, tengo {kept} ({'vantaggio' if p['advantage'] > 0 else 'svantaggio'})"
        rolls = [kept]
    total = sum(rolls) + bonus
    target = p["target"]
    won = None if target is None else total >= target
    notes = _events((kind or {}).get("eventi"), rolls, won, False, only_special=False) if count == 1 or p["advantage"] else []
    detail = f"{count}d{sides}: {shown}" + (f" {bonus:+d}" if bonus else "") + (f" = {total}" if bonus or len(rolls) > 1 else "")
    return {"rolls": rolls, "special_rolls": [], "value": total, "unit": "totale", "detail": detail, "notes": notes, "won": won}


def roll(spec: str, profile: dict[str, Any] | None = None, sheet: Any = None) -> dict[str, Any]:
    """Tira secondo il marcatore e il profilo. `sheet` serve per i dadi speciali presi dalla scheda."""
    p = parse(spec, profile)
    kind = p["kind"]
    if kind is not None and kind.get("tipo") == "successi" or (kind is None and p["count_at"] is not None):
        if kind is None:
            found = _NOTATION_RE.match(p["notation"])
            if found.group(3):
                raise ValueError("Una riserva che conta i successi non ha bonus: usa 'dadi' in piu' o in meno")
            kind = {"dado": int(found.group(2)), "successo_da": p["count_at"]}
            p["dice"] = int(found.group(1) or 1)
        from_sheet = None
        path = (kind.get("speciali") or {}).get("quanti_da")
        if path and sheet is not None:
            try:
                from_sheet = int(sheet.get(path))
            except (ValueError, TypeError):
                from_sheet = None
        out = _roll_successes(p, kind, from_sheet)
    else:
        out = _roll_sum(p, kind)
    target = p["target"]
    verdict = ""
    if target is not None:
        margin = out["value"] - target
        verdict = f" contro difficolta' {target}: " + (f"RIUSCITO (margine {margin})" if margin >= 0 else f"FALLITO (mancano {-margin})")
    head = f"{out['value']} successi" if out["unit"] == "successi" else f"totale {out['value']}"
    text = f"[Tiro] {p['name']} — {out['detail']} -> {head}{verdict}" + "".join(f". {n}" for n in out["notes"]) + "."
    return {"name": p["name"], "target": target, "success": out["won"], "value": out["value"], "rolls": out["rolls"],
            "special_rolls": out["special_rolls"], "notes": out["notes"], "text": text}


# --- cio' che il narratore deve sapere: generato dal profilo, non scritto a mano ------------------

def prompt_section(profile: dict[str, Any] | None) -> str:
    head = ("\n## Dadi: li tira il programma\n"
            "Tu non tiri dadi e non inventi risultati. Quando le regole vogliono un tiro, FERMATI prima dell'esito e "
            "chiudi la narrazione con un marcatore su una riga sua (uno solo per messaggio, niente scelte dopo). Il "
            "giocatore preme il pulsante, il programma tira e ti arriva un messaggio `[Tiro] ...` con dadi, esito ed "
            "eventuali eventi in MAIUSCOLO: quello fa fede, anche quando rovina i tuoi piani. Narrane le conseguenze "
            "secondo le regole. I numeri li prendi dalla scheda, la difficolta' dalle regole. Se il giocatore ti scrive "
            "a mano un risultato, accettalo.\n")
    if not profile:
        return head + ("Questa campagna non ha ancora un profilo dei dadi: scrivi i dadi per esteso, come dicono le regole in "
                       f"`{MECHANICS_DIR}/`.\n  [[tiro: <nome> | 2d6+1 | difficolta 8]]   somma contro una soglia\n"
                       "  [[tiro: <nome> | 6d10 | successi 6+ | difficolta 3]]   riserva che conta i successi\n")
    lines = [head, f"Tipi di tiro di questa campagna ({profile.get('sistema', 'dal manuale')}):"]
    for k in profile["tiri"]:
        name = k.get("nome", "tiro")
        if k.get("tipo") == "successi":
            shape = f"[[tiro: <nome> | {name}" + ("" if k.get("dadi_fissi") else " | dadi <N>") + " | difficolta <D>]]"
            how = f"d{k.get('dado')}, successo da {k.get('successo_da')}"
        else:
            shape = f"[[tiro: <nome> | {name} | bonus <+K> | difficolta <D>]]"
            how = f"{k.get('dadi_fissi') or 1}d{k.get('dado')} + bonus" + ("; aggiungi `| vantaggio` o `| svantaggio` quando le regole lo danno" if k.get("vantaggio") else "")
        special = k.get("speciali") or {}
        if special:
            how += (f"; i dadi {special.get('nome')} li mette il programma leggendo `{special.get('quanti_da')}` dalla scheda"
                    f" (per forzarli: `| {special.get('nome')} <N>`)")
        lines.append(f"- `{name}` ({how}): {shape}" + (f" — {k['uso']}" if k.get("uso") else ""))
    if profile.get("note"):
        lines.append(f"Da ricordare: {profile['note']}")
    lines.append("Se nessun tipo si adatta puoi ancora scrivere i dadi per esteso: [[tiro: <nome> | 1d6 | difficolta 4]].")
    return "\n".join(lines) + "\n"


# --- dal manuale al profilo: una chiamata, poi resta su disco -----------------------------------

RULE_TITLES = re.compile(r"tir[oi]|dad[oi]|riserv|prov[ae]\b|success|difficolt|critic|confront|test|check|vantaggio", re.I)
DICE_IN_TEXT = re.compile(r"\b\d{0,2}d\d{1,3}\b|\bdad[oi]\b", re.I)
RULES_LIMIT = 16_000

_NULLABLE_INT = {"type": ["integer", "null"]}
_EVENT = {
    "type": "object",
    "properties": {
        "nome": {"type": "string", "description": "Il nome che il manuale da' all'evento"},
        "faccia": {"type": "integer", "description": "La faccia del dado che lo fa scattare"},
        "quando": {"type": "string", "enum": list(WHEN), "description": "sempre; solo se il tiro e' riuscito; solo se e' fallito; solo se c'e' un critico (coppie)"},
        "solo_speciali": {"type": "boolean", "description": "true se conta solo quando la faccia esce su un dado speciale"},
        "effetto": {"type": "string", "description": "Promemoria breve dell'effetto, o stringa vuota"},
    },
    "required": ["nome", "faccia", "quando", "solo_speciali", "effetto"],
    "additionalProperties": False,
}
PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sistema": {"type": "string"},
        "note": {"type": "string", "description": "Regole sui tiri che il narratore deve ricordare (ritiri, margine...), 2-3 frasi"},
        "tiri": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "Una parola minuscola, come la usa il manuale (una per ogni modo di tirare)"},
                    "uso": {"type": "string", "description": "Quando si usa, in una frase"},
                    "tipo": {"type": "string", "enum": ["successi", "somma"], "description": "successi = si contano i dadi riusciti; somma = si sommano i dadi e un bonus"},
                    "dado": {"type": "integer", "description": "Facce del dado"},
                    "dadi_fissi": {**_NULLABLE_INT, "description": "Numero di dadi se e' sempre lo stesso; null se dipende dai tratti"},
                    "successo_da": {**_NULLABLE_INT, "description": "Per `successi`: un dado riesce da questo valore in su"},
                    "vantaggio": {"type": "boolean", "description": "Per `somma`: esiste il tirare due volte e tenere il migliore/peggiore"},
                    "nome_zero_successi": {"type": "string", "description": "Nome dell'esito con zero successi, o stringa vuota"},
                    "coppie": {
                        "type": ["object", "null"],
                        "description": "Se le coppie di una faccia valgono successi in piu'",
                        "properties": {"faccia": {"type": "integer"}, "successi_extra": {"type": "integer", "description": "Successi IN PIU' per ogni coppia, oltre a quelli dei due dadi"}, "nome": {"type": "string"}},
                        "required": ["faccia", "successi_extra", "nome"],
                        "additionalProperties": False,
                    },
                    "speciali": {
                        "type": ["object", "null"],
                        "description": "Dadi di tipo diverso dentro il tiro, il cui numero viene da un valore della scheda",
                        "properties": {"nome": {"type": "string", "description": "Una parola minuscola"}, "quanti_da": {"type": "string", "description": "Percorso nella scheda JSON del valore che dice quanti sono"},
                                       "sostituiscono": {"type": "boolean", "description": "true se prendono il posto di dadi normali, false se si aggiungono"}},
                        "required": ["nome", "quanti_da", "sostituiscono"],
                        "additionalProperties": False,
                    },
                    "eventi": {"type": "array", "items": _EVENT},
                },
                "required": ["nome", "uso", "tipo", "dado", "dadi_fissi", "successo_da", "vantaggio", "nome_zero_successi", "coppie", "speciali", "eventi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sistema", "note", "tiri"],
    "additionalProperties": False,
}

PROFILE_PROMPT = """\
Ricavi dalle regole di un gioco di ruolo il PROFILO DEI DADI: i dati con cui un programma tirera' i dadi al
posto del master. Descrivi SOLO cio' che le regole qui sotto dicono, senza aggiungere nulla dalla tua conoscenza
del gioco. Un tipo di tiro per ogni modo diverso di tirare (di solito da uno a tre). Se una scheda di esempio e'
fornita, usa i suoi percorsi per `quanti_da`. Se le regole non parlano di qualcosa, lascialo null o vuoto."""


def rules_about_dice(campaign: Campaign) -> str:
    parts, size = [], 0
    for f in campaign.files():
        if not f.name.startswith(MECHANICS_DIR + "/"):
            continue
        for title, body in f.sections().items():
            if title and RULE_TITLES.search(title) and size + len(body) <= RULES_LIMIT:
                parts.append(f"## {title} ({f.name})\n{body.strip()}")
                size += len(body)
    if not parts:  # regole brevi, senza un titolo dedicato ai tiri: si guarda nel testo
        for f in campaign.files():
            if f.name.startswith(MECHANICS_DIR + "/"):
                for title, body in f.sections().items():
                    if title and DICE_IN_TEXT.search(body) and size + len(body) <= RULES_LIMIT:
                        parts.append(f"## {title} ({f.name})\n{body.strip()}")
                        size += len(body)
    return "\n\n".join(parts)


def extract_profile(campaign: Campaign, client: Any, model: str, sheet_json: str = "") -> dict[str, Any] | None:
    """Una chiamata: dalle sezioni di `meccanica/` che parlano di tiri al profilo in `meccanica/dadi.json`."""
    rules = rules_about_dice(campaign)
    if not rules:
        log.warning("profilo dadi: in %s/ non ci sono sezioni che parlano di tiri", MECHANICS_DIR)
        return None
    from .prepare import create_message, parse_json_object

    content = rules + (f"\n\nSCHEDA DI ESEMPIO (per i percorsi):\n{sheet_json}" if sheet_json else "")
    response = create_message(client, max_seconds=300, model=model, max_tokens=4000, system=PROFILE_PROMPT,
                              messages=[{"role": "user", "content": content}],
                              output_config={"format": {"type": "json_schema", "schema": PROFILE_SCHEMA}})
    usage = getattr(response, "usage", None)
    from .models import cost_usd

    campaign.record_spend("dadi", model, cost_usd(model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0))
    profile = parse_json_object("\n".join(b.text for b in response.content if b.type == "text"))
    if not profile.get("tiri"):
        log.warning("profilo dadi: il modello non ha trovato tipi di tiro")
        return None
    profile["fonte"] = "ricavato dalle regole in meccanica/ (sezioni su tiri e dadi)"
    save_profile(campaign, profile)
    log.info("profilo dadi salvato: %s", ", ".join(k["nome"] for k in profile["tiri"]))
    return profile
