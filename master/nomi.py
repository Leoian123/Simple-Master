"""Nomi approssimati: il narratore scrive "Incontri", il file dice "incontri nella palude".

Un marcatore che non trova il suo nome ferma il tiro e manda il giocatore a rileggere; ma nove volte su dieci
il nome c'e', scritto a meta'. La regola e' stretta apposta: prima il nome esatto, poi l'unico nome che contiene
cio' che e' stato scritto (o ne e' contenuto). Due candidati non si sciolgono a caso: si rifiuta, nominandoli,
perche' una tabella sbagliata e' peggio di un errore. Vale per le tabelle e per i tipi di tiro.
"""

from __future__ import annotations

MIN_CHARS = 3  # sotto, "d" o "pr" prenderebbero qualunque cosa


def risolvi(wanted: str, names: list[str]) -> tuple[str | None, list[str]]:
    """(nome scelto, candidati). Esatto -> quello; un solo nome che contiene/e' contenuto -> quello; altrimenti (None, candidati)."""
    key = str(wanted or "").strip().lower()
    if not key:
        return None, []
    for n in names:
        if str(n).strip().lower() == key:
            return n, [n]
    if len(key) < MIN_CHARS:
        return None, []
    near = [n for n in names if key in str(n).strip().lower() or str(n).strip().lower() in key]
    return (near[0], near) if len(near) == 1 else (None, near)
