"""I trigger: le regole del manuale che il programma puo' contare, scritte come dati.

`meccanica/trigger.json` e' un elenco di regole `condizioni -> effetti`. Le condizioni leggono
solo cio' che il programma possiede: un valore della scheda, un orologio, la relazione o lo
stadio di un PNG, l'esito dell'ultimo tiro, i contatori di turni e sessioni. Gli effetti sono
tipizzati: un avviso al narratore (`[TRIGGER <nome>: ...]` in testa al messaggio del turno),
un orologio che avanza (o nasce), una relazione che si muove di un passo. Niente testo libero
da giudicare, niente script, niente AI: a ogni turno, prima della chiamata del narratore, il
codice valuta tutte le regole (AND fra le condizioni), applica gli effetti e segna quelle non
ricorrenti come scattate (`stato.json` -> `trigger_scattati`). Cosi' "stress a 4 o piu': rischio
di crollo" arriva al narratore dal contatore della scheda, non dalla sua memoria.

E' il linguaggio del World Engine di Voyage ridotto alla parte deterministica: condizioni
strutturali ed effetti chiusi, tetti bassi (cinque condizioni, tre effetti, cento regole),
un file che o e' valido tutto o si ignora, e la verifica che lo dice.

Esempio:

    {"trigger": [
      {"nome": "rischio di crollo", "ricorrente": true,
       "quando": [{"scheda": "stato.stress", "op": ">=", "valore": 4}],
       "effetti": [{"avviso": "Stress a 4 o piu': a ogni tentazione serve una prova, secondo le regole"}]},
      {"nome": "la ferita si infetta",
       "quando": [{"scheda": "stato.punti_ferita.attuale", "op": "<", "valore": 5}],
       "effetti": [{"orologio": "L'infezione", "avanza": 1, "segmenti": 6, "conseguenza": "Kael perde una notte di riposo"}]}
    ]}
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import testi
from .campaign import MECHANICS_DIR, Campaign

log = logging.getLogger("master.trigger")

FILE_NAME = "trigger.json"
STATE_KEY = "trigger_scattati"
MAX_TRIGGERS, MAX_CONDITIONS, MAX_EFFECTS = 100, 5, 3
OPS = ("==", "!=", "<", "<=", ">", ">=", "contiene", "ogni")
RICORRENZE = (True, False, "sessione")  # ogni turno in cui vale / una volta sola / una volta per sessione
NUMERIC_OPS = ("<", "<=", ">", ">=")
CONDIZIONI: dict[str, str] = {
    "scheda": "un valore della scheda del personaggio attivo, per percorso (es. `stato.stress`); se il percorso non esiste la condizione e' falsa",
    "orologio": "i segmenti pieni di un orologio attivo, per nome; se l'orologio non esiste la condizione e' falsa",
    "relazione": "il numero della relazione di un PNG (-100..100); se non e' mai stata contata, falsa",
    "stadio": "lo stadio della relazione di un PNG, per nome (es. `Ostile`), confronto solo con `==` / `!=`",
    "esito": "il nome della fascia dell'ultimo tiro, solo nel turno in cui l'esito arriva (es. `fallito`), `==` / `!=`",
    "tabella": "la riga uscita da una tabella, per nome della tabella, solo nel turno in cui l'esito arriva: `==` / `!=` / `contiene` con un testo",
    "contatore": "`turni` (giocati dal personaggio) o `sessione` (in corso); con `op: ogni` e un intero N: vero al turno (o sessione) N, 2N, 3N...",
}
EFFETTI: dict[str, str] = {
    "avviso": "una riga al narratore in testa al messaggio del turno: `[TRIGGER <nome>: avviso]`",
    "orologio": "fa avanzare l'orologio di `avanza` segmenti (anche negativi); se non esiste e c'e' `segmenti`, lo crea (con `conseguenza`)",
    "relazione": "muove la relazione del PNG di un passo: `verso` sale|scende, `forza` lieve|media|forte",
    "tabella": "tira la tabella con quel nome (meccanica/tabelle.json) e porta la riga al narratore: `[TRIGGER <nome>: tabella <t> -> riga]`",
}
_ROLL_OUTCOME = re.compile(r"contro difficolta' \d+: ([^(]+?) \(")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --- la forma --------------------------------------------------------------------------------------

def problems(data: Any) -> list[tuple[str, str]]:
    """(percorso, problema) per ogni punto in cui il file non ha la forma giusta."""
    if not isinstance(data, dict) or "trigger" not in data:
        return [("", "il file deve contenere {\"trigger\": [...]}")]
    triggers = data["trigger"]
    if not isinstance(triggers, list):
        return [("trigger", "dev'essere un elenco")]
    out: list[tuple[str, str]] = []
    if len(triggers) > MAX_TRIGGERS:
        out.append(("trigger", f"al massimo {MAX_TRIGGERS} regole"))
    names: set[str] = set()
    for i, t in enumerate(triggers):
        at = f"trigger[{i}]"
        if not isinstance(t, dict):
            out.append((at, "una regola e' {nome, quando, effetti, ricorrente}"))
            continue
        name = str(t.get("nome") or "").strip().lower()
        bad_name = testi.problema_nome(t.get("nome"))
        if bad_name:
            out.append((f"{at}.nome", bad_name))
        elif name in names:
            out.append((f"{at}.nome", f"'{name}' e' gia' usato da un'altra regola"))
        names.add(name)
        if "ricorrente" in t and not (isinstance(t["ricorrente"], bool) or t["ricorrente"] == "sessione"):
            out.append((f"{at}.ricorrente", "true, false o \"sessione\" (una volta per sessione)"))
        conds = t.get("quando")
        if not isinstance(conds, list) or not conds:
            out.append((f"{at}.quando", "almeno una condizione, in un elenco"))
        else:
            if len(conds) > MAX_CONDITIONS:
                out.append((f"{at}.quando", f"al massimo {MAX_CONDITIONS} condizioni"))
            for j, c in enumerate(conds):
                out += [(f"{at}.quando[{j}]{where}", problem) for where, problem in _condition_problems(c)]
        effects = t.get("effetti")
        if not isinstance(effects, list) or not effects:
            out.append((f"{at}.effetti", "almeno un effetto, in un elenco"))
        else:
            if len(effects) > MAX_EFFECTS:
                out.append((f"{at}.effetti", f"al massimo {MAX_EFFECTS} effetti"))
            for j, e in enumerate(effects):
                out += [(f"{at}.effetti[{j}]{where}", problem) for where, problem in _effect_problems(e)]
    return out


def _condition_problems(c: Any) -> list[tuple[str, str]]:
    if not isinstance(c, dict):
        return [("", "una condizione e' un oggetto con una sola chiave fra " + ", ".join(CONDIZIONI))]
    kinds = [k for k in CONDIZIONI if k in c]
    if len(kinds) != 1:
        return [("", "una condizione ha esattamente una chiave fra " + ", ".join(CONDIZIONI))]
    kind = kinds[0]
    out: list[tuple[str, str]] = []
    target = c[kind]
    if not isinstance(target, str) or not target.strip():
        out.append((f".{kind}", "un nome o un percorso, come testo"))
    if kind == "contatore" and str(target).strip().lower() not in ("turni", "sessione"):
        out.append((".contatore", "`turni` o `sessione`"))
    op = c.get("op")
    if op not in OPS:
        out.append((".op", f"uno fra {', '.join(OPS)}"))
    elif op == "ogni" and kind != "contatore":
        out.append((".op", "`ogni` vale solo per `contatore` (ogni N turni o sessioni)"))
    elif op == "ogni" and not (_is_int(c.get("valore")) and c.get("valore") >= 1):
        out.append((".valore", "con `ogni` serve un intero da 1 in su"))
    elif kind in ("stadio", "esito") and op not in ("==", "!="):
        out.append((".op", f"per `{kind}` solo == o !="))
    elif kind == "tabella" and op not in ("==", "!=", "contiene"):
        out.append((".op", "per `tabella` solo ==, != o contiene"))
    if "valore" not in c:
        out.append((".valore", "manca il valore con cui confrontare"))
    elif op in NUMERIC_OPS and not _is_number(c["valore"]):
        out.append((".valore", f"con `{op}` serve un numero"))
    elif kind in ("stadio", "esito", "tabella") and not isinstance(c["valore"], str):
        out.append((".valore", "un nome, come testo"))
    extra = sorted(k for k in c if k not in (kind, "op", "valore"))
    if extra:
        out.append(("", f"chiavi sconosciute: {extra}"))
    return out


def _effect_problems(e: Any) -> list[tuple[str, str]]:
    if not isinstance(e, dict):
        return [("", "un effetto e' un oggetto con una chiave fra " + ", ".join(EFFETTI))]
    kinds = [k for k in EFFETTI if k in e]
    if len(kinds) != 1:
        return [("", "un effetto ha esattamente una chiave fra " + ", ".join(EFFETTI))]
    kind = kinds[0]
    out: list[tuple[str, str]] = []
    bad_text = testi.problema_riga(e[kind]) if kind == "avviso" else testi.problema_nome(e[kind])
    if bad_text:
        out.append((f".{kind}", bad_text))
    if kind == "orologio" and e.get("conseguenza") not in (None, "") and testi.problema_riga(e.get("conseguenza")):
        out.append((".conseguenza", testi.problema_riga(e.get("conseguenza"))))
    allowed = {"avviso": set(), "orologio": {"avanza", "segmenti", "conseguenza"}, "relazione": {"verso", "forza"}, "tabella": set()}[kind]
    if kind == "orologio":
        if not _is_int(e.get("avanza")):
            out.append((".avanza", "intero, anche negativo"))
        if "segmenti" in e and (not _is_int(e["segmenti"]) or not 2 <= e["segmenti"] <= 12):
            out.append((".segmenti", "da 2 a 12, se si vuole creare l'orologio"))
    if kind == "relazione":
        from .relazioni import DELTA, VERSI

        if e.get("verso") not in VERSI:
            out.append((".verso", " o ".join(VERSI)))
        if e.get("forza") not in DELTA:
            out.append((".forza", ", ".join(DELTA)))
    extra = sorted(k for k in e if k not in allowed and k != kind)
    if extra:
        out.append(("", f"chiavi sconosciute: {extra}"))
    return out


def sheet_path_problems(data: Any, sheet: Any) -> list[tuple[str, str]]:
    """I percorsi `scheda` che nella scheda attiva non esistono: la condizione non scatterebbe mai."""
    out = []
    if not isinstance(data, dict) or not isinstance(data.get("trigger"), list) or sheet is None:
        return out
    for i, t in enumerate(data["trigger"]):
        for j, c in enumerate(t.get("quando") or [] if isinstance(t, dict) else []):
            if isinstance(c, dict) and isinstance(c.get("scheda"), str):
                try:
                    sheet.get(c["scheda"])
                except ValueError:
                    out.append((f"trigger[{i}].quando[{j}].scheda", f"`{c['scheda']}` non esiste nella scheda attiva: non scatterebbe mai"))
    return out


# --- caricamento ----------------------------------------------------------------------------------

def carica(campaign: Campaign) -> list[dict[str, Any]]:
    """Le regole della campagna, se il file e' valido; altrimenti nessuna (la verifica lo dice)."""
    path = campaign.root / MECHANICS_DIR / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [] if problems(data) else list(data["trigger"])


# --- valutazione ----------------------------------------------------------------------------------

def _compare(actual: Any, op: str, wanted: Any) -> bool:
    if op == "contiene":
        if isinstance(actual, (list, tuple, set)):
            return any(str(x).strip().lower() == str(wanted).strip().lower() for x in actual)
        return str(wanted).lower() in str(actual).lower()
    if isinstance(actual, str) or isinstance(wanted, str):
        a, w = str(actual).strip().lower(), str(wanted).strip().lower()
        return a == w if op == "==" else a != w if op == "!=" else False
    if not _is_number(actual) or not _is_number(wanted):
        return False
    return {"==": actual == wanted, "!=": actual != wanted, "<": actual < wanted, "<=": actual <= wanted,
            ">": actual > wanted, ">=": actual >= wanted}[op]


def roll_outcome(spoken: str) -> str | None:
    """Il nome della fascia in un messaggio `[Tiro] ...`, o None se il turno non e' un esito."""
    if not str(spoken).startswith("[Tiro]"):
        return None
    found = _ROLL_OUTCOME.search(spoken)
    return found.group(1).strip().lower() if found else None


class _Fatti:
    """Lettura pigra dei fatti del programma: scheda, orologi, relazioni, esito, contatori."""

    def __init__(self, campaign: Campaign, spoken: str):
        self.campaign = campaign
        self.spoken = spoken
        self._sheet: Any = None

    def sheet(self) -> Any:
        if self._sheet is None:
            from .sheet import Sheet

            pg = self.campaign.active_pg
            self._sheet = Sheet(self.campaign, pg) if pg else False
        return self._sheet or None

    def holds(self, c: dict[str, Any]) -> bool:
        op, wanted = c.get("op"), c.get("valore")
        if "scheda" in c:
            sheet = self.sheet()
            if sheet is None or not sheet.exists:
                return False
            try:
                return _compare(sheet.get(c["scheda"]), op, wanted)
            except ValueError:
                return False
        if "orologio" in c:
            clock = next((v for k, v in self.campaign.clocks().items() if k.lower() == c["orologio"].strip().lower()), None)
            return clock is not None and _compare(clock.get("pieni", 0), op, wanted)
        if "relazione" in c:
            value = self.campaign.relation(c["relazione"])
            return value is not None and _compare(value, op, wanted)
        if "stadio" in c:
            from . import relazioni

            value = self.campaign.relation(c["stadio"])
            return value is not None and _compare(relazioni.stadio(value, relazioni.carica(self.campaign)), op, wanted)
        if "esito" in c:
            outcome = roll_outcome(self.spoken)
            return outcome is not None and _compare(outcome, op, wanted)
        if "tabella" in c:
            from . import tabelle

            wanted_table = str(c["tabella"]).strip().lower()
            return any(name.lower() == wanted_table and _compare(result, op, wanted) for name, result in tabelle.outcomes(self.spoken))
        if "contatore" in c:
            which = str(c["contatore"]).strip().lower()
            value = self.campaign.played_turns() if which == "turni" else self.campaign.session_number
            if op == "ogni":  # "ogni 3 turni": al terzo, al sesto...; mai al turno zero
                return _is_int(wanted) and wanted >= 1 and value > 0 and value % wanted == 0
            return _compare(value, op, wanted)
        return False


def valuta(campaign: Campaign, spoken: str = "") -> list[str]:
    """Valuta tutte le regole, applica gli effetti, segna le non ricorrenti. Ritorna gli avvisi per il narratore."""
    rules = carica(campaign)
    if not rules:
        return []
    fired = campaign.triggers_fired()
    facts = _Fatti(campaign, spoken)
    notes: list[str] = []
    newly: list[str] = []
    for rule in rules:
        name = str(rule["nome"]).strip()
        mode = rule.get("ricorrente")
        # la chiave con cui la regola si segna come scattata: il nome (una volta sola) o nome@sessione (una per sessione)
        key = None if mode is True else name.lower() if not mode else f"{name.lower()}@{campaign.session_number}"
        if key is not None and key in fired:
            continue
        if not all(facts.holds(c) for c in rule["quando"]):
            continue
        for effect in rule["effetti"]:
            notes += _apply(campaign, name, effect)
        if key is not None:
            newly.append(key)
        log.info("trigger '%s' scattato", name)
    if newly:
        campaign.mark_triggers_fired(newly)
    return notes


def _apply(campaign: Campaign, name: str, effect: dict[str, Any]) -> list[str]:
    if "avviso" in effect:
        return [f"[TRIGGER {name}: {str(effect['avviso']).strip()}]"]
    if "orologio" in effect:
        clock_name = str(effect["orologio"]).strip()
        exists = any(k.lower() == clock_name.lower() for k in campaign.clocks())
        if not exists:
            if "segmenti" not in effect:
                log.warning("trigger '%s': orologio '%s' inesistente e senza `segmenti`: nulla da fare", name, clock_name)
                return []
            campaign.set_clock(clock_name, int(effect["segmenti"]), str(effect.get("conseguenza") or ""))
        campaign.tick_clock(clock_name, int(effect["avanza"]))
        return []
    if "relazione" in effect:
        campaign.shift_relation(str(effect["relazione"]), str(effect["verso"]), str(effect["forza"]), f"trigger: {name}")
        return []
    if "tabella" in effect:
        from . import tabelle

        sheet = None
        if campaign.active_pg:
            from .sheet import Sheet

            try:
                sheet = Sheet(campaign, campaign.active_pg)
            except ValueError:
                sheet = None
        try:
            rolled = tabelle.tira(str(effect["tabella"]), tabelle.carica(campaign), sheet)
        except ValueError as exc:  # la tabella non c'e' (la verifica lo dice): la regola scatta senza riga
            log.warning("trigger '%s': %s", name, exc)
            return []
        rows = [rolled] + rolled.get("chain", [])
        return [f"[TRIGGER {name}: tabella {step['name']} -> {step['result']}]" for step in rows]
    return []


def table_name_problems(data: Any, tables: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Gli effetti `tabella` che nominano una tabella che la campagna non ha: scatterebbero a vuoto."""
    out = []
    known = {str(t.get("nome", "")).strip().lower() for t in tables}
    if not isinstance(data, dict) or not isinstance(data.get("trigger"), list):
        return out
    for i, t in enumerate(data["trigger"]):
        for j, e in enumerate(t.get("effetti") or [] if isinstance(t, dict) else []):
            if isinstance(e, dict) and isinstance(e.get("tabella"), str) and e["tabella"].strip().lower() not in known:
                out.append((f"trigger[{i}].effetti[{j}].tabella", f"'{e['tabella']}' non e' una tabella di meccanica/tabelle.json: la regola scatterebbe senza riga"))
    return out


# --- dal manuale ai trigger: una chiamata, poi il codice controlla ---------------------------------

# una parola di soglia vicina a un numero: "scende sotto 5", "raggiunge 4", "a zero"; "se" e "quando" da soli non bastano
_STRONG = re.compile(r"\b(soglia|soglie|raggiung\w*|scend\w*|sale|salgono|salire|super\w*|almeno|oltre|sotto|a zero|a 0|pari a|"
                     r"uguale a|maggiore|minore|inferiore|superiore|ogni volta che|ogni turno|ogni sessione|si riempie|pieno|piena)\b", re.I)
_NUMBER = re.compile(r"\b\d+\b")
NEAR_CHARS = 60
MIN_SCORE = 2
MAX_PATHS = 80
SOURCE_LIMIT = 36_000  # ~10k token: su un manuale vero di ~100 sezioni ci stanno le sei a soglia piu' dense


def sheet_paths(data: Any, prefix: str = "") -> list[str]:
    """I percorsi della scheda che una condizione puo' leggere: solo le foglie numeriche o testuali."""
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "esperienza":
                continue
            out += sheet_paths(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(data, (int, float, str)) and not isinstance(data, bool) and prefix:
        out.append(prefix)
    return out[:MAX_PATHS]


def threshold_score(body: str) -> int:
    """Quante parole di soglia stanno a un passo da un numero: la misura di quanto una sezione 'conta'."""
    return sum(1 for m in _STRONG.finditer(body) if _NUMBER.search(body[max(0, m.start() - NEAR_CHARS): m.end() + NEAR_CHARS]))


def threshold_sections(campaign: Campaign) -> list[tuple[int, str, str, str]]:
    """(punteggio, file, titolo, corpo) delle sezioni di `meccanica/` con soglie, dalla piu' densa, entro il tetto.

    Misurato su un manuale vero di ~100 sezioni (2026-10-21): per ordine dei file il tetto si riempiva di creature e
    opzioni di creazione senza arrivare alle risorse con soglie; per punteggio le prime sono proprio quelle."""
    scored = []
    for f in campaign.files():
        if not f.name.startswith(MECHANICS_DIR + "/"):
            continue
        for title, body in f.sections().items():
            if title:
                score = threshold_score(body)
                if score >= MIN_SCORE:
                    scored.append((score, f.name, title, body.strip()))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    out, size = [], 0
    for row in scored:
        if size + len(row[3]) > SOURCE_LIMIT:
            continue
        out.append(row)
        size += len(row[3])
    return out


def rules_with_thresholds(campaign: Campaign) -> str:
    """Il testo che la chiamata legge: le sezioni con soglie, dalla piu' densa."""
    return "\n\n".join(f"## {title} ({name})\n{body}" for _, name, title, body in threshold_sections(campaign))


_CONDITION = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": list(CONDIZIONI), "description": "; ".join(f"{k}: {v}" for k, v in CONDIZIONI.items())},
        "soggetto": {"type": "string", "description": "Il percorso della scheda (fra quelli dati, esatto), il nome dell'orologio o del PNG, la fascia dell'esito, `turni` o `sessione`"},
        "op": {"type": "string", "enum": list(OPS)},
        "valore": {"type": "string", "description": "Il valore con cui confrontare: un numero scritto in cifre, o un nome"},
    },
    "required": ["tipo", "soggetto", "op", "valore"],
    "additionalProperties": False,
}
_EFFECT = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": list(EFFETTI), "description": "; ".join(f"{k}: {v}" for k, v in EFFETTI.items())},
        "testo": {"type": "string", "description": "L'avviso al narratore (per `avviso`), il nome dell'orologio o del PNG"},
        "avanza": {"type": ["integer", "null"], "description": "Solo per `orologio`: i segmenti che avanza"},
        "segmenti": {"type": ["integer", "null"], "description": "Solo per `orologio` da creare: 2..12"},
        "conseguenza": {"type": ["string", "null"], "description": "Solo per `orologio` da creare: cosa succede quando si riempie"},
        "verso": {"type": ["string", "null"], "enum": ["sale", "scende", None], "description": "Solo per `relazione`"},
        "forza": {"type": ["string", "null"], "enum": ["lieve", "media", "forte", None], "description": "Solo per `relazione`"},
    },
    "required": ["tipo", "testo", "avanza", "segmenti", "conseguenza", "verso", "forza"],
    "additionalProperties": False,
}
TRIGGERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "trigger": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "Breve, minuscolo, unico"},
                    "ricorrente": {"type": ["boolean", "string"], "enum": [True, False, "sessione"],
                                   "description": "true se vale ogni turno in cui la condizione regge; false se scatta una volta sola; \"sessione\" se una volta per sessione di gioco"},
                    "quando": {"type": "array", "items": _CONDITION, "description": f"Da 1 a {MAX_CONDITIONS} condizioni, tutte vere insieme"},
                    "effetti": {"type": "array", "items": _EFFECT, "description": f"Da 1 a {MAX_EFFECTS} effetti"},
                    "fonte": {"type": "string", "description": "La sezione da cui viene"},
                },
                "required": ["nome", "ricorrente", "quando", "effetti", "fonte"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["trigger"],
    "additionalProperties": False,
}
TRIGGERS_PROMPT = """\
Trascrivi come TRIGGER le regole di un gioco di ruolo che il programma puo' contare da solo: una SOGLIA su un valore
che il programma possiede (un numero della scheda, fra i percorsi dati; un orologio; la relazione o lo stadio di un PNG;
la fascia dell'ultimo tiro; la riga uscita da una tabella che il programma tira; i turni giocati) e una CONSEGUENZA chiusa (un avviso al master con la regola da applicare,
un orologio che avanza, una relazione che si muove, una tabella da tirare fra quelle che il programma tira). Esempi: "con lo stress a 4 o piu' ogni tentazione richiede una
prova" -> scheda stato.stress >= 4, avviso ricorrente; "a 0 punti ferita il personaggio cade" -> avviso.
Il tempo: "all'inizio di ogni sessione" -> ricorrente "sessione" con contatore sessione >= 1; "ogni 3 turni" -> contatore
turni op "ogni" valore 3.
Solo cio' che le sezioni dicono, con i numeri del manuale. Usa SOLO percorsi della scheda fra quelli elencati: se la
regola parla di un valore che la scheda non ha, lasciala fuori. L'avviso cita la regola in una frase, non la narra.
Lascia fuori cio' che richiede un giudizio ("se il personaggio e' in pericolo") o un tiro: quello lo fa il master."""


def _number_or_text(value: Any) -> Any:
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _to_rule(t: dict[str, Any]) -> dict[str, Any]:
    """Dalla forma piatta dello schema alla forma del file (una chiave per condizione ed effetto)."""
    conds = []
    for c in t.get("quando") or []:
        if isinstance(c, dict):
            conds.append({str(c.get("tipo")): str(c.get("soggetto", "")).strip(), "op": c.get("op"), "valore": _number_or_text(c.get("valore"))})
    effects = []
    for e in t.get("effetti") or []:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("tipo"))
        out: dict[str, Any] = {kind: str(e.get("testo", "")).strip()}
        if kind == "orologio":
            out["avanza"] = e.get("avanza") if e.get("avanza") is not None else 1
            if e.get("segmenti") is not None:
                out["segmenti"] = e["segmenti"]
                out["conseguenza"] = str(e.get("conseguenza") or "").strip()
        if kind == "relazione":
            out["verso"], out["forza"] = e.get("verso"), e.get("forza")
        if kind == "tabella":
            out[kind] = str(e.get("testo", "")).strip()  # il nome della tabella da tirare
        effects.append(out)
    mode = t.get("ricorrente")
    rule = {"nome": str(t.get("nome", "")).strip().lower(), "ricorrente": "sessione" if mode == "sessione" else bool(mode), "quando": conds, "effetti": effects}
    if t.get("fonte"):
        rule["fonte"] = str(t["fonte"]).strip()
    return rule


def extract_triggers(campaign: Campaign, client: Any, model: str, sheet: Any = None) -> list[dict[str, Any]]:
    """Una chiamata sulle sezioni con soglie; tiene solo le regole che passano `problems` e i percorsi della scheda.

    Le regole scritte a mano in `trigger.json` restano: le nuove si aggiungono se il nome e' libero."""
    rules = rules_with_thresholds(campaign)
    if not rules:
        log.info("trigger: nessuna sezione con soglie nelle regole")
        return []
    from .models import cost_usd
    from .prepare import create_message, parse_json_object

    paths = sheet_paths(dict(getattr(sheet, "data", {}) or {})) if sheet is not None else []
    head = ("PERCORSI DELLA SCHEDA (gli unici che una condizione `scheda` puo' leggere): " + ", ".join(paths)
            if paths else "La campagna non ha ancora una scheda: non usare condizioni `scheda`.")
    from . import tabelle

    clocks = ", ".join(campaign.clocks()) or "nessuno"
    tables = ", ".join(str(t.get("nome")) for t in tabelle.carica(campaign)) or "nessuna"
    response = create_message(client, max_seconds=300, model=model, max_tokens=8000, system=TRIGGERS_PROMPT,
                              messages=[{"role": "user", "content": f"{head}\nOROLOGI ATTIVI: {clocks}\nTABELLE CHE IL PROGRAMMA TIRA: {tables}\n\nREGOLE:\n\n{rules}"}],
                              output_config={"format": {"type": "json_schema", "schema": TRIGGERS_SCHEMA}})
    usage = getattr(response, "usage", None)
    campaign.record_spend("trigger", model, cost_usd(model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0))
    data = parse_json_object("\n".join(b.text for b in response.content if b.type == "text"))
    found = data.get("trigger") if isinstance(data, dict) else None
    existing = carica(campaign)
    kept = list(existing)
    names = {str(r.get("nome", "")).lower() for r in existing}
    for raw in found if isinstance(found, list) else []:
        if not isinstance(raw, dict):
            continue
        rule = _to_rule(raw)
        if rule["nome"] in names:
            continue
        probe = {"trigger": [rule]}
        bad = problems(probe)
        if sheet is not None:
            bad += sheet_path_problems(probe, sheet)
        elif any("scheda" in c for c in rule["quando"]):
            bad.append(("quando", "senza scheda attiva una condizione `scheda` non si verifica"))
        if bad or len(kept) >= MAX_TRIGGERS:
            log.warning("trigger scartato '%s': %s", rule["nome"], "; ".join(f"{p}: {m}" for p, m in bad[:3]) or "troppe regole")
            continue
        kept.append(rule)
        names.add(rule["nome"])
    if not kept and not existing:
        log.info("trigger: nessuna regola valida nelle soglie trovate")
        return []
    path = campaign.root / MECHANICS_DIR / FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trigger": kept}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log.info("trigger salvati: %s", ", ".join(r["nome"] for r in kept) or "nessuno")
    return kept[len(existing):]
