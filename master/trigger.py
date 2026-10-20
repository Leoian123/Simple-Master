"""I trigger: le regole del manuale che il programma puo' contare, scritte come dati.

`meccanica/trigger.json` e' un elenco di regole `condizioni -> effetti`. Le condizioni leggono
solo cio' che il programma possiede: un valore della scheda, un orologio, la relazione o lo
stadio di un PNG, l'esito dell'ultimo tiro, i contatori di turni e sessioni. Gli effetti sono
tipizzati: un avviso al narratore (`[TRIGGER <nome>: ...]` in testa al messaggio del turno),
un orologio che avanza (o nasce), una relazione che si muove di un passo. Niente testo libero
da giudicare, niente script, niente AI: a ogni turno, prima della chiamata del narratore, il
codice valuta tutte le regole (AND fra le condizioni), applica gli effetti e segna quelle non
ricorrenti come scattate (`stato.json` -> `trigger_scattati`). Cosi' "fame a 4 o piu': rischio
di frenesia" arriva al narratore dal contatore della scheda, non dalla sua memoria.

E' il linguaggio del World Engine di Voyage ridotto alla parte deterministica: condizioni
strutturali ed effetti chiusi, tetti bassi (cinque condizioni, tre effetti, cento regole),
un file che o e' valido tutto o si ignora, e la verifica che lo dice.

Esempio:

    {"trigger": [
      {"nome": "rischio di frenesia", "ricorrente": true,
       "quando": [{"scheda": "stato.fame", "op": ">=", "valore": 4}],
       "effetti": [{"avviso": "Fame a 4 o piu': a ogni tentazione serve una prova, secondo le regole"}]},
      {"nome": "la Bestia si avvicina",
       "quando": [{"scheda": "stato.umanita", "op": "<", "valore": 5}],
       "effetti": [{"orologio": "La Bestia", "avanza": 1, "segmenti": 6, "conseguenza": "Jonathan perde una notte"}]}
    ]}
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .campaign import MECHANICS_DIR, Campaign

log = logging.getLogger("master.trigger")

FILE_NAME = "trigger.json"
STATE_KEY = "trigger_scattati"
MAX_TRIGGERS, MAX_CONDITIONS, MAX_EFFECTS = 100, 5, 3
OPS = ("==", "!=", "<", "<=", ">", ">=", "contiene")
NUMERIC_OPS = ("<", "<=", ">", ">=")
CONDIZIONI: dict[str, str] = {
    "scheda": "un valore della scheda del personaggio attivo, per percorso (es. `stato.fame`); se il percorso non esiste la condizione e' falsa",
    "orologio": "i segmenti pieni di un orologio attivo, per nome; se l'orologio non esiste la condizione e' falsa",
    "relazione": "il numero della relazione di un PNG (-100..100); se non e' mai stata contata, falsa",
    "stadio": "lo stadio della relazione di un PNG, per nome (es. `Ostile`), confronto solo con `==` / `!=`",
    "esito": "il nome della fascia dell'ultimo tiro, solo nel turno in cui l'esito arriva (es. `fallito`), `==` / `!=`",
    "contatore": "`turni` (giocati dal personaggio) o `sessione` (in corso)",
}
EFFETTI: dict[str, str] = {
    "avviso": "una riga al narratore in testa al messaggio del turno: `[TRIGGER <nome>: avviso]`",
    "orologio": "fa avanzare l'orologio di `avanza` segmenti (anche negativi); se non esiste e c'e' `segmenti`, lo crea (con `conseguenza`)",
    "relazione": "muove la relazione del PNG di un passo: `verso` sale|scende, `forza` lieve|media|forte",
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
        if not name:
            out.append((f"{at}.nome", "manca il nome della regola"))
        elif name in names:
            out.append((f"{at}.nome", f"'{name}' e' gia' usato da un'altra regola"))
        names.add(name)
        if "ricorrente" in t and not isinstance(t["ricorrente"], bool):
            out.append((f"{at}.ricorrente", "true o false"))
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
    elif kind in ("stadio", "esito") and op not in ("==", "!="):
        out.append((".op", f"per `{kind}` solo == o !="))
    if "valore" not in c:
        out.append((".valore", "manca il valore con cui confrontare"))
    elif op in NUMERIC_OPS and not _is_number(c["valore"]):
        out.append((".valore", f"con `{op}` serve un numero"))
    elif kind in ("stadio", "esito") and not isinstance(c["valore"], str):
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
    if not isinstance(e[kind], str) or not e[kind].strip():
        out.append((f".{kind}", "un testo (l'avviso, o il nome)"))
    allowed = {"avviso": set(), "orologio": {"avanza", "segmenti", "conseguenza"}, "relazione": {"verso", "forza"}}[kind]
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
        if "contatore" in c:
            which = str(c["contatore"]).strip().lower()
            value = self.campaign.played_turns() if which == "turni" else self.campaign.session_number
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
        if not rule.get("ricorrente") and name.lower() in fired:
            continue
        if not all(facts.holds(c) for c in rule["quando"]):
            continue
        for effect in rule["effetti"]:
            notes += _apply(campaign, name, effect)
        if not rule.get("ricorrente"):
            newly.append(name.lower())
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
    return []
