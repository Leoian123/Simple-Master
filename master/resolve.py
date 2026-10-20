"""Risoluzione: quando due manuali parlano dello stesso argomento, quale testo vale.

La composizione (lineage.py) mette le parti di una sezione una dopo l'altra, dalla piu' vecchia alla
piu' recente. Va bene finche' la parte nuova aggiunge; non va bene quando corregge, perche' il master
legge una sezione sola, spesso solo l'inizio, e trova prima la regola superata. Qui, per ogni punto in
cui un manuale tocca testo di un'altra origine, una chiamata stabilisce il rapporto tra i due testi e,
se il nuovo corregge il vecchio, indica le modifiche minime. Il resto lo fa il codice:

    nessuno, estende, variante   il testo nuovo si accoda, come nella composizione
    identica                     il testo nuovo non entra: non porta nulla
    sostituisce, contraddice     le modifiche si applicano al testo in vigore; vince il piu' recente.
                                 Se il testo nuovo non dichiara la correzione, la decisione resta segnata
                                 nel registro come spareggio

Il modello non riscrive la sezione: cita il passo da cambiare, dice cosa diventa e cita la fonte nel
testo nuovo. Il codice controlla che il passo esista una volta sola, che la fonte esista davvero e che
nessun numero nasca dal nulla. Un piano che non supera i controlli non entra: resta la composizione
semplice, e il caso finisce nel registro come quarantena. Nessuno viene interpellato.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .campaign import Campaign
from .lineage import CAMPAIGN_ORIGIN, Lineage

log = logging.getLogger("master.risoluzione")

RELATIONS = ("nessuno", "identica", "estende", "variante", "sostituisce", "contraddice")
REWRITING = {"sostituisce", "contraddice"}
CACHE_DIR = "risoluzioni"
MAX_TOKENS = 8000

RESOLVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rapporto": {"type": "string", "enum": list(RELATIONS)},
        "dichiarato": {"type": "boolean"},
        "motivo": {"type": "string"},
        "tutto": {"type": "boolean"},
        "modifiche": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"esistente": {"type": "string"}, "diventa": {"type": "string"}, "fonte": {"type": "string"}},
                "required": ["esistente", "diventa", "fonte"],
                "additionalProperties": False,
            },
        },
        "aggiunte": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rapporto", "dichiarato", "motivo", "tutto", "modifiche", "aggiunte"],
    "additionalProperties": False,
}

RESOLVE_PROMPT = """\
Lavori alla preparazione di una campagna di gioco di ruolo per un master AI. La campagna e'
costruita da piu' pubblicazioni dello stesso gioco: un manuale base, espansioni, errata. Durante
il gioco il master legge UNA sezione per argomento, spesso solo l'inizio: se la sezione contiene
due versioni della stessa regola una dopo l'altra, applica quella superata. Il tuo compito e'
stabilire il RAPPORTO TRA DUE TESTI e, quando il nuovo corregge il vecchio, indicare le modifiche
minime che portano la sezione allo stato in vigore. Restituisci SOLO un JSON.

Ricevi:
- ESISTENTE: la sezione oggi in vigore su un argomento.
- NUOVO: un testo di un'altra pubblicazione, di solito successiva. Puo' essere la sezione con lo
  stesso titolo, oppure una sezione su un altro argomento che nomina questo.

## Il rapporto (`rapporto`)
Chiediti che cosa fa NUOVO a cio' che ESISTENTE afferma:
- `nessuno`: non lo riguarda. NUOVO nomina l'argomento o lo usa, ma non ne cambia e non ne
  arricchisce il contenuto (un oggetto che da' un bonus a una prova non modifica la regola di
  quella prova; un luogo che nomina una fazione non ne cambia la descrizione).
- `identica`: ripete le stesse cose, anche con altre parole. Non porta niente di nuovo.
- `estende`: aggiunge materiale compatibile (un caso in piu', una riga di tabella, un fatto
  nuovo) senza toccare cio' che ESISTENTE afferma.
- `variante`: propone una regola opzionale o alternativa, che il tavolo puo' adottare o no.
  La regola di ESISTENTE resta quella standard.
- `sostituisce`: corregge, aggiorna o riscrive qualcosa che ESISTENTE afferma: un'errata, una
  revisione della regola, il mondo di gioco che e' andato avanti. Vale anche quando NUOVO non lo
  dichiara ma ripresenta l'argomento con contenuti diversi.
- `contraddice`: afferma di passaggio qualcosa di incompatibile con ESISTENTE, senza
  presentarlo come correzione o aggiornamento e senza ripresentare l'argomento: un numero o un
  fatto diverso, detto mentre parla d'altro.

`dichiarato`: true se NUOVO dice in modo esplicito che cosa fa ("errata", "sostituisce",
"regola opzionale", "variante", "dopo la riforma del..."). `motivo`: una frase.

## Il piano (solo per `sostituisce` e `contraddice`)
Per gli altri rapporti lascia `tutto` false e le liste vuote: accodare o scartare il testo e'
compito del programma.
- `tutto`: true se NUOVO ripresenta l'argomento per intero e puo' stare da solo al posto di
  ESISTENTE senza che si perda nulla di ancora valido. In quel caso le liste restano vuote.
  Solo quando NUOVO e' la sezione con lo stesso titolo.
- `modifiche`: altrimenti, le modifiche minime a ESISTENTE. Ognuna ha:
  - `esistente`: il passo di ESISTENTE da cambiare, copiato carattere per carattere. Il piu'
    corto possibile, ma abbastanza lungo da comparire una volta sola in ESISTENTE (per una riga
    di tabella, copia la riga dall'inizio fino al valore da cambiare).
  - `diventa`: il testo che ne prende il posto, nella stessa forma (una riga di tabella resta
    una riga di tabella, una frase resta una frase), con i soli fatti di NUOVO. Cambia solo cio'
    che NUOVO cambia e lascia la frase grammaticalmente intera: il materiale in piu' di NUOVO
    non va infilato qui, va in `aggiunte`. Niente note del tipo "corretto da" o "in
    precedenza": il valore superato sparisce dalla sezione.
  - `fonte`: il passo di NUOVO che lo giustifica, copiato carattere per carattere.
- `aggiunte`: i paragrafi di NUOVO, copiati carattere per carattere, che portano materiale in
  piu' rispetto alle modifiche e vanno accodati alla sezione. Non mettere qui le frasi che
  servono solo a dichiarare la correzione ("Errata: ...", "il valore stampato e' sbagliato").
  Solo quando NUOVO e' la sezione con lo stesso titolo.

Tutto cio' che in ESISTENTE non e' toccato da NUOVO resta com'e': non riscriverlo e non
riassumerlo. Non introdurre numeri, nomi o regole che non siano in NUOVO.
"""


class Rejected(ValueError):
    """Il piano non supera i controlli del codice: non entra nella sezione."""


def _find(text: str, quote: str) -> list[re.Match[str]]:
    """Dove sta `quote` in `text`, senza badare a come sono spezzate le righe."""
    words = quote.split()
    return list(re.finditer(r"\s+".join(re.escape(w) for w in words), text)) if words else []


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def apply_plan(existing: str, new: str, plan: dict[str, Any], same_title: bool) -> str:
    """Il testo in vigore dopo che `new` ha incontrato `existing`, secondo il piano. Solleva Rejected
    se il piano chiede qualcosa che il codice non puo' verificare sui due testi."""
    relation = plan.get("rapporto")
    if relation not in RELATIONS:
        raise Rejected(f"rapporto sconosciuto: {relation!r}")
    if relation == "identica":
        return existing
    if relation not in REWRITING:
        return f"{existing}\n\n{new}" if same_title else existing
    if plan.get("tutto"):
        if not same_title:
            raise Rejected("una sezione su un altro argomento non puo' prendere il posto di questa per intero")
        return new
    edits, extras = plan.get("modifiche") or [], plan.get("aggiunte") or []
    if not edits:
        raise Rejected("nessuna modifica indicata per un testo che ne corregge un altro")
    text = existing
    for edit in edits:
        old, becomes, source = (str(edit.get(k) or "") for k in ("esistente", "diventa", "fonte"))
        spots = _find(text, old)
        if len(spots) != 1:
            raise Rejected(f"il passo da cambiare {'non si trova' if not spots else 'compare piu volte'}: «{old[:80]}»")
        if not _find(new, source):
            raise Rejected(f"la fonte non e' nel testo nuovo: «{source[:80]}»")
        invented = _numbers(becomes) - _numbers(source) - _numbers(old)
        if invented:
            raise Rejected(f"numeri che non vengono da nessuno dei due testi: {', '.join(sorted(invented))}")
        text = text[:spots[0].start()] + becomes.strip() + text[spots[0].end():]
    for quote in extras if same_title else []:
        spots = _find(new, str(quote))
        if not spots:
            raise Rejected(f"l'aggiunta non e' nel testo nuovo: «{str(quote)[:80]}»")
        text += "\n\n" + new[spots[0].start():spots[0].end()]
    return text


@dataclass
class Step:
    manual: str   # da quale manuale viene il testo nuovo
    title: str    # la sua sezione
    text: str
    cross: bool   # True: sezione su un altro argomento, che nomina questo
    term: str = ""


@dataclass
class Job:
    file: str
    ntitle: str
    title: str
    base: tuple[str, str]  # (origine, testo) da cui si parte
    steps: list[Step] = field(default_factory=list)


@dataclass
class ResolveReport:
    steps: list[dict[str, Any]] = field(default_factory=list)  # un esito per ogni incontro tra due testi
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    remembered: int = 0  # incontri gia' decisi (registro o cache): nessuna chiamata

    def summary(self) -> str:
        if not self.steps:
            return "risoluzione: nessun punto in cui due manuali si toccano"
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.get("rapporto") or "non deciso"] = counts.get(step.get("rapporto") or "non deciso", 0) + 1
        held = sum(1 for s in self.steps if s["esito"] == "quarantena")
        return (f"risoluzione: {len(self.steps)} incontri tra testi ("
                + ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])) + ")"
                + (f", {held} in quarantena (resta la composizione semplice)" if held else "")
                + (f", {self.remembered} gia' decisi" if self.remembered else ""))


class Resolver:
    """Decide, per ogni sezione toccata da piu' origini, il testo in vigore."""

    def __init__(self, campaign: Campaign, lineage: Lineage, send: Callable[[dict[str, dict[str, Any]]], dict[str, Any]],
                 model: str, effort: str | None, cache_root: Path, targets: Iterable[str]):
        self.campaign = campaign
        self.lineage = lineage
        self.send = send  # richieste (id -> parametri) -> risposte riuscite (id -> messaggio)
        self.model = model
        self.effort = effort
        self.cache = Path(cache_root) / CACHE_DIR
        self.targets = list(targets)

    # --- che cosa c'e' da decidere ------------------------------------------------------

    def jobs(self) -> list[Job]:
        from .sanitize import norm_title

        order = {key: i for i, key in enumerate(self.lineage.manual_order())}

        def rank(source: str) -> int:
            return -1 if source == CAMPAIGN_ORIGIN else order.get(source, len(order))

        jobs: dict[tuple[str, str], Job] = {}

        def job_for(name: str, ntitle: str, entry: dict[str, Any]) -> Job:
            if (name, ntitle) not in jobs:
                jobs[(name, ntitle)] = Job(name, ntitle, entry["title"], entry["pieces"][0],
                                           [Step(source, entry["title"], text, False) for source, text in entry["pieces"][1:]])
            return jobs[(name, ntitle)]

        for (name, ntitle), entry in list(self.lineage.composed.items()):
            if len(entry["pieces"]) >= 2 and not entry["protected"]:
                job_for(name, ntitle, entry)
        for found in self.lineage.candidates(self.targets):
            if found.why == "titolo":
                continue
            entry = self.lineage.base_for(found.other_file, found.other_title)
            # il testo scritto dal giocatore o nato in gioco non si corregge; e un testo piu' vecchio
            # non corregge uno piu' recente
            if entry is None or entry["protected"] or rank(found.manual) <= rank(entry["pieces"][0][0]):
                continue
            body = self.lineage.part(found.manual, found.file, found.title)
            if body:
                job_for(found.other_file, norm_title(found.other_title), entry).steps.append(
                    Step(found.manual, found.title, body, True, found.why.split(": ", 1)[-1]))
        for job in jobs.values():
            job.steps.sort(key=lambda s: (rank(s.manual), s.cross, s.title))
        return [job for job in jobs.values() if job.steps]

    # --- una decisione ------------------------------------------------------------------

    @staticmethod
    def digest(existing: str, step: Step) -> str:
        payload = json.dumps([RESOLVE_PROMPT, existing, step.text, step.cross], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _source_label(self, source: str) -> str:
        if source == CAMPAIGN_ORIGIN:
            return "testo gia' nella campagna"
        manual = self.lineage.data["manuali"].get(source, {})
        return (manual.get("titolo") or manual.get("nome") or source) + (f", {manual['anno']}" if manual.get("anno") else "")

    def params(self, job: Job, existing: str, step: Step) -> dict[str, Any]:
        what = (f"sezione «{step.title}», che parla d'altro e nomina «{step.term}»" if step.cross
                else "la sezione con lo stesso titolo")
        user = (f"Argomento: `{job.file}` § {job.title}\n\n"
                f"ESISTENTE (da: {self._source_label(job.base[0])}):\n<<<\n{existing}\n>>>\n\n"
                f"NUOVO (da: {self._source_label(step.manual)}; {what}):\n<<<\n{step.text}\n>>>")
        params: dict[str, Any] = dict(
            model=self.model, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": RESOLVE_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": RESOLVE_SCHEMA}},
        )
        if self.effort:
            params["output_config"]["effort"] = self.effort
        return params

    def _remembered(self, job: Job, digest: str) -> dict[str, Any] | None:
        """Un incontro gia' deciso: nel registro della campagna, o nella cache condivisa."""
        datum = self.lineage.data["sezioni"].get(job.file, {}).get(job.ntitle, {})
        for step in datum.get("risoluzione", []):
            if step.get("chiave") == digest and isinstance(step.get("piano"), dict):
                return step["piano"]
        try:
            plan = json.loads((self.cache / f"{digest}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return plan if isinstance(plan, dict) else None

    def _parse(self, message: Any, report: ResolveReport) -> tuple[dict[str, Any] | None, str]:
        from .prepare import parse_json_object

        if message is None:
            return None, "nessuna risposta dal modello"
        usage = getattr(message, "usage", None)
        report.calls += 1
        report.input_tokens += getattr(usage, "input_tokens", 0) or 0
        report.output_tokens += getattr(usage, "output_tokens", 0) or 0
        if getattr(message, "stop_reason", "end_turn") != "end_turn":
            return None, f"risposta interrotta ({message.stop_reason})"
        try:
            plan = parse_json_object("\n".join(b.text for b in message.content if b.type == "text"))
        except ValueError as exc:
            return None, f"risposta non valida ({exc})"
        return (plan, "") if plan.get("rapporto") in RELATIONS else (None, "risposta senza un rapporto valido")

    # --- il giro completo ---------------------------------------------------------------

    def run(self, progress: Callable[[str], None] = lambda text: None) -> ResolveReport:
        report = ResolveReport()
        jobs = self.jobs()
        if not jobs:
            return report
        text = {id(job): job.base[1] for job in jobs}
        done = {id(job): 0 for job in jobs}
        records: dict[int, list[dict[str, Any]]] = {id(job): [] for job in jobs}

        def settle(job: Job, step: Step, digest: str, plan: dict[str, Any] | None, why: str) -> None:
            record: dict[str, Any] = {"manuale": step.manual, "sezione": step.title, "trasversale": step.cross, "chiave": digest,
                                      "rapporto": (plan or {}).get("rapporto"), "dichiarato": bool((plan or {}).get("dichiarato")),
                                      "motivo": (plan or {}).get("motivo", "")}
            fallback = text[id(job)] if step.cross else f"{text[id(job)]}\n\n{step.text}"
            try:
                if plan is None:
                    raise Rejected(why)
                text[id(job)] = apply_plan(text[id(job)], step.text, plan, same_title=not step.cross)
                record.update({"esito": "applicata", "piano": plan})
                if plan["rapporto"] in REWRITING and not plan.get("dichiarato"):
                    record["spareggio"] = "il testo nuovo non dichiara la correzione: vince il piu' recente"
            except Rejected as exc:
                text[id(job)] = fallback
                record.update({"esito": "quarantena", "perche": str(exc)})
                if plan is not None:
                    record["piano_scartato"] = plan
                log.warning("risoluzione: %s § %s <- %s: quarantena (%s)", job.file, job.title, step.title, exc)
            records[id(job)].append(record)
            report.steps.append({"file": job.file, "titolo": job.title, **record})
            done[id(job)] += 1

        while True:
            pending: dict[str, tuple[Job, Step, str]] = {}
            for job in jobs:
                while done[id(job)] < len(job.steps):  # ogni passo parte dal testo lasciato dal precedente
                    step = job.steps[done[id(job)]]
                    digest = self.digest(text[id(job)], step)
                    plan = self._remembered(job, digest)
                    if plan is None:
                        pending[f"ris-{digest}"] = (job, step, digest)
                        break
                    report.remembered += 1
                    settle(job, step, digest, plan, "")
            if not pending:
                break
            progress(f"  risoluzione: {len(pending)} incontri tra testi da decidere")
            messages = self.send({cid: self.params(job, text[id(job)], step) for cid, (job, step, _) in pending.items()})
            for cid, (job, step, digest) in pending.items():
                plan, why = self._parse(messages.get(cid), report)
                if plan is not None:
                    self.cache.mkdir(parents=True, exist_ok=True)
                    (self.cache / f"{digest}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
                settle(job, step, digest, plan, why)

        for job in jobs:
            self.lineage.resolved(job.file, job.ntitle, job.title, text[id(job)], records[id(job)])
        log.info("%s", report.summary())
        return report
