"""I testi che il modello scrive e il programma ricopia pari pari: una riga, senza marcatori.

Un nome di tabella con una barra dentro spezza il marcatore; una riga di tabella con un a capo e un
`[Tabella] ...` finto in coda diventerebbe un esito che i trigger leggono; una voce di prefazione con
`## Titolo` dentro aprirebbe una sezione nelle regole. Lo schema forzato non lo impedisce: lo impedisce
il programma, con la stessa regola per l'estrazione, la verifica e il test di mutazione.
"""

from __future__ import annotations

import re

MAX_NAME = 80
MAX_LINE = 300
_MARKERS = ("[[", "[Tabella]", "[Tiro]", "[TRIGGER", "[CONTESTO", "<appunti")
_FORBIDDEN_IN_NAME = "|[]`\n\r"


def problema_nome(value: object, max_len: int = MAX_NAME) -> str | None:
    """Perche' `value` non va bene come nome che finisce in un marcatore, o None."""
    if not isinstance(value, str) or not value.strip():
        return "manca il nome"
    bad = sorted({c for c in value if c in _FORBIDDEN_IN_NAME})
    if bad:
        return "il nome non puo' contenere " + ", ".join(repr(c) for c in bad) + ": finisce in un marcatore"
    if len(value.strip()) > max_len:
        return f"nome troppo lungo (oltre {max_len} caratteri)"
    return None


def problema_riga(value: object, max_len: int = MAX_LINE) -> str | None:
    """Perche' `value` non va bene come testo di una riga ricopiata dal programma, o None."""
    if not isinstance(value, str) or not value.strip():
        return "manca il testo"
    if "\n" in value or "\r" in value:
        return "il testo sta su una riga sola"
    found = next((m for m in _MARKERS if m.lower() in value.lower()), None)
    if found:
        return f"il testo non puo' contenere '{found}': e' un segnale del programma"
    if len(value.strip()) > max_len:
        return f"testo troppo lungo (oltre {max_len} caratteri)"
    return None


def riga_pulita(value: object, max_len: int = MAX_LINE) -> str:
    """Il testo ridotto a una riga, senza segnali del programma, entro il tetto; "" se non resta nulla."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    lowered = text.lower()
    if any(m.lower() in lowered for m in _MARKERS):
        return ""
    return text[:max_len].strip()
