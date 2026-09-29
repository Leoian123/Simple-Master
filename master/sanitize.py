"""Sanificazione: toglie l'informazione duplicata da una campagna.

Un manuale letto a blocchi lascia tre tipi di doppioni, misurati sul manuale di Vampiri:
sezioni sullo stesso soggetto con nomi varianti ("Tremere" / "Clan Tremere", quattro
sezioni sull'Inquisizione), voci di glossario equivalenti ("Fame" / "La Fame"), e a volte
blocchi di testo ripetuti. Il master paga ogni doppione due volte: in lettura e in confusione.

Passo gratuito (deterministico, nessuna chiamata):
  - sezioni dello stesso file con titolo uguale a meno di articolo, maiuscole, accenti e
    punteggiatura -> fuse sotto il titolo migliore;
  - blocchi di testo identici ripetuti nello stesso file -> ne resta il primo;
  - voci di glossario equivalenti -> una sola, con la descrizione piu' completa.

Passo approfondito (API, pochi centesimi per il riconoscimento):
  - per ogni file viaggia SOLO l'elenco dei titoli: il modello raggruppa quelli che
    trattano lo stesso soggetto e sceglie il titolo; la fusione e' fatta dal codice;
  - le sezioni fuse passano poi dal consolidamento per sezioni della preparazione
    (stessa cache per contenuto, stima del costo prima di partire).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .campaign import GLOSSARY_NAME, SHEETS_DIR, Campaign
from .files import CampaignFile
from .glossary import GlossaryEntry
from .logbook import short, usage_line
from .prepare import CACHE_ROOT, Preparer, PrepReport, create_message, parse_json_object

log = logging.getLogger("master.sanifica")

_ARTICLE = re.compile(r"^(il|lo|la|i|gli|le|l|un|uno|una|the)\s+")
MIN_BLOCK = 80  # sotto questa lunghezza un blocco ripetuto puo' essere legittimo (etichette, righe brevi)

GROUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gruppi": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titolo": {"type": "string", "description": "Il titolo da tenere: uno di quelli del gruppo"},
                    "unisci": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["titolo", "unisci"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["gruppi"],
    "additionalProperties": False,
}

GROUP_PROMPT = """\
Ricevi l'elenco dei titoli di sezione di un file di una campagna di gioco di ruolo,
costruito leggendo un manuale a blocchi. Alcuni titoli indicano LO STESSO soggetto con
nomi varianti, perche' vengono da blocchi diversi. Trova quei gruppi.

Raggruppa SOLO quando e' lo stesso soggetto:
- varianti di nome: "Tremere" e "Clan Tremere"; "Camarilla" e "La Camarilla";
  "Vili (senza clan)" e "I Vili (Senza Clan)"; stesse parole in ordine diverso.
NON raggruppare:
- un argomento generale con un suo sotto-argomento o un caso particolare
  ("Combattimento" con "Combattimento a distanza"; "Discipline" con "Disciplina: Potenza");
- una sezione composita con le sue parti ("La Jyhad e la Camarilla" con "Camarilla");
- soggetti solo imparentati ("Prima Inquisizione" con "Seconda Inquisizione").
Nel dubbio, non raggruppare: un doppione rimasto costa poco, una fusione sbagliata confonde.

Per ogni gruppo scegli in `titolo` il nome piu' riconoscibile e completo tra quelli del
gruppo, e metti in `unisci` TUTTI i titoli del gruppo (compreso quello scelto), scritti
esattamente come li hai ricevuti. Restituisci solo i gruppi con almeno due titoli.
"""


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def norm_title(title: str) -> str:
    n = norm_text(title)
    while _ARTICLE.match(n):
        n = _ARTICLE.sub("", n)
    return n


def best_title(titles: list[str]) -> str:
    """Tra titoli equivalenti: quello senza articolo iniziale, poi il piu' corto, poi il primo."""
    def key(t: str) -> tuple[int, int]:
        return (1 if _ARTICLE.match(norm_text(t) + " ") else 0, len(t))
    return sorted(titles, key=key)[0]


@dataclass
class SanitizeReport:
    merged_sections: list[str] = field(default_factory=list)  # "file: A + B -> A"
    removed_blocks: int = 0
    removed_chars: int = 0
    merged_glossary: list[str] = field(default_factory=list)
    deep_groups: list[str] = field(default_factory=list)
    touched: dict[str, list[str]] = field(default_factory=dict)  # file -> titoli fusi (da consolidare)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Sezioni fuse per titolo equivalente: {len(self.merged_sections)}",
            *(f"  {m}" for m in self.merged_sections[:12]),
            f"Sezioni fuse per stesso soggetto (passo approfondito): {len(self.deep_groups)}",
            *(f"  {m}" for m in self.deep_groups[:20]),
            f"Blocchi di testo ripetuti tolti: {self.removed_blocks} ({self.removed_chars:,} caratteri)".replace(",", "."),
            f"Voci di glossario equivalenti unite: {len(self.merged_glossary)}" + (f" ({'; '.join(self.merged_glossary[:8])})" if self.merged_glossary else ""),
            f"Chiamate API: {self.calls} | token in/out {self.input_tokens}/{self.output_tokens}",
        ]
        lines += [f"Attenzione: {w}" for w in self.warnings]
        return "\n".join(lines)


class Sanitizer:
    def __init__(self, campaign: Campaign, client: Any = None, model: str | None = None,
                 effort: str | None = "low", cache_root: Path | None = None):
        self.campaign = campaign
        self._client = client
        self.model = model
        self.effort = effort
        self.cache_root = Path(cache_root or CACHE_ROOT)

    # --- quali file -------------------------------------------------------------------

    def content_files(self) -> list[CampaignFile]:
        return [f for f in self.campaign.files()
                if f.name != GLOSSARY_NAME and not f.name.startswith(("fonte-", SHEETS_DIR + "/")) and f.path.suffix == ".md"]

    # --- passo gratuito ---------------------------------------------------------------

    def run_free(self, report: SanitizeReport | None = None) -> SanitizeReport:
        report = report or SanitizeReport()
        for f in self.content_files():
            groups: dict[str, list[str]] = {}
            for title in f.outline():
                groups.setdefault(norm_title(title), []).append(title)
            for titles in groups.values():
                if len(titles) > 1:
                    keep = best_title(titles)
                    self._merge(f, titles, keep, report, report.merged_sections)
            self._dedupe_blocks(f, report)
        self._dedupe_glossary(report)
        log.info("sanificazione gratuita: %d sezioni fuse, %d blocchi tolti, %d voci di glossario unite",
                 len(report.merged_sections), report.removed_blocks, len(report.merged_glossary))
        return report

    def _merge(self, f: CampaignFile, titles: list[str], keep: str, report: SanitizeReport, bucket: list[str]) -> None:
        sections = f.sections()
        present = [t for t in f.outline() if t in titles]  # nell'ordine del file
        if len(present) < 2:
            return
        bodies: list[str] = []
        seen: set[str] = set()
        for t in present:
            for block in re.split(r"\n\s*\n", sections.get(t, "")):
                b = block.strip()
                if not b:
                    continue
                k = norm_text(b)
                if len(b) >= MIN_BLOCK and k in seen:
                    report.removed_blocks += 1
                    report.removed_chars += len(b)
                    continue
                seen.add(k)
                bodies.append(b)
        first = present[0]
        for t in present[1:]:
            f.remove_section(t)
        if first != keep:
            # rinomina tenendo la posizione della prima sezione del gruppo
            content = f.read()
            content = re.sub(rf"^##\s+{re.escape(first)}\s*$", f"## {keep}", content, count=1, flags=re.MULTILINE)
            f.write(content)
        f.set_section(keep, "\n\n".join(bodies))
        bucket.append(f"{f.name}: {' + '.join(present)} -> {keep}")
        report.touched.setdefault(f.name, [])
        if keep.lower() not in report.touched[f.name]:
            report.touched[f.name].append(keep.lower())
        log.info("sanificazione: %s: fuse %s in '%s'", f.name, present, keep)

    def _dedupe_blocks(self, f: CampaignFile, report: SanitizeReport) -> None:
        """Blocchi identici (normalizzati) ripetuti nello stesso file: resta il primo."""
        sections = f.sections()
        seen: set[str] = set()
        for title in f.outline():
            blocks = [b.strip() for b in re.split(r"\n\s*\n", sections.get(title, "")) if b.strip()]
            kept: list[str] = []
            removed = 0
            for b in blocks:
                k = norm_text(b)
                if len(b) >= MIN_BLOCK and k in seen:
                    removed += 1
                    report.removed_chars += len(b)
                    continue
                seen.add(k)
                kept.append(b)
            if removed and kept:
                f.set_section(title, "\n\n".join(kept))
                report.removed_blocks += removed
                log.info("sanificazione: %s / %s: tolti %d blocchi ripetuti", f.name, title, removed)

    def _dedupe_glossary(self, report: SanitizeReport) -> None:
        g = self.campaign.glossary
        groups: dict[str, list[GlossaryEntry]] = {}
        for e in g.entries():
            groups.setdefault(norm_title(e.term), []).append(e)
        for entries in groups.values():
            if len(entries) < 2:
                continue
            term = best_title([e.term for e in entries])
            desc = max((e.description for e in entries), key=len)
            kinds = Counter(e.kind for e in entries if e.kind and e.kind != "altro")
            kind = kinds.most_common(1)[0][0] if kinds else entries[0].kind
            file = next((e.file for e in entries if e.term == term), entries[0].file)
            for e in entries:
                g.remove(e.term)
            g.upsert(GlossaryEntry(term, kind, desc, file))
            report.merged_glossary.append(" = ".join(e.term for e in entries))

    # --- passo approfondito: stesso soggetto, nomi diversi -------------------------------------

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def group_titles(self, f: CampaignFile, report: SanitizeReport) -> list[tuple[str, list[str]]]:
        titles = f.outline()
        if len(titles) < 6:
            return []
        params: dict[str, Any] = dict(
            model=self.model or Preparer(self.campaign, client=self._client).model,
            max_tokens=8000,
            system=[{"type": "text", "text": GROUP_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"File `{f.name}`, {len(titles)} titoli:\n" + "\n".join(f"- {t}" for t in titles)}],
            output_config={"format": {"type": "json_schema", "schema": GROUP_SCHEMA}},
        )
        if self.effort:
            params["output_config"]["effort"] = self.effort
        response = create_message(self.client, max_seconds=300, **params)
        report.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            report.input_tokens += getattr(usage, "input_tokens", 0) or 0
            report.output_tokens += getattr(usage, "output_tokens", 0) or 0
        log.info("sanificazione: titoli di %s (%d): %s", f.name, len(titles), usage_line(response))
        text = "\n".join(b.text for b in response.content if b.type == "text")
        try:
            data = parse_json_object(text)
        except ValueError as exc:
            report.warnings.append(f"{f.name}: risposta non valida sui titoli ({exc}), file lasciato com'era")
            return []
        by_low = {t.lower(): t for t in titles}
        used: set[str] = set()
        out: list[tuple[str, list[str]]] = []
        for g in data.get("gruppi", []):
            members = []
            for t in g.get("unisci", []):
                real = by_low.get(str(t).strip().lower())
                if real and real not in used and real not in members:
                    members.append(real)
            keep = by_low.get(str(g.get("titolo", "")).strip().lower())
            if len(members) < 2 or len(members) > 6 or keep not in members:
                if g.get("unisci"):
                    log.info("sanificazione: gruppo scartato in %s: %s", f.name, short(g, 160))
                continue
            used.update(members)
            out.append((keep, members))
        return out

    def run_deep(self, report: SanitizeReport, progress: Callable[[str], None] = print, consolidate: bool = True) -> SanitizeReport:
        for f in self.content_files():
            groups = self.group_titles(f, report)
            for keep, members in groups:
                self._merge(f, members, keep, report, report.deep_groups)
            if groups:
                progress(f"  {f.name}: {len(groups)} gruppi di sezioni sullo stesso soggetto fusi")
        if consolidate and report.touched:
            prep = Preparer(self.campaign, client=self.client, model=self.model, effort="medium", cache_root=self.cache_root)
            prep_report = PrepReport(source="sanificazione")
            state_dir = self.cache_root / "sanifica"
            state_dir.mkdir(parents=True, exist_ok=True)
            prep.consolidate_sections(report.touched, state_dir, prep_report, progress, use_batch=False)
            report.calls += prep_report.calls
            report.input_tokens += prep_report.input_tokens
            report.output_tokens += prep_report.output_tokens
            report.warnings += prep_report.warnings
        return report

    def run(self, deep: bool = False, progress: Callable[[str], None] = print) -> SanitizeReport:
        progress("Sanificazione: passo gratuito (titoli equivalenti, blocchi ripetuti, glossario)...")
        report = self.run_free()
        progress(f"  {len(report.merged_sections)} sezioni fuse, {report.removed_blocks} blocchi ripetuti tolti, "
                 f"{len(report.merged_glossary)} voci di glossario unite")
        if deep:
            progress("Sanificazione: passo approfondito (sezioni sullo stesso soggetto con nomi diversi)...")
            self.run_deep(report, progress)
        elif report.touched:
            progress("  le sezioni fuse contengono ancora i pezzi accodati: il passo approfondito le ripulisce")
        log.info("sanificazione FINITA: %s", short(report.summary(), 400))
        return report
