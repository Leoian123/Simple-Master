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
    "relazioni": ("Le relazioni dei PNG", "il numero in stato.json, la scala degli stadi, il passo per enum, il tool, l'override dal manuale", "relazioni.py, tools.RELATION_TOOLS"),
    "modi": ("I modi di gioco", "la lente sul narratore: i tre modi predefiniti, il blocco nel prompt, meccanica/modi.json", "modi.py"),
    "trigger": ("I trigger", "meccanica/trigger.json: condizioni sui fatti del programma, effetti tipizzati, valutati a ogni turno", "trigger.py"),
    "tabelle": ("Le tabelle casuali", "meccanica/tabelle.json: le tabelle del manuale come dati, il marcatore [[tabella: ...]], l'estrazione", "tabelle.py"),
    "prefazione": ("La prefazione delle regole", "la sezione 'Come usare queste regole' in testa a meccanica/regole.md: precedenza, rimandi, riserva; generata", "prefazione.py"),
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
    from .dice import DEFAULT_ESITI, PROFILE_SCHEMA, WHEN, scala_esiti

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
        "## Tiri sotto il valore (`tipo: \"sotto\"`)",
        "",
        "Per i sistemi in cui si riesce tirando FINO AL valore della scheda (d100 di CoC, BRP, Delta Green, WFRP,",
        "Mythras). Il marcatore porta il valore: `[[tiro: <nome> | <tipo> | valore <V>]]` (`| bonus <+K>` modifica il",
        "valore). I gradi sono `soglie` a frazioni del valore (es. difficile 0.5, estremo 0.2), il `fallimento_critico`",
        "un totale alto (`{da, sotto_valore, altrimenti_da}`: 96 con valore sotto 50, altrimenti 100), il",
        "`successo_critico_fino_a` un totale basso. L'esito e' il nome del grado o uno fra successo critico / riuscito /",
        "fallito / fallimento critico, con la guida del grado se il manuale la da'. `esiti` non si applica a `sotto`.",
        "",
        _params_table(kind["properties"]["soglie"]["items"]),
        "",
        "## Tieni ed esplosioni",
        "",
        "`tieni: {quanti, quale}` (per `somma`): si tirano N dadi e se ne tengono i `quanti` piu' alti o piu' bassi;",
        "con `esiti_su: \"dado\"` le fasce di `esiti` sono valori del dado tenuto e il marcatore non porta una",
        "difficolta' (Forged in the Dark: `dadi <N>`, tieni 1 alto, 6 pieno / 4-5 con conseguenze / 1-3 fallito;",
        "`coppie` sulla faccia 6 da' la nota CRITICO quando due dadi la mostrano). `esplode_su`: una faccia da quel",
        "valore in su si ritira e si somma (`somma`: 6+6+2=14) o aggiunge un dado alla riserva (`successi`, il",
        "\"10 again\"); al massimo 10 esplosioni per dado. Nei campioni le facce vanno nell'ordine in cui il",
        "programma le consuma: ogni dado seguito dalle sue esplosioni.",
        "",
        "## I campioni (`tiri[i].esempi[j]`)",
        "",
        "Da 2 a 4 casi per tipo di tiro, ricavati insieme al profilo: un marcatore completo, le facce uscite e l'esito",
        "che le regole prevedono. `dice.verifica_esempi` li esegue a dadi fissi: `extract_profile` rifa' la chiamata una",
        "volta se non tornano e poi salva comunque segnando `campioni_falliti`; la verifica li riesegue a zero chiamate",
        "e segnala ogni campione che non torna come errore. Il gioco non li legge.",
        "",
        _params_table(kind["properties"]["esempi"]["items"]),
        "",
        "## Le fasce dell'esito (`tiri[i].esiti[j]`)",
        "",
        "Con una difficolta', il margine (successi o totale oltre la difficolta', negativo se sotto) cade in una",
        "fascia con un nome e una guida per il narratore. Le fasce devono coprire tutti i margini senza buchi,",
        "la piu' bassa aperta in basso e la piu' alta in alto (`dice.esiti_problems` e' la regola, usata anche",
        "dalla verifica). Se il manuale non gradua l'esito (`esiti: []` o assente) valgono le fasce predefinite:",
        "",
        *[f"- `{tipo}`: {scala_esiti(None, tipo)}" for tipo in DEFAULT_ESITI],
        "",
        _params_table(kind["properties"]["esiti"]["items"]),
        "",
        "## Il marcatore che il narratore scrive",
        "",
        "Uno per messaggio, su una riga sua, in coda alla narrazione. La pagina lo mostra come pulsante.",
        "",
        "```",
        "[[tiro: <nome> | <tipo di tiro del profilo> | dadi N | bonus +K | difficolta D]]",
        "[[tiro: <nome> | <tipo> | dadi N | <nome dei dadi speciali> N]]      per forzare i dadi speciali",
        "[[tiro: <nome> | <tipo> | bonus +K | difficolta D | vantaggio]]      o svantaggio, se il tipo lo ammette",
        "[[tiro: <nome> | <tipo sotto> | valore <V>]]                       roll-under: riesce fino al valore",
        "[[tiro: <nome> | <tipo con tieni e esiti sul dado> | dadi N]]        decide il dado tenuto, nessuna difficolta'",
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
        "[Tiro] <nome> — 5d10: 7, 6, 4 | fame: 2, 9 -> 3 successi contro difficolta' 2: RIUSCITO (margine 1). CRITICO. FALLIMENTO BESTIALE (effetto). Guida: ...",
        "[Tiro] <nome> — 1d20: 14 +3 = 17 contro difficolta' 15: RIUSCITO DI MISURA (margine 2). Guida: ...",
        "```",
        "",
        "Gli eventi compaiono in MAIUSCOLO con l'effetto tra parentesi; `nome_zero_successi` compare quando",
        "nessun dado riesce; con `difficolta` il verdetto e' il NOME DELLA FASCIA in maiuscolo con il margine",
        "(o quanto manca), e in coda la guida della fascia.",
        "Nel codice: `dice.roll(spec, profile, sheet) -> {name, target, success, value, rolls, special_rolls, notes, esito, guida, text}`.",
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


def _gen_relations() -> str:
    from .relazioni import DEFAULT_STADI, DELTA, FILE_NAME, MAX, MIN, scala
    from .tools import RELATION_TOOLS

    tool = RELATION_TOOLS[0]
    out = [
        f"Ogni PNG ha un atteggiamento verso il personaggio da {MIN} a {MAX}, in `stato.json` sotto `png`:",
        "",
        "```json",
        '"png": {"Mira Sol": {"relazione": -23}}',
        "```",
        "",
        "Il numero lo possiede il codice (`Campaign.shift_relation`). Il narratore non lo vede mai: nel contesto",
        "del turno ogni PNG porta l'etichetta del suo STADIO accanto al tipo (`### Mira Sol (png · Diffidente)`),",
        "e lo scriba non scrive numeri: dice il verso e la forza, il codice applica il passo e clampa.",
        "",
        "## La scala degli stadi",
        "",
        f"Predefinita: {scala(DEFAULT_STADI)}.",
        "",
        f"Un manuale con i suoi nomi la sovrascrive con `meccanica/{FILE_NAME}`:",
        "",
        "```json",
        '{"stadi": [{"nome": "Ostile", "da": -100, "a": -21}, {"nome": "Neutrale", "da": -20, "a": 20}, {"nome": "Amico", "da": 21, "a": 100}]}',
        "```",
        "",
        f"Gli stadi devono tassellare {MIN}..{MAX} senza buchi ne' sovrapposizioni, con nomi unici",
        "(`relazioni.stadi_problems`, la regola usata anche dalla verifica). Un file che non la rispetta viene",
        "ignorato (valgono i predefiniti) e la verifica lo segnala come errore.",
        "",
        "## Il passo",
        "",
        "| forza | passo |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in DELTA.items()],
        "",
        "`sale` aggiunge, `scende` toglie; il valore resta nella scala. Un PNG mai contato parte da 0 (Neutrale).",
        "Il nome si cerca senza distinzione di maiuscole fra i PNG gia' contati.",
        "",
        "## Chi la muove",
        "",
        f"- Lo scriba, con `{tool['name']}`, solo per la riga `- relazione: <png> sale|scende (lieve|media|forte) — motivo`",
        "  degli appunti del narratore (sezione `appunti`); mai di propria iniziativa.",
        "- Il consolidamento della memoria (`memory.py`): per ogni PNG toccato dice `sale`, `scende` o `invariata`,",
        "  e il codice applica un passo `media`; solo per interazioni dirette e sostanziali.",
        "- Nessun altro: il narratore e il costruttore di schede non hanno il tool.",
        "",
        f"### `{tool['name']}`",
        "",
        str(tool["description"]).strip(),
        "",
        _params_table(tool["input_schema"]),
        "",
        "Rifiutato: nome vuoto, `verso` o `forza` fuori vocabolario (anche se lo schema li ammetterebbe come testo).",
        "",
        "## Dove si legge",
        "",
        "- Nel contesto del turno (`context.compose`): l'etichetta accanto a ogni PNG nominato, mai il numero.",
        "- Nel contesto dello scriba e del consolidamento: `RELAZIONI DEI PNG: Mira Sol: Diffidente (-23); ...`.",
        "- `Campaign.relation(nome)` -> intero o None; `relations_line()`; `relazioni.stadio(valore, stadi)`.",
    ]
    return "\n".join(out)


def _gen_modes() -> str:
    from .modi import DEFAULT, DEFAULT_MODI, FILE_NAME, testo

    out = [
        "Un modo di gioco e' una LENTE sul narratore: tono, ritmo, quanto il mondo concede alle soluzioni",
        "improbabili, quanto costano gli errori, come si inclinano i PNG. Non e' una regola: le difficolta' restano",
        "quelle del manuale, i dadi li tira il programma, la scheda non cambia. Si sceglie dalla pagina (selettore",
        "'Modo' nella testata) o scrivendo `modo` in `stato.json`; vale dal turno dopo. Entra nel prompt di sistema",
        "come parte del primo blocco, in cache: si riscrive solo quando il giocatore lo cambia. Zero chiamate in piu'.",
        "",
        f"## I modi predefiniti (`{DEFAULT}` se non si sceglie)",
        "",
    ]
    for m in DEFAULT_MODI:
        out += [f"### `{m['nome']}`", "", m["descrizione"], "", "Blocco nel prompt, parola per parola:", "", "```", testo(m).strip(), "```", ""]
    out += [
        f"## `meccanica/{FILE_NAME}`: i modi della campagna",
        "",
        "```json",
        '{"modi": [{"nome": "cupo", "descrizione": "una riga per il selettore", "istruzioni": "cosa cambia per il narratore", "png": "come si inclinano i PNG (facoltativo)"}]}',
        "```",
        "",
        "Se il file c'e' ed e' valido SOSTITUISCE i predefiniti (tutti); `nome`, `descrizione` e `istruzioni` sono",
        "testi obbligatori, `png` facoltativo, nomi unici (`modi.modi_problems`, la regola usata anche dalla verifica).",
        "Un file malformato viene ignorato e la verifica lo segnala come errore; un `modo` in `stato.json` che non",
        "esiste piu' e' un errore (il narratore giocherebbe in `classico` senza dirlo).",
        "",
        "Nel codice: `modi.carica(campaign)`, `modi.attivo(campaign)`, `modi.testo(modo)`, `Campaign.set_mode(nome)`,",
        "`POST /api/mode {name}`; lo stato della pagina espone `mode` e `modes`.",
    ]
    return "\n".join(out)


def _gen_triggers() -> str:
    from .trigger import MIN_SCORE, NEAR_CHARS, SOURCE_LIMIT
    from .trigger import CONDIZIONI, EFFETTI, FILE_NAME, MAX_CONDITIONS, MAX_EFFECTS, MAX_TRIGGERS, OPS, STATE_KEY

    out = [
        f"`meccanica/{FILE_NAME}` e' un elenco di regole `condizioni -> effetti` che il programma valuta a ogni turno,",
        "prima della chiamata del narratore, in testa al messaggio del turno: dopo le scadenze degli orologi e prima",
        "della consegna degli orologi pieni (un trigger puo' riempirne uno nello stesso turno). Le condizioni leggono",
        "solo cio' che il programma possiede; gli effetti sono tipizzati; nessun testo da giudicare, nessuno script,",
        "nessuna chiamata. Un file che non rispetta la forma viene ignorato intero, e la verifica lo dice.",
        "",
        "```json",
        '{"trigger": [',
        '  {"nome": "rischio di frenesia", "ricorrente": true,',
        '   "quando": [{"scheda": "stato.fame", "op": ">=", "valore": 4}],',
        '   "effetti": [{"avviso": "Fame a 4 o piu\': a ogni tentazione serve una prova, secondo le regole"}]},',
        '  {"nome": "la Bestia si avvicina",',
        '   "quando": [{"scheda": "stato.umanita", "op": "<", "valore": 5}],',
        '   "effetti": [{"orologio": "La Bestia", "avanza": 1, "segmenti": 6, "conseguenza": "Jonathan perde una notte"}]}',
        "]}",
        "```",
        "",
        "## Una regola",
        "",
        "| campo | significato |",
        "|---|---|",
        "| `nome` | unico; e' cio' che il narratore legge in `[TRIGGER nome: ...]` |",
        f"| `quando` | da 1 a {MAX_CONDITIONS} condizioni, tutte vere insieme (AND); non esiste OR: due regole |",
        f"| `effetti` | da 1 a {MAX_EFFECTS} effetti, applicati in ordine |",
        f"| `ricorrente` | `true`: scatta a ogni turno in cui vale; `false` (default): una volta sola, poi il nome finisce in `stato.json` -> `{STATE_KEY}` |",
        "",
        f"Al massimo {MAX_TRIGGERS} regole per campagna.",
        "",
        "## Le condizioni (`quando[j]`: una sola chiave fra queste, piu' `op` e `valore`)",
        "",
        "| chiave | legge |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in CONDIZIONI.items()],
        "",
        f"`op`: {', '.join(f'`{o}`' for o in OPS)}. Con `<`, `<=`, `>`, `>=` il valore e' un numero; `contiene` cerca un",
        "elemento in un elenco (o un pezzo in un testo); `stadio` ed `esito` ammettono solo `==` / `!=` con un nome.",
        "Un confronto fra tipi diversi e' falso, mai un errore: una regola scritta male non scatta.",
        "",
        "## Gli effetti (`effetti[j]`: una sola chiave fra queste)",
        "",
        "| chiave | fa |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in EFFETTI.items()],
        "",
        "L'avviso arriva al narratore come `[TRIGGER <nome>: testo]`, come gli orologi pieni; gli altri effetti",
        "cambiano `stato.json` e si vedono dove si vedono gli orologi e le relazioni. Nessun effetto tocca la scheda:",
        "i numeri del personaggio li muove solo lo scriba, su indicazione del narratore.",
        "",
        "## Verifica",
        "",
        "La verifica segnala come errore un file illeggibile o malformato (con il percorso della regola) e un percorso",
        "`scheda` che nella scheda attiva non esiste (la regola non scatterebbe mai). Nel codice: `trigger.problems(data)`,",
        "`trigger.carica(campaign)`, `trigger.valuta(campaign, messaggio_del_turno)`.",
        "",
        "## Da dove vengono",
        "",
        "Si scrivono a mano, oppure `trigger.extract_triggers(campaign, client, model, sheet)` (pulsante 'Ricava i trigger' nel",
        "tavolo, `POST /api/triggers/extract`; alla fine della preparazione se c'e' una scheda attiva, in cache per contenuto):",
        "una chiamata sulle sezioni di `meccanica/` che legano un numero a una conseguenza (`trigger.threshold_sections`: una",
        f"parola di soglia a meno di {NEAR_CHARS} caratteri da un numero vale un punto, servono almeno {MIN_SCORE} punti, le sezioni",
        f"entrano dalla piu' densa fino a {SOURCE_LIMIT // 1000}k caratteri), con i percorsi della scheda attiva",
        "come unici percorsi ammessi (`trigger.sheet_paths`), schema forzato in forma piatta (`tipo`, `soggetto`, `op`, `valore`",
        "per le condizioni; `tipo`, `testo` e i campi dell'effetto). Il codice ritraduce nella forma del file, passa ogni regola",
        "per `problems` e `sheet_path_problems`, scarta cio' che non quadra (registrato nel log) e aggiunge le regole nuove a",
        "quelle gia' scritte, senza toccarle (vince il nome gia' in uso).",
    ]
    return "\n".join(out)


def _gen_tables() -> str:
    from .tabelle import FILE_NAME, MAX_ROWS, MAX_TABLES, MIN_RANGE_LINES, TABLES_SCHEMA

    item = TABLES_SCHEMA["properties"]["tabelle"]["items"]
    out = [
        f"Le tabelle casuali del manuale (incontri, tesori, effetti: \"1d100: 01-05 ...\") stanno in `meccanica/{FILE_NAME}`",
        "come dati, e le tira il programma. Lasciate in prosa il narratore sceglierebbe lui la riga.",
        "",
        "```json",
        '{"tabelle": [{"nome": "incontri nella palude", "uso": "ogni ora di marcia", "dado": "1d100",',
        '              "righe": [{"da": 1, "a": 30, "risultato": "Nulla"}, {"da": 31, "a": 100, "risultato": "..."}],',
        '              "fonte": "meccanica/creature.md: Incontri casuali"}]}',
        "```",
        "",
        "## Una tabella",
        "",
        _params_table(item),
        "",
        _params_table(item["properties"]["righe"]["items"]),
        "",
        f"Regole (`tabelle.problems`, usate anche dalla verifica): nomi unici; `dado` e' una notazione NdS+K; le righe coprono",
        f"tutto l'intervallo del dado (dal minimo al massimo) senza buchi ne' sovrapposizioni; al massimo {MAX_TABLES} tabelle e",
        f"{MAX_ROWS} righe per tabella. Un file che non le rispetta si ignora intero e la verifica lo segnala come errore.",
        "",
        "## Il marcatore e l'esito",
        "",
        "Il narratore chiude la narrazione con `[[tabella: <nome>]]` (uno per messaggio, in alternativa a un tiro); la pagina",
        "lo mostra come pulsante; `POST /api/table {name}` tira il dado della tabella, trova la riga e manda l'esito al",
        "narratore come messaggio del giocatore:",
        "",
        "```",
        "[Tabella] incontri nella palude — 1d100: 37 -> 37: Un branco di cani selvatici",
        "```",
        "",
        "Tirare non costa chiamate. Il prompt di sistema elenca le tabelle con il loro uso (blocco in cache).",
        "",
        "## Da dove vengono",
        "",
        f"`extract_tables` (pulsante 'Ricava le tabelle' nel tavolo, `POST /api/tables/extract`): una chiamata sulle sezioni",
        f"di `meccanica/` e `ambientazione/` che hanno la forma di una tabella (almeno {MIN_RANGE_LINES} righe con un intervallo",
        "come `01-05` e una notazione di dado nel testo), con schema forzato; il codice controlla ogni tabella e scarta",
        "quelle che non quadrano (registrate nel log). Si possono scrivere anche a mano. La verifica consiglia di ricavarle",
        "quando ci sono sezioni con quella forma e il file manca; `python main.py --campaign <cartella> --ricava tabelle` lo fa",
        "dalla riga di comando (anche `dadi`, `prefazione`, `trigger`). Il tavolo mostra la stima di costo accanto al pulsante.",
    ]
    return "\n".join(out)


def _gen_preface() -> str:
    from .prefazione import HEADS, MAX_ITEMS, MIN_SECTIONS, PREFACE_SCHEMA, RULES_FILE, SECTION_TITLE

    out = [
        f"Un manuale smontato in sezioni dice le regole, non come orientarsi fra loro. La sezione `## {SECTION_TITLE}`",
        f"in testa a `{RULES_FILE}` lo dice, in tre liste con titoli fissi; il prompt di sistema la copia (blocco in cache),",
        "quindi il narratore la legge a ogni turno senza che costi. L'instradatore delle regole non la propone (e' gia' li').",
        "",
        "## Le tre liste",
        "",
        f"- `### {HEADS[0]}`: elenco numerato, da chi vince a chi cede, quando due fonti dicono cose diverse.",
        f"- `### {HEADS[1]}`: una riga per rimando, nella forma esatta `- <argomento>: `<file>` § <sezione>`: cio' che va riletto",
        "  nei file e non ricordato (numeri, costi, tabelle). La verifica rilegge queste righe e segnala come avviso un",
        "  rimando a un file o a una sezione che non esiste.",
        f"- `### {HEADS[2]}`: cosa fare in gioco quando una regola non si trova (quale tiro generico, quando decidere senza tiro).",
        "",
        f"Al massimo {MAX_ITEMS} voci per lista. Si puo' scrivere a mano, rispettando i tre titoli e la forma dei rimandi.",
        f"La verifica consiglia di scriverla quando le regole hanno almeno {MIN_SECTIONS} sezioni: sotto, si leggono intere.",
        "",
        "## Da dove viene",
        "",
        "`extract_preface` (pulsante 'Scrivi la prefazione' nel tavolo, `POST /api/preface/extract`; alla fine della",
        "preparazione di un manuale, in cache per contenuto dell'indice): una chiamata con l'indice dei file di regole, i tipi",
        "di tiro e le tabelle che il programma gestisce, schema forzato:",
        "",
        _params_table(PREFACE_SCHEMA),
        "",
        "Il codice tiene nei rimandi solo file e sezioni che esistono davvero (titolo ricopiato esatto), scarta il resto",
        "(registrato nel log) e scrive la sezione in testa alle regole, sostituendo quella precedente.",
        "",
        "Nel codice: `prefazione.leggi(campaign)`, `prefazione.prompt_section(campaign)`, `prefazione.broken_lookups(campaign)`,",
        "`prefazione.scrivi(campaign, corpo)`.",
    ]
    return "\n".join(out)


GENERATED: dict[str, Callable[[], str]] = {
    "scheda": _gen_sheet,
    "relazioni": _gen_relations,
    "modi": _gen_modes,
    "trigger": _gen_triggers,
    "tabelle": _gen_tables,
    "prefazione": _gen_preface,
    "appunti": _gen_notes,
    "strumenti-scriba": _gen_tools,
    "dadi": _gen_dice,
}


def dumps(obj: Any) -> str:  # pragma: no cover - per il debug
    return json.dumps(obj, ensure_ascii=False, indent=1)
