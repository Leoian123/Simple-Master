"""La scheda del personaggio come dati: `schede/<slug>.json`.

Il JSON e' l'unica fonte dei dati della scheda. Il master legge i valori direttamente
("stato.fame" e' 2, non una frase da interpretare) e li cambia per percorso, senza dover
ritrovare e riscrivere frasi in un Markdown. `schede/<slug>.md` resta, ma e' solo la vista
leggibile, rigenerata a ogni salvataggio: non si modifica a mano.

L'esperienza non e' un numero da ricordare: e' un registro di movimenti (`esperienza.registro`)
scritto solo da `award` e `spend`. Guadagnati, spesi e disponibili si calcolano da li', ogni volta.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any

from .campaign import SHEETS_DIR, Campaign

XP_KEY = "esperienza"
# Le strade per cui i punti esperienza entrano in scheda. Ognuna lascia una riga nel registro.
XP_ROUTES = {
    "creazione": "punti concessi dalle regole di creazione e non ancora spesi",
    "sessione": "chiusura di una sessione di gioco, secondo le regole del manuale (una volta per sessione)",
    "storia": "conclusione di una storia o di un arco narrativo",
    "regola": "una regola del manuale che concede punti in gioco (citala nel motivo)",
    "narratore": "premio eccezionale del narratore, fuori dalle regole (raro)",
}
ONCE_PER_SESSION = {"sessione"}


def slugify(name: str) -> str:
    return re.sub(r"[^\w-]+", "-", name.strip().lower()).strip("-")


# le chiavi sono senza accenti per essere percorsi sicuri; nella vista tornano parole
_ACCENTS = {"identita": "identità", "abilita": "abilità", "eta": "età", "umanita": "umanità", "volonta": "volontà",
            "specialita": "specialità", "gravita": "gravità", "agilita": "agilità", "affinita": "affinità",
            "quantita": "quantità", "velocita": "velocità", "difficolta": "difficoltà", "qualita": "qualità"}
_LONG_TEXT = {"storia", "background", "note", "descrizione"}
_PLAIN_FIELDS = {"nota", "note", "regola", "rapporto", "descrizione"}


def label(key: str) -> str:
    text = " ".join(_ACCENTS.get(w, w) for w in str(key).replace("_", " ").split())
    return text[:1].upper() + text[1:]


class Sheet:
    """Dati di una scheda, con modifica per percorso e registro dell'esperienza."""

    def __init__(self, campaign: Campaign, slug: str):
        self.campaign = campaign
        self.slug = slugify(slug)
        if not self.slug:
            raise ValueError("Nome del personaggio vuoto")
        self.path: Path = campaign.root / SHEETS_DIR / f"{self.slug}.json"
        self.view_name = f"{SHEETS_DIR}/{self.slug}.md"
        self.data: dict[str, Any] = {}
        if self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data = loaded if isinstance(loaded, dict) else {}
            except ValueError as exc:
                raise ValueError(f"{self.path.name} non e' JSON valido: {exc}") from exc

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def name(self) -> str:
        return str(self.data.get("nome") or self.slug.replace("-", " ").title())

    # --- percorsi: "stato.salute.attuale", "equipaggiamento.2" -----------------------------

    @staticmethod
    def _parts(path: str) -> list[str]:
        parts = [p.strip() for p in str(path).split(".") if p.strip()]
        if not parts:
            raise ValueError("Percorso vuoto")
        return parts

    def get(self, path: str) -> Any:
        node: Any = self.data
        for p in self._parts(path):
            if isinstance(node, dict) and p in node:
                node = node[p]
            elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
                node = node[int(p)]
            else:
                raise ValueError(f"'{path}' non esiste nella scheda. {self._hint(path)}")
        return node

    def _hint(self, path: str) -> str:
        parts = self._parts(path)
        node: Any = self.data
        for p in parts[:-1]:
            node = node.get(p) if isinstance(node, dict) else None
        keys = list(node) if isinstance(node, dict) else list(self.data)
        return f"Chiavi disponibili li': {keys[:30]}"

    def set(self, path: str, value: Any) -> Any:
        """Imposta un valore; crea i livelli intermedi. `value` None cancella. Ritorna il valore precedente."""
        parts = self._parts(path)
        if parts[0] == XP_KEY:
            raise ValueError("L'esperienza non si scrive a mano: usa award_xp e spend_xp, il conto lo tiene il registro")
        node: Any = self.data
        for p in parts[:-1]:
            if isinstance(node, list):
                if not (p.isdigit() and int(p) < len(node)):
                    raise ValueError(f"Indice '{p}' fuori dall'elenco in '{path}'")
                node = node[int(p)]
            else:
                node = node.setdefault(p, {})
            if not isinstance(node, (dict, list)):
                raise ValueError(f"'{p}' in '{path}' e' un valore, non un gruppo: imposta il gruppo intero")
        last = parts[-1]
        if isinstance(node, list):
            if last == "+" or (last.isdigit() and int(last) == len(node)):
                node.append(value)
                return None
            if not (last.isdigit() and int(last) < len(node)):
                raise ValueError(f"Indice '{last}' fuori dall'elenco in '{path}' (usa '+' per aggiungere)")
            old = node[int(last)]
            if value is None:
                del node[int(last)]
            else:
                node[int(last)] = value
            return old
        old = node.get(last)
        if value is None:
            node.pop(last, None)
        else:
            node[last] = value
        return old

    def apply(self, changes: list[dict[str, Any]]) -> list[str]:
        """Applica piu' modifiche {path, value}; o tutte o nessuna."""
        if not isinstance(changes, list) or not changes:
            raise ValueError("Indica almeno una modifica {path, value}")
        backup = json.loads(json.dumps(self.data))
        done = []
        try:
            for c in changes:
                if not isinstance(c, dict) or "path" not in c:
                    raise ValueError(f"Modifica non valida: {c!r}")
                old = self.set(c["path"], c.get("value"))
                done.append(f"{c['path']}: {json.dumps(old, ensure_ascii=False)} -> {json.dumps(c.get('value'), ensure_ascii=False)}")
        except ValueError:
            self.data = backup
            raise
        return done

    # --- esperienza: un registro, non un numero da ricordare -------------------------------

    def _ledger(self) -> list[dict[str, Any]]:
        xp = self.data.get(XP_KEY)
        rows = xp.get("registro") if isinstance(xp, dict) else None
        return rows if isinstance(rows, list) else []

    def xp(self) -> dict[str, int]:
        rows = self._ledger()
        earned = sum(int(r.get("punti", 0)) for r in rows if int(r.get("punti", 0)) > 0)
        spent = -sum(int(r.get("punti", 0)) for r in rows if int(r.get("punti", 0)) < 0)
        return {"guadagnati": earned, "spesi": spent, "disponibili": earned - spent}

    def _record(self, points: int, route: str, reason: str) -> None:
        reason = str(reason).strip()
        if not reason:
            raise ValueError("Serve il motivo: finisce nel registro dell'esperienza")
        xp = self.data.get(XP_KEY)
        if not isinstance(xp, dict):
            xp = self.data[XP_KEY] = {}
        if not isinstance(xp.get("registro"), list):
            xp["registro"] = []
        xp["registro"].append({"quando": _dt.datetime.now().isoformat(timespec="minutes"),
                               "sessione": self.campaign.session_number, "via": route, "punti": points, "motivo": reason})

    def award(self, amount: int, route: str, reason: str) -> dict[str, int]:
        amount = self._positive(amount)
        if route not in XP_ROUTES:
            raise ValueError(f"Via sconosciuta '{route}'. Vie ammesse: {', '.join(XP_ROUTES)}")
        session = self.campaign.session_number
        if route in ONCE_PER_SESSION and any(r.get("via") == route and r.get("sessione") == session for r in self._ledger()):
            raise ValueError(f"I punti per la via '{route}' della sessione {session} sono gia' stati assegnati: non si assegnano due volte")
        self._record(amount, route, reason)
        return self.xp()

    def spend(self, amount: int, reason: str, changes: list[dict[str, Any]]) -> tuple[dict[str, int], list[str]]:
        amount = self._positive(amount)
        available = self.xp()["disponibili"]
        if amount > available:
            raise ValueError(f"Punti insufficienti: ne servono {amount}, disponibili {available}")
        done = self.apply(changes)  # la spesa e il tratto che cresce: insieme o niente
        self._record(-amount, "spesa", reason)
        return self.xp(), done

    @staticmethod
    def _positive(amount: Any) -> int:
        try:
            value = int(amount)
        except (TypeError, ValueError):
            raise ValueError(f"Quantita' non valida: {amount!r}") from None
        if value <= 0:
            raise ValueError("La quantita' dev'essere un intero positivo")
        return value

    # --- salvataggio e viste ----------------------------------------------------------------

    def replace(self, data: dict[str, Any], name: str | None = None) -> None:
        """Sostituisce i dati (nuova scheda o scheda rifatta) conservando il registro dell'esperienza."""
        if not isinstance(data, dict) or not data:
            raise ValueError("`data` dev'essere un oggetto JSON con i dati della scheda")
        ledger = self._ledger()
        self.data = {k: v for k, v in data.items() if k != XP_KEY}
        if name:
            self.data = {"nome": name.strip(), **{k: v for k, v in self.data.items() if k != "nome"}}
        if ledger:
            self.data[XP_KEY] = {"registro": ledger}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        self.campaign.file(self.view_name).write(self.markdown())

    def prompt_data(self, last_rows: int = 5) -> dict[str, Any]:
        """I dati per il prompt: l'esperienza con i totali gia' calcolati e solo gli ultimi movimenti."""
        data = {k: v for k, v in self.data.items() if k != XP_KEY}
        data[XP_KEY] = {**self.xp(), "ultimi_movimenti": self._ledger()[-last_rows:]}
        return data

    def prompt_json(self) -> str:
        return json.dumps(self.prompt_data(), ensure_ascii=False, separators=(",", ":"))

    # La scheda ha due velocita'. Tratti, identita', storia cambiano di rado: stanno nel prompt di
    # sistema, in cache (letti a un decimo del prezzo). Lo stato e l'esperienza cambiano quasi a
    # ogni turno: viaggiano nel contesto del turno. Misurato il 2026-09-18: la scheda intera erano
    # ~2.200 token a prezzo pieno a ogni turno, e lo stato ne e' una piccola parte.
    VOLATILE = ("stato", XP_KEY)

    def stable_json(self) -> str:
        data = {k: v for k, v in self.data.items() if k not in self.VOLATILE}
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))  # ordine di inserimento: stabile, la cache regge

    def volatile_json(self) -> str:
        data = {k: v for k, v in self.prompt_data().items() if k in self.VOLATILE}
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    def working_json(self) -> str:
        """Per lo scriba: cio' che di solito si aggiorna, per intero; del resto solo i nomi dei gruppi."""
        data = self.prompt_data()
        often = {k: v for k, v in data.items() if k in self.VOLATILE or k == "equipaggiamento"}
        often["altri_gruppi_della_scheda"] = [k for k in data if k not in often]
        return json.dumps(often, ensure_ascii=False, separators=(",", ":"))

    def markdown(self) -> str:
        """La vista leggibile: una sezione `##` per ogni chiave di primo livello."""
        lines = [f"# {self.name}", "", f"*Vista generata da `{SHEETS_DIR}/{self.slug}.json`: le modifiche si fanno li'.*", ""]
        scalars = [(k, v) for k, v in self.data.items() if k != "nome" and not isinstance(v, (dict, list))
                   and not (isinstance(v, str) and (len(v) > 120 or k in _LONG_TEXT))]
        if scalars:
            lines += [f"- **{label(k)}:** {_scalar(v)}" for k, v in scalars] + [""]
        for key, value in self.data.items():
            if key in ("nome", XP_KEY) or (key, value) in scalars:
                continue
            lines += [f"## {label(key)}", ""]
            lines += [str(value).strip()] if isinstance(value, str) else _render(value, 0)
            lines.append("")
        xp = self.xp()
        lines += ["## Esperienza", "",
                  f"- **Disponibili:** {xp['disponibili']} (guadagnati {xp['guadagnati']}, spesi {xp['spesi']})"]
        for r in self._ledger():
            lines.append(f"- {str(r.get('quando', ''))[:10]} · sessione {r.get('sessione', '?')} · {r.get('via', '')} · "
                         f"{int(r.get('punti', 0)):+d} — {r.get('motivo', '')}")
        return "\n".join(lines).rstrip("\n") + "\n"


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "si'" if value else "no"
    return str(value)


def _is_flat(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and all(not isinstance(v, (dict, list)) for v in value.values())


def _inline(value: dict[str, Any]) -> str:
    # {"attuale": 9, "massimo": 9} si legge meglio come 9 / 9
    if {"attuale", "massimo"} <= set(value):
        rest = ", ".join(f"{label(k).lower()} {_scalar(v)}" for k, v in value.items() if k not in ("attuale", "massimo"))
        return f"{value['attuale']} / {value['massimo']}" + (f" ({rest})" if rest else "")
    if "nome" in value:
        # {"nome": "Risorse", "valore": 3, "nota": "..."} -> Risorse 3 — ...
        head = str(value["nome"]) + "".join(f" {_scalar(value[k])}" for k in ("valore", "livello") if k in value)
        tail = [(_scalar(v) if k in _PLAIN_FIELDS else f"{label(k).lower()} {_scalar(v)}")
                for k, v in value.items() if k not in ("nome", "valore", "livello")]
        return " — ".join([head, *tail])
    return ", ".join(f"{label(k)} {_scalar(v)}" for k, v in value.items())


def _render(value: Any, depth: int) -> list[str]:
    pad = "  " * depth
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if _is_flat(v) and (len(_inline(v)) <= 110 or "nome" in v):
                out.append(f"{pad}- **{label(k)}:** {_inline(v)}")
            elif isinstance(v, (dict, list)) and v:
                out.append(f"{pad}- **{label(k)}:**")
                out += _render(v, depth + 1)
            else:
                out.append(f"{pad}- **{label(k)}:** {_scalar(v) if not isinstance(v, (dict, list)) else 'nessuna voce'}")
    elif isinstance(value, list):
        for item in value:
            if _is_flat(item):
                out.append(f"{pad}- {_inline(item)}")
            elif isinstance(item, (dict, list)):
                out.append(f"{pad}-")
                out += _render(item, depth + 1)
            else:
                out.append(f"{pad}- {_scalar(item)}")
    else:
        out.append(f"{pad}{_scalar(value)}")
    return out
