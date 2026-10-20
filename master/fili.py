"""I fili aperti e la loro eta': ogni riga porta la sessione in cui e' nata, e un filo fermo da troppo si segnala.

La sezione "Fili aperti di <pg>" in `ambientazione/avventura.md` resta prosa: la scrive lo scriba
(`set_section`) o il consolidamento della memoria, come testo intero. Il codice, a ogni scrittura,
confronta le righe nuove con quelle di prima: una riga che c'era gia' conserva la sua etichetta
`[sN]`, una riga nuova prende la sessione in corso. Cosi' nessuno deve ricordare quando un filo
e' nato, e un filo che nessuno tocca da due sessioni diventa STANTIO: il narratore lo riceve
all'apertura della sessione e il consolidamento lo riceve fra i documenti, con l'invito a
chiuderlo (pagato dal destino) o a rilanciarlo. Lezione degli eventi narrativi di Voyage:
un filo ha un tempo atteso, e dopo il doppio si chiude da solo invece di restare per sempre.
"""

from __future__ import annotations

import re

TAG_RE = re.compile(r"^(\s*[-*]\s*)\[s(\d+)\]\s*")
STALE_AFTER = 2  # sessioni senza che la riga cambi


def _split(line: str) -> tuple[str, int | None, str]:
    """(prefisso di elenco, sessione dell'etichetta o None, testo senza etichetta)."""
    found = TAG_RE.match(line)
    if found:
        return found.group(1), int(found.group(2)), line[found.end():].strip()
    bullet = re.match(r"^(\s*[-*]\s*)", line)
    prefix = bullet.group(1) if bullet else ""
    return prefix, None, line[len(prefix):].strip()


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def tag_lines(old_text: str, new_text: str, session: int) -> str:
    """Il testo nuovo con le etichette: conservate per le righe che c'erano, `[s<session>]` per le nuove."""
    known: dict[str, int] = {}
    for line in (old_text or "").splitlines():
        _, tag, text = _split(line)
        if text and tag is not None:
            known.setdefault(_key(text), tag)
    out = []
    for line in (new_text or "").splitlines():
        if not line.strip():
            out.append(line)
            continue
        prefix, tag, text = _split(line)
        if not text:
            out.append(line)
            continue
        if tag is None:
            tag = known.get(_key(text), session)
        out.append(f"{prefix or '- '}[s{tag}] {text}")
    return "\n".join(out)


def stale(text: str, session: int, after: int = STALE_AFTER) -> list[str]:
    """Le righe nate almeno `after` sessioni fa, senza etichetta della sessione."""
    out = []
    for line in (text or "").splitlines():
        _, tag, body = _split(line)
        if body and tag is not None and session - tag >= after:
            out.append(body)
    return out


def ages(text: str, session: int) -> list[tuple[int | None, str]]:
    """(eta' in sessioni o None, testo) per ogni riga: per chi vuole mostrarle."""
    out = []
    for line in (text or "").splitlines():
        _, tag, body = _split(line)
        if body:
            out.append((None if tag is None else session - tag, body))
    return out
