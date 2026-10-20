"""Verifica di una campagna: errori, avvisi, consigli. Nessuna chiamata, nessuna scrittura.

Tre livelli, come il validatore che la community di Voyage espone ai creator:

    errors           la campagna non funziona come deve: un file che il codice non legge, un
                     profilo dei dadi che non rispetta la forma, un registro dell'esperienza
                     scritto a mano. Da correggere prima di giocare.
    warnings         funziona, ma puo' sbagliare in gioco: una voce di glossario verso un file
                     sparito, un termine che nel file non c'e', una sezione in quarantena.
    recommendations  migliorie facoltative: ricavare i dadi, consolidare la memoria.

Ogni rilievo ha un `path` (file, e dentro il file la sezione o la chiave), un titolo breve,
il messaggio e il `fix`: cosa fare, in una riga. L'uscita e' la stessa a terminale, in JSON
(`--json`) e nella pagina, cosi' un agente puo' lavorare a ciclo "modifica, verifica, correggi".
I controlli riusano i criteri dei moduli che possiedono i dati (tidy, dice, sheet, campaign,
lineage), senza eseguire le loro correzioni: qui si guarda soltanto.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .campaign import DIARY, MECHANICS_DIR, PREP_DIR, SETTING_DIR, SHEETS_DIR, STATE_FILE, Campaign
from .dice import PROFILE_NAME, WHEN, esiti_problems, profile_path, rules_about_dice
from .lineage import REGISTRY_NAME, SCHEMA as LINEAGE_SCHEMA
from .modi import FILE_NAME as MODES_NAME, modi_problems
from .trigger import FILE_NAME as TRIGGER_NAME, problems as trigger_problems, sheet_path_problems
from .relazioni import FILE_NAME as RELATIONS_NAME, MAX as REL_MAX, MIN as REL_MIN, stadi_problems
from .sheet import XP_KEY, XP_ROUTES

VERSION = 1
LEVELS = ("error", "warning", "recommendation")
LISTS = {"error": "errors", "warning": "warnings", "recommendation": "recommendations"}
LABELS = {"error": "ERRORI", "warning": "AVVISI", "recommendation": "CONSIGLI"}
CLOCK_SEGMENTS = (2, 12)          # come Campaign.set_clock
MIN_RULES_CHARS = 80              # come la UI: sotto, la campagna e' "da preparare"
MIN_TURNS_TO_CONSOLIDATE = 3      # come il pulsante "Consolida memoria"
HOOK_LINES_PER_LEVEL = 6          # all'avvio di sessione si legge il titolo, non l'elenco: il resto con --validate
XP_VIAS = set(XP_ROUTES) | {"spesa"}
KINDS_WITHOUT_TERM_CHECK = {"file", "fonte", "cronaca"}  # come tidy.sync_files
SCENE = f"{SETTING_DIR}/scena.md"
ADVENTURE = f"{SETTING_DIR}/avventura.md"


@dataclass
class Finding:
    level: str
    path: str
    title: str
    message: str
    fix: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    def line(self) -> str:
        return f"- {self.path} | {self.title}: {self.message} -> {self.fix}"


@dataclass
class ValidationReport:
    campaign: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, path: str, title: str, message: str, fix: str) -> None:
        if level not in LEVELS:
            raise ValueError(level)
        self.findings.append(Finding(level, path, title, message, fix))

    def of(self, level: str) -> list[Finding]:
        return [f for f in self.findings if f.level == level]

    @property
    def errors(self) -> list[Finding]:
        return self.of("error")

    @property
    def warnings(self) -> list[Finding]:
        return self.of("warning")

    @property
    def recommendations(self) -> list[Finding]:
        return self.of("recommendation")

    @property
    def ok(self) -> bool:
        """Verde = nessun errore. Avvisi e consigli non bloccano."""
        return not self.errors

    def counts(self) -> dict[str, int]:
        return {LISTS[level]: len(self.of(level)) for level in LEVELS}

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"campaign": self.campaign, "validatorVersion": VERSION, "counts": self.counts()}
        for level in LEVELS:
            out[LISTS[level]] = [f.as_dict() for f in self.of(level)]
        return out

    def headline(self) -> str:
        c = self.counts()
        if not self.findings:
            return f"Verifica di {self.campaign}: tutto in ordine."
        parts = [f"{c['errors']} error{'e' if c['errors'] == 1 else 'i'}",
                 f"{c['warnings']} avvis{'o' if c['warnings'] == 1 else 'i'}",
                 f"{c['recommendations']} consigli{'o' if c['recommendations'] == 1 else ''}"]
        return f"Verifica di {self.campaign}: " + ", ".join(parts) + "."

    def lines(self, levels: tuple[str, ...] = LEVELS) -> list[str]:
        out = [self.headline()]
        for level in levels:
            items = self.of(level)
            if items:
                out.append(LABELS[level])
                out += [f.line() for f in items]
        return out

    def text(self, levels: tuple[str, ...] = LEVELS) -> str:
        return "\n".join(self.lines(levels))


# --- l'ingresso ------------------------------------------------------------------------------

def validate(campaign: Campaign) -> ValidationReport:
    report = ValidationReport(campaign.name)
    _check_mechanics(campaign, report)
    _check_glossary(campaign, report)
    _check_dice(campaign, report)
    _check_sheets(campaign, report)
    _check_state(campaign, report)
    _check_relations(campaign, report)
    _check_modes(campaign, report)
    _check_triggers(campaign, report)
    _check_lineage(campaign, report)
    _check_play(campaign, report)
    return report


def validate_all(campaigns_dir: Path) -> list[ValidationReport]:
    """Tutte le campagne di una cartella, in ordine di nome. Le cartelle nascoste non contano."""
    out = []
    for p in sorted(campaigns_dir.iterdir()) if campaigns_dir.is_dir() else []:
        if p.is_dir() and not p.name.startswith("."):
            out.append(validate(Campaign(p)))
    return out


def hook_text(reports: list[ValidationReport]) -> str:
    """Per l'hook di Claude Code: solo errori e avvisi, pochi per livello, nulla se tutto e' in ordine."""
    noisy = [r for r in reports if r.errors or r.warnings]
    if not noisy:
        return ""
    lines = [f"VERIFICA CAMPAGNE SIMPLE MASTER: {len(noisy)} campagn{'a' if len(noisy) == 1 else 'e'} con errori o avvisi."]
    for r in noisy:
        lines.append(r.headline())
        for level in ("error", "warning"):
            items = r.of(level)
            if not items:
                continue
            lines.append(LABELS[level])
            lines += [f.line() for f in items[:HOOK_LINES_PER_LEVEL]]
            if len(items) > HOOK_LINES_PER_LEVEL:
                lines.append(f"  ... e altri {len(items) - HOOK_LINES_PER_LEVEL}")
    lines.append("Dettaglio e consigli: python main.py --campaign campaigns/<nome> --validate (--json per gli agenti).")
    return "\n".join(lines)


# --- i controlli -------------------------------------------------------------------------------

def _read_json(path: Path) -> tuple[Any, str | None]:
    """(dati, errore). Un file assente non e' un errore: dati None."""
    if not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except ValueError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, str(exc)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_mechanics(campaign: Campaign, report: ValidationReport) -> None:
    rules = campaign.file(f"{MECHANICS_DIR}/regole.md")
    if not rules.exists() or len(rules.read()) <= MIN_RULES_CHARS:
        report.add("recommendation", f"{MECHANICS_DIR}/regole.md", "campagna da preparare",
                   "le regole sono assenti o vuote: il master non ha un sistema da applicare",
                   "Prepara da manuale (menu) o scrivi le regole a mano in meccanica/")


def _check_glossary(campaign: Campaign, report: ValidationReport) -> None:
    gname = campaign.glossary.file.name
    entries = campaign.glossary.entries()
    existing = {f.name for f in campaign.files()}
    seen: dict[str, str] = {}
    cache: dict[str, str] = {}
    for e in entries:
        key = e.term.strip().lower()
        if key in seen:
            report.add("warning", f"{gname} § {e.term}", "voce doppia",
                       f"il termine compare due volte (anche come '{seen[key]}'): il contesto del turno ne prende una a caso",
                       "tieni la voce piu' completa; 'Sanifica doppioni' lo fa da solo")
        seen.setdefault(key, e.term)
        if not e.file:
            report.add("warning", f"{gname} § {e.term}", "voce senza file",
                       "la voce non dice dove stanno i dettagli: nel contesto entra solo la descrizione",
                       "indica il file (e la sezione nel file) o togli la voce")
            continue
        if e.file not in existing:
            report.add("warning", f"{gname} § {e.term}", "voce verso un file sparito",
                       f"punta a `{e.file}`, che non esiste: il contesto del turno non trova nulla",
                       "python main.py --tidy-glossary la toglie; oppure correggi il file nella voce")
            continue
        if e.kind in KINDS_WITHOUT_TERM_CHECK:
            continue
        text = cache.setdefault(e.file, campaign.file(e.file).read().lower())
        if key not in text:
            report.add("warning", f"{gname} § {e.term}", "termine assente dal file",
                       f"'{e.term}' non compare in `{e.file}`: la sezione da portare nel contesto non si trova",
                       "correggi il termine o il file nella voce; --tidy-glossary chiede all'API se tenerla")


def _profile_problems(data: Any) -> list[tuple[str, str]]:
    """(percorso, problema) per ogni punto in cui il profilo non ha la forma di PROFILE_SCHEMA."""
    out: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return [("", "non e' un oggetto JSON")]
    tiri = data.get("tiri")
    if not isinstance(tiri, list) or not tiri:
        return [("tiri", "manca l'elenco dei tipi di tiro, o e' vuoto")]
    names: set[str] = set()
    for i, kind in enumerate(tiri):
        at = f"tiri[{i}]"
        if not isinstance(kind, dict):
            out.append((at, "non e' un oggetto"))
            continue
        name = str(kind.get("nome") or "").strip().lower()
        if not name:
            out.append((f"{at}.nome", "manca il nome del tipo di tiro"))
        elif name in names:
            out.append((f"{at}.nome", f"'{name}' e' gia' usato da un altro tipo: il marcatore prenderebbe il primo"))
        names.add(name)
        if kind.get("tipo") not in ("successi", "somma"):
            out.append((f"{at}.tipo", "dev'essere `successi` o `somma`"))
        dado = kind.get("dado")
        if not _is_int(dado) or dado < 2:
            out.append((f"{at}.dado", "le facce del dado devono essere un intero da 2 in su"))
            dado = None
        fixed = kind.get("dadi_fissi")
        if fixed is not None and (not _is_int(fixed) or fixed < 1):
            out.append((f"{at}.dadi_fissi", "null, oppure un intero da 1 in su"))
        threshold = kind.get("successo_da")
        if threshold is not None and (not _is_int(threshold) or threshold < 1 or (dado and threshold > dado)):
            out.append((f"{at}.successo_da", "null, oppure una faccia del dado"))
        if kind.get("tipo") == "successi" and threshold is None:
            out.append((f"{at}.successo_da", "un tiro a successi deve dire da che faccia un dado riesce"))
        pair = kind.get("coppie")
        if pair is not None:
            if not isinstance(pair, dict) or not _is_int(pair.get("faccia")) or not _is_int(pair.get("successi_extra")):
                out.append((f"{at}.coppie", "null, oppure {faccia, successi_extra, nome}"))
            elif dado and not 1 <= pair["faccia"] <= dado:
                out.append((f"{at}.coppie.faccia", "non e' una faccia del dado"))
        special = kind.get("speciali")
        if special is not None and (not isinstance(special, dict) or not str(special.get("quanti_da") or "").strip()):
            out.append((f"{at}.speciali", "null, oppure {nome, quanti_da, sostituiscono} con il percorso nella scheda"))
        if kind.get("esiti") is not None:
            out += [(f"{at}.esiti{where}", problem) for where, problem in esiti_problems(kind["esiti"])]
        events = kind.get("eventi")
        if events is None:
            continue
        if not isinstance(events, list):
            out.append((f"{at}.eventi", "dev'essere un elenco"))
            continue
        for j, ev in enumerate(events):
            if not isinstance(ev, dict) or not _is_int(ev.get("faccia")):
                out.append((f"{at}.eventi[{j}]", "ogni evento ha {nome, faccia, quando, solo_speciali, effetto}"))
            elif ev.get("quando") not in WHEN:
                out.append((f"{at}.eventi[{j}].quando", f"dev'essere uno tra {', '.join(WHEN)}"))
            elif dado and not 1 <= ev["faccia"] <= dado:
                out.append((f"{at}.eventi[{j}].faccia", "non e' una faccia del dado"))
    return out


def _check_dice(campaign: Campaign, report: ValidationReport) -> None:
    name = f"{MECHANICS_DIR}/{PROFILE_NAME}"
    data, err = _read_json(profile_path(campaign))
    if err is not None:
        report.add("error", name, "profilo dei dadi illeggibile", f"non e' JSON valido ({err}): il programma gioca come se non ci fosse",
                   "ripara il JSON, oppure cancella il file e premi 'Ricava i dadi dalle regole'")
        return
    if data is None:
        if rules_about_dice(campaign):
            report.add("recommendation", name, "dadi non ancora ricavati",
                       "le regole parlano di tiri ma il profilo non c'e': il narratore scrive i dadi per esteso",
                       "nella pagina della campagna premi 'Ricava i dadi dalle regole' (una chiamata, una volta)")
        return
    for at, problem in _profile_problems(data):
        report.add("error", f"{name}:{at}" if at else name, "profilo dei dadi malformato", problem,
                   "correggi il campo, oppure cancella il file e ricava di nuovo il profilo")
    if not isinstance(data, dict) or not isinstance(data.get("tiri"), list):
        return
    sheet_info = campaign.active_sheet()
    if sheet_info is None:
        return
    from .sheet import Sheet

    try:
        sheet = Sheet(campaign, sheet_info["slug"])
    except ValueError:
        return  # la scheda illeggibile ha gia' il suo errore in _check_sheets
    if not sheet.exists:
        return
    for i, kind in enumerate(data["tiri"]):
        special = kind.get("speciali") if isinstance(kind, dict) else None
        path = str(special.get("quanti_da") or "").strip() if isinstance(special, dict) else ""
        if not path:
            continue
        label = str(special.get("nome") or "speciali")
        try:
            value = sheet.get(path)
        except ValueError:
            report.add("error", f"{name}:tiri[{i}].speciali.quanti_da", "percorso assente dalla scheda",
                       f"`{path}` non esiste in `{SHEETS_DIR}/{sheet.slug}.json`: i dadi '{label}' sparirebbero dal tiro senza avviso",
                       "allinea il percorso alla scheda (o la scheda al percorso)")
            continue
        if not _is_int(value):
            report.add("error", f"{name}:tiri[{i}].speciali.quanti_da", "valore non numerico nella scheda",
                       f"`{path}` vale {json.dumps(value, ensure_ascii=False)}: serve un intero per contare i dadi",
                       "metti un numero in quel percorso della scheda")


def _check_sheets(campaign: Campaign, report: ValidationReport) -> None:
    folder = campaign.root / SHEETS_DIR
    if not folder.is_dir():
        return
    jsons = sorted(p for p in folder.glob("*.json") if not p.name.startswith("."))
    for md in sorted(folder.glob("*.md")):
        if not (folder / f"{md.stem}.json").is_file():
            report.add("recommendation", f"{SHEETS_DIR}/{md.name}", "scheda solo testo",
                       "il master deve interpretare frasi invece di leggere numeri",
                       "aprila nel costruttore di schede e salvala: diventa dati (JSON) alla prima modifica")
    if not jsons and not list(folder.glob("*.md")):
        report.add("recommendation", SHEETS_DIR, "nessun personaggio", "senza una scheda la sezione Gioca e' disattivata",
                   "crea un personaggio dalla sezione Personaggio")
    for path in jsons:
        name = f"{SHEETS_DIR}/{path.name}"
        data, err = _read_json(path)
        if err is not None:
            report.add("error", name, "scheda illeggibile", f"non e' JSON valido ({err}): il gioco con questo personaggio si ferma",
                       "ripara il JSON (la vista .md accanto aiuta a ricostruirla)")
            continue
        if not isinstance(data, dict) or not data:
            report.add("error", name, "scheda vuota", "il file non contiene un oggetto JSON con i dati",
                       "rifai la scheda dal costruttore")
            continue
        if not str(data.get("nome") or "").strip():
            report.add("warning", f"{name}:nome", "scheda senza nome",
                       "il nome viene dedotto dal file: nel glossario e nel prompt comparira' lo slug",
                       "aggiungi `nome` in cima alla scheda")
        if XP_KEY in data:
            _check_ledger(name, data[XP_KEY], report)


def _check_ledger(name: str, xp: Any, report: ValidationReport) -> None:
    at = f"{name}:{XP_KEY}"
    if not isinstance(xp, dict):
        report.add("error", at, "esperienza non e' un registro", "deve essere {\"registro\": [...]}",
                   "ricostruisci il registro: una riga per movimento (quando, sessione, via, punti, motivo)")
        return
    extra = sorted(k for k in xp if k != "registro")
    if extra:
        report.add("error", at, "totali scritti a mano",
                   f"chiavi {extra}: guadagnati, spesi e disponibili si calcolano dal registro a ogni lettura, un totale scritto a mano mente",
                   "togli le chiavi: resta solo `registro`")
    rows = xp.get("registro")
    if not isinstance(rows, list):
        report.add("error", f"{at}.registro", "registro assente", "manca l'elenco dei movimenti",
                   "`registro: []` se il personaggio non ha ancora punti")
        return
    balance = 0
    once: dict[int, int] = {}
    for i, row in enumerate(rows):
        where = f"{at}.registro[{i}]"
        if not isinstance(row, dict) or not _is_int(row.get("punti")):
            report.add("error", where, "movimento malformato", "ogni riga ha punti (intero), via, motivo, sessione",
                       "correggi la riga")
            continue
        via, points = row.get("via"), row["punti"]
        if via not in XP_VIAS:
            report.add("warning", where, "via sconosciuta", f"'{via}' non e' tra {', '.join(sorted(XP_VIAS))}",
                       "usa una via ammessa: il narratore le conosce e le cita")
        if (points < 0) != (via == "spesa"):
            report.add("error", where, "segno e via non concordano",
                       "i punti spesi sono negativi con via `spesa`; i guadagni positivi con la loro via",
                       "correggi segno o via")
        if not str(row.get("motivo") or "").strip():
            report.add("warning", where, "movimento senza motivo", "il registro e' la memoria del perche'",
                       "scrivi il motivo")
        if via == "sessione":
            s = row.get("sessione")
            if _is_int(s):
                once[s] = once.get(s, 0) + 1
        balance += points
    for s, n in once.items():
        if n > 1:
            report.add("error", f"{at}.registro", "punti di sessione assegnati due volte",
                       f"la sessione {s} ha {n} righe con via `sessione`: il codice ne ammette una",
                       "togli le righe in piu' (il totale si ricalcola da solo)")
    if balance < 0:
        report.add("error", f"{at}.registro", "spesi piu' punti di quelli guadagnati",
                   f"il saldo del registro e' {balance}", "controlla le righe di spesa")


def _check_state(campaign: Campaign, report: ValidationReport) -> None:
    path = campaign.root / STATE_FILE
    data, err = _read_json(path)
    if err is not None:
        report.add("error", STATE_FILE, "stato illeggibile",
                   f"non e' JSON valido ({err}): il programma riparte senza personaggio attivo, orologi e contatore delle sessioni",
                   "ripara il JSON (pg_attivo, sessione, orologi, memoria)")
        return
    if data is None:
        return
    if not isinstance(data, dict):
        report.add("error", STATE_FILE, "stato non e' un oggetto", "il file deve contenere un oggetto JSON",
                   "ripara il file")
        return
    slugs = {s["slug"] for s in campaign.sheets()}
    pg = data.get("pg_attivo")
    if pg is not None and pg not in slugs:
        report.add("warning", f"{STATE_FILE}:pg_attivo", "personaggio attivo inesistente",
                   f"'{pg}' non e' tra le schede ({', '.join(sorted(slugs)) or 'nessuna'}): la campagna si apre senza personaggio",
                   "scegli un personaggio con 'Gioca con questo' o correggi il valore")
    s = data.get("sessione")
    if s is not None and (not _is_int(s) or s < 1):
        report.add("error", f"{STATE_FILE}:sessione", "numero di sessione non valido", "dev'essere un intero da 1 in su",
                   "correggi il valore")
    clocks = data.get("orologi")
    if clocks is not None:
        if not isinstance(clocks, dict):
            report.add("error", f"{STATE_FILE}:orologi", "orologi non e' un oggetto", "gli orologi sono {nome: {segmenti, pieni, conseguenza}}",
                       "ripara la struttura")
        else:
            for cname, clock in clocks.items():
                where = f"{STATE_FILE}:orologi[{cname}]"
                seg = clock.get("segmenti") if isinstance(clock, dict) else None
                full = clock.get("pieni") if isinstance(clock, dict) else None
                if not _is_int(seg) or not CLOCK_SEGMENTS[0] <= seg <= CLOCK_SEGMENTS[1]:
                    report.add("error", where, "segmenti fuori misura", f"un orologio ha da {CLOCK_SEGMENTS[0]} a {CLOCK_SEGMENTS[1]} segmenti",
                               "correggi `segmenti`")
                elif not _is_int(full) or not 0 <= full <= seg:
                    report.add("error", where, "riempimento fuori misura", "`pieni` va da 0 ai segmenti",
                               "correggi `pieni`")
                if isinstance(clock, dict) and "scadenza_turni" in clock:
                    due = clock["scadenza_turni"]
                    if not _is_int(due) or due < 1:
                        report.add("error", f"{where}.scadenza_turni", "scadenza non valida", "e' un numero di turni, da 1 in su",
                                   "correggi `scadenza_turni` o toglila")
                    elif not _is_int(clock.get("creato_turno")) or clock["creato_turno"] < 0:
                        report.add("error", f"{where}.creato_turno", "scadenza senza turno di partenza",
                                   "con `scadenza_turni` serve `creato_turno` (intero da 0): da li' il programma conta i turni",
                                   "metti `creato_turno` al numero di turni giocati quando l'orologio e' nato")
                if isinstance(clock, dict) and not str(clock.get("conseguenza") or "").strip():
                    report.add("recommendation", where, "orologio senza conseguenza",
                               "quando si riempie il narratore ricevera' solo 'si compie'",
                               "scrivi la conseguenza negli appunti o nel file")
    fired = data.get("trigger_scattati")
    if fired is not None and (not isinstance(fired, list) or not all(isinstance(x, str) for x in fired)):
        report.add("error", f"{STATE_FILE}:trigger_scattati", "trigger scattati non validi", "e' un elenco di nomi di regole",
                   "ripara la struttura (o togli la chiave: le regole non ricorrenti scatterebbero di nuovo)")
    mode = data.get("modo")
    if mode is not None:
        from .modi import nomi

        if not isinstance(mode, str) or mode.strip().lower() not in nomi(campaign):
            report.add("error", f"{STATE_FILE}:modo", "modo di gioco sconosciuto",
                       f"{json.dumps(mode, ensure_ascii=False)} non e' tra {', '.join(nomi(campaign))}: il narratore giocherebbe in `classico` senza dirlo",
                       "scegli un modo dalla pagina, o correggi il valore")
    png = data.get("png")
    if png is not None:
        if not isinstance(png, dict):
            report.add("error", f"{STATE_FILE}:png", "relazioni non e' un oggetto", "le relazioni sono {nome del PNG: {relazione}}",
                       "ripara la struttura")
        else:
            for pname, entry in png.items():
                where = f"{STATE_FILE}:png[{pname}]"
                if not isinstance(entry, dict):
                    report.add("error", where, "voce di relazione non valida", "ogni PNG ha un oggetto {relazione}",
                               "ripara la voce")
                elif "relazione" not in entry:
                    report.add("error", where, "voce senza relazione", "manca il numero: il programma lo leggerebbe come 0",
                               "scrivi `relazione` (da -100 a 100) o togli la voce")
                elif not _is_int(entry["relazione"]) or not REL_MIN <= entry["relazione"] <= REL_MAX:
                    report.add("error", f"{where}.relazione", "relazione fuori scala", f"dev'essere un intero fra {REL_MIN} e {REL_MAX}",
                               "correggi il valore")
    memory = data.get("memoria")
    if memory is not None:
        if not isinstance(memory, dict):
            report.add("error", f"{STATE_FILE}:memoria", "memoria non e' un oggetto", "e' {personaggio: turni consolidati}",
                       "ripara la struttura")
        else:
            for mname, count in memory.items():
                where = f"{STATE_FILE}:memoria[{mname}]"
                if not _is_int(count) or count < 0:
                    report.add("error", where, "contatore non valido", "i turni consolidati sono un intero da 0 in su",
                               "correggi il valore")
                    continue
                played = len(campaign.all_turns(mname))
                if count > played:
                    report.add("error", where, "consolidato oltre il giocato",
                               f"risultano consolidati {count} turni ma il giocato di '{mname}' ne ha {played}: il prossimo consolidamento salterebbe turni",
                               f"porta il valore a {played} o meno")


def _check_relations(campaign: Campaign, report: ValidationReport) -> None:
    name = f"{MECHANICS_DIR}/{RELATIONS_NAME}"
    data, err = _read_json(campaign.root / MECHANICS_DIR / RELATIONS_NAME)
    if err is not None:
        report.add("error", name, "scala delle relazioni illeggibile",
                   f"non e' JSON valido ({err}): il programma usa gli stadi predefiniti e ignora il file",
                   "ripara il JSON, oppure cancella il file")
        return
    if data is None:
        return
    stadi = data.get("stadi") if isinstance(data, dict) else None
    if stadi is None:
        report.add("error", name, "scala delle relazioni senza stadi", "il file deve contenere {\"stadi\": [{nome, da, a}, ...]}",
                   "scrivi gli stadi, oppure cancella il file (valgono i predefiniti)")
        return
    for where, problem in stadi_problems(stadi):
        report.add("error", f"{name}:stadi{where}", "scala delle relazioni malformata",
                   problem + ": il programma usa gli stadi predefiniti e ignora il file",
                   "correggi gli stadi: devono tassellare -100..100 senza buchi")


def _check_modes(campaign: Campaign, report: ValidationReport) -> None:
    name = f"{MECHANICS_DIR}/{MODES_NAME}"
    data, err = _read_json(campaign.root / MECHANICS_DIR / MODES_NAME)
    if err is not None:
        report.add("error", name, "modi di gioco illeggibili", f"non e' JSON valido ({err}): il programma usa i modi predefiniti e ignora il file",
                   "ripara il JSON, oppure cancella il file")
        return
    if data is None:
        return
    modes = data.get("modi") if isinstance(data, dict) else None
    if modes is None:
        report.add("error", name, "modi di gioco senza elenco", "il file deve contenere {\"modi\": [{nome, descrizione, istruzioni, png}, ...]}",
                   "scrivi i modi, oppure cancella il file (valgono i predefiniti)")
        return
    for where, problem in modi_problems(modes):
        report.add("error", f"{name}:modi{where}", "modi di gioco malformati", problem + ": il programma usa i modi predefiniti e ignora il file",
                   "correggi il modo indicato")


def _check_triggers(campaign: Campaign, report: ValidationReport) -> None:
    name = f"{MECHANICS_DIR}/{TRIGGER_NAME}"
    data, err = _read_json(campaign.root / MECHANICS_DIR / TRIGGER_NAME)
    if err is not None:
        report.add("error", name, "trigger illeggibili", f"non e' JSON valido ({err}): nessuna regola scatta",
                   "ripara il JSON, oppure cancella il file")
        return
    if data is None:
        return
    found = trigger_problems(data)
    for where, problem in found:
        report.add("error", f"{name}:{where}" if where else name, "trigger malformati", problem + ": il file viene ignorato intero, nessuna regola scatta",
                   "correggi la regola indicata (python main.py --doc trigger)")
    if found:
        return
    sheet_info = campaign.active_sheet()
    if sheet_info is None:
        return
    from .sheet import Sheet

    try:
        sheet = Sheet(campaign, sheet_info["slug"])
    except ValueError:
        return
    if not sheet.exists:
        return
    for where, problem in sheet_path_problems(data, sheet):
        report.add("error", f"{name}:{where}", "percorso assente dalla scheda", problem,
                   "allinea il percorso alla scheda (o la scheda al percorso)")


def _check_lineage(campaign: Campaign, report: ValidationReport) -> None:
    path = campaign.root / PREP_DIR / REGISTRY_NAME
    name = f"{PREP_DIR}/{REGISTRY_NAME}"
    data, err = _read_json(path)
    if data is None and err is None:
        return
    if err is not None or not isinstance(data, dict) or data.get("schema") != LINEAGE_SCHEMA:
        report.add("warning", name, "registro della paternita' illeggibile o di un'altra versione",
                   "la prossima preparazione riparte da zero sulla paternita' (i testi restano)",
                   "se la campagna non ha manuali da fondere ignoralo; altrimenti rilancia la preparazione del manuale")
        return
    sections = data.get("sezioni") if isinstance(data.get("sezioni"), dict) else {}
    for file, titles in sections.items():
        if not isinstance(titles, dict):
            continue
        exists = False
        try:
            exists = campaign.file(file).exists()
        except ValueError:
            pass
        if not exists:
            report.add("warning", f"{name}:sezioni[{file}]", "file registrato ma sparito",
                       f"`{file}` ha paternita' registrata ma non esiste piu'",
                       "se l'hai tolto di proposito, nulla da fare: rilanciando il manuale non rinasce")
            continue
        outline = {t.strip().lower() for t in campaign.file(file).outline()}
        for ntitle, datum in titles.items():
            if not isinstance(datum, dict):
                continue
            title = str(datum.get("titolo") or ntitle)
            held = [s for s in (datum.get("risoluzione") or []) if isinstance(s, dict) and s.get("esito") == "quarantena"]
            if held:
                why = held[0].get("perche", "")
                report.add("warning", f"{file} § {title}", "sezione in quarantena",
                           f"due manuali si toccano e il piano di fusione non ha passato i controlli ({why}): le versioni sono una dopo l'altra",
                           "rileggi la sezione e sistemala a mano (il testo scritto a mano non viene piu' riscritto)")
            if title.strip().lower() not in outline:
                report.add("warning", f"{file} § {title}", "sezione registrata ma assente",
                           "il registro la conosce, il file non la contiene piu'",
                           "se l'hai tolta di proposito va bene cosi'; altrimenti rilancia la preparazione")


def _check_play(campaign: Campaign, report: ValidationReport) -> None:
    sheet_info = campaign.active_sheet()
    if sheet_info is None:
        return
    slug, name = sheet_info["slug"], sheet_info["name"]
    played = len(campaign.all_turns(slug))
    if not played:
        return
    if not campaign.file(SCENE).exists():
        report.add("recommendation", SCENE, "scena in corso assente",
                   f"{played} turni giocati ma nessun documento di scena: il prossimo turno riparte solo dagli ultimi scambi",
                   "'Consolida memoria' la scrive; da li' lo scriba la aggiorna a ogni turno")
    adventure = campaign.file(ADVENTURE)
    title = f"Fili aperti di {name}"
    if not adventure.exists() or not adventure.get_section(title):
        report.add("recommendation", f"{ADVENTURE} § {title}", "fili aperti assenti",
                   "il narratore non ha fili da muovere: ogni scena improvvisa dal nulla",
                   "'Consolida memoria' li ricava dal giocato; oppure scrivili a mano nella sezione")
    stale = campaign.stale_threads(name)
    if stale:
        report.add("recommendation", f"{ADVENTURE} § {title}", "fili stantii",
                   f"{len(stale)} fil{'o fermo' if len(stale) == 1 else 'i fermi'} da almeno due sessioni: {'; '.join(stale)[:200]}",
                   "il narratore li riceve alla prossima apertura e il consolidamento fra i documenti: chiudili o rilanciali")
    from .fatti import FILE as FACTS_FILE, MAX_LINES as FACTS_MAX, leggi as facts_of

    if len(facts_of(campaign)) > FACTS_MAX:
        report.add("recommendation", FACTS_FILE, "fatti da non perdere troppi",
                   f"{len(facts_of(campaign))} righe: e' un promemoria di cio' che non si puo' perdere, non un diario",
                   f"sfoltisci a meno di {FACTS_MAX} righe: il resto sta nella cronaca")
    pending = played - campaign.consolidated(slug)
    if pending >= MIN_TURNS_TO_CONSOLIDATE:
        report.add("recommendation", f"{STATE_FILE}:memoria[{slug}]", "memoria da consolidare",
                   f"{pending} turni giocati non sono ancora cronaca, fili e memoria dei PNG",
                   "premi 'Consolida memoria' (una chiamata) o chiudi la sessione")
    diary = campaign.file(DIARY)
    if not diary.exists():
        report.add("warning", DIARY, "diario assente", "lo scriba scrive qui a ogni turno: il file verra' creato, ma la cronaca passata manca",
                   "crea il file con un titolo `# Diario della campagna`")
