"""Le relazioni dei PNG: un numero per PNG, contato dal codice, letto come etichetta.

Ogni PNG ha un atteggiamento verso il personaggio da -100 a 100 (`stato.json` -> `png`). Il
narratore non vede il numero: vede lo STADIO, un nome preso da una scala chiusa che tassella
tutta la fascia (Nemico ... Devoto). Lo scriba non scrive numeri: dice `sale` o `scende` e con
che forza (`lieve`, `media`, `forte`), e il codice applica il passo e clampa. Cosi' il valore
non deriva mai da un'interpretazione, e cambia solo per quello che il narratore ha dichiarato.

La scala predefinita vale per qualunque gioco; un manuale con i suoi nomi (Legami, Fama...) la
sovrascrive con `meccanica/relazioni.json`: `{"stadi": [{"nome", "da", "a"}, ...]}`, che deve
tassellare -100..100 senza buchi (`stadi_problems`, la regola condivisa con la verifica).
"""

from __future__ import annotations

import json
from typing import Any

from .campaign import MECHANICS_DIR, Campaign

MIN, MAX = -100, 100
FILE_NAME = "relazioni.json"
DELTA = {"lieve": 3, "media": 8, "forte": 15}
VERSI = ("sale", "scende")
DEFAULT_STADI: list[dict[str, Any]] = [
    {"nome": "Nemico", "da": -100, "a": -81},
    {"nome": "Ostile", "da": -80, "a": -41},
    {"nome": "Diffidente", "da": -40, "a": -11},
    {"nome": "Neutrale", "da": -10, "a": 10},
    {"nome": "Cordiale", "da": 11, "a": 40},
    {"nome": "Amico", "da": 41, "a": 80},
    {"nome": "Devoto", "da": 81, "a": 100},
]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def stadi_problems(stadi: Any) -> list[tuple[str, str]]:
    """(percorso, problema) per ogni punto in cui la scala non tassella -100..100."""
    if not isinstance(stadi, list) or not stadi:
        return [("", "dev'essere un elenco di stadi, non vuoto")]
    out: list[tuple[str, str]] = []
    rows: list[tuple[int, dict[str, Any]]] = []
    for i, s in enumerate(stadi):
        if not isinstance(s, dict):
            out.append((f"[{i}]", "uno stadio e' {nome, da, a}"))
            continue
        bad = False
        if not str(s.get("nome") or "").strip():
            out.append((f"[{i}].nome", "manca il nome dello stadio"))
            bad = True
        for key in ("da", "a"):
            if not _is_int(s.get(key)) or not MIN <= s[key] <= MAX:
                out.append((f"[{i}].{key}", f"dev'essere un intero fra {MIN} e {MAX}"))
                bad = True
        if not bad:
            rows.append((i, s))
    if out:
        return out
    names = [str(s["nome"]).strip().lower() for _, s in rows]
    if len(set(names)) != len(names):
        out.append(("", "due stadi hanno lo stesso nome"))
    ordered = sorted(rows, key=lambda r: r[1]["da"])
    if ordered[0][1]["da"] != MIN:
        out.append((f"[{ordered[0][0]}].da", f"il primo stadio comincia a {MIN}"))
    if ordered[-1][1]["a"] != MAX:
        out.append((f"[{ordered[-1][0]}].a", f"l'ultimo stadio finisce a {MAX}"))
    for i, s in rows:
        if s["a"] < s["da"]:
            out.append((f"[{i}].a", "la fine dello stadio precede l'inizio"))
    for (_, s), (j, t) in zip(ordered, ordered[1:]):
        if t["da"] != s["a"] + 1:
            out.append((f"[{j}].da", f"deve valere {s['a'] + 1}: gli stadi si toccano senza buchi ne' sovrapposizioni"))
    return out


def carica(campaign: Campaign) -> list[dict[str, Any]]:
    """La scala della campagna: quella del manuale se c'e' ed e' valida, altrimenti la predefinita."""
    path = campaign.root / MECHANICS_DIR / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_STADI
    stadi = data.get("stadi") if isinstance(data, dict) else None
    return stadi if stadi and not stadi_problems(stadi) else DEFAULT_STADI


def clampa(value: Any) -> int:
    return max(MIN, min(MAX, int(value)))


def stadio(value: int, stadi: list[dict[str, Any]] | None = None) -> str:
    value = clampa(value)
    for s in stadi or DEFAULT_STADI:
        if s["da"] <= value <= s["a"]:
            return str(s["nome"])
    return "?"  # una scala con un buco: la verifica lo segnala


def scala(stadi: list[dict[str, Any]] | None = None) -> str:
    """La scala in una riga: `Nemico (-100..-81) · Ostile (-80..-41) · ...`."""
    return " · ".join(f"{s['nome']} ({s['da']}..{s['a']})" for s in sorted(stadi or DEFAULT_STADI, key=lambda s: s["da"]))


def passo(verso: str, forza: str) -> int:
    """Il delta di un movimento: enum in ingresso, numero in uscita."""
    verso, forza = str(verso).strip().lower(), str(forza).strip().lower()
    if verso not in VERSI:
        raise ValueError(f"`verso` dev'essere {' o '.join(VERSI)}, non '{verso}'")
    if forza not in DELTA:
        raise ValueError(f"`forza` dev'essere {', '.join(DELTA)}, non '{forza}'")
    return DELTA[forza] if verso == "sale" else -DELTA[forza]
