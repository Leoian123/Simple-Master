"""I fatti da non perdere: poche righe che la memoria non deve lasciar cadere.

`ambientazione/da-non-perdere.md` e' un elenco corto, una riga per fatto: una promessa, un
debito, una ferita che resta, un grado, un segreto svelato. Cio' che sarebbe stonato se un PNG
lo dimenticasse. Lo scrive il giocatore a mano, e il consolidamento della memoria puo'
aggiungere al massimo due righe per corsa; togliere e' solo a mano. Lo leggono il
consolidamento (per intero, cosi' la cronaca non lo contraddice), l'apertura di una sessione
(per intero) e il contesto del turno (solo le righe che il turno tocca, come per il glossario).
Lezione di Voyage (`summarization.custom`): si dice al condensatore cosa non puo' perdere,
non si spera che lo indovini.
"""

from __future__ import annotations

import re

from .campaign import SETTING_DIR, Campaign

FILE = f"{SETTING_DIR}/da-non-perdere.md"
TITLE = "# Da non perdere"
MAX_PER_RUN = 2       # righe che il consolidamento puo' aggiungere in una corsa
MAX_IN_CONTEXT = 5    # righe pertinenti nel contesto di un turno
MAX_LINES = 30        # oltre, la verifica consiglia di sfoltire: e' un promemoria, non un diario
MIN_WORD = 5
_STOP = {"della", "delle", "dello", "degli", "nella", "nelle", "nello", "negli", "sulla", "sulle", "sullo", "sugli",
         "dalla", "dalle", "dallo", "dagli", "questo", "questa", "quello", "quella", "perche", "ancora", "sempre",
         "mentre", "quando", "prima", "dopo", "senza", "contro", "verso", "essere", "avere", "stato", "stata",
         "fatto", "fatta", "detto", "detta", "tutto", "tutta", "tutti", "tutte", "anche", "molto", "poco", "ogni"}


_BULLET = re.compile(r"^\s*[-*]\s+")


def _lines(text: str) -> list[str]:
    """Solo le righe puntate sono fatti: titoli e note in corsivo restano fuori."""
    out = []
    for line in (text or "").splitlines():
        if _BULLET.match(line):
            body = _BULLET.sub("", line).strip()
            if body:
                out.append(body)
    return out


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def leggi(campaign: Campaign) -> list[str]:
    f = campaign.file(FILE)
    return _lines(f.read()) if f.exists() else []


def aggiungi(campaign: Campaign, proposals: list | None, cap: int = MAX_PER_RUN) -> list[str]:
    """Accoda le proposte nuove (al massimo `cap`, senza doppioni). Ritorna le righe aggiunte."""
    known = {_key(x) for x in leggi(campaign)}
    added: list[str] = []
    for item in proposals or []:
        text = str(item).strip().rstrip(".") if isinstance(item, str) else ""
        if not text or _key(text) in known or len(added) >= cap:
            continue
        known.add(_key(text))
        added.append(text)
    if added:
        f = campaign.file(FILE)
        head = "" if f.exists() and f.read().strip() else f"{TITLE}\n\n*Promesse, debiti, ferite che restano, segreti svelati: cio' che la memoria non deve perdere. Una riga per fatto; si toglie solo a mano.*\n\n"
        f.append(head + "\n".join(f"- {x}" for x in added))
    return added


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]+", text.lower()) if len(w) >= MIN_WORD and w not in _STOP}


def pertinenti(facts: list[str], sources: list[str], limit: int = MAX_IN_CONTEXT) -> list[str]:
    """Le righe che il turno tocca: un nome proprio in comune, oppure due parole piene in comune."""
    haystack = " ".join(sources).lower()
    words_in = _words(haystack)
    out = []
    for fact in facts:
        names = {w for w in re.findall(r"\b[A-ZÀ-Ý][^\W\d_]{2,}", fact) if _key(w) in haystack}
        shared = _words(fact) & words_in
        if names or len(shared) >= 2:
            out.append(fact)
        if len(out) >= limit:
            break
    return out
