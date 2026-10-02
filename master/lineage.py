"""Paternita' del dato: da dove viene ogni sezione, ogni voce di glossario, il profilo dei dadi.

Una campagna costruita da piu' manuali non puo' accodare i testi nell'ordine in cui arrivano:
il risultato dipenderebbe dall'ordine di preparazione e nessuno saprebbe piu' quale frase viene
da quale libro. Qui ogni manuale ha il suo spazio di lavoro e la campagna si compone da quelli:

    preparazione/fonti/<manuale>/    le sezioni di UN manuale, fuse e ripulite tra loro, intatte
    preparazione/fonti/_campagna/    il testo che la preparazione non ha scritto: quello che c'era
                                     gia', o che e' cambiato in gioco o a mano
    preparazione/paternita.json      il registro: per ogni dato, i genitori e l'impronta del testo

La sezione che il master legge e' sempre la composizione delle sue parti in ordine canonico (prima
il testo della campagna, poi i manuali dal piu' vecchio al piu' recente), quindi preparare base ed
espansione in un ordine o nell'altro da' lo stesso risultato. Le modifiche fatte fuori dalla
preparazione si riconoscono dall'impronta: il testo cambiato diventa esso stesso una parte, e la
preparazione non lo riscrive mai.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .campaign import PREP_DIR, Campaign
from .glossary import GlossaryEntry

log = logging.getLogger("master.paternita")

SCHEMA = 1
REGISTRY_NAME = "paternita.json"
SOURCES_DIR = "fonti"
KEPT = "_campagna"  # spazio del testo non scritto dalla preparazione
CAMPAIGN_ORIGIN = "campagna"
SOURCE_INDEX_PREFIX = "fonte-"
KEY_STEM_AT = 17    # una chiave di manuale e' "<16 esadecimali>-<nome>": qui comincia il nome
KEY_STEM_MAX = 40   # ...troncato a questa lunghezza (vedi prepare.source_key)


def fingerprint(text: str) -> str:
    """Impronta di un testo: basta a dire se e' cambiato, non a ricostruirlo."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


@dataclass
class ComposeReport:
    created: list[str] = field(default_factory=list)   # "file § titolo"
    updated: list[str] = field(default_factory=list)
    kept_outside: list[str] = field(default_factory=list)  # testo non della preparazione, conservato come parte
    glossary_written: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        line = f"composizione: {len(self.created)} sezioni nuove, {len(self.updated)} aggiornate, {self.glossary_written} voci di glossario"
        if self.kept_outside:
            line += f", {len(self.kept_outside)} con testo gia' presente o modificato fuori dalla preparazione (conservato)"
        return line


class Lineage:
    """Il registro della paternita' di una campagna e gli spazi di lavoro dei suoi manuali."""

    def __init__(self, campaign: Campaign):
        self.campaign = campaign
        self.path = campaign.root / PREP_DIR / REGISTRY_NAME
        self.sources = campaign.root / PREP_DIR / SOURCES_DIR
        self.data = self._load()

    # --- registro ----------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            if self.path.exists():
                log.warning("registro della paternita' illeggibile o di un'altra versione: si riparte (i testi restano)")
            data = {"schema": SCHEMA}
        for part in ("manuali", "sezioni", "glossario", "dadi"):
            data.setdefault(part, {})
        return data

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def picture(self) -> dict[str, Any]:
        """Il registro senza cio' che dipende dal momento della corsa: due preparazioni che
        portano allo stesso stato hanno la stessa fotografia, in qualunque ordine siano avvenute."""
        return json.loads(json.dumps({k: v for k, v in self.data.items() if k != "schema"}, sort_keys=True))

    # --- manuali -----------------------------------------------------------------------

    def manual_order(self) -> list[str]:
        """Ordine canonico: dal manuale piu' vecchio al piu' recente. Uno senza data conta come il
        piu' vecchio, cosi' non passa mai davanti a un altro senza che si sappia perche'."""
        manuals = self.data["manuali"]
        return sorted(manuals, key=lambda k: (manuals[k].get("anno") or 0, k))

    def _stage(self, key: str) -> Campaign | None:
        root = self.sources / key
        return Campaign(root) if root.is_dir() else None

    def open_manual(self, key: str, name: str, variant: str, meta: dict[str, Any] | None = None) -> Campaign:
        """Lo spazio di lavoro di un manuale. `variant` dice come e' stato estratto (versione dei
        prompt, modalita', taglio dei blocchi): se cambia, i blocchi sono altri e lo spazio si rifa'."""
        entry = self.data["manuali"].get(key)
        root = self.sources / key
        if entry is None or entry.get("variante") != variant or not root.is_dir():
            if root.exists():
                shutil.rmtree(root)
            if entry is not None:
                # il testo di questo manuale gia' dentro sezioni modificate non si puo' piu' confrontare
                # con il nuovo: resta com'e', e il manuale rifatto non vi si accoda una seconda volta
                for sections in self.data["sezioni"].values():
                    for datum in sections.values():
                        if key in datum.get("assorbiti", {}):
                            datum["assorbiti"][key] = None
                log.info("paternita': %s rifatto da capo (variante %s -> %s)", key, entry.get("variante"), variant)
            # un manuale adottato resta tale: il suo testo era nella campagna prima del registro
            entry = {"adottato": True, "origini": entry.get("origini", {})} if entry and entry.get("adottato") else {}
            self.data["manuali"][key] = entry
        entry.update({"nome": name, "variante": variant})
        for part in ("blocchi", "origini", "consolidate"):
            entry.setdefault(part, {})
        for item in ("titolo", "gioco", "edizione", "anno"):
            if meta and meta.get(item) is not None:
                entry[item] = meta[item]
        root.mkdir(parents=True, exist_ok=True)
        return Campaign(root)

    def other_edition(self, key: str, meta: dict[str, Any]) -> dict[str, Any] | None:
        """Il manuale gia' in campagna con cui `meta` non puo' convivere: stesso gioco, altra edizione.
        Vale solo quando entrambe le edizioni sono scritte nei volumi: nel dubbio non si rifiuta."""
        edition, game = meta.get("edizione"), _compact(str(meta.get("gioco") or ""))
        if type(edition) is not int or not game:
            return None
        for other, manual in self.data["manuali"].items():
            theirs = _compact(str(manual.get("gioco") or ""))
            if (other != key and type(manual.get("edizione")) is int and manual["edizione"] != edition
                    and theirs and (game in theirs or theirs in game)):
                return manual
        return None

    def take_block(self, key: str, block: str, pages: str, data: dict[str, Any]) -> bool:
        """Registra un blocco estratto. Ritorna False se questo stesso blocco e' gia' stato fuso
        nello spazio del manuale: rilanciare non accoda due volte, anche dopo un consolidamento."""
        from .sanitize import norm_title

        entry = self.data["manuali"][key]
        mark = fingerprint(json.dumps({k: data.get(k) for k in ("sezioni", "glossario")}, ensure_ascii=False, sort_keys=True))
        if entry["blocchi"].get(block) == mark:
            return False
        entry["blocchi"][block] = mark
        for sec in data.get("sezioni", []):
            title = str(sec.get("titolo", "")).strip().lstrip("#").strip()
            if title and sec.get("file"):
                pages_seen = entry["origini"].setdefault(str(sec["file"]), {}).setdefault(norm_title(title), [])
                if pages not in pages_seen:
                    pages_seen.append(pages)
        return True

    def needs_consolidation(self, key: str, stage: Campaign, targets: dict[str, list[str]]) -> dict[str, list[str]]:
        """Dei titoli da consolidare tiene quelli il cui testo e' cambiato dall'ultimo consolidamento
        riuscito: una sezione gia' ripulita e non piu' toccata non si rimanda al modello."""
        from .sanitize import norm_title

        done = self.data["manuali"][key].get("consolidate", {})
        out: dict[str, list[str]] = {}
        for name, lows in targets.items():
            sections = {t.lower(): b for t, b in stage.file(name).sections().items() if t} if stage.file(name).exists() else {}
            for low in lows:
                body = sections.get(low)
                if body is not None and done.get(name, {}).get(norm_title(low)) != fingerprint(body):
                    out.setdefault(name, []).append(low)
        return out

    def mark_consolidated(self, key: str, stage: Campaign, sections: Iterable[tuple[str, str]]) -> None:
        from .sanitize import norm_title

        done = self.data["manuali"][key].setdefault("consolidate", {})
        for name, title in sections:
            body = stage.file(name).get_section(title)
            if body is not None:
                done.setdefault(name, {})[norm_title(title)] = fingerprint(body)

    def adopt(self, cache_root: Path) -> list[str]:
        """Prima corsa su una campagna preparata quando la paternita' non c'era: i manuali gia' fusi
        si riconoscono dagli indici `fonte-*.md` e, se i loro blocchi sono ancora nella cache
        condivisa, da li' si ricava quali sezioni e quali pagine vengono da ciascuno. Nessuna chiamata."""
        from .sanitize import norm_title

        if self.exists or self.data["manuali"]:
            return []
        adopted: list[str] = []
        cached = [d for d in cache_root.iterdir() if d.is_dir() and re.match(r"^[0-9a-f]{16}-", d.name)] if cache_root.is_dir() else []
        for f in self.campaign.files():
            if "/" in f.name or not f.name.startswith(SOURCE_INDEX_PREFIX):
                continue
            slug = _compact(f.name[len(SOURCE_INDEX_PREFIX):-len(f.path.suffix)])
            matches = []
            for folder in cached:
                stem = folder.name[KEY_STEM_AT:]
                if _compact(stem) == slug or (len(stem) >= KEY_STEM_MAX and slug.startswith(_compact(stem))):
                    matches.append(folder)
            first = (f.read().splitlines() or [""])[0]
            for folder in matches:
                variants = sorted((v for v in folder.iterdir() if v.is_dir()), key=lambda v: len(list(v.glob("blocco_*.json"))))
                if not variants:
                    continue
                origins: dict[str, dict[str, list[str]]] = {}
                for block in sorted(variants[-1].glob("blocco_*_p*-*.json")):
                    try:
                        data = json.loads(block.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    pages = "pagine " + block.stem.rsplit("_p", 1)[1]
                    for sec in data.get("sezioni", []) if isinstance(data, dict) else []:
                        title = str(sec.get("titolo", "")).strip().lstrip("#").strip()
                        if title and sec.get("file"):
                            seen = origins.setdefault(str(sec["file"]), {}).setdefault(norm_title(title), [])
                            if pages not in seen:
                                seen.append(pages)
                self.data["manuali"][folder.name] = {"nome": first.split(":", 1)[-1].strip() or f.name, "adottato": True, "origini": origins}
                adopted.append(folder.name)
                log.info("paternita': adottato %s (%d sezioni riconosciute dalla cache)", folder.name, sum(len(v) for v in origins.values()))
        return adopted

    # --- composizione ------------------------------------------------------------------

    def _kept(self) -> Campaign:
        root = self.sources / KEPT
        root.mkdir(parents=True, exist_ok=True)
        return Campaign(root)

    def compose(self, targets: Iterable[str]) -> ComposeReport:
        """Riscrive nella campagna le sezioni che hanno parti nei manuali, componendole in ordine canonico."""
        from .sanitize import best_title, norm_text, norm_title

        report = ComposeReport()
        manuals = self.data["manuali"]
        stages = {key: stage for key in self.manual_order() if (stage := self._stage(key)) is not None}
        for name in targets:
            parts: dict[str, list[tuple[str, str, str]]] = {}
            for key, stage in stages.items():
                f = stage.file(name)
                if not f.exists():
                    continue
                for title, body in f.sections().items():
                    if title and body.strip():
                        parts.setdefault(norm_title(title), []).append((key, title, body.strip()))
            if not parts:
                continue
            target = self.campaign.file(name)
            if not target.exists():
                target.write(f"# {Path(name).stem.replace('-', ' ').capitalize()}\n")
            current: dict[str, tuple[str, str]] = {}
            for title, body in target.sections().items():
                if title:
                    current.setdefault(norm_title(title), (title, body.strip()))
            data = self.data["sezioni"].setdefault(name, {})
            for ntitle, found in parts.items():
                cur = current.get(ntitle)
                datum = data.get(ntitle)
                label = f"{name} § {cur[0] if cur else found[0][1]}"
                if cur is None and datum is not None and datum.get("impronta"):
                    # la sezione c'era ed e' stata tolta fuori dalla preparazione (fusa in un'altra, o cancellata):
                    # i manuali che gia' la componevano non la fanno rinascere
                    datum = {"titolo": datum.get("titolo", ""), "assorbiti": self._absorbed(datum, None), "rimossa": True}
                elif cur is not None and (datum is None or datum.get("impronta") != fingerprint(cur[1])):
                    # testo che la preparazione non ha scritto: diventa una parte, la prima, e non si tocca piu'
                    self._kept().file(name).set_section(cur[0], cur[1])
                    adopted = {k: None for k, m in manuals.items() if m.get("adottato") and ntitle in m.get("origini", {}).get(name, {})}
                    datum = {"titolo": cur[0], "campagna": fingerprint(cur[1]),
                             "assorbiti": self._absorbed(datum, adopted) if datum else adopted}
                    report.kept_outside.append(label)
                elif datum is None:
                    datum = {}
                absorbed: dict[str, str | None] = datum.get("assorbiti", {})
                pieces: list[str] = []
                parents: list[dict[str, Any]] = []
                if datum.get("campagna"):
                    kept_text = self._kept().file(name).get_section(datum["titolo"]) or ""
                    pieces.append(kept_text.strip())
                    parents.append({"origine": CAMPAIGN_ORIGIN, "impronta": datum["campagna"]})
                for key in sorted(set(absorbed) - {k for k, _, _ in found}):  # dentro il testo conservato, senza spazio di lavoro
                    parents.append({"manuale": key, "pagine": manuals.get(key, {}).get("origini", {}).get(name, {}).get(ntitle, []), "assorbito": True})
                for key, title, body in found:
                    pages = manuals[key].get("origini", {}).get(name, {}).get(ntitle, [])
                    mark = fingerprint(body)
                    if key in absorbed:
                        parents.append({"manuale": key, "pagine": pages, "assorbito": True})
                        if absorbed[key] is None or absorbed[key] == mark:
                            continue  # gia' dentro il testo conservato
                        body = self._delta(body, "\n\n".join(pieces), norm_text)  # il manuale e' cresciuto: solo il nuovo
                        if not body:
                            continue
                    elif body in "\n\n".join(pieces):
                        parents.append({"manuale": key, "impronta": mark, "pagine": pages, "ripresa": True})
                        continue  # ripresa parola per parola di cio' che c'e' gia'
                    else:
                        parents.append({"manuale": key, "impronta": mark, "pagine": pages})
                    pieces.append(body)
                text = "\n\n".join(p for p in pieces if p)
                if not text:
                    data[ntitle] = datum
                    continue
                title = cur[0] if cur else datum.get("titolo") or best_title([t for _, t, _ in found])
                if cur is None:
                    target.set_section(title, text)
                    report.created.append(label)
                elif cur[1] != text:
                    target.set_section(title, text)
                    report.updated.append(label)
                datum.pop("rimossa", None)
                datum.update({"titolo": title, "impronta": fingerprint(text), "genitori": parents, "atto": "composizione"})
                data[ntitle] = datum
        report.glossary_written = self._compose_glossary(stages)
        log.info("paternita': %s", report.summary())
        return report

    @staticmethod
    def _absorbed(datum: dict[str, Any], extra: dict[str, str | None] | None) -> dict[str, str | None]:
        """I manuali il cui testo sta gia' dentro una sezione che la preparazione non possiede piu'."""
        out: dict[str, str | None] = dict(datum.get("assorbiti", {}))
        for parent in datum.get("genitori", []):
            if parent.get("manuale") and not parent.get("assorbito"):
                out[parent["manuale"]] = parent.get("impronta")
        out.update(extra or {})
        return out

    @staticmethod
    def _delta(body: str, present: str, norm_text) -> str:
        """I paragrafi di `body` che non compaiono gia' in `present`."""
        have = norm_text(present)
        return "\n\n".join(b.strip() for b in re.split(r"\n\s*\n", body) if norm_text(b) and norm_text(b) not in have)

    @staticmethod
    def _entry_mark(entry: GlossaryEntry) -> str:
        return fingerprint(f"{entry.description}|{entry.file}")  # il tipo no: il riordino lo normalizza

    def _compose_glossary(self, stages: dict[str, Campaign]) -> int:
        """Una voce per termine: quella del manuale piu' vecchio che la definisce. Una voce che c'era
        gia', o che e' stata cambiata in gioco, resta com'e'."""
        winners: dict[str, tuple[str, GlossaryEntry]] = {}
        for key, stage in stages.items():
            for entry in stage.glossary.entries():
                winners.setdefault(entry.term.strip().lower(), (key, entry))
        glossary = self.campaign.glossary
        current = {e.term.strip().lower(): e for e in glossary.entries()}
        known = self.data["glossario"]
        written = 0
        for term, (key, entry) in winners.items():
            mark = self._entry_mark(entry)
            cur, datum = current.get(term), known.get(term)
            if cur is None:
                if datum and datum.get("impronta") == mark:
                    continue  # tolta dal riordino o a mano: la stessa voce non rientra da sola
                glossary.upsert(entry)
                written += 1
            elif datum and datum.get("manuale") and datum.get("impronta") == self._entry_mark(cur):
                if datum["impronta"] != mark:  # scritta dalla preparazione, e ora la definisce un manuale piu' vecchio
                    glossary.upsert(GlossaryEntry(cur.term, cur.kind or entry.kind, entry.description, entry.file))
                    written += 1
            else:
                known[term] = {"origine": CAMPAIGN_ORIGIN, "impronta": self._entry_mark(cur)}
                continue
            known[term] = {"manuale": key, "impronta": mark}
        return written

    # --- lettura -----------------------------------------------------------------------

    def parents(self, file: str, title: str) -> list[dict[str, Any]]:
        """I genitori di una sezione: manuali e pagine, o la campagna stessa se nessuno li conosce."""
        from .sanitize import norm_title

        ntitle = norm_title(title)
        datum = self.data["sezioni"].get(file, {}).get(ntitle)
        if datum and datum.get("genitori"):
            return datum["genitori"]
        adopted = [{"manuale": key, "pagine": m["origini"][file][ntitle], "assorbito": True}
                   for key, m in self.data["manuali"].items() if ntitle in m.get("origini", {}).get(file, {})]
        return adopted or [{"origine": CAMPAIGN_ORIGIN}]
