"""I dadi li tira il codice, secondo la meccanica estratta dal manuale.

Questo modulo non conosce nessun gioco. Sa tirare dadi in due modi (sommare, o contare i
successi) e applicare cio' che un PROFILO gli dice: che dado si usa, da che valore e' un
successo, quanto valgono le coppie di una faccia, se esistono dadi speciali presi da un valore
della scheda e quali facce fanno scattare un evento con un nome. Il profilo sta in
`meccanica/dadi.json` ed e' ricavato dalle regole che la preparazione ha estratto dal manuale
(`extract_profile`, una chiamata sola, poi resta su disco): un gioco a riserve diventa riserve
di d10 con dadi speciali, un gioco a d20 diventa d20 + bonus contro una soglia, senza toccare il codice.

Flusso: il narratore chiude la narrazione con un marcatore, la pagina lo mostra come pulsante,
`roll` tira, e l'esito torna al narratore come messaggio del giocatore. Tirare non costa chiamate.

    [[tiro: <nome> | <tipo di tiro del profilo> | dadi N | bonus +K | difficolta D]]

Senza profilo resta la forma esplicita, in cui il narratore dice i dadi per esteso:

    [[tiro: <nome> | 2d6+1 | difficolta 8]]      [[tiro: <nome> | 6d10 | successi 6+ | difficolta 3]]
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
from typing import Any

from .campaign import MECHANICS_DIR, Campaign

log = logging.getLogger("master.dadi")

PROFILE_NAME = "dadi.json"
MARK_RE = re.compile(r"\[\[\s*tiro\s*:(.*?)\]\]", re.S | re.I)
_NOTATION_RE = re.compile(r"^\s*(\d{0,3})\s*d\s*(\d{1,4})\s*([+-]\s*\d+)?\s*$", re.I)
MAX_DICE = 100
MAX_EXPLOSIONS = 10  # per dado: un dado che esplode all'infinito e' un errore del profilo, non un tiro
WHEN = ("sempre", "riuscito", "fallito", "critico")  # quando una faccia fa scattare un evento

# Le fasce dell'esito: il margine (successi o totale oltre la difficolta', negativo se sotto) cade in
# una fascia con un nome e una guida per il narratore. Il manuale puo' darle (`esiti` nel tipo di
# tiro); se tace valgono queste, una scala per i tiri a successi e una per le somme. `da`/`a` None =
# fascia aperta. La guida e' cio' che il narratore legge nell'esito: narra quella, non il numero.
DEFAULT_ESITI: dict[str, list[dict[str, Any]]] = {
    "successi": [
        {"nome": "fallito", "da_margine": None, "a_margine": -1, "riuscito": False,
         "guida": "L'azione non riesce. Se c'e' almeno un successo e le regole lo prevedono, puoi offrire una vittoria costosa."},
        {"nome": "riuscito di misura", "da_margine": 0, "a_margine": 0, "riuscito": True,
         "guida": "Riesce appena: l'obiettivo e' raggiunto e basta, un prezzo o una complicazione sono plausibili."},
        {"nome": "riuscito", "da_margine": 1, "a_margine": 2, "riuscito": True,
         "guida": "Riesce con margine: il risultato e' pulito."},
        {"nome": "riuscito bene", "da_margine": 3, "a_margine": None, "riuscito": True,
         "guida": "Riesce con ampio margine: un vantaggio in piu', un dettaglio che apre una porta."},
    ],
    "somma": [
        {"nome": "fallito malamente", "da_margine": None, "a_margine": -10, "riuscito": False,
         "guida": "Fallisce di molto: una conseguenza concreta, non solo il mancato risultato."},
        {"nome": "fallito", "da_margine": -9, "a_margine": -1, "riuscito": False,
         "guida": "L'azione non riesce; la situazione resta aperta."},
        {"nome": "riuscito di misura", "da_margine": 0, "a_margine": 4, "riuscito": True,
         "guida": "Riesce appena: l'obiettivo e' raggiunto e basta, un prezzo o una complicazione sono plausibili."},
        {"nome": "riuscito", "da_margine": 5, "a_margine": 9, "riuscito": True,
         "guida": "Riesce con margine: il risultato e' pulito."},
        {"nome": "riuscito bene", "da_margine": 10, "a_margine": None, "riuscito": True,
         "guida": "Riesce con ampio margine: un vantaggio in piu', un dettaglio che apre una porta."},
    ],
}


def esiti_problems(esiti: Any) -> list[tuple[str, str]]:
    """(percorso, problema) per ogni punto in cui le fasce non sono una scala valida.

    Una scala valida: fasce che si toccano senza buchi ne' sovrapposizioni, aperta ai due capi
    (`da_margine` null sulla piu' bassa, `a_margine` null sulla piu' alta), nomi unici, `riuscito`
    booleano e mai falso sopra una fascia riuscita. E' la stessa regola del validatore e del test di
    mutazione: vive qui perche' il dato e' dei dadi.
    """
    if not isinstance(esiti, list):
        return [("", "dev'essere un elenco di fasce")]
    if not esiti:
        return []
    out: list[tuple[str, str]] = []
    rows: list[tuple[int, dict[str, Any]]] = []
    for i, f in enumerate(esiti):
        if not isinstance(f, dict):
            out.append((f"[{i}]", "una fascia e' {nome, da_margine, a_margine, riuscito, guida}"))
            continue
        if not str(f.get("nome") or "").strip():
            out.append((f"[{i}].nome", "manca il nome della fascia"))
        for key in ("da_margine", "a_margine"):
            v = f.get(key)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
                out.append((f"[{i}].{key}", "null (fascia aperta) oppure un intero"))
        if not isinstance(f.get("riuscito"), bool):
            out.append((f"[{i}].riuscito", "dev'essere true o false"))
        rows.append((i, f))
    if out:
        return out
    names = [str(f["nome"]).strip().lower() for _, f in rows]
    if len(set(names)) != len(names):
        out.append(("", "due fasce hanno lo stesso nome"))
    ordered = sorted(rows, key=lambda r: (r[1]["da_margine"] is not None, r[1]["da_margine"] or 0))
    if ordered[0][1]["da_margine"] is not None:
        out.append((f"[{ordered[0][0]}].da_margine", "la fascia piu' bassa deve essere aperta (null)"))
    if ordered[-1][1]["a_margine"] is not None:
        out.append((f"[{ordered[-1][0]}].a_margine", "la fascia piu' alta deve essere aperta (null)"))
    for (i, f), (j, g) in zip(ordered, ordered[1:]):
        if f["a_margine"] is None or g["da_margine"] is None:
            out.append((f"[{j}].da_margine", "solo la fascia piu' bassa e la piu' alta sono aperte"))
        elif g["da_margine"] != f["a_margine"] + 1:
            out.append((f"[{j}].da_margine", f"deve valere {f['a_margine'] + 1}: le fasce si toccano senza buchi ne' sovrapposizioni"))
        if f["riuscito"] and not g["riuscito"]:
            out.append((f"[{j}].riuscito", "una fascia sopra una riuscita non puo' essere fallita"))
    for i, f in rows:
        if f["da_margine"] is not None and f["a_margine"] is not None and f["a_margine"] < f["da_margine"]:
            out.append((f"[{i}].a_margine", "la fine della fascia precede l'inizio"))
    return out


# I tiri "sotto il valore" (d100 di CoC, BRP, Delta Green, WFRP, Mythras): riesce se il totale e' fino al
# valore della scheda; i gradi sono frazioni del valore (`soglie`), il fallimento critico una faccia alta
# (`fallimento_critico`, che puo' dipendere dal valore), il successo critico una faccia bassa. La guida di un
# grado la da' il manuale (`soglie[].guida`); per gli esiti fissi vale questa.
GUIDA_SOTTO = {
    "successo critico": "Riesce nel modo migliore possibile: un vantaggio concreto in piu'.",
    "riuscito": "Riesce come previsto dalle regole.",
    "fallito": "Non riesce; la situazione resta aperta.",
    "fallimento critico": "Fallisce malamente: una conseguenza concreta, secondo le regole.",
}


def _esiti_di(kind: dict[str, Any] | None, tipo: str) -> list[dict[str, Any]]:
    given = (kind or {}).get("esiti")
    return given if given else DEFAULT_ESITI[tipo]


def _verdetto(kind: dict[str, Any] | None, tipo: str, value: int, target: int | None) -> tuple[bool | None, dict[str, Any] | None]:
    """(riuscito?, fascia) per un valore contro una difficolta'. Senza difficolta': (None, None)."""
    if target is None:
        return None, None
    margin = value - target
    for f in _esiti_di(kind, tipo):
        lo, hi = f.get("da_margine"), f.get("a_margine")
        if (lo is None or margin >= lo) and (hi is None or margin <= hi):
            return bool(f.get("riuscito")), f
    return margin >= 0, None  # fasce con un buco: vale la soglia nuda


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
    """Il tipo di tiro per nome, o per uno dei suoi alias (il nome che il manuale usa in quel passo)."""
    if not profile:
        return None
    wanted = name.strip().lower()
    for k in profile["tiri"]:
        if str(k.get("nome", "")).lower() == wanted:
            return k
    for k in profile["tiri"]:
        if any(str(a).strip().lower() == wanted for a in (k.get("alias") or []) if isinstance(k.get("alias"), list)):
            return k
    # il nome scritto a meta' ("prova" per "prova di abilita'"): solo se e' uno solo (nomi.risolvi); mai per "2d6+1"
    if _NOTATION_RE.match(name):
        return None
    from . import nomi

    names = [str(k.get("nome", "")) for k in profile["tiri"]]
    chosen, near = nomi.risolvi(wanted, names)
    if chosen is None and len(near) > 1:
        raise ValueError(f"'{name.strip()}' puo' essere {' o '.join(repr(n) for n in near)}: scrivi il nome intero")
    return next((k for k in profile["tiri"] if str(k.get("nome", "")) == chosen), None) if chosen else None


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
                           "target": None, "count_at": None, "special": None, "advantage": 0, "value": None}
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
        elif low.startswith("val"):
            out["value"] = number
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
    explode_at = kind.get("esplode_su")
    extra: list[int] = []
    if explode_at is not None:  # ogni faccia da `esplode_su` in su aggiunge un dado alla riserva (il "10 again")
        for face in normal + special:
            extra += _explode(sides, face, int(explode_at))[1:]
    rolls = normal + special + extra
    successes = sum(1 for r in rolls if r >= threshold)
    pair = kind.get("coppie") or {}
    pairs = rolls.count(pair["faccia"]) // 2 if pair.get("faccia") else 0
    successes += pairs * int(pair.get("successi_extra") or 0)
    target = p["target"]
    won, fascia = _verdetto(kind, "successi", successes, target)
    critical = pairs > 0
    notes = []
    if critical and won is not False and pair.get("nome"):
        notes.append(str(pair["nome"]).upper())
    notes += _events(kind.get("eventi"), rolls, won, critical, only_special=False)
    notes += _events(kind.get("eventi"), special, won, critical, only_special=True)
    if successes == 0 and kind.get("nome_zero_successi"):
        notes.append(str(kind["nome_zero_successi"]).upper())
    label = str(special_rule.get("nome") or "speciali")
    detail = (f"{count}d{sides}: {', '.join(map(str, normal)) or '-'}" + (f" | {label}: {', '.join(map(str, special))}" if special else "")
              + (f" | esplosi: {', '.join(map(str, extra))}" if extra else ""))
    return {"rolls": rolls, "special_rolls": special, "value": successes, "unit": "successi", "detail": detail, "notes": notes,
            "won": won, "fascia": fascia}


def _roll_sum(p: dict[str, Any], kind: dict[str, Any] | None) -> dict[str, Any]:
    if kind is not None:
        sides, count, bonus = int(kind.get("dado") or 20), int(p["dice"] or kind.get("dadi_fissi") or 1), int(p["bonus"])
    else:
        found = _NOTATION_RE.match(p["notation"])
        count, sides = int(found.group(1) or 1), int(found.group(2))
        bonus = int((found.group(3) or "0").replace(" ", "")) + int(p["bonus"])
    _check(count, sides)
    keep = _keep(kind)
    explode_at = (kind or {}).get("esplode_su")
    faces = [_die(sides) for _ in range(count)]
    if explode_at is not None:  # ogni dado porta con se' le sue esplosioni: il suo valore e' la somma della catena
        chains = [_explode(sides, f, int(explode_at)) for f in faces]
        rolls = [sum(c) for c in chains]
        shown = ", ".join("+".join(map(str, c)) + (f"={sum(c)}" if len(c) > 1 else "") for c in chains)
    else:
        rolls = list(faces)
        shown = ", ".join(map(str, rolls))
    notes_faces = list(rolls)
    if p["advantage"] and count == 1 and not keep:  # si tira due volte e si tiene il migliore (o il peggiore)
        other = _die(sides)
        kept = max(rolls[0], other) if p["advantage"] > 0 else min(rolls[0], other)
        shown = f"{rolls[0]} e {other}, tengo {kept} ({'vantaggio' if p['advantage'] > 0 else 'svantaggio'})"
        rolls = notes_faces = [kept]
    elif keep:
        n, which = keep
        ordered = sorted(rolls, reverse=(which == "alto"))
        kept_dice = ordered[:n]
        if len(rolls) > n:
            label = "i piu' alti" if which == "alto" else "i piu' bassi"
            shown = f"{shown} -> tengo {', '.join(map(str, kept_dice))} ({label})"
        rolls = notes_faces = kept_dice
    total = sum(rolls) + bonus
    target = p["target"]
    notes: list[str] = []
    pair = (kind or {}).get("coppie") or {}
    if pair.get("faccia") is not None and faces.count(int(pair["faccia"])) >= 2 and pair.get("nome"):
        notes.append(str(pair["nome"]).upper())  # due dadi uguali sulla faccia detta: il critico di chi tiene il piu' alto
    if (kind or {}).get("esiti_su") == "dado":
        decisive = max(rolls) if (keep or (None, "alto"))[1] == "alto" else min(rolls)
        fascia = _fascia_per_valore(kind, decisive)
        won = bool(fascia.get("riuscito")) if fascia else None
    else:
        won, fascia = _verdetto(kind, "somma", total, target)
    notes += _events((kind or {}).get("eventi"), notes_faces, won, False, only_special=False) if count == 1 or p["advantage"] or keep else []
    detail = f"{count}d{sides}: {shown}" + (f" {bonus:+d}" if bonus else "") + (f" = {total}" if bonus or len(rolls) > 1 else "")
    return {"rolls": rolls, "special_rolls": [], "value": total, "unit": "totale", "detail": detail, "notes": notes,
            "won": won, "fascia": fascia}


def _explode(sides: int, first: int, explode_at: int) -> list[int]:
    """Il dado e le sue esplosioni, in ordine di uscita: ogni faccia da `explode_at` in su ne tira un'altra."""
    chain = [first]
    while chain[-1] >= explode_at and len(chain) <= MAX_EXPLOSIONS:
        chain.append(_die(sides))
    return chain


def _keep(kind: dict[str, Any] | None) -> tuple[int, str] | None:
    """(quanti, 'alto'|'basso') se il tipo tiene una parte dei dadi; valida qui, non nel modello."""
    rule = (kind or {}).get("tieni")
    if not rule:
        return None
    if not isinstance(rule, dict):
        raise ValueError("`tieni` dev'essere {quanti, quale}")
    n, which = rule.get("quanti"), str(rule.get("quale") or "").strip().lower()
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("`tieni.quanti` dev'essere un intero da 1 in su")
    if which not in ("alto", "basso"):
        raise ValueError("`tieni.quale` dev'essere `alto` o `basso`")
    return n, which


def _fascia_per_valore(kind: dict[str, Any] | None, value: int) -> dict[str, Any] | None:
    """La fascia in cui cade un valore (non un margine): per gli esiti letti sul dado tenuto."""
    for f in (kind or {}).get("esiti") or []:
        lo, hi = f.get("da_margine"), f.get("a_margine")
        if (lo is None or value >= lo) and (hi is None or value <= hi):
            return f
    return None


def _fumble_from(kind: dict[str, Any], value: int) -> int | None:
    rule = kind.get("fallimento_critico")
    if not rule:
        return None
    below = rule.get("sotto_valore")
    if below is not None and value < int(below):
        return int(rule["da"])
    return int(rule.get("altrimenti_da") or rule["da"])


def _roll_under(p: dict[str, Any], kind: dict[str, Any]) -> dict[str, Any]:
    """Tiro sotto il valore: riesce con un totale fino al valore; i gradi sono frazioni del valore."""
    sides = int(kind.get("dado") or 100)
    count = int(p["dice"] or kind.get("dadi_fissi") or 1)
    _check(count, sides)
    if p["value"] is None:
        raise ValueError("Un tiro sotto il valore ha bisogno di `valore <V>` nel marcatore: il valore letto dalla scheda")
    value = int(p["value"]) + int(p["bonus"])
    rolls = [_die(sides) for _ in range(count)]
    total = sum(rolls)
    fumble = _fumble_from(kind, value)
    crit_to = kind.get("successo_critico_fino_a")
    grade: dict[str, Any] | None = None
    if fumble is not None and total >= fumble:
        esito, won = "fallimento critico", False
    elif crit_to is not None and total <= int(crit_to):
        esito, won = "successo critico", True
    else:
        esito, won = ("riuscito", True) if total <= value else ("fallito", False)
        for s in sorted(kind.get("soglie") or [], key=lambda s: float(s["frazione"])):
            if total <= math.floor(value * float(s["frazione"])):
                grade, esito, won = s, str(s.get("nome") or "grado"), True
                break
    guida = str((grade or {}).get("guida") or GUIDA_SOTTO.get(esito) or GUIDA_SOTTO["riuscito" if won else "fallito"])
    notes = _events(kind.get("eventi"), rolls, won, False, only_special=False)
    detail = f"{count}d{sides}: {', '.join(map(str, rolls))}" + (f" (valore {int(p['value'])} {int(p['bonus']):+d} = {value})" if p["bonus"] else "")
    return {"rolls": rolls, "special_rolls": [], "value": total, "unit": "totale", "detail": detail, "notes": notes, "won": won,
            "fascia": {"nome": esito, "riuscito": won, "guida": guida}, "target": value, "target_label": "valore"}


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
    elif kind is not None and kind.get("tipo") == "sotto":
        out = _roll_under(p, kind)
    else:
        out = _roll_sum(p, kind)
    under = out.get("target_label") == "valore"
    target = out["target"] if under else p["target"]
    verdict, esito, guida = "", "", ""
    if target is not None:
        margin = (target - out["value"]) if under else (out["value"] - target)
        fascia = out["fascia"] or {}
        esito = str(fascia.get("nome") or ("riuscito" if out["won"] else "fallito"))
        guida = str(fascia.get("guida") or "")
        measure = f"margine {margin}" if out["won"] or margin >= 0 else f"mancano {-margin}"
        verdict = f" contro {'valore' if under else 'difficolta' + chr(39)} {target}: {esito.upper()} ({measure})"
    elif out.get("fascia"):  # esiti letti sul dado tenuto: il verdetto non ha bisogno di una difficolta'
        fascia = out["fascia"]
        esito, guida = str(fascia.get("nome") or ""), str(fascia.get("guida") or "")
        verdict = f": {esito.upper()}"
    head = f"{out['value']} successi" if out["unit"] == "successi" else f"totale {out['value']}"
    text = (f"[Tiro] {p['name']} — {out['detail']} -> {head}{verdict}" + "".join(f". {n}" for n in out["notes"]) + "."
            + (f" Guida: {guida}" if guida else ""))
    return {"name": p["name"], "target": target, "success": out["won"], "value": out["value"], "rolls": out["rolls"],
            "special_rolls": out["special_rolls"], "notes": out["notes"], "esito": esito, "guida": guida, "text": text}


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
        elif k.get("tipo") == "sotto":
            shape = f"[[tiro: <nome> | {name} | valore <V>]]"
            how = f"{k.get('dadi_fissi') or 1}d{k.get('dado')} sotto il valore della scheda; `| bonus <+K>` modifica il valore"
        else:
            on_die = k.get("esiti_su") == "dado"
            shape = (f"[[tiro: <nome> | {name}" + ("" if k.get("dadi_fissi") else " | dadi <N>")
                     + ("" if on_die else " | bonus <+K> | difficolta <D>") + "]]")
            how = f"{k.get('dadi_fissi') or '<N>'}d{k.get('dado')}" + ("" if on_die else " + bonus") + ("; aggiungi `| vantaggio` o `| svantaggio` quando le regole lo danno" if k.get("vantaggio") else "")
            keep = k.get("tieni") or {}
            if keep:
                how += f"; si tengono i {keep.get('quanti')} piu' {'alti' if keep.get('quale') == 'alto' else 'bassi'}" + (" e decide il dado tenuto, senza difficolta'" if on_die else "")
        if k.get("esplode_su") is not None:
            how += f"; una faccia da {k['esplode_su']} in su si ritira e si somma"
        special = k.get("speciali") or {}
        if special:
            how += (f"; i dadi {special.get('nome')} li mette il programma leggendo `{special.get('quanti_da')}` dalla scheda"
                    f" (per forzarli: `| {special.get('nome')} <N>`)")
        aliases = [str(a) for a in (k.get("alias") or []) if str(a).strip()] if isinstance(k.get("alias"), list) else []
        lines.append(f"- `{name}` ({how}): {shape}" + (f" — {k['uso']}" if k.get("uso") else "") + (f" (anche: {', '.join(aliases)})" if aliases else ""))
        if k.get("tipo") == "sotto":
            lines.append(f"  esiti sul totale: {scala_sotto(k)}")
        elif k.get("esiti_su") == "dado":
            lines.append(f"  esiti sul dado tenuto: {scala_esiti(k)}")
        else:
            lines.append(f"  esiti per margine: {scala_esiti(k)}")
    if profile.get("note"):
        lines.append(f"Da ricordare: {profile['note']}")
    lines.append("Con una difficolta' l'esito arriva con il NOME della fascia e una guida: narra quella, non reinterpretare il margine.")
    lines.append("Se nessun tipo si adatta puoi ancora scrivere i dadi per esteso: [[tiro: <nome> | 1d6 | difficolta 4]].")
    return "\n".join(lines) + "\n"


def scala_sotto(kind: dict[str, Any]) -> str:
    """La scala di un tiro sotto il valore in una riga, dal migliore al peggiore."""
    parts = []
    crit = kind.get("successo_critico_fino_a")
    if crit is not None:
        parts.append(f"successo critico (fino a {crit})")
    for s in sorted(kind.get("soglie") or [], key=lambda s: float(s["frazione"])):
        parts.append(f"{s.get('nome')} (fino a {s['frazione']} del valore)")
    parts.append("riuscito (fino al valore) · fallito (oltre)")
    rule = kind.get("fallimento_critico") or {}
    if rule.get("da") is not None:
        if rule.get("sotto_valore") is not None:
            parts.append(f"fallimento critico (da {rule['da']} con valore sotto {rule['sotto_valore']}, altrimenti da {rule.get('altrimenti_da') or rule['da']})")
        else:
            parts.append(f"fallimento critico (da {rule['da']})")
    return " · ".join(parts)


def scala_esiti(kind: dict[str, Any] | None, tipo: str | None = None) -> str:
    """Le fasce di un tipo di tiro in una riga: `fallito (fino a -1) · riuscito di misura (0) · ...`."""
    tipo = tipo or str((kind or {}).get("tipo") or "somma")
    parts = []
    for f in sorted(_esiti_di(kind, tipo if tipo in DEFAULT_ESITI else "somma"),
                    key=lambda f: (f.get("da_margine") is not None, f.get("da_margine") or 0)):
        lo, hi = f.get("da_margine"), f.get("a_margine")
        span = f"fino a {hi}" if lo is None else f"da {lo}" if hi is None else f"{lo}" if lo == hi else f"{lo}..{hi}"
        parts.append(f"{f.get('nome')} ({span})")
    return " · ".join(parts)


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
_ESITO = {
    "type": "object",
    "properties": {
        "nome": {"type": "string", "description": "Il nome che il manuale da' a questo esito (es. 'vittoria costosa')"},
        "da_margine": {**_NULLABLE_INT, "description": "Margine minimo compreso; null = aperta in basso"},
        "a_margine": {**_NULLABLE_INT, "description": "Margine massimo compreso; null = aperta in alto"},
        "riuscito": {"type": "boolean", "description": "true se in questa fascia l'azione riesce"},
        "guida": {"type": "string", "description": "Come narrare questo esito secondo il manuale, in una frase"},
    },
    "required": ["nome", "da_margine", "a_margine", "riuscito", "guida"],
    "additionalProperties": False,
}
_SOGLIA = {
    "type": "object",
    "properties": {
        "nome": {"type": "string", "description": "Il nome del grado nel manuale (es. 'successo estremo')"},
        "frazione": {"type": "number", "description": "Il grado vale se il totale e' fino a questa frazione del valore (es. 0.5 = meta', 0.2 = un quinto)"},
        "guida": {"type": "string", "description": "Come narrare questo grado, in una frase; stringa vuota se il manuale non lo dice"},
    },
    "required": ["nome", "frazione", "guida"],
    "additionalProperties": False,
}
_ESEMPIO = {
    "type": "object",
    "properties": {
        "marcatore": {"type": "string", "description": "Un marcatore completo, come lo scriverebbe il narratore: `<nome> | <tipo> | dadi N | difficolta D` (per i dadi speciali forzali: `| <nome dei dadi speciali> 2`)"},
        "dadi": {"type": "array", "items": {"type": "integer"}, "description": "Le facce uscite, in ordine, tante quante il tiro ne usa"},
        "atteso": {
            "type": "object",
            "properties": {
                "valore": {**_NULLABLE_INT, "description": "Successi o totale attesi"},
                "esito": {"type": ["string", "null"], "description": "Il nome dell'esito atteso (fascia o grado), minuscolo"},
                "riuscito": {"type": ["boolean", "null"], "description": "Se l'azione riesce"},
            },
            "required": ["valore", "esito", "riuscito"],
            "additionalProperties": False,
        },
    },
    "required": ["marcatore", "dadi", "atteso"],
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
                    "alias": {"type": "array", "items": {"type": "string"}, "description": "Altri nomi con cui il manuale chiama lo stesso tiro ('test', 'prova di abilita''); [] se nessuno"},
                    "uso": {"type": "string", "description": "Quando si usa, in una frase"},
                    "tipo": {"type": "string", "enum": ["successi", "somma", "sotto"], "description": "successi = si contano i dadi riusciti; somma = si sommano i dadi e un bonus contro una difficolta'; sotto = si riesce con un totale fino al valore della scheda (d100 roll-under)"},
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
                    "soglie": {"type": "array", "items": _SOGLIA, "description": "Solo per `sotto`: i gradi di successo come frazioni del valore (es. difficile 0.5, estremo 0.2); [] altrimenti"},
                    "fallimento_critico": {
                        "type": ["object", "null"],
                        "description": "Solo per `sotto`: da quale totale il tiro e' un fallimento critico; `sotto_valore`/`altrimenti_da` se la soglia dipende dal valore (es. 96 sotto 50, altrimenti 100)",
                        "properties": {"da": {"type": "integer"}, "sotto_valore": _NULLABLE_INT, "altrimenti_da": _NULLABLE_INT},
                        "required": ["da", "sotto_valore", "altrimenti_da"],
                        "additionalProperties": False,
                    },
                    "successo_critico_fino_a": {**_NULLABLE_INT, "description": "Solo per `sotto`: un totale fino a questo e' un successo critico (es. 1); null altrimenti"},
                    "tieni": {
                        "type": ["object", "null"],
                        "description": "Per `somma`: si tirano N dadi e se ne tengono `quanti`, i piu' alti o i piu' bassi (es. Forged in the Dark: dadi <N>, tieni 1 alto; due dadi a venti facce, tieni 1). null se si sommano tutti",
                        "properties": {"quanti": {"type": "integer"}, "quale": {"type": "string", "enum": ["alto", "basso"]}},
                        "required": ["quanti", "quale"],
                        "additionalProperties": False,
                    },
                    "esiti_su": {"type": "string", "enum": ["margine", "dado"], "description": "Per `somma`: `margine` = le fasce di `esiti` si leggono sul margine contro la difficolta'; `dado` = si leggono sul dado tenuto, senza difficolta' (es. 6 pieno, 4-5 con conseguenze, 1-3 fallito)"},
                    "esplode_su": {**_NULLABLE_INT, "description": "Una faccia da questo valore in su si ritira e si somma (somma) o aggiunge un dado (successi); null se i dadi non esplodono"},
                    "esempi": {"type": "array", "items": _ESEMPIO, "description": "Da 2 a 4 casi con dadi fissi ed esito atteso SECONDO LE REGOLE: il programma li esegue e rifiuta un profilo che non li rispetta"},
                    "esiti": {
                        "type": "array",
                        "description": "Le fasce che il manuale da' all'esito in base al MARGINE (successi o totale oltre la difficolta', negativo se sotto): devono coprire tutti i margini senza buchi, la piu' bassa aperta in basso (da_margine null) e la piu' alta in alto (a_margine null). [] se il manuale non gradua l'esito: valgono fasce predefinite",
                        "items": _ESITO,
                    },
                },
                "required": ["nome", "alias", "uso", "tipo", "dado", "dadi_fissi", "successo_da", "vantaggio", "nome_zero_successi", "coppie", "speciali", "eventi", "esiti",
                             "soglie", "fallimento_critico", "successo_critico_fino_a", "tieni", "esiti_su", "esplode_su", "esempi"],
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
fornita, usa i suoi percorsi per `quanti_da`. Se le regole non parlano di qualcosa, lascialo null o vuoto.
`esiti`: solo se il manuale gradua l'esito in base al margine (es. fallimento, successo, successo con margine
ampio, vittoria costosa): una fascia per grado, con il nome del manuale e una frase su come si narra; le fasce
coprono tutti i margini senza buchi, la piu' bassa aperta in basso e la piu' alta in alto. Altrimenti [].
`tipo` sotto: per i sistemi in cui si riesce tirando FINO AL valore della scheda (d100 roll-under): i gradi in
`soglie` come frazioni del valore, il fallimento critico e il successo critico come totali.
`tieni` + `esiti_su: dado`: per i sistemi in cui decide il dado migliore (o peggiore) e non la somma (Forged
in the Dark, due dadi a venti facce): le fasce di `esiti` sono allora valori del dado tenuto, non margini. `esplode_su`: la
faccia che si ritira e si somma (o aggiunge un dado alla riserva), se il manuale lo prevede. Nei campioni le
facce vanno date nell'ordine in cui il programma le consuma: ogni dado seguito dalle sue esplosioni.
`esempi`: per ogni tipo di tiro da 2 a 4 casi con le facce uscite e l'esito che le regole prevedono (valore,
nome dell'esito, riuscito). Il programma li esegue: un profilo i cui esempi non tornano viene rifatto."""


class _DadiFissi:
    """Sostituisce il dado con una sequenza data: i campioni si eseguono senza caso."""

    def __init__(self, faces: list[int]):
        self.faces = list(faces)
        self.spent = 0

    def __enter__(self):
        global _die
        self._real = _die
        it = iter(self.faces)

        def fixed(sides: int) -> int:
            self.spent += 1
            try:
                return int(next(it))
            except StopIteration:
                raise ValueError(f"il tiro usa piu' di {len(self.faces)} dadi: aggiungi facce a `dadi`") from None

        _die = fixed
        return self

    def __exit__(self, *exc):
        global _die
        _die = self._real
        return False


def verifica_esempi(profile: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(percorso, problema) per ogni campione del profilo che il programma non riproduce. Zero chiamate."""
    out: list[tuple[str, str]] = []
    if not isinstance(profile, dict) or not isinstance(profile.get("tiri"), list):
        return out
    for i, kind in enumerate(profile["tiri"]):
        if not isinstance(kind, dict):
            continue
        for j, ex in enumerate(kind.get("esempi") or []):
            at = f"tiri[{i}].esempi[{j}]"
            if not isinstance(ex, dict) or not isinstance(ex.get("dadi"), list) or not isinstance(ex.get("atteso"), dict):
                out.append((at, "un campione e' {marcatore, dadi: [facce], atteso: {valore, esito, riuscito}}"))
                continue
            try:
                with _DadiFissi(ex["dadi"]):
                    r = roll(str(ex.get("marcatore") or ""), profile)
            except (ValueError, TypeError, KeyError) as exc:
                out.append((f"{at}.marcatore", str(exc)))
                continue
            atteso = ex["atteso"]
            if atteso.get("valore") is not None and int(atteso["valore"]) != r["value"]:
                out.append((f"{at}.atteso.valore", f"atteso {atteso['valore']}, il programma da' {r['value']}"))
            if atteso.get("esito") is not None and str(atteso["esito"]).strip().lower() != r["esito"].lower():
                out.append((f"{at}.atteso.esito", f"atteso '{atteso['esito']}', il programma da' '{r['esito'] or 'nessun esito'}'"))
            if atteso.get("riuscito") is not None and bool(atteso["riuscito"]) != bool(r["success"]):
                out.append((f"{at}.atteso.riuscito", f"atteso {atteso['riuscito']}, il programma da' {r['success']}"))
    return out


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


def _nomi_puliti(kinds: Any) -> list[dict[str, Any]]:
    """I tipi di tiro il cui nome puo' stare in un marcatore; gli altri (una barra, una parentesi quadra) si scartano."""
    from . import testi

    out = []
    for k in kinds if isinstance(kinds, list) else []:
        if not isinstance(k, dict):
            continue
        problem = testi.problema_nome(k.get("nome"))
        if problem:
            log.warning("profilo dadi: tipo scartato %r: %s", k.get("nome"), problem)
            continue
        if isinstance(k.get("alias"), list):
            k["alias"] = [a for a in k["alias"] if isinstance(a, str) and not testi.problema_nome(a)]
        out.append(k)
    return out


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
    raw = "\n".join(b.text for b in response.content if b.type == "text")
    profile = parse_json_object(raw)
    profile["tiri"] = _nomi_puliti(profile.get("tiri"))
    if not profile.get("tiri"):
        log.warning("profilo dadi: il modello non ha trovato tipi di tiro")
        return None
    problems = verifica_esempi(profile)
    if problems:
        # i campioni non tornano: una seconda chiamata con l'errore sotto gli occhi, poi si salva comunque, segnando
        log.warning("profilo dadi: %d campioni non tornano, rifaccio una volta: %s", len(problems), "; ".join(f"{p}: {m}" for p, m in problems[:4]))
        complaint = ("Il programma ha eseguito i tuoi `esempi` con le facce indicate e questi non tornano:\n"
                     + "\n".join(f"- {p}: {m}" for p, m in problems)
                     + "\nRifai il profilo INTERO: o il tipo di tiro e' descritto male, o il campione sbaglia l'esito "
                       "secondo le regole. Correggi cio' che non rispetta le regole, non il campione per far tornare i conti.")
        response = create_message(client, max_seconds=300, model=model, max_tokens=4000, system=PROFILE_PROMPT,
                                  messages=[{"role": "user", "content": content}, {"role": "assistant", "content": raw},
                                            {"role": "user", "content": complaint}],
                                  output_config={"format": {"type": "json_schema", "schema": PROFILE_SCHEMA}})
        usage = getattr(response, "usage", None)
        campaign.record_spend("dadi", model, cost_usd(model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0))
        second = parse_json_object("\n".join(b.text for b in response.content if b.type == "text"))
        if second.get("tiri"):
            profile = second
        problems = verifica_esempi(profile)
        if problems:
            log.warning("profilo dadi: %d campioni non tornano ancora: salvo e la verifica li segnala", len(problems))
            profile["campioni_falliti"] = [f"{p}: {m}" for p, m in problems]
    profile.pop("campioni_falliti", None) if not problems else None
    profile["fonte"] = "ricavato dalle regole in meccanica/ (sezioni su tiri e dadi)"
    save_profile(campaign, profile)
    log.info("profilo dadi salvato: %s", ", ".join(k["nome"] for k in profile["tiri"]))
    return profile
