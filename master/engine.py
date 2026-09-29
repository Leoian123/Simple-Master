"""Il gioco: una chiamata del narratore per turno, su un contesto composto dal codice.

    1. arriva il testo del giocatore;
    2. il programma compone il contesto del turno dai documenti della campagna (context.py):
       scheda, scena, fili aperti, chi e cosa e' nominato, regole pertinenti, ultimi scambi;
    3. il narratore (solo strumenti di lettura, di riserva) narra e lascia un blocco di appunti;
    4. lo scriba, un modello economico, trascrive gli appunti negli archivi e aggiorna la scena;
    5. la narrazione torna al giocatore e il turno viene trascritto.

Niente conversazione che cresce: lo stato della storia vive nei file, e il costo di un turno
non dipende da quanto e' lunga la sessione. Alla chiusura di una sessione il livello lento
(memory.py) rilegge il giocato e lo consolida in memoria.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from . import dice
from .campaign import DIARY, MECHANICS_DIR, SETTING_DIR, SHEETS_DIR, Campaign
from .conversation import CACHE, DEFAULT_MODEL, ConversationEngine, TurnResult
from .logbook import describe_error, short
from .models import cost_usd
from .sheet import Sheet
from .tools import TOOLS, tool_result_block

__all__ = ["DEFAULT_MODEL", "MasterEngine", "TurnResult"]

PRELUDE_MARK = "[Apertura della campagna]"
OPENING_MARK = "[Apertura della sessione]"
CLOSING_MARK = "[Chiusura della sessione]"
MIN_TURNS_TO_CLOSE = 3  # sotto, "nuova sessione" e' un rifare da capo: niente chiusura, niente esperienza
NL_ = "\n"

MASTER_PROMPT = f"""\
Sei il master di una campagna di gioco di ruolo testuale. Parli in italiano.

## Dove stanno le informazioni
- `{MECHANICS_DIR}/`: le REGOLE. Numeri, tiri, tabelle, statistiche di oggetti e
  creature. Quando serve risolvere un'azione, applicale alla lettera.
- `{SETTING_DIR}/`: il MONDO. Luoghi, fazioni, personaggi non giocanti, trame,
  e il diario (`{DIARY}`) con cio' che e' successo finora.
- `{SHEETS_DIR}/`: le SCHEDE dei personaggi giocanti. Il giocatore parla e agisce come il suo
  personaggio. La parte della scheda che cambia di rado (tratti, identita', storia) e' subito
  dopo queste istruzioni; stato ed esperienza, aggiornati, sono nel contesto di ogni turno. I
  valori si leggono direttamente (una riserva di dadi e' la somma di due numeri della scheda).
Tieni separate le due cose: le regole non si inventano, il mondo si' (e poi finisce negli appunti).

## Da dove nasce una scena
Nessuna scena nasce dal nulla. Prima di narrare chiediti: cosa e' appena successo a
questo personaggio? cosa vuole, cosa teme, chi lo cerca? quali fili sono aperti?
La scena parte da li': luogo, ora e situazione sono conseguenze della sua storia, mai un
fondale neutro. Un dettaglio del background reso concreto (un nome, un oggetto, un debito)
vale piu' di un'atmosfera generica.

## Come lavori a ogni turno: una chiamata
Non hai una conversazione alle spalle e non devi cercare nulla: a ogni turno il programma ti
consegna, prima delle parole del giocatore, il CONTESTO DEL TURNO composto dai documenti della
campagna: stato ed esperienza del personaggio, la scena in corso, i fili aperti, chi e cosa e' in
gioco, le regole pertinenti e gli ultimi scambi parola per parola.
1. Quel contesto fa fede e di norma basta: rispondi subito, senza strumenti.
2. LETTURA DI RISERVA. Solo se per decidere ti manca qualcosa di indispensabile (una regola che
   il contesto non riporta, un luogo mai descritto) usa `lookup_glossary` / `read_file` con
   `section`, tutto in un solo giro. Costa una chiamata in piu': non farlo per scrupolo.
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
4. Quando un'istruzione del tavolo (apertura, chiusura) chiede di annotare qualcosa, lo metti
   negli appunti: lo esegue lo scriba.

## Orologi
Minacce e progetti che avanzano nel tempo non li tieni a mente: sono orologi a segmenti
contati dal programma (es. "La guardia indaga 2/6"). Quelli attivi ti arrivano in testa
al messaggio del giocatore. Negli appunti: `- orologio: nuovo "<nome>", 6 segmenti,
conseguenza: ...` per crearne uno quando un filo comincia a stringere (due o tre attivi
bastano), `- orologio: "<nome>" +1` quando un fatto lo fa avanzare. Quando uno si riempie ti
arriva `[OROLOGIO PIENO: ...]`: la conseguenza accade in quella scena, non si rimanda.

## Esperienza
Il conto dei punti esperienza non lo tieni tu: sta nel registro della scheda, e in
`esperienza` (nel contesto del turno) trovi guadagnati, spesi e disponibili gia' calcolati.
Non scrivere mai totali e non fidarti di cio' che ricordi.
- I punti entrano solo per una di queste vie: `sessione` (alla chiusura della sessione, quando
  te lo chiede il tavolo: una volta sola per sessione), `storia` (si chiude una storia o un
  arco), `regola` (una regola del manuale li concede in gioco: citala), `narratore` (premio
  eccezionale, raro). Quanti punti, lo dicono le regole sull'esperienza in `{MECHANICS_DIR}/`:
  se il contesto non le riporta leggile, non andare a memoria.
- I punti escono quando il giocatore chiede di migliorare un tratto: calcola il costo dalle
  regole, verifica le condizioni che il manuale pone (tempo, addestramento, requisiti
  narrativi) e che i disponibili bastino; se non bastano, dillo al giocatore.
In entrambi i casi metti negli appunti la riga `pe` (punti, via e motivo; oppure costo e
tratto): lo scriba la registra sulla scheda.

## Stile
- Scene brevi e concrete: 1-3 paragrafi, poi una domanda o una scelta.
- Non decidere mai le azioni del personaggio del giocatore.
- Quando l'esito e' incerto chiedi un tiro secondo le regole in `{MECHANICS_DIR}/`;
  se il giocatore ha gia' dato un risultato, interpretalo.
- Coerenza prima di tutto: cio' che sta nel contesto e nei file e' vero, il resto lo
  inventi e lo metti negli appunti.
- Non spiegare le tue operazioni: il giocatore vede solo la storia.
- La campagna continua da una sessione all'altra: gli ultimi scambi del contesto sono gli
  ultimi giocati, anche se di giorni fa; scena, fili aperti e diario dicono il resto. Non
  ricominciare la storia e non ripresentare cio' che il giocatore conosce gia'.
"""

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
- SCENA: chiama SEMPRE `set_scene`, anche se gli appunti dicono "nulla". E' il documento da cui il
  narratore ripartira' al prossimo turno, senza altra memoria: deve dire dove si e', quando, chi e'
  presente, cosa e' appena successo (comprese le azioni del giocatore, con precisione) e cosa resta
  da decidere. Parti dalla scena di prima e aggiornala con questo turno; in `da_richiamare` tieni i
  dettagli ancora vivi.
Stile telegrafico. Dopo gli strumenti non scrivere altro.
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
   Se ti manca una regola indispensabile, leggila in un unico giro, al massimo tre sezioni.
5. MOTORE. Pianta un'urgenza immediata e almeno due fili che porteranno avanti la
   campagna: chi c'entra, cosa vuole, cosa succede se il personaggio non fa nulla.
6. SCRIVI. Narra in seconda persona, 3-5 paragrafi, e chiudi su una scelta aperta.
   Negli appunti: il diario con la voce "Preludio"; un "segreto" con i "Fili aperti di {name}"
   (3-6 righe per te: cosa e' successo davvero, chi c'entra e cosa vuole, le prossime
   complicazioni; sono segreti, mai rivelarli direttamente); lo stato se il personaggio
   cambia; al massimo tre "nuovo" per cio' che tornera'. Stile telegrafico.
"""


OPENING_BRIEF = """\
[APERTURA DELLA SESSIONE {number}: istruzioni del tavolo, non parole del personaggio]
{name} riprende dopo una pausa. La sessione scorsa e' chiusa e archiviata: quello che e'
successo sta nei documenti.
{notes}
1. NON leggere nulla: scena in corso, fili aperti e l'ultima parte del diario sono gia' qui, nel
   contesto sopra e in fondo a questo messaggio. Una lettura adesso sarebbe una chiamata buttata.
2. Apri la scena da li': stesso punto o poco dopo, con il tempo trascorso dichiarato. Due
   righe di "dove eravamo" dentro la narrazione, poi un filo aperto che si muove da solo
   e chiede una risposta. Non ripresentare cio' che il giocatore conosce gia'.
3. 2-4 paragrafi in seconda persona, chiusi su una scelta. Negli appunti il diario con la voce
   "Sessione {number} — apertura".
"""
# 2026-09-19: l'apertura della sessione 2 e' costata due chiamate (0,15 $) per rileggere diario e fili aperti
# che aveva gia' davanti, solo perche' l'istruzione glielo chiedeva: da qui il punto 1.

CLOSING_BRIEF = """\
[CHIUSURA DELLA SESSIONE {number}: istruzioni del tavolo, non parole del personaggio]
Il giocatore chiude qui la sessione.
1. Un paragrafo breve che ferma la scena dove si trova (niente eventi nuovi, niente scelte).
2. Niente riepilogo e niente fili aperti: subito dopo la tua risposta il giocato di questa
   sessione viene riletto per intero e consolidato in memoria.
3. ESPERIENZA. Negli appunti una riga "pe" per la via `sessione`, con i punti che le regole di
   questa campagna prevedono per una sessione giocata; se in questa sessione si e' chiusa una
   storia, un'altra per la via `storia`. Nel testo di' al giocatore quanti punti riceve e
   perche'; il totale non scriverlo: lo calcola il programma e lo aggiunge in coda.
{rules}"""

XP_RULE_TITLES = re.compile(r"esperienz|avanzament|crescita del personaggio|passaggi? di livello", re.I)
XP_RULES_LIMIT = 4_000


class MasterEngine(ConversationEngine):
    """Il narratore del gioco: niente conversazione, un contesto composto dal codice a ogni turno."""

    spend_process = "gioco"
    restart_is_discarded = True

    def __init__(
        self,
        campaign: Campaign,
        client: Any = None,
        model: str | None = None,
        max_tokens: int = 16000,
        effort: str | None = None,
        max_tool_rounds: int = 12,
        scribe_model: str | None = None,
        memory_model: str | None = None,
        log_session: bool = True,
    ):
        super().__init__(
            campaign, client=client, model=model, max_tokens=max_tokens, effort=effort,
            max_tool_rounds=max_tool_rounds, prompt=MASTER_PROMPT,
            tools=[t for t in TOOLS if t["name"] in READ_TOOL_NAMES],
            log_session=log_session, logger_name="master.gioco",
        )
        # scriba: la narrazione e le scritture sono due chiamate separate. Il narratore ha solo
        # strumenti di lettura; le scritture le fa un modello economico a partire dai suoi appunti.
        self.scribe_model = scribe_model or os.environ.get("MASTER_SCRIBE_MODEL") or DEFAULT_SCRIBE_MODEL
        self._unwritten_notes: list[str] = []
        # memoria lenta: alla chiusura di una sessione (o a comando) il giocato grezzo diventa memoria
        self.memory_model = memory_model or os.environ.get("MASTER_MEMORY_MODEL") or None
        self.last_memory_report: Any = None

    # --- prompt: si ricompone a ogni turno, la cache va a contenuto -----------------------

    def system_blocks(self) -> list[dict[str, Any]]:
        """Primo blocco: non cambia mai. Secondo: la parte stabile della scheda, che cambia solo quando
        il personaggio cresce; se cambia si riscrive lei sola, il primo blocco resta in cache."""
        blocks = [{"type": "text", "text": self.prompt + self.dice_text(), "cache_control": CACHE}]
        sheet = self.campaign.active_sheet()
        data = Sheet(self.campaign, sheet["slug"]) if sheet else None
        if data is not None and data.exists:
            blocks.append({"type": "text", "cache_control": CACHE, "text":
                           f"# SCHEDA DI {sheet['name'].upper()}: la parte che cambia di rado (tratti, identita', storia)\n"
                           "Stato ed esperienza, che cambiano spesso, li trovi aggiornati nel contesto di ogni turno.\n"
                           "```json\n" + data.stable_json() + "\n```"})
        return blocks

    def dice_text(self) -> str:
        """Come si chiede un tiro in QUESTA campagna: generato dal profilo ricavato dal manuale."""
        return dice.prompt_section(dice.load_profile(self.campaign))

    def _system(self) -> list[dict[str, Any]]:
        return self.system_blocks()  # nessuna conversazione da proteggere: niente fotografia

    def _cached_messages(self) -> list[dict[str, Any]]:
        return list(self.messages)  # il contesto cambia a ogni turno: scriverlo in cache costerebbe doppio per nulla

    # --- il turno ------------------------------------------------------------------

    def _start_turn(self) -> None:
        self.messages.clear()
        self._pending_results = []
        self.turns = self.campaign.turn_count(self.save_name or "")

    def _turn_text(self, spoken: str, saved_as: str | None) -> str:
        from .context import compose

        # le istruzioni del tavolo e gli esiti dei dadi non sono azioni da instradare
        router = self._route_rules if saved_as is None and not spoken.startswith("[Tiro]") else None
        context = compose(self.campaign, spoken if saved_as is None else "", self.save_name or "", router=router)
        self.log.info("turno %d contesto: %s", self.turns, context.stats())
        return context.text + self._table_state() + ("GIOCATORE: " if saved_as is None else "") + spoken

    def _after_narration(self, result: TurnResult, spoken: str, saved_as: str | None, calls: int) -> None:
        if result.stop_reason in ("end_turn", "max_tokens"):
            result.text, result.notes = self._split_notes(result.text)
            result.scribe_cost_usd = self._run_scribe(saved_as or spoken, result.text, result.notes)
        if self.executor.files_read or calls > 1:  # quanto spesso la selezione del codice non e' bastata
            self.log.info("turno %d LETTURA DI RISERVA: %d chiamate, letti=%s", self.turns, calls,
                          ", ".join(self.executor.files_read) or "-")
        self.messages.clear()

    def _pg_name(self) -> str | None:
        sheet = self.campaign.active_sheet()
        return sheet["name"] if sheet else None

    def _record_spend(self, result: TurnResult, calls: int, narrator_cost: float) -> None:
        super()._record_spend(result, calls, narrator_cost)
        self.campaign.record_spend("scriba", self.scribe_model, result.scribe_cost_usd, turno=self.turns)

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

    # --- aperture e chiusure --------------------------------------------------------

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
            # dalla seconda sessione non si rinasce dal background: si riparte da scena, diario e fili aperti
            brief = OPENING_BRIEF.format(number=number, name=sheet["name"], notes=hint) + self._diary_tail()
            return self.play(brief, saved_as=OPENING_MARK + (f" {notes}" if notes else ""))
        brief = PRELUDE_BRIEF.format(name=sheet["name"], notes=hint)
        return self.play(brief, saved_as=PRELUDE_MARK + (f" {notes}" if notes else ""))

    def _diary_tail(self) -> str:
        """L'ultima parte del diario, gia' nel messaggio: scena e fili aperti sono nel contesto del turno."""
        diary = self.campaign.file(DIARY)
        tail = diary.read().strip()[-2500:] if diary.exists() else ""
        cut = tail.find(NL_)
        tail = tail[cut + 1:] if 0 <= cut < 200 else tail  # niente riga mozzata in testa
        return f"{NL_}## Ultima parte del diario{NL_}{NL_}{tail}{NL_}" if tail else ""

    def _played_turns(self) -> int:
        """I turni giocati davvero in questa conversazione: aperture e chiusure del tavolo non contano."""
        marks = (PRELUDE_MARK, OPENING_MARK, CLOSING_MARK)
        return sum(1 for t in self.campaign.read_turns(self.save_name or "") if not t["player"].startswith(marks))

    def can_close(self) -> bool:
        return self.campaign.active_sheet() is not None and self._played_turns() >= MIN_TURNS_TO_CLOSE

    def close_session(self, consolidate: bool = True) -> TurnResult | None:
        """La via `sessione` dell'esperienza: punti e consolidamento della memoria, poi si archivia.

        Con meno di MIN_TURNS_TO_CLOSE turni giocati non c'e' una sessione da chiudere: si
        archivia e basta, senza chiamate e senza far avanzare il contatore delle sessioni.
        """
        result = None
        if self.can_close():
            sheet = self.campaign.active_sheet()
            brief = CLOSING_BRIEF.format(number=self.campaign.session_number, rules=self._xp_rules())
            result = self.play(brief, saved_as=CLOSING_MARK)
            if consolidate:
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

    # --- lo scriba: la chiamata di scrittura, separata da quella di narrazione ------------------

    @staticmethod
    def _split_notes(text: str) -> tuple[str, str | None]:
        """Divide la narrazione dagli appunti. None = il narratore non ha lasciato il blocco."""
        found = NOTES_RE.search(text)
        if not found:
            return text.strip(), None
        return (text[:found.start()] + text[found.end():]).strip(), found.group(1).strip()

    def _scribe_context(self) -> str:
        from .memory import SCENE

        sheet = self.campaign.active_sheet()
        if sheet is None:
            return ""
        parts = [f"PERSONAGGIO: {sheet['name']} | sessione {self.campaign.session_number}"]
        data = Sheet(self.campaign, sheet["slug"])
        if data.exists:
            parts.append("SCHEDA (JSON, per i percorsi di update_sheet):" + "\n" + data.working_json())
        adventure = self.campaign.file(f"{SETTING_DIR}/avventura.md")
        title = f"Fili aperti di {sheet['name']}"
        threads = adventure.get_section(title) if adventure.exists() else None
        scene = self.campaign.file(SCENE)
        parts.append("SCENA PRIMA DI QUESTO TURNO:" + NL_ + (scene.read().strip() if scene.exists() else "(nessuna: e' la prima)"))
        parts.append("OROLOGI ATTIVI: " + (self.campaign.clocks_line() or "(nessuno)"))
        parts.append(f'FILI APERTI ATTUALI (sezione "{title}"):' + "\n" + (threads.strip() if threads else "(nessuno)"))
        return "\n\n".join(parts)

    def _run_scribe(self, player: str, narration: str, notes: str | None) -> float:
        """Trascrive negli archivi gli appunti del narratore e aggiorna la scena. Ritorna il costo.

        Parte a ogni turno, anche con appunti "nulla": la scena va aggiornata comunque.
        Un errore dello scriba non butta via il turno (la narrazione e' gia' pagata): gli appunti
        restano in attesa e partono insieme a quelli del turno dopo.
        """
        if notes is not None and notes.strip(" .").lower() in ("", "nulla", "niente", "nessuno"):
            notes = ""
        backlog = "".join(f"APPUNTI RIMASTI DA UN TURNO PRECEDENTE:\n{n}\n\n" for n in self._unwritten_notes)
        body = (self._scribe_context() + "\n\n" + backlog + f"GIOCATORE:\n{short(player, 600)}\n\nNARRAZIONE:\n{narration}\n\n"
                + "APPUNTI DEL NARRATORE:\n" + (notes if notes else
                                               "nulla" if notes is not None else
                                               "(non li ha lasciati: ricava dalla narrazione solo i fatti certi)"))
        messages: list[dict[str, Any]] = [{"role": "user", "content": body}]
        tokens = [0, 0]
        try:
            for round_ in range(2):  # il secondo giro solo per correggere uno strumento fallito
                response = self.client.messages.create(model=self.scribe_model, max_tokens=4000, system=SCRIBE_PROMPT,
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
        """Cio' che lo scriba aggiunge al diario e' provvisorio: finisce sotto `## Appunti`, che il
        consolidamento sostituira' con la cronaca. Niente seconde copie dei fatti."""
        if tool != "append_file" or str(args.get("name", "")).strip() != DIARY:
            return args
        from .memory import NOTES_SECTION

        text = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"**\1**", str(args.get("text", ""))).strip()
        diary = self.campaign.file(DIARY)
        titles = diary.outline() if diary.exists() else []
        head = "" if titles and titles[-1] == NOTES_SECTION else f"{NL_}## {NOTES_SECTION}{NL_}{NL_}"
        return {**args, "text": head + text}

    def _scribe_tools(self) -> list[dict[str, Any]]:
        from .tools import SCENE_TOOL

        return [t for t in TOOLS if t["name"] not in READ_TOOL_NAMES] + [SCENE_TOOL]
