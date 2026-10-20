"""I modi di gioco: una lente sul narratore, non una regola.

Un modo dice al narratore tono, ritmo, quanto il mondo concede alle soluzioni improbabili, quanto
costano gli errori, e come si inclinano i PNG. Non tocca le regole: le difficolta' restano quelle
del manuale, i dadi li tira il programma, la scheda non cambia. E' la lezione dei Game Modes di
Voyage (istruzioni al narratore + istruzioni ai PNG, scelte dal giocatore) senza la parte che da
noi non ha senso, i moltiplicatori alle statistiche: qui il ruleset e' del manuale.

Il modo scelto sta in `stato.json` (`modo`), per campagna; entra nel prompt di sistema come
blocco stabile (cambia solo quando il giocatore lo cambia, e la cache si riscrive una volta).
Tre modi predefiniti; una campagna puo' ridefinirli con `meccanica/modi.json`:
`{"modi": [{"nome", "descrizione", "istruzioni", "png"}, ...]}` (valido tutto o niente).
"""

from __future__ import annotations

import json
from typing import Any

from .campaign import MECHANICS_DIR, Campaign

FILE_NAME = "modi.json"
DEFAULT = "classico"
DEFAULT_MODI: list[dict[str, Any]] = [
    {
        "nome": "quieto",
        "descrizione": "Tono caldo, ritmo disteso: si esplora e si parla, gli errori costano tempo, non la vita.",
        "istruzioni": (
            "Tono caldo, ritmo disteso. Lascia respirare le scene di dialogo e di scoperta. Una soluzione "
            "improbabile ma ben raccontata puo' riuscire: nel dubbio scegli la difficolta' piu' bassa che le regole "
            "ammettono. Un fallimento costa tempo, una risorsa o una complicazione; la morte arriva solo per una "
            "scelta consapevole e annunciata."
        ),
        "png": "I PNG sono inclini ad ascoltare: un estraneo parte dalla curiosita', non dal sospetto; chi e' ostile lo dice prima di colpire.",
    },
    {
        "nome": "classico",
        "descrizione": "Il tono del manuale: difficolta' senza sconti ne' rincari, conseguenze proporzionate.",
        "istruzioni": (
            "Tono e ritmo del manuale. Le difficolta' seguono le regole senza sconti ne' rincari; un fallimento ha "
            "conseguenze proporzionate a cio' che si rischiava; la morte e' possibile quando le regole la prevedono e "
            "il giocatore ha accettato il rischio."
        ),
        "png": "I PNG seguono i loro interessi: aiutano chi gli conviene, diffidano degli sconosciuti, ricordano i torti.",
    },
    {
        "nome": "spietato",
        "descrizione": "Tono asciutto, ritmo serrato: ogni errore lascia un segno, la morte e' sul tavolo.",
        "istruzioni": (
            "Tono asciutto, ritmo serrato. Nel dubbio scegli la difficolta' piu' alta che le regole ammettono. Ogni "
            "fallimento lascia un segno concreto: una ferita, una perdita, un nemico. La morte e' sul tavolo a ogni "
            "scontro e non viene annunciata. Nessuna soluzione riesce per il solo fatto di essere ingegnosa: serve il tiro."
        ),
        "png": "I PNG non fanno sconti: mentono se gli conviene, pretendono qualcosa in cambio, e chi e' stato tradito non dimentica.",
    },
]
TEXT_KEYS = ("nome", "descrizione", "istruzioni")


def modi_problems(modi: Any) -> list[tuple[str, str]]:
    """(percorso, problema) per ogni punto in cui l'elenco dei modi non ha la forma giusta."""
    if not isinstance(modi, list) or not modi:
        return [("", "dev'essere un elenco di modi, non vuoto")]
    out: list[tuple[str, str]] = []
    names: list[str] = []
    for i, m in enumerate(modi):
        if not isinstance(m, dict):
            out.append((f"[{i}]", "un modo e' {nome, descrizione, istruzioni, png}"))
            continue
        for key in TEXT_KEYS:
            if not isinstance(m.get(key), str) or not m[key].strip():
                out.append((f"[{i}].{key}", "testo obbligatorio"))
        if "png" in m and not isinstance(m["png"], str):
            out.append((f"[{i}].png", "se c'e', e' un testo"))
        name = str(m.get("nome") or "").strip().lower()
        if name and name in names:
            out.append((f"[{i}].nome", f"'{name}' e' gia' usato da un altro modo"))
        names.append(name)
    return out


def carica(campaign: Campaign) -> list[dict[str, Any]]:
    """I modi della campagna: quelli di `meccanica/modi.json` se il file e' valido, altrimenti i predefiniti."""
    path = campaign.root / MECHANICS_DIR / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_MODI
    modi = data.get("modi") if isinstance(data, dict) else None
    return modi if modi and not modi_problems(modi) else DEFAULT_MODI


def nomi(campaign: Campaign) -> list[str]:
    return [str(m["nome"]).strip().lower() for m in carica(campaign)]


def trova(campaign: Campaign, name: str | None) -> dict[str, Any] | None:
    wanted = str(name or "").strip().lower()
    return next((m for m in carica(campaign) if str(m["nome"]).strip().lower() == wanted), None)


def attivo(campaign: Campaign) -> dict[str, Any]:
    """Il modo scelto per la campagna; se non c'e' o non esiste piu', `classico` (o il primo dell'elenco)."""
    modi = carica(campaign)
    chosen = trova(campaign, campaign.mode_name())
    if chosen is not None:
        return chosen
    return next((m for m in modi if str(m["nome"]).strip().lower() == DEFAULT), modi[0])


def testo(mode: dict[str, Any]) -> str:
    """Il blocco per il prompt di sistema: la lente, e il promemoria che le regole non cambiano."""
    lines = [f"\n## Modo di gioco: {str(mode['nome']).strip()}", str(mode["istruzioni"]).strip()]
    if str(mode.get("png") or "").strip():
        lines.append("I PNG in questo modo: " + str(mode["png"]).strip())
    lines.append("Il modo e' una lente: cambia tono, ritmo e quanto il mondo concede. Le regole, le difficolta' "
                 "previste dal manuale, i dadi e la scheda non cambiano con il modo.")
    return "\n".join(lines) + "\n"
