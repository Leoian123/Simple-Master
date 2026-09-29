"""Il ciclo a conversazione: il modello chiede i file con gli strumenti, legge, scrive e risponde.

    1. arriva il testo del giocatore;
    2. si chiama l'API con prompt, glossario e indice dei file nel prompt di sistema;
    3. l'API chiede (via tool) i file che le servono e li legge;
    4. l'API scrive (via tool) e risponde;
    5. la risposta torna al giocatore e il turno viene trascritto.

La conversazione cresce turno dopo turno: per questo il prompt di sistema resta fermo per
qualche turno, lo storico si dimezza invece di limarsi e le letture vecchie diventano una riga.
Lo usa il costruttore di schede (character.py). Il gioco (engine.py) parte da questo ciclo ma
non tiene conversazione: il contesto di ogni turno lo compone il codice.

Ogni passo finisce nel registro (logbook.py): turno, chiamate API, tool,
errori con il punto esatto in cui il turno si e' interrotto.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .campaign import Campaign
from .logbook import describe_error, short, usage_line
from .models import cost_usd
from .tools import TOOLS, ToolExecutor, tool_result_block

DEFAULT_MODEL = "claude-opus-5"
CACHE = {"type": "ephemeral", "ttl": "1h"}
SNAPSHOT_TURNS = 8      # ogni quanti turni si rinnova la fotografia del prompt di sistema
STUB_ABOVE = 800        # caratteri: oltre, una lettura dei turni passati diventa una riga
MIN_NARRATION = 80      # caratteri di testo perche' un messaggio con sole scritture chiuda il turno
WRITE_TOOLS = {"append_file", "set_section", "replace_text", "upsert_glossary", "save_sheet",
               "update_sheet", "award_xp", "spend_xp", "set_clock", "tick_clock"}


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


class ConversationEngine:
    """Tiene la conversazione e guida il ciclo API <-> file."""

    spend_process = "scheda"      # la voce del registro delle spese
    restart_is_discarded = False  # un archivio senza chiusura e' un inizio rifatto da capo?

    def __init__(
        self,
        campaign: Campaign,
        client: Any = None,
        model: str | None = None,
        max_tokens: int = 16000,
        effort: str | None = None,
        max_history: int = 30,
        max_tool_rounds: int = 12,
        prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        log_session: bool = True,
        logger_name: str = "master.scheda",
        save_name: str | None = None,
        resume_turns: int = 12,
    ):
        self.campaign = campaign
        self.model = model or os.environ.get("MASTER_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.effort = effort or os.environ.get("MASTER_EFFORT") or None
        self.max_history = max_history
        self.max_tool_rounds = max_tool_rounds
        self.prompt = prompt
        self.tools = tools if tools is not None else TOOLS
        self.log_session = log_session
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
        """Prompt stabile (cache) + glossario + indice file (cache)."""
        # Due punti di cache: dopo il prompt (non cambia mai) e dopo glossario + indice (cambiano di
        # rado). Durata un'ora: tra un turno e l'altro un giocatore impiega minuti, e con i 5
        # minuti predefiniti la cache scadeva e si ripagava tutto a prezzo pieno piu' la riscrittura.
        return [
            {"type": "text", "text": self.prompt, "cache_control": CACHE},
            {"type": "text", "text": "# GLOSSARIO (primo riferimento)\n\n" + self.glossary_text()},
            {"type": "text", "text": "# File disponibili\n" + self.index_text(), "cache_control": CACHE},
        ]

    def glossary_text(self) -> str:
        return self.campaign.glossary.prompt_text()

    def index_text(self) -> str:
        return self.campaign.index(max_titles=8)

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

        La cache e' un prefisso: strumenti, sistema, conversazione. Se a meta' turno il modello
        aggiorna glossario o schede e il prompt viene ricostruito, tutto cio' che segue (l'intera
        conversazione) si invalida e viene riscritto al doppio del prezzo: il 2026-09-18 una scena
        e' costata 48 centesimi, 40 dei quali di sola riscrittura. Le modifiche che il modello fa
        ai file le conosce dalla conversazione; la fotografia si rinnova ogni pochi turni, quando
        lo storico viene ridotto e a ogni ripresa o nuova sessione.
        """
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

    def new_session(self, advance: bool = False) -> bool:
        """Archivia la conversazione corrente e ne apre una vuota. I file di campagna restano."""
        # senza chiusura e' un inizio rifatto da capo: resta in archivio, ma non entra nella memoria
        discarded = not advance and self.restart_is_discarded
        archived = self.campaign.archive_turns(self.save_name, discarded=discarded) if self.save_name else None
        self._fresh_context()
        self.turns = 0
        if archived:
            number = self.campaign.next_session() if advance else self.campaign.session_number
            self.log.info("nuova sessione (%d): conversazione precedente archiviata in %s", number, archived.name)
        return archived is not None

    def reset_snapshot(self) -> None:
        """Il prompt va rifatto alla prossima chiamata."""
        self._system_snapshot = None

    def reset_session(self) -> None:
        self._fresh_context()

    # --- ciclo di un turno --------------------------------------------------

    def _start_turn(self) -> None:
        """Prima del turno: si riprende la conversazione salvata, se il processo e' nuovo."""
        self.resume()

    def _turn_text(self, spoken: str, saved_as: str | None) -> str:
        """Il messaggio che parte davvero verso l'API, a partire dalle parole del giocatore."""
        return spoken

    def _after_narration(self, result: TurnResult, spoken: str, saved_as: str | None, calls: int) -> None:
        """Dopo l'ultima chiamata del turno, prima di salvarlo."""

    def _pg_name(self) -> str | None:
        """Il personaggio a cui appartiene il turno salvato."""
        return None

    def _record_spend(self, result: TurnResult, calls: int, narrator_cost: float) -> None:
        self.campaign.record_spend(self.spend_process, self.model, narrator_cost, turno=self.turns, chiamate=calls,
                                   token=[result.input_tokens, result.output_tokens, result.cache_read_tokens, result.cache_write_tokens])

    def play(self, player_text: str, saved_as: str | None = None) -> TurnResult:
        player_text = player_text.strip()
        if not player_text:
            raise ValueError("Testo del giocatore vuoto")
        self._start_turn()
        self.turns += 1
        self.log.info("turno %d | giocatore: %s", self.turns, short(saved_as or player_text, 200))
        self.executor.reset()
        self._compact_previous_turn()
        spoken = player_text  # le parole del giocatore: cio' che si salva
        player_text = self._turn_text(spoken, saved_as)
        if self._pending_results:
            # le scritture chiuse insieme alla risposta del turno scorso: i loro esiti viaggiano ora
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
                # risposta e scritture nello stesso messaggio: il turno e' finito, niente giro in piu'.
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
        self._after_narration(result, spoken, saved_as, calls)
        result.files_read = list(self.executor.files_read)
        result.files_changed = list(self.executor.files_changed)
        if result.stop_reason not in ("end_turn", "tool_use"):
            self.log.warning("turno %d chiuso con stop=%s: %s", self.turns, result.stop_reason, short(result.text, 200))
        if self.save_name and result.stop_reason in ("end_turn", "max_tokens"):
            self.campaign.append_turn(
                self.save_name, saved_as or spoken, result.text,
                pg=self._pg_name(),
                read=result.files_read, changed=result.files_changed,
                tokens=[result.input_tokens, result.output_tokens],
            )
        result.cost_usd = round(narrator_cost + result.scribe_cost_usd, 4)
        self._record_spend(result, calls, narrator_cost)
        self.log.info(
            "turno %d fine | chiamate=%d token in/out=%d/%d cache letti/scritti=%d/%d costo~$%.3f "
            "| uscita: narrazione %d car, tool %d car | letti=%s | modificati=%s",
            self.turns, calls, result.input_tokens, result.output_tokens, result.cache_read_tokens,
            result.cache_write_tokens, result.cost_usd, self._out_chars[0], self._out_chars[1],
            ", ".join(result.files_read) or "-", ", ".join(result.files_changed) or "-",
        )
        return result

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
        Lo stato vive nei file, quindi perdere turni vecchi non perde informazione.
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
