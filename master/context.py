"""Il livello veloce: il contesto del turno lo compone il codice, come un insieme di documenti.

Niente conversazione che cresce e niente giro di chiamate per decidere cosa leggere. A ogni turno
il programma mette insieme cio' che serve, e il narratore risponde in una chiamata sola:

    scheda (JSON) · scena in corso · fili aperti · chi e cosa e' nominato (glossario come tabella
    di collegamento: termine -> file e sezione, piu' la memoria dei PNG) · regole pertinenti
    all'azione · gli ultimi scambi, PAROLA PER PAROLA

Gli ultimi scambi restano testuali di proposito: comprimere la memoria fa perdere per primo cio'
che il giocatore ha detto e fatto. Tutto il resto viene dai documenti, che il livello lento
(memory.py) e lo scriba tengono aggiornati. Cio' che il codice non trova, il narratore puo' ancora
leggerlo da se' (lettura di riserva): costa una chiamata in piu' e finisce nel registro, cosi' si
vede quanto spesso la selezione non basta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .campaign import MECHANICS_DIR, SETTING_DIR, Campaign
from .glossary import GlossaryEntry
from .memory import ADVENTURE, NPC_MEMORY, SCENE
from .sheet import Sheet

LAST_EXCHANGES = 3
MAX_ENTITIES = 8
MAX_RULES = 3
ENTITY_CHARS = 700
RULE_CHARS = 2_500
MIN_TERM = 4
_WORD = r"(?<![\w]){}(?![\w])"


@dataclass
class TurnContext:
    text: str
    entities: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    exchanges: int = 0

    def stats(self) -> str:
        return (f"{len(self.text)} car | entita': {', '.join(self.entities) or '-'} | regole: {', '.join(self.rules) or '-'} "
                f"| scambi testuali: {self.exchanges}")


def _mentions(term: str, text: str) -> bool:
    return bool(re.search(_WORD.format(re.escape(term.lower())), text))


def _section(campaign: Campaign, entry: GlossaryEntry, limit: int) -> str:
    """La sezione che porta il nome del termine nel file indicato dal glossario, se c'e'."""
    try:
        f = campaign.file(entry.file)
    except ValueError:
        return ""
    if not f.exists():
        return ""
    wanted = entry.term.lower()
    for title, body in f.sections().items():
        if title and (title.lower() == wanted or wanted in title.lower()):
            body = body.strip()
            return body if len(body) <= limit else body[:limit].rsplit("\n", 1)[0] + "\n[... il resto in " + f"{entry.file} § {title}]"
    return ""


_HEADING = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
_GENERIC = re.compile(r"^(livello|grado|rango|capitolo|parte|tabella|esempi?o?|nota|regole?|disciplina|potere|incantesimo)\b[\s\d]*$", re.I)


def _rule_blocks(campaign: Campaign, player_text: str, skip: set[str]) -> list[tuple[str, str, str]]:
    """Regole trovate per TITOLO, anche di sottosezione: un potere o una manovra nominati dal giocatore
    stanno spesso sotto un `###` dentro una sezione piu' grande, e il glossario non li elenca.
    Ritorna (nome, file, testo del blocco)."""
    text = player_text.lower()
    out: list[tuple[str, str, str]] = []
    for f in campaign.files():
        if not f.name.startswith(MECHANICS_DIR + "/"):
            continue
        lines = f.read().splitlines()
        heads = [(i, len(m.group(1)), m.group(2)) for i, line in enumerate(lines) if (m := _HEADING.match(line))]
        for n, (i, level, title) in enumerate(heads):
            keys = [k.strip(" *_`") for k in re.split(r"\s+[—–-]\s+|:\s+|\s*\(", title)]
            key = next((k for k in keys if len(k) >= MIN_TERM and not _GENERIC.match(k) and _mentions(k, text)), None)
            if key is None or key.lower() in skip:
                continue
            end = next((j for j, lv, _ in heads[n + 1:] if lv <= level), len(lines))
            body = "\n".join(lines[i + 1:end]).strip()
            if body:
                skip.add(key.lower())
                out.append((title, f.name, body if len(body) <= RULE_CHARS else body[:RULE_CHARS].rsplit("\n", 1)[0] + f"\n[... il resto in {f.name}]"))
    return out


def rule_titles(campaign: Campaign) -> list[tuple[str, str]]:
    """(file, titolo) di ogni sezione di regole: l'elenco tra cui l'instradatore sceglie."""
    return [(f.name, t) for f in campaign.files() if f.name.startswith(MECHANICS_DIR + "/") for t in f.outline()]


def compose(campaign: Campaign, player_text: str, save_name: str, router=None) -> TurnContext:
    """`router(titoli, azione, scena) -> titoli scelti`: chiamato solo quando l'azione del giocatore non
    nomina nessuna regola ("salto dal tetto"): e' la parte della selezione che le parole non risolvono."""
    sheet_info = campaign.active_sheet()
    parts: list[str] = ["[CONTESTO DEL TURNO: composto dal programma dai documenti della campagna. Fa fede.]"]
    ctx = TurnContext(text="")
    name = sheet_info["name"] if sheet_info else ""

    if sheet_info:
        sheet = Sheet(campaign, sheet_info["slug"])
        if sheet.exists:  # tratti e storia sono nel prompt di sistema, in cache: qui solo cio' che cambia
            body = "Stato attuale ed esperienza (il resto della scheda e' nel prompt di sistema):\n```json\n" + sheet.volatile_json() + "\n```"
        else:
            body = campaign.file(sheet_info["file"]).read().strip()
        parts.append(f"## Personaggio del giocatore: {name} (sessione {campaign.session_number})\n{body}")

    scene = campaign.file(SCENE)
    scene_text = scene.read().strip() if scene.exists() else ""
    if scene_text:
        parts.append(scene_text.replace("# Scena in corso", "## Scena in corso", 1))

    adventure = campaign.file(ADVENTURE)
    threads = adventure.get_section(f"Fili aperti di {name}") if name and adventure.exists() else None
    if threads:
        parts.append(f"## Fili aperti (segreti tuoi)\n{threads.strip()}")

    turns = campaign.read_turns(save_name, LAST_EXCHANGES) if save_name else []
    last_narration = turns[-1]["master"] if turns else ""

    # chi e cosa e' nominato: prima nelle parole del giocatore, poi nella scena, poi nell'ultima narrazione
    sources = [player_text.lower(), scene_text.lower(), last_narration.lower()]
    found: list[tuple[int, int, GlossaryEntry]] = []
    for entry in campaign.glossary.entries():
        term = entry.term.strip()
        if len(term) < MIN_TERM or entry.kind == "pg":
            continue
        rank = next((i for i, text in enumerate(sources) if text and _mentions(term, text)), None)
        if rank is not None:
            found.append((rank, -len(term), entry))
    found.sort(key=lambda x: (x[0], x[1]))

    memory = campaign.file(NPC_MEMORY)
    npc_memory = memory.sections() if memory.exists() else {}
    world, rules = [], []
    # prima le regole nominate dal giocatore, cercate per titolo anche nelle sottosezioni
    for title, file, body in _rule_blocks(campaign, player_text, set())[:MAX_RULES]:
        rules.append(f"### {title} ({file})\n{body}")
        ctx.rules.append(title)
    for rank, _, entry in found:
        is_rule = entry.file.startswith(MECHANICS_DIR + "/")
        if is_rule:
            if any(entry.term.lower() in r.lower() for r in ctx.rules):
                continue  # gia' trovata per titolo
            if rank > 0:
                continue  # le regole si scelgono sull'azione del giocatore, non su cio' che la scena nomina
            if len(rules) < MAX_RULES:
                body = _section(campaign, entry, RULE_CHARS)
                if body:
                    rules.append(f"### {entry.term} ({entry.file})\n{body}")
                    ctx.rules.append(entry.term)
        elif len(world) < MAX_ENTITIES:
            lines = [f"### {entry.term} ({entry.kind}) — {entry.description}"]
            remembered = next((b for t, b in npc_memory.items() if t and t.lower() == entry.term.lower()), "")
            if remembered:
                lines.append(remembered.strip())
            else:
                body = _section(campaign, entry, ENTITY_CHARS)
                if body:
                    lines.append(body)
            world.append("\n".join(lines))
            ctx.entities.append(entry.term)
    # l'instradatore completa cio' che le parole non dicono: "lo uccido bevendo" nomina la Fame ma non l'Umanita'
    # (2026-09-18: proprio cosi' il narratore ha dovuto fare una lettura di riserva)
    if len(rules) < MAX_RULES and router is not None and player_text.strip():
        titles = rule_titles(campaign)
        try:
            chosen = router([t for _, t in titles], player_text, scene_text[:600])
        except Exception:  # l'instradatore e' un aiuto: se cade, resta la lettura di riserva del narratore
            chosen = []
        for wanted in chosen:
            if len(rules) >= MAX_RULES:
                break
            if any(str(wanted).strip().lower() in r.lower() for r in ctx.rules):
                continue
            hit = next(((f, t) for f, t in titles if t.lower() == str(wanted).strip().lower()), None)
            if hit:
                body = (campaign.file(hit[0]).get_section(hit[1]) or "").strip()
                if body:
                    if len(body) > RULE_CHARS:
                        body = body[:RULE_CHARS].rsplit("\n", 1)[0] + f"\n[... il resto in {hit[0]} § {hit[1]}]"
                    rules.append(f"### {hit[1]} ({hit[0]})\n{body}")
                    ctx.rules.append(hit[1] + " (instradata)")
    # i PNG che la memoria conosce e la scena dichiara presenti, anche se il glossario non li ha
    for title, body in npc_memory.items():
        if title and title not in ctx.entities and len(world) < MAX_ENTITIES and _mentions(title, sources[0] + "\n" + sources[1]):
            world.append(f"### {title} (png)\n{body.strip()}")
            ctx.entities.append(title)
    if world:
        parts.append("## Chi e cosa e' in gioco adesso\n" + "\n\n".join(world))
    if rules:
        parts.append("## Regole pertinenti (dal manuale: applicale alla lettera)\n" + "\n\n".join(rules))

    if turns:
        lines = []
        for t in turns:
            lines.append(f"GIOCATORE: {t['player'].strip()}\nNARRATORE: {t['master'].strip()}")
        parts.append("## Ultimi scambi, parola per parola\n" + "\n\n".join(lines))
        ctx.exchanges = len(turns)

    parts.append("[FINE DEL CONTESTO. Qui sotto, il turno a cui rispondere.]\n")
    ctx.text = "\n\n".join(parts) + "\n"
    return ctx
