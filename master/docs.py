"""La documentazione a sezioni: una pagina per ogni dato che ha una forma, letta da chi deve toccarlo.

Chi lavora sul progetto (una persona o un agente) non deve rileggere il README intero per
sapere com'e' fatta una scheda o quali strumenti ha lo scriba: carica la sezione che gli
serve, con `python main.py --doc <sezione>`, e vale la regola "se un campo non e' nella
sezione, non esiste". E' il modo in cui la community di Voyage documenta lo schema del
mondo per gli agenti (una pagina per sezione, fetchabile da sola).

Le sezioni che hanno uno schema nel codice sono GENERATE da quello schema, cosi' non
possono restare indietro: `dadi` da `dice.PROFILE_SCHEMA`, `strumenti-scriba` da
`tools.TOOLS`, `appunti` e `scheda` dalle parti dei prompt che le definiscono. Le altre
(`campagna`, `stato`, `glossario`, `verifica`) sono scritte a mano in `docs/sezioni/`.
`--doc --rigenera` riscrive i file generati; un test verifica che siano aggiornati.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SECTIONS_DIR = ROOT / "docs" / "sezioni"
GENERATED_MARK = "<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->"

# nome -> (titolo, una riga per l'indice, cosa toccare per cambiarla)
INDEX: dict[str, tuple[str, str, str]] = {
    "campagna": ("La cartella di una campagna", "le tre cartelle, i file fissi, cosa e' indicizzato e cosa no", "docs/sezioni/campagna.md"),
    "scheda": ("La scheda del personaggio", "il JSON di schede/<pg>.json: gruppi, percorsi, esperienza, le due velocita', la vista", "character.BUILDER_PROMPT, sheet.py"),
    "appunti": ("Gli appunti del narratore e lo scriba", "le righe che il narratore lascia e come lo scriba le trascrive", "engine.MASTER_PROMPT, engine.SCRIBE_PROMPT"),
    "strumenti-scriba": ("Gli strumenti dell'API", "ogni tool con parametri, chi lo usa e cosa viene rifiutato", "tools.py"),
    "dadi": ("Il profilo dei dadi", "meccanica/dadi.json: campi, marcatore [[tiro: ...]], esito [Tiro]", "dice.PROFILE_SCHEMA"),
    "stato": ("Lo stato della campagna", "stato.json, sessioni/<pg>.jsonl, spese.jsonl, impostazioni.json", "docs/sezioni/stato.md"),
    "glossario": ("Il glossario", "la tabella, i tipi, come entra nel contesto del turno", "docs/sezioni/glossario.md"),
    "verifica": ("La verifica della campagna", "i tre livelli, il JSON, l'hook", "docs/sezioni/verifica.md"),
}


def names() -> list[str]:
    return list(INDEX)


def listing() -> str:
    width = max(len(n) for n in INDEX)
    lines = ["Sezioni della documentazione (python main.py --doc <sezione>):"]
    for name, (title, line, _) in INDEX.items():
        mark = " (generata)" if name in GENERATED else ""
        lines.append(f"  {name:<{width}}  {title}: {line}{mark}")
    lines.append("Regola: se un campo non e' nella sezione, non esiste. `--doc --rigenera` riscrive le sezioni generate.")
    return "\n".join(lines)


def render(name: str) -> str:
    """Il testo di una sezione: generato dal codice, oppure letto da docs/sezioni/."""
    if name not in INDEX:
        raise KeyError(f"Sezione sconosciuta '{name}'. Esistono: {', '.join(INDEX)}")
    if name in GENERATED:
        title = INDEX[name][0]
        return f"# {title}\n\n{GENERATED_MARK}\n\n{GENERATED[name]().strip()}\n"
    path = SECTIONS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Manca {path.relative_to(ROOT)}: la sezione '{name}' va scritta a mano")
    return path.read_text(encoding="utf-8")


def stale() -> list[str]:
    """Le sezioni generate il cui file su disco non coincide con il codice."""
    out = []
    for name in GENERATED:
        path = SECTIONS_DIR / f"{name}.md"
        if not path.is_file() or path.read_text(encoding="utf-8") != render(name):
            out.append(name)
    return out


def regenerate() -> list[str]:
    """Riscrive i file delle sezioni generate. Ritorna quelle cambiate."""
    changed = stale()
    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    for name in changed:
        (SECTIONS_DIR / f"{name}.md").write_text(render(name), encoding="utf-8", newline="\n")
    return changed


# --- i pezzi comuni ------------------------------------------------------------------------

def _prompt_section(prompt: str, title: str) -> str:
    """Il corpo di una sezione `## title` di un prompt, com'e' scritta li'."""
    parts = re.split(r"(?m)^## ", prompt)
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        if head.strip() == title:
            return body.strip("\n")
    raise KeyError(f"Il prompt non ha la sezione '{title}'")


def _type_of(schema: dict[str, Any]) -> str:
    t = schema.get("type", "qualunque")
    if isinstance(t, list):
        t = " o ".join(map(str, t))  # mai `|`: e' il separatore delle celle della tabella
    if t == "array" and isinstance(schema.get("items"), dict):
        t = f"elenco di {_type_of(schema['items'])}"
    if "enum" in schema:
        t += " (" + ", ".join(f"`{v}`" for v in schema["enum"]) + ")"
    for key in ("minimum", "maximum"):
        if key in schema:
            t += f", {'min' if key == 'minimum' else 'max'} {schema[key]}"
    return str(t)


def _params_table(schema: dict[str, Any], indent: str = "") -> str:
    props = schema.get("properties") or {}
    if not props:
        return f"{indent}(nessun parametro)"
    required = set(schema.get("required") or [])
    lines = [f"{indent}| parametro | tipo | obbligatorio | significato |", f"{indent}|---|---|---|---|"]
    for key, sub in props.items():
        desc = str(sub.get("description") or "").replace("|", "\\|")
        if sub.get("type") == "array" and isinstance(sub.get("items"), dict) and sub["items"].get("properties"):
            inner = ", ".join(f"`{k}`" + (f" ({v.get('description')})" if v.get("description") else "") for k, v in sub["items"]["properties"].items())
            desc = (desc + " " if desc else "") + f"Ogni elemento: {inner}."
        lines.append(f"{indent}| `{key}` | {_type_of(sub)} | {'si' if key in required else 'no'} | {desc} |")
    return "\n".join(lines)


# --- le sezioni generate --------------------------------------------------------------------

def _gen_tools() -> str:
    from .engine import READ_TOOL_NAMES
    from .tools import SAVE_SHEET_TOOL, SCENE_TOOL, TOOLS, WHOLE_FILE_LIMIT

    groups = [
        ("Lettura (narratore, costruttore di schede)", [t for t in TOOLS if t["name"] in READ_TOOL_NAMES]),
        ("Scrittura (solo lo scriba)", [t for t in TOOLS if t["name"] not in READ_TOOL_NAMES] + [SCENE_TOOL]),
        ("Salvataggio della scheda (solo il costruttore)", [SAVE_SHEET_TOOL]),
    ]
    out = ["Tutti i tool sono definiti in `master/tools.py` ed eseguiti da `ToolExecutor` sui file della",
           "campagna: l'AI chiede cosa leggere o cosa cambiare, il codice esegue e non riscrive mai un file intero.",
           "Chi ha quali strumenti e' deciso dal codice: il narratore del gioco ha solo quelli di lettura e",
           "scrive gli appunti; lo scriba ha quelli di scrittura e non legge; il costruttore legge e salva la scheda.", ""]
    for title, tools in groups:
        out.append(f"## {title}")
        out.append("")
        for t in tools:
            out.append(f"### `{t['name']}`")
            out.append("")
            out.append(str(t["description"]).strip())
            out.append("")
            out.append(_params_table(t["input_schema"]))
            out.append("")
    out += [
        "## Cosa viene rifiutato (il tool torna un errore, nulla viene scritto)",
        "",
        f"- `read_file` senza `section` su un file oltre {WHOLE_FILE_LIMIT:,} caratteri: ritorna l'elenco delle sezioni, non il testo.".replace(",", "."),
        "- Scrivere sulla vista `schede/<pg>.md` di una scheda che ha il JSON: la vista e' generata, i dati si cambiano con `update_sheet`.",
        "- `update_sheet` su un percorso che comincia con `esperienza`: i punti entrano solo da `award_xp` e `spend_xp`.",
        "- `update_sheet` su un percorso che non esiste (il messaggio elenca le chiavi disponibili), un indice fuori dall'elenco, o un gruppo usato come valore. Le modifiche sono tutte o nessuna.",
        "- `award_xp` e `spend_xp` dallo scriba quando gli appunti del narratore non hanno una riga `pe:` (il motore apre il permesso solo per quel giro); `award_xp` con la via `creazione` (e' del costruttore, via `save_sheet`).",
        "- `award_xp` con una via sconosciuta, con punti non positivi, senza motivo, o per la via `sessione` quando la sessione in corso li ha gia' ricevuti.",
        "- `spend_xp` se i punti disponibili non bastano, o se una delle modifiche al tratto fallisce (spesa e crescita insieme o niente).",
        "- `upsert_glossary` con un termine vuoto, o con un termine che non compare nel file indicato (salvo i tipi `file`, `fonte`, `cronaca`): prima la sezione, poi la voce.",
        "- `set_scene` con `presenti` o `da_richiamare` che non sono elenchi di testi, o con un campo di testo che non e' un testo: la scena di prima resta.",
        "- `set_clock` con segmenti fuori da 2..12 (0 toglie l'orologio); `tick_clock` su un nome che non esiste (il messaggio elenca quelli attivi).",
        "- Lo scriba che chiama uno strumento di lettura o `save_sheet`: 'Lo scriba non legge e non crea schede: trascrive soltanto.'",
        "",
        "`tests/test_scriba_ostile.py` prova ognuno di questi rifiuti con un modello finto che rispetta lo schema e cerca la scappatoia.",
        "- Qualunque nome di file fuori dalla campagna, con `..`, o con estensione diversa da `.md`/`.txt`.",
    ]
    return "\n".join(out)


def _gen_dice() -> str:
    from .dice import PROFILE_SCHEMA, WHEN

    kind = PROFILE_SCHEMA["properties"]["tiri"]["items"]
    event = kind["properties"]["eventi"]["items"]
    out = [
        "Il profilo sta in `meccanica/dadi.json`. Lo ricava una chiamata (`extract_profile`, dal pulsante",
        "'Ricava i dadi dalle regole' o alla fine della preparazione) leggendo le sezioni di `meccanica/` che",
        "parlano di tiri; poi resta su disco e `master/dice.py` lo applica senza conoscere il gioco.",
        "Il modulo sa fare due cose: sommare dadi e un bonus contro una soglia (`somma`), o contare i dadi",
        "che superano un valore (`successi`). Tutto il resto e' nel profilo.",
        "",
        "## Il file",
        "",
        "| campo | tipo | significato |",
        "|---|---|---|",
        f"| `sistema` | testo | {PROFILE_SCHEMA['properties']['sistema'].get('description', 'il nome del sistema di gioco')} |",
        f"| `note` | testo | {PROFILE_SCHEMA['properties']['note']['description']} |",
        "| `tiri` | elenco | un elemento per ogni modo di tirare (i campi qui sotto) |",
        "| `fonte` | testo | scritto dal programma: da quali sezioni e' stato ricavato |",
        "",
        "## Un tipo di tiro (`tiri[i]`)",
        "",
        _params_table(kind),
        "",
        "`coppie`: " + "; ".join(f"`{k}` {v.get('description', '')}".strip() for k, v in kind["properties"]["coppie"]["properties"].items()) + ".",
        "`speciali`: " + "; ".join(f"`{k}` {v.get('description', '')}".strip() for k, v in kind["properties"]["speciali"]["properties"].items()) + ".",
        "",
        "## Un evento (`tiri[i].eventi[j]`)",
        "",
        "Una faccia che, quando esce, fa scattare qualcosa con un nome (critico caotico, fallimento bestiale).",
        "",
        _params_table(event),
        "",
        f"`quando` vale uno tra: {', '.join(f'`{w}`' for w in WHEN)}.",
        "",
        "## Il marcatore che il narratore scrive",
        "",
        "Uno per messaggio, su una riga sua, in coda alla narrazione. La pagina lo mostra come pulsante.",
        "",
        "```",
        "[[tiro: <nome> | <tipo di tiro del profilo> | dadi N | bonus +K | difficolta D]]",
        "[[tiro: <nome> | <tipo> | dadi N | <nome dei dadi speciali> N]]      per forzare i dadi speciali",
        "[[tiro: <nome> | <tipo> | bonus +K | difficolta D | vantaggio]]      o svantaggio, se il tipo lo ammette",
        "[[tiro: <nome> | 2d6+1 | difficolta 8]]                             senza profilo: dadi per esteso",
        "[[tiro: <nome> | 6d10 | successi 6+ | difficolta 3]]                senza profilo: riserva a successi",
        "```",
        "",
        "`dadi` e' obbligatorio se il tipo non ha `dadi_fissi`; `difficolta` e' facoltativa (senza, nessun verdetto).",
        "I dadi speciali li conta il programma leggendo `quanti_da` dalla scheda del personaggio attivo.",
        "`/tira 2d6+1` nel campo di testo della pagina fa un tiro libero.",
        "",
        "## L'esito che torna al narratore",
        "",
        "Tirare non costa chiamate. L'esito e' il messaggio del giocatore del turno dopo:",
        "",
        "```",
        "[Tiro] <nome> — 5d10: 7, 6, 4 | fame: 2, 9 -> 3 successi contro difficolta' 2: RIUSCITO (margine 1). CRITICO. FALLIMENTO BESTIALE (effetto).",
        "[Tiro] <nome> — 1d20: 14 +3 = 17 contro difficolta' 15: RIUSCITO (margine 2).",
        "```",
        "",
        "Gli eventi compaiono in MAIUSCOLO con l'effetto tra parentesi; `nome_zero_successi` compare quando",
        "nessun dado riesce; con `difficolta` il verdetto e' RIUSCITO (margine) o FALLITO (mancano).",
        "Nel codice: `dice.roll(spec, profile, sheet) -> {name, target, success, value, rolls, special_rolls, notes, text}`.",
    ]
    return "\n".join(out)


def _gen_notes() -> str:
    from .engine import MASTER_PROMPT, SCRIBE_PROMPT

    out = [
        "Il turno di gioco e' due chiamate che non si parlano. Il narratore scrive la narrazione e, in coda,",
        "un blocco `<appunti>` che il giocatore non vede e che non finisce nel salvataggio; lo scriba",
        "(modello economico) riceve narrazione, appunti, la parte volatile della scheda, la scena precedente,",
        "gli orologi e i fili aperti, e fa tutte le scritture con gli strumenti (sezione `strumenti-scriba`).",
        "Quello che non sta negli appunti o nella narrazione non viene ricordato.",
        "",
        "## Cosa legge il narratore (dal prompt di sistema, parola per parola)",
        "",
        "### Come lavora a ogni turno",
        "",
        _prompt_section(MASTER_PROMPT, "Come lavori a ogni turno: una chiamata"),
        "",
        "### Orologi",
        "",
        _prompt_section(MASTER_PROMPT, "Orologi"),
        "",
        "### Esperienza",
        "",
        _prompt_section(MASTER_PROMPT, "Esperienza"),
        "",
        "## Cosa legge lo scriba (il suo prompt intero, parola per parola)",
        "",
        SCRIBE_PROMPT.strip(),
        "",
        "## Cosa fa il codice intorno",
        "",
        "- Gli appunti si separano dalla narrazione con la regex `<appunti>...</appunti>`; `nulla`, `niente`, `nessuno` = niente da trascrivere, ma lo scriba parte lo stesso per `set_scene`.",
        "- Se lo scriba fallisce (errore API) il turno resta valido e gli appunti restano in attesa: partono con quelli del turno dopo, sotto 'APPUNTI RIMASTI DA UN TURNO PRECEDENTE'.",
        "- Lo scriba ha due giri al massimo: il secondo solo per correggere uno strumento fallito.",
        "- Cio' che lo scriba appende al diario finisce sotto `## Appunti`: e' provvisorio, il consolidamento della memoria lo sostituisce con la cronaca (`memory.py`).",
        "- Gli orologi pieni arrivano al narratore in testa al messaggio del turno come `[OROLOGIO PIENO: ...]`, una volta sola, e spariscono; quelli attivi come `[Orologi: nome 2/6; ...]`.",
    ]
    return "\n".join(out)


def _gen_sheet() -> str:
    from .character import BUILDER_PROMPT
    from .sheet import ONCE_PER_SESSION, XP_ROUTES, Sheet

    routes = "\n".join(f"| `{k}` | {v} |" for k, v in XP_ROUTES.items())
    out = [
        "La scheda di un personaggio giocante e' `schede/<slug>.json` (lo slug e' il nome in minuscolo con `-`).",
        "E' l'unica fonte: ogni valore di gioco e' un numero. `schede/<slug>.md` e' la vista leggibile rigenerata",
        "a ogni salvataggio: non si scrive a mano e gli strumenti di testo rifiutano di toccarla. Una scheda",
        "solo testo (`.md` senza `.json`) funziona ancora e diventa dati la prima volta che il costruttore la salva.",
        "",
        "## Come e' fatta (dal prompt del costruttore di schede, parola per parola)",
        "",
        _prompt_section(BUILDER_PROMPT, "Come si scrive `data`"),
        "",
        "La chiave `nome` sta in cima, messa dal programma al salvataggio (`Sheet.replace`).",
        "",
        "## I percorsi (`update_sheet`, `Sheet.get` / `Sheet.set`)",
        "",
        "- Con i punti: `stato.fame`, `stato.salute.attuale`, `tratti.abilita.rissa`, `equipaggiamento.2`.",
        "- In un elenco: un indice (`condizioni.0`), oppure `+` per aggiungere in coda (`stato.condizioni.+`).",
        "- `value` `null` cancella la voce. I livelli intermedi mancanti vengono creati; un valore non si puo' usare come gruppo.",
        "- Un percorso che comincia con `esperienza` e' rifiutato: i punti passano solo dal registro.",
        "- Le modifiche di una chiamata sono tutte o nessuna; il risultato elenca `percorso: prima -> dopo`.",
        "",
        "## L'esperienza: un registro, non un numero",
        "",
        "```json",
        '"esperienza": {"registro": [{"quando": "2026-09-18T16:58", "sessione": 1, "via": "sessione", "punti": 1, "motivo": "..."}]}',
        "```",
        "",
        "Guadagnati, spesi e disponibili si calcolano dal registro a ogni lettura; nessun totale e' salvato",
        "(la verifica segnala come errore qualunque altra chiave sotto `esperienza`). Le spese hanno punti",
        "negativi e via `spesa`. Le vie di ingresso:",
        "",
        "| via | quando |",
        "|---|---|",
        routes,
        "",
        f"La via `{', '.join(sorted(ONCE_PER_SESSION))}` si concede una volta per numero di sessione. `spend_xp` rifiuta se i",
        "disponibili non bastano e applica insieme spesa e crescita del tratto.",
        "",
        "## Le due velocita' nel prompt",
        "",
        f"- Nel prompt di sistema, in cache, la parte stabile: tutti i gruppi tranne {', '.join(f'`{k}`' for k in Sheet.VOLATILE)} (`Sheet.stable_json`).",
        f"- Nel contesto di ogni turno la parte volatile: {', '.join(f'`{k}`' for k in Sheet.VOLATILE)}, con l'esperienza gia' calcolata",
        "  (`guadagnati`, `spesi`, `disponibili`, `ultimi_movimenti`: gli ultimi 5) (`Sheet.volatile_json`).",
        "- Allo scriba: `stato`, `equipaggiamento`, `esperienza` per intero e i soli nomi degli altri gruppi (`Sheet.working_json`).",
        "",
        "## Il personaggio attivo",
        "",
        "`stato.json` -> `pg_attivo` (slug). Se manca o non esiste piu': l'unica scheda presente, altrimenti nessuno",
        "(e la sezione Gioca e' disattivata). Il giocato di ogni personaggio sta in `sessioni/<slug>.jsonl`.",
    ]
    return "\n".join(out)


GENERATED: dict[str, Callable[[], str]] = {
    "scheda": _gen_sheet,
    "appunti": _gen_notes,
    "strumenti-scriba": _gen_tools,
    "dadi": _gen_dice,
}


def dumps(obj: Any) -> str:  # pragma: no cover - per il debug
    return json.dumps(obj, ensure_ascii=False, indent=1)
