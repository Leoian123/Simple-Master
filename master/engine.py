"""Il ciclo di mastering.

    1. arriva il testo del giocatore;
    2. si chiama l'API con il glossario nel system prompt;
    3. l'API chiede (via tool) i file che le servono e li legge;
    4. l'API aggiorna diario/glossario/schede (via tool) e narra;
    5. la narrazione torna al giocatore e la sessione viene trascritta.

Il glossario e' sempre presente, i file vengono letti solo su richiesta:
il contesto resta piccolo e lo stato della storia vive nei file, non nella
memoria della chat. Lo stesso ciclo, con prompt e strumenti diversi, serve
anche al costruttore di schede (vedi character.py).

Ogni passo finisce nel registro (logbook.py): turno, chiamate API, tool,
errori con il punto esatto in cui il turno si e' interrotto.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from . import dice
from .campaign import DIARY, MECHANICS_DIR, SETTING_DIR, SHEETS_DIR, Campaign
from .logbook import describe_error, short, usage_line
from .models import cost_usd
from .sheet import Sheet
from .tools import TOOLS, ToolExecutor, tool_result_block

DEFAULT_MODEL = "claude-opus-5"
CACHE = {"type": "ephemeral", "ttl": "1h"}
SNAPSHOT_TURNS = 8      # ogni quanti turni si rinnova la fotografia del prompt di sistema
STUB_ABOVE = 800        # caratteri: oltre, una lettura dei turni passati diventa una riga
MIN_NARRATION = 80      # caratteri di testo perche' un messaggio con sole scritture chiuda il turno
WRITE_TOOLS = {"append_file", "set_section", "replace_text", "upsert_glossary", "save_sheet",
               "update_sheet", "award_xp", "spend_xp", "set_clock", "tick_clock"}
PRELUDE_MARK = "[Apertura della campagna]"
OPENING_MARK = "[Apertura della sessione]"
CLOSING_MARK = "[Chiusura della sessione]"
MIN_TURNS_TO_CLOSE = 3  # sotto, "nuova sessione" e' un rifare da capo: niente chiusura, niente esperienza

_WORK_INLINE = f"""\
3. NARRA E SCRIVI NELLO STESSO MESSAGGIO. Scrivi la narrazione per il giocatore
   e, nello stesso messaggio, fai tutte le scritture:
   - `append_file` su `{DIARY}`: una voce breve per ogni evento rilevante;
   - `update_sheet` per la scheda del personaggio (tutte le modifiche in una chiamata);
   - `set_section` / `replace_text` per PNG, luoghi, fili aperti;
   - `upsert_glossary` solo per cio' che tornera' (un PNG con un nome, un luogo
     ricorrente), non per ogni oggetto di scena.
   Dopo le scritture il turno e' chiuso: non ci sara' un altro giro per aggiungere testo.
4. LE SCRITTURE COSTANO QUANTO LA NARRAZIONE, parola per parola. Sono appunti per te,
   non prosa: stile telegrafico, niente che sia gia' nella narrazione o nella scheda.
   Tetto per un turno normale: 80 parole in tutto. Diario: una riga, due al massimo.
   Glossario: al massimo due voci a turno, una frase breve. Un PNG nuovo e' la sua
   voce di glossario e basta; la sezione in `npc.md` (tre righe) solo quando torna in
   scena una seconda volta. In `update_sheet` cambia la singola voce (`stato.condizioni.+`),
   non riscrivere un elenco intero. Molti turni non richiedono nessuna scrittura.

"""

# Con lo scriba il narratore non scrive file: narra, e lascia appunti. Una seconda chiamata,
# separata e su un modello economico, li trascrive negli archivi (vedi _run_scribe).
_WORK_SCRIBE = f"""\
3. NARRA. TU NON SCRIVI FILE: non hai strumenti di scrittura. Scrivi la narrazione per il
   giocatore e, in coda, un blocco di appunti che il giocatore non vedra':
   <appunti>
   - diario: cio' che e' successo, una riga
   - stato: cio' che cambia sulla scheda, con i numeri (es. "volonta' -1 superficiale, fame 3")
   - nuovo: PNG o luoghi che torneranno (nome, tipo, una frase)
   - segreto: cio' che e' vero dietro la scena e cambia i fili aperti
   - pe: solo se assegni o spendi punti esperienza (punti, via, motivo / costo e tratto)
   </appunti>
   Solo le righe che servono, stile telegrafico, 60 parole al massimo (150 in un'apertura o
   in una chiusura). Uno scriba li trascrivera' negli archivi con una chiamata a parte: cio'
   che non sta negli appunti o nella narrazione non verra' ricordato. Se non c'e' nulla da
   annotare scrivi <appunti>nulla</appunti>.
4. Quando un'istruzione del tavolo (apertura, chiusura) ti chiede una scrittura su file o
   un'assegnazione di punti, non la esegui tu: la metti negli appunti e la esegue lo scriba.

"""

MASTER_PROMPT = f"""\
Sei il master di una campagna di gioco di ruolo testuale. Parli in italiano.

## Dove stanno le informazioni
- `{MECHANICS_DIR}/`: le REGOLE. Numeri, tiri, tabelle, statistiche di oggetti e
  creature. Quando serve risolvere un'azione, leggi qui e applica alla lettera.
- `{SETTING_DIR}/`: il MONDO. Luoghi, fazioni, personaggi non giocanti, trame,
  e il diario (`{DIARY}`) con cio' che e' successo finora.
- `{SHEETS_DIR}/`: le SCHEDE dei personaggi giocanti. Il personaggio con cui il
  giocatore sta giocando e' in fondo, in "PERSONAGGIO ATTIVO", con la scheda
  completa in JSON: il giocatore parla e agisce come lui. I valori si leggono
  direttamente (una riserva di dadi e' la somma di due numeri della scheda) e
  si cambiano con `update_sheet`: stato, risorse ed equipaggiamento sono tuoi da
  aggiornare quando cambiano.
Tieni separate le due cose: le regole non si inventano, il mondo si' (e poi si scrive).

## Da dove nasce una scena
Nessuna scena nasce dal nulla. Prima di narrare chiediti: cosa e' appena successo a
questo personaggio? cosa vuole, cosa teme, chi lo cerca? quali fili sono aperti
(`{SETTING_DIR}/avventura.md`, diario)? La scena parte da li': luogo, ora e
situazione sono conseguenze della sua storia, mai un fondale neutro. Un dettaglio
del background reso concreto (un nome, un oggetto, un debito) vale piu' di
un'atmosfera generica.

## Come lavori a ogni turno: due chiamate, non sei
Ogni giro di strumenti rimanda tutta la conversazione: costa. Un turno normale e'
fatto di due chiamate, spesso di una sola.
1. Il glossario qui sotto e' il tuo indice: dice cosa esiste e in quale file. Se
   e' in forma compatta (solo termini), `lookup_glossary` ti da' descrizione e file.
2. LEGGI IN UN SOLO GIRO. Decidi subito tutto cio' che ti serve e chiama
   `read_file` / `lookup_glossary` insieme, nello stesso messaggio, indicando
   sempre `section`. Non leggere cio' che hai gia': la scheda del personaggio e'
   in fondo a questo prompt, gli ultimi eventi sono nella conversazione, e un
   diario vuoto non ha nulla da dire. Se non ti serve nulla, non leggere.
{_WORK_INLINE}## Orologi
Minacce e progetti che avanzano nel tempo non li tieni a mente: sono orologi a segmenti
contati dal programma (es. "La guardia indaga 2/6"). Quelli attivi ti arrivano in testa
al messaggio del giocatore. Negli appunti: `- orologio: nuovo "<nome>", 6 segmenti,
conseguenza: ...` per crearne uno quando un filo comincia a stringere (due o tre attivi
bastano), `- orologio: "<nome>" +1` quando un fatto lo fa avanzare. Quando uno si riempie ti
arriva `[OROLOGIO PIENO: ...]`: la conseguenza accade in quella scena, non si rimanda.

## Esperienza
Il conto dei punti esperienza non lo tieni tu: sta nel registro della scheda, e in
`esperienza` trovi guadagnati, spesi e disponibili gia' calcolati. Non scrivere mai
totali a mano e non fidarti di cio' che ricordi.
- I punti entrano solo con `award_xp`, per una di queste vie: `sessione` (alla
  chiusura della sessione, quando te lo chiede il tavolo: una volta sola per
  sessione), `storia` (si chiude una storia o un arco), `regola` (una regola del
  manuale li concede in gioco: citala), `narratore` (premio eccezionale, raro).
  Quanti punti, lo dicono le regole sull'esperienza in `{MECHANICS_DIR}/`: leggile la
  prima volta che ti servono, non andare a memoria.
- I punti escono solo con `spend_xp`, quando il giocatore chiede di migliorare un
  tratto: calcola il costo dalle regole, verifica le condizioni che il manuale
  pone (tempo, addestramento, requisiti narrativi) e passa insieme costo e
  modifiche alla scheda. Se i punti non bastano il codice rifiuta: dillo al giocatore.

## Stile
- Scene brevi e concrete: 1-3 paragrafi, poi una domanda o una scelta.
- Non decidere mai le azioni del personaggio del giocatore.
- Quando l'esito e' incerto chiedi un tiro secondo le regole in `{MECHANICS_DIR}/`;
  se il giocatore ha gia' dato un risultato, interpretalo.
- Coerenza prima di tutto: cio' che sta nei file e' vero, il resto lo inventi
  e poi lo scrivi nei file.
- Non spiegare le tue operazioni sui file: il giocatore vede solo la storia.
- La campagna continua da una sessione all'altra: i turni qui sopra sono gli
  ultimi giocati, anche se di giorni fa. Se ti serve cio' che e' successo prima,
  leggi il diario. Non ricominciare la storia e non ripresentare cio' che il
  giocatore conosce gia'.
"""


MASTER_PROMPT_SCRIBE = MASTER_PROMPT.replace(_WORK_INLINE, _WORK_SCRIBE)

# Livello veloce: il contesto lo compone il programma, il narratore risponde in una chiamata.
_READ_LOOP = MASTER_PROMPT[MASTER_PROMPT.index("## Come lavori a ogni turno"):MASTER_PROMPT.index("3. NARRA E SCRIVI")]
_READ_FAST = """\
## Come lavori a ogni turno: una chiamata
Non hai una conversazione alle spalle e non devi cercare nulla: a ogni turno il programma ti
consegna, prima delle parole del giocatore, il CONTESTO DEL TURNO composto dai documenti della
campagna: la scheda del personaggio (e' li', non "in fondo al prompt"), la scena in corso, i fili
aperti, chi e cosa e' in gioco, le regole pertinenti e gli ultimi scambi parola per parola.
1. Quel contesto fa fede e di norma basta: rispondi subito, senza strumenti.
2. LETTURA DI RISERVA. Solo se per decidere ti manca qualcosa di indispensabile (una regola che
   il contesto non riporta, un luogo mai descritto) usa `lookup_glossary` / `read_file` con
   `section`, tutto in un solo giro. Costa una chiamata in piu': non farlo per scrupolo.
"""
MASTER_PROMPT_FAST = MASTER_PROMPT_SCRIBE.replace(_READ_LOOP, _READ_FAST)

SCRIBE_PROMPT = f"""\
Sei lo scriba di una campagna di gioco di ruolo. Non narri e non decidi nulla: trascrivi
negli archivi cio' che il narratore ha stabilito in questo turno, usando gli strumenti,
tutti in un solo messaggio. Fonti: gli APPUNTI DEL NARRATORE (fanno fede) e la NARRAZIONE.
Non inventare e non aggiungere: se una cosa non e' li', non esiste.
- Diario: `append_file` su `{DIARY}`. Turno normale: una riga che comincia con "- ". Apertura
  o chiusura: un titolo "## ..." come indicato e poche righe.
- Scheda: `update_sheet` con i percorsi del JSON che ricevi (es. `stato.fame`,
  `stato.volonta.attuale`, `stato.condizioni.+` per aggiungere una voce). Una chiamata sola.
  Fai tu il conto dal valore attuale: "-1" su 6 diventa 5.
- Esperienza: `award_xp` / `spend_xp` SOLO se gli appunti hanno una riga "pe" esplicita con
  punti e via (o costo e tratto). Mai di tua iniziativa.
- Fili aperti: se c'e' un "segreto" o i fili cambiano, `set_section` su
  `{SETTING_DIR}/avventura.md`, sezione "Fili aperti di <personaggio>", col testo intero
  aggiornato: tieni le righe ancora valide, togli cio' che e' risolto. Sei righe al massimo.
- Orologi: riga "orologio" negli appunti -> `set_clock` per uno nuovo, `tick_clock` per farne
  avanzare uno esistente (usa il nome esatto tra quelli che ricevi). Mai di tua iniziativa.
- Glossario: `upsert_glossary` per i "nuovo" (al massimo due a turno, tre in un'apertura), una
  frase breve; file `{SETTING_DIR}/npc.md` per i PNG, `{SETTING_DIR}/mondo.md` per i luoghi.
Stile telegrafico. Dopo gli strumenti non scrivere altro. Se non c'e' nulla da trascrivere
rispondi solo "nulla".
"""
SCRIBE_SCENE = """
- SCENA: chiama SEMPRE `set_scene`, anche se gli appunti dicono "nulla". E' il documento da cui il
  narratore ripartira' al prossimo turno, senza altra memoria: deve dire dove si e', quando, chi e'
  presente, cosa e' appena successo (comprese le azioni del giocatore, con precisione) e cosa resta
  da decidere. Parti dalla scena di prima e aggiornala con questo turno; in `da_richiamare` tieni i
  dettagli ancora vivi.
"""
NOTES_RE = re.compile(r"<appunti>(.*?)(?:</appunti>|$)", re.S | re.I)
DEFAULT_SCRIBE_MODEL = "claude-haiku-4-5"
READ_TOOL_NAMES = {"read_file", "lookup_glossary", "list_files"}


PRELUDE_BRIEF = """\
[APERTURA DELLA CAMPAGNA: istruzioni del tavolo, non parole del personaggio]
Questa e' la prima scena di {name}. Non esiste ancora nulla di giocato: la scena deve
nascere dal suo background, non da un luogo neutro.
{notes}
Ragiona cosi' prima di scrivere. E' un metodo, non una scena da copiare:
1. ROTTURA. Nel background della scheda, qual e' l'evento piu' recente che ha spezzato
   la sua vita? Dove, quando, con chi, cosa e' rimasto in sospeso?
2. PUNTO D'INGRESSO. Entra nell'istante in cui le conseguenze di quell'evento
   diventano inevitabili: il risveglio dopo, il luogo dove e' successo, la prima
   persona che se ne accorge. Mai "una sera qualunque", mai un posto sicuro e vuoto.
3. CONCRETEZZA. Trasforma il background in cose che si vedono e si toccano: il luogo
   esatto che deriva dalla sua storia, cio' che e' rimasto di quella notte, gli oggetti,
   i nomi delle persone citate nella scheda, cio' che il suo mestiere e le sue abitudini
   gli fanno notare. Ricordi o lampi dell'accaduto dentro la scena, non come riassunto.
4. REGOLE E MONDO. Il suo stato secondo le regole di questa campagna dev'essere vero
   sulla pagina (per un vampiro appena creato: la Fame, il rischio di frenesia, l'alba).
   Cerca solo cio' che ti serve, in un unico giro di letture e al massimo tre sezioni.
5. MOTORE. Pianta un'urgenza immediata e almeno due fili che porteranno avanti la
   campagna: chi c'entra, cosa vuole, cosa succede se il personaggio non fa nulla.
6. SCRIVI. Narra in seconda persona, 3-5 paragrafi, e chiudi su una scelta aperta.
   Nello stesso messaggio: `append_file` su `{diary}` con la voce "Preludio";
   `set_section` su `{adventure}` con la sezione "Fili aperti di {name}" (3-6 righe per
   te: cosa e' successo davvero, chi c'entra e cosa vuole, le prossime complicazioni;
   sono segreti, mai rivelarli direttamente); `update_sheet` se lo stato del personaggio
   cambia; il glossario solo per cio' che tornera' (al massimo tre voci).
   Tetto per tutte le scritture del preludio: 200 parole, stile telegrafico. Niente
   sezioni in `npc.md` adesso: i PNG nuovi stanno nei fili aperti e nel glossario.
"""


OPENING_BRIEF = """\
[APERTURA DELLA SESSIONE {number}: istruzioni del tavolo, non parole del personaggio]
{name} riprende dopo una pausa. La sessione scorsa e' chiusa e archiviata: qui sopra non c'e'
nulla, quello che e' successo sta nei file.
{notes}
{sources}
2. Apri la scena da li': stesso punto o poco dopo, con il tempo trascorso dichiarato. Due
   righe di "dove eravamo" dentro la narrazione, poi un filo aperto che si muove da solo
   e chiede una risposta. Non ripresentare cio' che il giocatore conosce gia'.
3. 2-4 paragrafi in seconda persona, chiusi su una scelta. Nello stesso messaggio
   `append_file` sul diario con la voce "Sessione {number} — apertura".
"""

OPENING_SOURCES_READ = """\
1. Leggi in un solo giro l'ultima parte del diario (`{diary}`) e la sezione
   "Fili aperti di {name}" in `{adventure}`."""
# 2026-09-19: l'apertura della sessione 2 e' costata due chiamate (0,15 $) per rileggere diario e fili aperti
# che aveva gia' davanti, solo perche' questa istruzione glielo chiedeva.
OPENING_SOURCES_GIVEN = """\
1. NON leggere nulla: scena in corso, fili aperti e l'ultima parte del diario sono gia' qui, nel
   contesto sopra e in fondo a questo messaggio. Una lettura adesso sarebbe una chiamata buttata."""

CLOSING_BRIEF = """\
[CHIUSURA DELLA SESSIONE {number}: istruzioni del tavolo, non parole del personaggio]
Il giocatore chiude qui la sessione. Tutto in UN SOLO messaggio: prima il testo per il
giocatore, poi gli strumenti, senza aspettarne gli esiti.
1. Un paragrafo breve che ferma la scena dove si trova (niente eventi nuovi, niente scelte).
{recap}
4. ESPERIENZA. Assegna con `award_xp`, via `sessione`, i punti che le regole di questa campagna
   prevedono per una sessione giocata; se in questa sessione si e' chiusa una storia, anche via
   `storia`. Nel testo di' al giocatore quanti punti riceve e perche'; il totale non
   scriverlo: lo calcola il programma e lo aggiunge in coda.
{rules}"""

CLOSING_RECAP_BY_NARRATOR = """\
2. `append_file` su `{diary}`: "Sessione {number} — riepilogo", 3-6 righe con fatti, nomi, conseguenze.
3. `set_section` su `{adventure}`, "Fili aperti di {name}": aggiornati a cio' che e' successo
   (cosa e' risolto, cosa si e' mosso, cosa incombe alla ripresa)."""
CLOSING_RECAP_BY_MEMORY = """\
2. Niente riepilogo e niente fili aperti: subito dopo la tua risposta il giocato di questa sessione
   viene riletto per intero e consolidato in memoria. Negli appunti metti solo i punti esperienza."""
NL_ = "\n"

XP_RULE_TITLES = re.compile(r"esperienz|avanzament|crescita del personaggio|passaggi? di livello", re.I)
XP_RULES_LIMIT = 4_000


@dataclass
class TurnResult:
    text: str
    notes: str = ""          # gli appunti del narratore per lo scriba (il giocatore non li vede)
    scribe_cost_usd: float = 0.0
    files_read: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


class MasterEngine:
    """Tiene la conversazione della sessione e guida il ciclo API <-> file."""

    def __init__(
        self,
        campaign: Campaign,
        client: Any = None,
        model: str | None = None,
        max_tokens: int = 16000,
        effort: str | None = None,
        max_history: int = 30,
        max_tool_rounds: int = 12,
        prompt: str = MASTER_PROMPT,
        tools: list[dict[str, Any]] | None = None,
        log_session: bool = True,
        logger_name: str = "master.gioco",
        with_active_pg: bool = True,
        save_name: str | None = None,
        resume_turns: int = 12,
        scribe: bool = False,
        scribe_model: str | None = None,
        memory: bool = False,
        memory_model: str | None = None,
        fast: bool = False,
    ):
        self.campaign = campaign
        self.model = model or os.environ.get("MASTER_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.effort = effort or os.environ.get("MASTER_EFFORT") or None
        self.max_history = max_history
        self.max_tool_rounds = max_tool_rounds
        # scriba: la narrazione e le scritture sono due chiamate separate. Il narratore ha solo
        # strumenti di lettura; le scritture le fa un modello economico a partire dai suoi appunti.
        self.scribe = scribe
        self.scribe_model = scribe_model or os.environ.get("MASTER_SCRIBE_MODEL") or DEFAULT_SCRIBE_MODEL
        self._unwritten_notes: list[str] = []
        # memoria lenta: alla chiusura di una sessione (o a comando) il giocato grezzo diventa memoria
        self.memory = memory
        self.memory_model = memory_model or os.environ.get("MASTER_MEMORY_MODEL") or None
        self.last_memory_report: Any = None
        # livello veloce: niente conversazione, il contesto del turno lo compone il codice (context.py)
        self.fast = bool(fast and scribe)
        if scribe and prompt is MASTER_PROMPT:
            prompt = MASTER_PROMPT_FAST if self.fast else MASTER_PROMPT_SCRIBE
        if scribe and tools is None:
            tools = [t for t in TOOLS if t["name"] in READ_TOOL_NAMES]
        self.prompt = prompt
        self.tools = tools if tools is not None else TOOLS
        self.log_session = log_session
        self.with_active_pg = with_active_pg
        self.fixed_save_name = save_name
        self.resume_turns = resume_turns
        self.log = logging.getLogger(logger_name)
        self.messages: list[dict[str, Any]] = []
        self.executor = ToolExecutor(campaign)
        self.turns = 0
        self._system_snapshot: list[dict[str, Any]] | None = None
        self._snapshot_turn = 0
        self._pending_results: list[dict[str, Any]] = []
        self._anchors: list[dict[str, Any]] = []  # i messaggi del giocatore degli ultimi due turni: li' sta la cache
        self._calls_in_turn = 0
        self._out_chars = [0, 0]  # caratteri prodotti nel turno: narrazione, argomenti dei tool
        self._client = client

    # --- client pigro ----------------------------------------------------

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # importato qui per permettere test senza rete

            self._client = anthropic.Anthropic()
        return self._client

    # --- prompt -----------------------------------------------------------

    def system_blocks(self) -> list[dict[str, Any]]:
        """Prompt stabile (cache) + glossario (cache) + indice file (volatile)."""
        # Due punti di cache: dopo il prompt (non cambia mai) e dopo glossario + indice (cambiano di
        # rado). Durata un'ora: tra un turno e l'altro un giocatore impiega minuti, e con i 5
        # minuti predefiniti la cache scadeva e si ripagava tutto a prezzo pieno piu' la riscrittura.
        if self.fast:
            # primo blocco: non cambia mai. Secondo: la parte stabile della scheda, che cambia solo quando
            # il personaggio cresce; se cambia si riscrive lei sola, il primo blocco resta in cache.
            blocks = [{"type": "text", "text": self.prompt + self.dice_text(), "cache_control": CACHE}]
            sheet = self.campaign.active_sheet() if self.with_active_pg else None
            data = Sheet(self.campaign, sheet["slug"]) if sheet else None
            if data is not None and data.exists:
                blocks.append({"type": "text", "cache_control": CACHE, "text":
                               f"# SCHEDA DI {sheet['name'].upper()}: la parte che cambia di rado (tratti, identita', storia)\n"
                               "Stato ed esperienza, che cambiano spesso, li trovi aggiornati nel contesto di ogni turno.\n"
                               "```json\n" + data.stable_json() + "\n```"})
            return blocks
        blocks = [
            {"type": "text", "text": self.prompt + self.dice_text(), "cache_control": CACHE},
            {"type": "text", "text": "# GLOSSARIO (primo riferimento)\n\n" + self.glossary_text()},
            {"type": "text", "text": "# File disponibili\n" + self.index_text(), "cache_control": CACHE},
        ]
        pg = self.pg_block()
        if pg:
            blocks.append({"type": "text", "text": pg})
        return blocks

    def dice_text(self) -> str:
        """Come si chiede un tiro in QUESTA campagna: generato dal profilo ricavato dal manuale."""
        return dice.prompt_section(dice.load_profile(self.campaign)) if self.with_active_pg else ""

    def glossary_text(self) -> str:
        return self.campaign.glossary.prompt_text()

    def index_text(self) -> str:
        return self.campaign.index(max_titles=8)

    def pg_block(self) -> str:
        """La scheda del personaggio attivo, sempre sotto gli occhi del master (niente read_file per averla)."""
        if not self.with_active_pg:
            return ""
        sheet = self.campaign.active_sheet()
        if sheet is None:
            return ""
        data = Sheet(self.campaign, sheet["slug"])
        if data.exists:
            return (f"# PERSONAGGIO ATTIVO: {sheet['name']} (dati `{SHEETS_DIR}/{data.slug}.json`)\n"
                    "E' il personaggio del giocatore in questa campagna, e questa e' la sua scheda intera: non serve "
                    "leggerla. Si cambia con `update_sheet` usando i percorsi di questo JSON (es. `stato.fame`); "
                    "l'esperienza solo con `award_xp` / `spend_xp`. Se in questa conversazione l'hai gia' modificata, "
                    f"valgono gli esiti piu' recenti dei tuoi strumenti. Sessione in corso: {self.campaign.session_number}.\n\n"
                    "```json\n" + data.prompt_json() + "\n```")
        text = self.campaign.file(sheet["file"]).read()
        return (f"# PERSONAGGIO ATTIVO: {sheet['name']} (file `{sheet['file']}`)\n"
                "E' il personaggio del giocatore in questa campagna. Non serve rileggere la scheda: e' questa. "
                "Quando qualcosa cambia (Stato, Equipaggiamento...), aggiorna quel file; se in questa conversazione "
                "l'hai gia' modificata, valgono le tue modifiche piu' recenti.\n\n" + text)

    def _cached_messages(self) -> list[dict[str, Any]]:
        """La conversazione con i punti di cache sulle parole del giocatore, non sulle letture.

        Scrivere in cache costa il doppio dell'ingresso, leggere un decimo: conviene solo per cio'
        che verra' riletto. Le sezioni lette con i tool servono alla chiamata successiva e poi, dal
        turno dopo, diventano una riga: metterle in cache voleva dire pagarle doppio per nulla
        (il 2026-09-18, 6.400 token su un preludio). Il punto sta quindi sul messaggio del
        giocatore di questo turno e su quello del turno prima, cosi' il prefisso gia' scritto si
        ritrova sempre. Solo dalla terza chiamata dello stesso turno, quando le letture verrebbero
        ripagate piu' volte, il punto passa anche sull'ultimo messaggio.
        La lista salvata resta pulita: i segni si mettono su copie.
        """
        if not self.messages:
            return []
        if self.fast:  # il contesto cambia a ogni turno: scriverlo in cache costerebbe doppio per nulla
            return list(self.messages)
        marked = [m for m in self.messages if any(m is a for a in self._anchors)]
        if self._calls_in_turn >= 2 or not marked:
            marked = marked[-1:] + [self.messages[-1]]
        out = []
        for m in self.messages:
            if not any(m is x for x in marked):
                out.append(m)
                continue
            copy = dict(m)
            content = copy["content"]
            if isinstance(content, str):
                blocks: list[Any] = [{"type": "text", "text": content}]
            else:
                blocks = [dict(b) if isinstance(b, dict) else b for b in content]
            if blocks and isinstance(blocks[-1], dict):
                blocks[-1] = {**blocks[-1], "cache_control": CACHE}
                copy["content"] = blocks
            out.append(copy)
        return out

    # --- richiesta: tutto cio' che sta prima della conversazione resta fermo ------------------

    def _system(self) -> list[dict[str, Any]]:
        """Il prompt di sistema, fotografato e tenuto fermo per SNAPSHOT_TURNS turni.

        La cache e' un prefisso: strumenti, sistema, conversazione. Se a meta' turno il master
        aggiorna glossario o scheda e il prompt viene ricostruito, tutto cio' che segue (l'intera
        conversazione) si invalida e viene riscritto al doppio del prezzo: il 2026-09-18 una scena
        e' costata 48 centesimi, 40 dei quali di sola riscrittura. Le modifiche che il master fa
        ai file le conosce dalla conversazione; la fotografia si rinnova ogni pochi turni, quando
        lo storico viene ridotto e a ogni ripresa o nuova sessione.
        """
        if self.fast:  # nessuna conversazione da proteggere: il prompt si ricompone a ogni turno, la cache va a contenuto
            return self.system_blocks()
        if self._system_snapshot is None or self.turns - self._snapshot_turn >= SNAPSHOT_TURNS:
            self._system_snapshot = self.system_blocks()
            self._snapshot_turn = self.turns
        return self._system_snapshot

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._system(),
            tools=self.tools,
            messages=self._cached_messages(),
        )
        if self.effort:
            kwargs["extra_body"] = {"output_config": {"effort": self.effort}}
        return kwargs

    def _call_api(self, result: TurnResult, n: int) -> Any:
        self.log.info(
            "turno %d api #%d -> %s effort=%s messaggi=%d", self.turns, n, self.model, self.effort or "-", len(self.messages)
        )
        try:
            response = self.client.messages.create(**self._request_kwargs())
        except Exception as exc:
            self.log.error(
                "turno %d INTERROTTO alla chiamata api #%d: %s", self.turns, n, describe_error(exc), exc_info=True
            )
            raise
        self._calls_in_turn += 1
        self._count(result, response)
        for b in response.content:  # dove va l'uscita, che e' la voce piu' cara: racconto o appunti sui file
            if getattr(b, "type", "") == "text":
                self._out_chars[0] += len(b.text)
            elif getattr(b, "type", "") == "tool_use":
                self._out_chars[1] += len(json.dumps(dict(b.input), ensure_ascii=False))
        self.log.info("turno %d api #%d <- %s", self.turns, n, usage_line(response))
        self.messages.append({"role": "assistant", "content": response.content})
        return response

    # --- persistenza: si riparte da dove si era ------------------------------------------

    @property
    def save_name(self) -> str | None:
        """Il nome dell'archivio del giocato: fisso (creazione schede) o lo slug del personaggio attivo."""
        if not self.log_session:
            return None
        if self.fixed_save_name:
            return self.fixed_save_name
        return self.campaign.active_pg or "senza-pg"

    def history(self, limit: int | None = 40) -> list[dict]:
        name = self.save_name
        return self.campaign.read_turns(name, limit) if name else []

    def _fresh_context(self) -> None:
        self.messages.clear()
        self._anchors = []
        self._pending_results = []
        self._system_snapshot = None

    def resume(self) -> int:
        """Ricarica nella conversazione gli ultimi turni salvati, come testo semplice.

        Solo le battute: niente tool ne' ragionamenti dei turni passati, che non servono
        (lo stato vive nei file) e legherebbero il salvataggio a un modello o a una versione.
        """
        if self.messages or not self.save_name:
            return 0
        self._fresh_context()
        turns = self.campaign.read_turns(self.save_name, self.resume_turns)
        for t in turns:
            self.messages.append({"role": "user", "content": t["player"]})
            self.messages.append({"role": "assistant", "content": t["master"]})
        if turns:
            self.turns = self.campaign.turn_count(self.save_name)
            self.log.info("conversazione ripresa da %s: %d turni in archivio, ultimi %d in contesto",
                          self.save_name, self.turns, len(turns))
        return len(turns)

    def _scene_memory(self, name: str) -> str:
        """Cio' che la memoria lenta ha lasciato per la ripresa, gia' nel messaggio: non serve leggerlo."""
        from .memory import SCENE

        if self.fast:  # scena e fili aperti sono gia' nel contesto del turno: qui si aggiunge solo la coda del diario
            diary = self.campaign.file(DIARY)
            tail = diary.read().strip()[-2500:] if diary.exists() else ""
            cut = tail.find(NL_)
            tail = tail[cut + 1:] if 0 <= cut < 200 else tail  # niente riga mozzata in testa
            return f"{NL_}## Ultima parte del diario{NL_}{NL_}{tail}{NL_}" if tail else ""
        parts = []
        scene = self.campaign.file(SCENE)
        if scene.exists():
            parts.append(scene.read().strip())
        adventure = self.campaign.file(f"{SETTING_DIR}/avventura.md")
        threads = adventure.get_section(f"Fili aperti di {name}") if adventure.exists() else None
        if threads:
            parts.append(f"## Fili aperti di {name}{NL_}{NL_}{threads.strip()}")
        if not parts:
            return ""
        return f"{NL_}Dalla memoria della campagna (non serve leggere questi file, li hai qui):{NL_}{NL_}" + f"{NL_}{NL_}".join(parts) + NL_

    def _played_turns(self) -> int:
        """I turni giocati davvero in questa conversazione: aperture e chiusure del tavolo non contano."""
        marks = (PRELUDE_MARK, OPENING_MARK, CLOSING_MARK)
        return sum(1 for t in self.campaign.read_turns(self.save_name or "") if not t["player"].startswith(marks))

    def can_close(self) -> bool:
        return self.with_active_pg and self.campaign.active_sheet() is not None and self._played_turns() >= MIN_TURNS_TO_CLOSE

    def close_session(self, consolidate: bool = True) -> TurnResult | None:
        """La via `sessione` dell'esperienza: riepilogo, fili aperti e punti, poi si archivia.

        Con meno di MIN_TURNS_TO_CLOSE turni giocati non c'e' una sessione da chiudere: si
        archivia e basta, senza chiamate e senza far avanzare il contatore delle sessioni.
        """
        result = None
        if self.can_close():
            sheet = self.campaign.active_sheet()
            number = self.campaign.session_number
            recap = (CLOSING_RECAP_BY_MEMORY if self.memory else CLOSING_RECAP_BY_NARRATOR).format(
                number=number, name=sheet["name"], diary=DIARY, adventure=f"{SETTING_DIR}/avventura.md")
            brief = CLOSING_BRIEF.format(number=number, name=sheet["name"], recap=recap, rules=self._xp_rules())
            result = self.play(brief, saved_as=CLOSING_MARK)
            if self.memory and consolidate:
                try:  # la sessione si chiude comunque: il giocato resta da consolidare alla prossima occasione
                    report = self.consolidate()
                    result.cost_usd = round(result.cost_usd + report.cost_usd, 4)
                    result.files_changed = list(dict.fromkeys([*result.files_changed, *report.files_changed]))
                    result.text += f"{NL_}{NL_}*{report.summary()}*"
                except Exception as exc:
                    self.log.error("chiusura: memoria non consolidata (%s), si riprovera'", describe_error(exc))
            data = Sheet(self.campaign, sheet["slug"])
            if data.exists:  # il totale lo dice il registro, non il master
                xp = data.xp()
                result.text += (f"\n\n*Esperienza di {sheet['name']}: {xp['disponibili']} disponibili "
                                f"({xp['guadagnati']} guadagnati, {xp['spesi']} spesi).*")
        self.new_session(advance=result is not None)
        return result

    def consolidate(self, progress: Any = None) -> Any:
        """Il livello lento: rilegge il giocato grezzo non ancora consolidato e lo converte in memoria."""
        from .memory import Consolidator

        report = Consolidator(self.campaign, self.client, self.memory_model).run(progress or (lambda text: None))
        self.last_memory_report = report
        if report.turns:
            self._system_snapshot = None  # indice dei file e fili aperti sono cambiati
        return report

    def _xp_rules(self) -> str:
        """Le regole sull'esperienza prese da `meccanica/`, gia' nel messaggio: niente giro di letture per cercarle."""
        parts, size = [], 0
        for f in self.campaign.files():
            if not f.name.startswith(MECHANICS_DIR + "/"):
                continue
            for title, body in f.sections().items():
                if title and XP_RULE_TITLES.search(title) and size + len(body) <= XP_RULES_LIMIT:
                    parts.append(f"### {title} ({f.name})\n{body.strip()}")
                    size += len(body)
        if not parts:
            return (f"\nNon ho trovato in `{MECHANICS_DIR}/` una sezione sull'esperienza: se il manuale ne parla altrove "
                    "leggila (una sola lettura), altrimenti assegna 1 punto per la sessione.\n")
        return ("\nLe regole di questa campagna sull'esperienza (non serve leggerle):\n\n"
                + "\n\n".join(parts) + "\n")

    def new_session(self, advance: bool = False) -> bool:
        """Archivia la conversazione corrente e ne apre una vuota. I file di campagna restano."""
        # senza chiusura e' un inizio rifatto da capo: resta in archivio, ma non entra nella memoria
        discarded = not advance and self.with_active_pg
        archived = self.campaign.archive_turns(self.save_name, discarded=discarded) if self.save_name else None
        self._fresh_context()
        self.turns = 0
        if archived:
            number = self.campaign.next_session() if advance else self.campaign.session_number
            self.log.info("nuova sessione (%d): conversazione precedente archiviata in %s", number, archived.name)
        return archived is not None

    # --- ciclo di un turno --------------------------------------------------

    def prelude(self, notes: str = "") -> TurnResult:
        """Apre la campagna per il personaggio attivo: la prima scena nasce dal suo background."""
        sheet = self.campaign.active_sheet()
        if sheet is None:
            raise ValueError("Crea o scegli prima un personaggio")
        if self.campaign.turn_count(self.save_name or ""):
            raise ValueError("Questa campagna e' gia' iniziata: il preludio apre solo una conversazione vuota")
        notes = notes.strip()
        hint = f"\nIndicazioni del giocatore per l'apertura: {notes}\n" if notes else ""
        number = self.campaign.session_number
        if number > 1:
            # dalla seconda sessione non si rinasce dal background: si riparte da diario e fili aperti
            sources = (OPENING_SOURCES_GIVEN if self.fast else OPENING_SOURCES_READ).format(
                name=sheet["name"], diary=DIARY, adventure=f"{SETTING_DIR}/avventura.md")
            brief = OPENING_BRIEF.format(number=number, name=sheet["name"], diary=DIARY, sources=sources,
                                         adventure=f"{SETTING_DIR}/avventura.md", notes=hint) + self._scene_memory(sheet["name"])
            return self.play(brief, saved_as=OPENING_MARK + (f" {notes}" if notes else ""))
        brief = PRELUDE_BRIEF.format(name=sheet["name"], diary=DIARY, adventure=f"{SETTING_DIR}/avventura.md", notes=hint)
        return self.play(brief, saved_as=PRELUDE_MARK + (f" {notes}" if notes else ""))

    def play(self, player_text: str, saved_as: str | None = None) -> TurnResult:
        player_text = player_text.strip()
        if not player_text:
            raise ValueError("Testo del giocatore vuoto")
        if self.fast:
            self.messages.clear()
            self._pending_results = []
            self.turns = self.campaign.turn_count(self.save_name or "")
        else:
            self.resume()
        self.turns += 1
        self.log.info("turno %d | giocatore: %s", self.turns, short(saved_as or player_text, 200))
        self.executor.reset()
        self._compact_previous_turn()
        spoken = player_text  # le parole del giocatore: cio' che si salva e che legge lo scriba
        if self.fast:
            from .context import compose

            # le istruzioni del tavolo e gli esiti dei dadi non sono azioni da instradare
            router = self._route_rules if saved_as is None and not spoken.startswith("[Tiro]") else None
            context = compose(self.campaign, spoken if saved_as is None else "", self.save_name or "", router=router)
            self.log.info("turno %d contesto: %s", self.turns, context.stats())
            player_text = context.text + self._table_state() + ("GIOCATORE: " if saved_as is None else "") + player_text
        elif self.scribe:
            player_text = self._table_state() + player_text
        if self._pending_results:
            # le scritture chiuse insieme alla narrazione del turno scorso: i loro esiti viaggiano ora
            content: Any = [*self._pending_results, {"type": "text", "text": player_text}]
            self._pending_results = []
        else:
            content = player_text
        self.messages.append({"role": "user", "content": content})
        self._anchors = (self._anchors + [self.messages[-1]])[-2:]
        self._calls_in_turn = 0
        self._out_chars = [0, 0]
        self._trim_history()

        result = TurnResult(text="")
        response = None
        calls = 0
        closed_with_writes = False
        for _ in range(self.max_tool_rounds + 1):
            calls += 1
            response = self._call_api(result, calls)
            if response.stop_reason != "tool_use":
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            results = []
            errors = 0
            for b in tool_uses:
                text, is_error = self.executor.execute(b.name, dict(b.input))
                errors += int(is_error)
                self.log.log(
                    logging.WARNING if is_error else logging.INFO,
                    "turno %d tool %s(%s) -> %s", self.turns, b.name, short(b.input, 160),
                    ("ERRORE " + short(text, 200)) if is_error else short(text, 80),
                )
                results.append(tool_result_block(b.id, text, is_error))
            narration = self._text_of(response)
            if (len(narration) >= MIN_NARRATION and not errors
                    and all(b.name in WRITE_TOOLS for b in tool_uses)):
                # narrazione e scritture nello stesso messaggio: il turno e' finito, niente giro in piu'.
                # Gli esiti delle scritture aprono il prossimo messaggio del giocatore.
                self._pending_results = results
                closed_with_writes = True
                self.log.info("turno %d chiuso con narrazione e %d scritture nello stesso messaggio", self.turns, len(tool_uses))
                break
            self.messages.append({"role": "user", "content": results})
        else:
            # troppi giri di tool: chiudiamo il turno chiedendo la risposta
            self.log.warning("turno %d: raggiunto il limite di %d giri di tool, chiedo la risposta", self.turns, self.max_tool_rounds)
            self.messages.append(
                {"role": "user", "content": "Basta operazioni sui file: rispondi ora al giocatore."}
            )
            calls += 1
            response = self._call_api(result, calls)

        result.stop_reason = "end_turn" if closed_with_writes else response.stop_reason
        result.text = self._text_of(response) or self._fallback_text(response)
        narrator_cost = cost_usd(self.model, result.input_tokens, result.output_tokens,
                                 cache_read=result.cache_read_tokens, cache_write=result.cache_write_tokens)
        if self.scribe and result.stop_reason in ("end_turn", "max_tokens"):
            result.text, result.notes = self._split_notes(result.text)
            result.scribe_cost_usd = self._run_scribe(saved_as or spoken, result.text, result.notes)
        result.files_read = list(self.executor.files_read)
        result.files_changed = list(self.executor.files_changed)
        if self.fast:
            if result.files_read or calls > 1:  # quanto spesso la selezione del codice non e' bastata
                self.log.info("turno %d LETTURA DI RISERVA: %d chiamate, letti=%s", self.turns, calls, ", ".join(result.files_read) or "-")
            self.messages.clear()
        if result.stop_reason not in ("end_turn", "tool_use"):
            self.log.warning("turno %d chiuso con stop=%s: %s", self.turns, result.stop_reason, short(result.text, 200))
        if self.save_name and result.stop_reason in ("end_turn", "max_tokens"):
            sheet = self.campaign.active_sheet() if self.with_active_pg else None
            self.campaign.append_turn(
                self.save_name, saved_as or spoken, result.text,
                pg=sheet["name"] if sheet else None,
                read=result.files_read, changed=result.files_changed,
                tokens=[result.input_tokens, result.output_tokens],
            )
        result.cost_usd = round(narrator_cost + result.scribe_cost_usd, 4)
        process = "gioco" if self.with_active_pg else "scheda"
        self.campaign.record_spend(process, self.model, narrator_cost, turno=self.turns, chiamate=calls,
                                   token=[result.input_tokens, result.output_tokens, result.cache_read_tokens, result.cache_write_tokens])
        self.campaign.record_spend("scriba", self.scribe_model, result.scribe_cost_usd, turno=self.turns)
        self.log.info(
            "turno %d fine | chiamate=%d token in/out=%d/%d cache letti/scritti=%d/%d costo~$%.3f "
            "| uscita: narrazione %d car, tool %d car | letti=%s | modificati=%s",
            self.turns, calls, result.input_tokens, result.output_tokens, result.cache_read_tokens,
            result.cache_write_tokens, result.cost_usd, self._out_chars[0], self._out_chars[1],
            ", ".join(result.files_read) or "-", ", ".join(result.files_changed) or "-",
        )
        return result

    def _table_state(self) -> str:
        """Cio' che il programma conta al posto del narratore, in testa al messaggio del turno.

        Sta qui e non nel prompt di sistema perche' cambia spesso: nel prompt invaliderebbe la cache.
        """
        lines = [f"[OROLOGIO PIENO: {c}. La conseguenza accade in questa scena.]" for c in self.campaign.pop_full_clocks()]
        active = self.campaign.clocks_line()
        if active:
            lines.append(f"[Orologi: {active}]")
        return "\n".join(lines) + "\n" if lines else ""

    def _route_rules(self, titles: list[str], action: str, scene: str) -> list[str]:
        """L'instradatore: quali sezioni di regole servono per questa azione? Una chiamata minuscola sul
        modello dello scriba, solo quando le parole del giocatore non nominano nessuna regola."""
        if not titles:
            return []
        system = [{"type": "text", "cache_control": CACHE, "text":
                   "Scegli le sezioni di regole di un gioco di ruolo che servono al master per risolvere l'azione di un "
                   "giocatore. Rispondi SOLO con i titoli esatti, uno per riga, al massimo 3, dal piu' utile. Se l'azione "
                   "non richiede regole (parlare, guardarsi attorno, spostarsi senza rischi) rispondi: nessuna." + NL_ + NL_
                   + "SEZIONI:" + NL_ + NL_.join(titles)}]
        response = self.client.messages.create(model=self.scribe_model, max_tokens=200, system=system,
                                               messages=[{"role": "user", "content": f"SCENA: {scene}{NL_}{NL_}AZIONE DEL GIOCATORE: {action}"}])
        usage = getattr(response, "usage", None)
        cost = cost_usd(self.scribe_model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0,
                        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0, cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0)
        self.campaign.record_spend("instradamento", self.scribe_model, cost, turno=self.turns)
        text = NL_.join(b.text for b in response.content if b.type == "text")
        chosen = [line.strip(" -*`") for line in text.splitlines() if line.strip()]
        self.log.info("turno %d instradamento regole: %s (costo~$%.4f)", self.turns, ", ".join(chosen) or "nessuna", cost)
        return [c for c in chosen if c.lower() not in ("nessuna", "nessuna.")]

    # --- lo scriba: la chiamata di scrittura, separata da quella di narrazione ------------------

    @staticmethod
    def _split_notes(text: str) -> tuple[str, str | None]:
        """Divide la narrazione dagli appunti. None = il narratore non ha lasciato il blocco."""
        found = NOTES_RE.search(text)
        if not found:
            return text.strip(), None
        return (text[:found.start()] + text[found.end():]).strip(), found.group(1).strip()

    def _scribe_context(self) -> str:
        sheet = self.campaign.active_sheet() if self.with_active_pg else None
        if sheet is None:
            return ""
        parts = [f"PERSONAGGIO: {sheet['name']} | sessione {self.campaign.session_number}"]
        data = Sheet(self.campaign, sheet["slug"])
        if data.exists:
            parts.append("SCHEDA (JSON, per i percorsi di update_sheet):" + "\n" + (data.working_json() if self.fast else data.prompt_json()))
        adventure = self.campaign.file(f"{SETTING_DIR}/avventura.md")
        title = f"Fili aperti di {sheet['name']}"
        threads = adventure.get_section(title) if adventure.exists() else None
        if self.fast:
            from .memory import SCENE

            scene = self.campaign.file(SCENE)
            parts.append("SCENA PRIMA DI QUESTO TURNO:" + NL_ + (scene.read().strip() if scene.exists() else "(nessuna: e' la prima)"))
        parts.append("OROLOGI ATTIVI: " + (self.campaign.clocks_line() or "(nessuno)"))
        parts.append(f'FILI APERTI ATTUALI (sezione "{title}"):' + "\n" + (threads.strip() if threads else "(nessuno)"))
        return "\n\n".join(parts)

    def _run_scribe(self, player: str, narration: str, notes: str | None) -> float:
        """Trascrive negli archivi gli appunti del narratore. Ritorna il costo della chiamata.

        Un errore dello scriba non butta via il turno (la narrazione e' gia' pagata): gli appunti
        restano in attesa e partono insieme a quelli del turno dopo.
        """
        if notes is not None and notes.strip(" .").lower() in ("", "nulla", "niente", "nessuno"):
            if not self._unwritten_notes and not self.fast:
                self.log.info("turno %d scriba: nulla da trascrivere", self.turns)
                return 0.0
            notes = ""
        backlog = "".join(f"APPUNTI RIMASTI DA UN TURNO PRECEDENTE:\n{n}\n\n" for n in self._unwritten_notes)
        body = (self._scribe_context() + "\n\n" + backlog + f"GIOCATORE:\n{short(player, 600)}\n\nNARRAZIONE:\n{narration}\n\n"
                + "APPUNTI DEL NARRATORE:\n" + (notes if notes else "(non li ha lasciati: ricava dalla narrazione solo i fatti certi)"))
        messages: list[dict[str, Any]] = [{"role": "user", "content": body}]
        tokens = [0, 0]
        try:
            for round_ in range(2):  # il secondo giro solo per correggere uno strumento fallito
                response = self.client.messages.create(model=self.scribe_model, max_tokens=4000,
                                                       system=SCRIBE_PROMPT + (SCRIBE_SCENE if self.fast else ""),
                                                       tools=self._scribe_tools(), messages=messages)
                usage = getattr(response, "usage", None)
                tokens[0] += getattr(usage, "input_tokens", 0) or 0
                tokens[1] += getattr(usage, "output_tokens", 0) or 0
                uses = [b for b in response.content if b.type == "tool_use"]
                results, errors = [], 0
                for b in uses:
                    if b.name in READ_TOOL_NAMES or b.name == "save_sheet":
                        text, is_error = "Lo scriba non legge e non crea schede: trascrive soltanto.", True
                    else:
                        text, is_error = self.executor.execute(b.name, self._as_notes(b.name, dict(b.input)))
                    errors += int(is_error)
                    self.log.log(logging.WARNING if is_error else logging.INFO, "turno %d scriba %s(%s) -> %s", self.turns, b.name,
                                 short(b.input, 160), ("ERRORE " + short(text, 200)) if is_error else short(text, 80))
                    results.append(tool_result_block(b.id, text, is_error))
                if not errors or round_:
                    break
                messages += [{"role": "assistant", "content": response.content}, {"role": "user", "content": results}]
        except Exception as exc:
            if notes:
                self._unwritten_notes.append(notes)
            self.log.error("turno %d SCRIBA FALLITO (%s): appunti tenuti per il prossimo turno: %s",
                           self.turns, describe_error(exc), short(notes or "", 400))
            return 0.0
        self._unwritten_notes = []
        cost = cost_usd(self.scribe_model, tokens[0], tokens[1])
        self.log.info("turno %d scriba %s | token in/out=%d/%d costo~$%.4f", self.turns, self.scribe_model, tokens[0], tokens[1], cost)
        return cost

    def _as_notes(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Con la memoria lenta, cio' che lo scriba aggiunge al diario e' provvisorio: finisce sotto
        `## Appunti`, che il consolidamento sostituira' con la cronaca. Niente seconde copie dei fatti."""
        if not self.memory or tool != "append_file" or str(args.get("name", "")).strip() != DIARY:
            return args
        from .memory import NOTES_SECTION

        text = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"**\1**", str(args.get("text", ""))).strip()
        diary = self.campaign.file(DIARY)
        titles = diary.outline() if diary.exists() else []
        head = "" if titles and titles[-1] == NOTES_SECTION else f"{NL_}## {NOTES_SECTION}{NL_}{NL_}"
        return {**args, "text": head + text}

    def _scribe_tools(self) -> list[dict[str, Any]]:
        from .tools import SCENE_TOOL

        return [t for t in TOOLS if t["name"] not in READ_TOOL_NAMES] + ([SCENE_TOOL] if self.fast else [])

    # --- utilita' -------------------------------------------------------------

    @staticmethod
    def _text_of(response: Any) -> str:
        return "\n".join(b.text for b in response.content if b.type == "text").strip()

    @staticmethod
    def _fallback_text(response: Any) -> str:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            why = getattr(details, "explanation", None) or "richiesta rifiutata"
            return f"[Il master non puo' proseguire su questa strada: {why}]"
        if response.stop_reason == "max_tokens":
            return "[Risposta troncata: riprova con una richiesta piu' breve]"
        return "[Il master resta in silenzio]"

    @staticmethod
    def _count(result: TurnResult, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        result.input_tokens += getattr(usage, "input_tokens", 0) or 0
        result.output_tokens += getattr(usage, "output_tokens", 0) or 0
        result.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        result.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @staticmethod
    def _has_tool_results(message: dict[str, Any]) -> bool:
        content = message.get("content")
        return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)

    def _compact_previous_turn(self) -> None:
        """Le sezioni lette nei turni passati diventano una riga: cosa era, e che si puo' rileggere.

        Una sezione di regole sono migliaia di token che altrimenti restano nella conversazione
        per tutta la sessione. Si fa una volta sola, a inizio turno, su cio' che non e' gia' stato
        ridotto: il prefisso in cache cambia solo dalla prima sezione ridotta in poi.
        Non con Fable: li' modificare lo storico invalida i blocchi di ragionamento.
        """
        if "fable" in self.model or "mythos" in self.model:
            return
        calls: dict[str, str] = {}
        for m in self.messages:
            content = m.get("content")
            if not isinstance(content, list):
                continue
            if m["role"] == "assistant":
                for b in content:
                    if getattr(b, "type", None) == "tool_use" or (isinstance(b, dict) and b.get("type") == "tool_use"):
                        name = getattr(b, "name", None) or b.get("name")
                        args = getattr(b, "input", None) or (b.get("input") if isinstance(b, dict) else {}) or {}
                        where = str(args.get("name", "")) + (f" § {args['section']}" if args.get("section") else "")
                        calls[getattr(b, "id", None) or b.get("id")] = f"{name} {where}".strip()
            else:
                for i, b in enumerate(content):
                    if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), str) \
                            and len(b["content"]) > STUB_ABOVE:
                        what = calls.get(b.get("tool_use_id"), "lettura")
                        content[i] = {**b, "content": f"[{what}: letto in un turno precedente ({len(b['content'])} caratteri). "
                                                      "Se ti serve ancora, rileggilo.]"}

    def _trim_history(self) -> None:
        """Quando lo storico supera il limite lo si dimezza, tagliando su un messaggio pulito del giocatore.

        Dimezzare e non togliere un messaggio alla volta: ogni taglio cambia l'inizio della
        conversazione e invalida tutta la cache. Con il taglio a ogni turno la creazione di una
        scheda e' arrivata a 45 centesimi a turno di sola riscrittura; cosi' succede di rado.
        Lo stato della storia vive nei file, quindi perdere turni vecchi non perde informazione.
        """
        if len(self.messages) <= self.max_history:
            return
        start = len(self.messages) - max(2, self.max_history // 2)
        for i in range(start, len(self.messages)):
            m = self.messages[i]
            if m["role"] == "user" and not self._has_tool_results(m):
                self.log.info("storico dimezzato: tolti %d messaggi vecchi, ne restano %d", i, len(self.messages) - i)
                del self.messages[:i]
                self._system_snapshot = None  # la cache e' comunque da rifare: si aggiorna anche la fotografia
                return

    def reset_snapshot(self) -> None:
        """Il prompt va rifatto alla prossima chiamata (es. e' arrivato il profilo dei dadi)."""
        self._system_snapshot = None

    def reset_session(self) -> None:
        self._fresh_context()
