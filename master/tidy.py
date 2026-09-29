"""Riordino del glossario: parte dopo la preparazione (o da solo).

Due passi:

    sync_files   deterministico, senza API. Aggiunge una voce per ogni file
                 della campagna che non ne ha una, aggiorna la descrizione
                 dei file gia' indicizzati, toglie le voci che puntano a file
                 inesistenti e segnala come "sospette" quelle il cui termine
                 non compare nel file indicato.
    organize     una chiamata veloce all'API: decide quali voci tenere e quali
                 cancellare (vecchie, doppie, sospette) e normalizza il tipo.
                 Le descrizioni NON vengono riscritte: il glossario che ne esce
                 contiene solo voci gia' esistenti, riordinate per tipo.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .campaign import Campaign
from .engine import DEFAULT_MODEL
from .glossary import GlossaryEntry
from .logbook import short, usage_line
from .prepare import parse_json_object

log = logging.getLogger("master.riordino")

KIND_ORDER = [
    "regola", "fonte", "file", "pg", "png", "creatura", "fazione", "luogo",
    "razza", "classe", "oggetto", "evento", "cronaca", "altro",
]

ORGANIZE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mantieni": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "kind": {"type": "string", "enum": KIND_ORDER},
                },
                "required": ["term", "kind"],
                "additionalProperties": False,
            },
        },
        "rimuovi": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "motivo": {"type": "string"}},
                "required": ["term", "motivo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["mantieni", "rimuovi"],
    "additionalProperties": False,
}

ORGANIZE_PROMPT = """\
Riordini il glossario di una campagna di gioco di ruolo. Il glossario e' un
indice: una riga per entita', con il file dove stanno i dettagli.

Ricevi le voci attuali, l'elenco dei file con le loro sezioni, e una lista di
voci sospette (il termine non compare nel file indicato). Restituisci SOLO un
JSON con due liste:
- `mantieni`: ogni voce da tenere, con il `kind` normalizzato tra questi valori:
  {kinds}
- `rimuovi`: le voci da cancellare, con il motivo.

Cancella una voce solo se: e' un doppione di un'altra (tieni quella con il
termine piu' completo), e' sospetta e nessuna sezione dei file la riguarda,
oppure e' chiaramente superata da una voce piu' recente. Nel dubbio, tienila.
Ogni termine ricevuto deve comparire in una e una sola delle due liste.
Non inventare termini nuovi.
"""


@dataclass
class TidyReport:
    files_added: list[str] = field(default_factory=list)
    files_updated: list[str] = field(default_factory=list)
    removed_missing: list[str] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)
    removed_by_api: list[tuple[str, str]] = field(default_factory=list)
    kept: int = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Voci file aggiunte: {', '.join(self.files_added) or '-'}",
            f"Voci file aggiornate: {', '.join(self.files_updated) or '-'}",
            f"Rimosse (file inesistente): {', '.join(self.removed_missing) or '-'}",
            f"Rimosse dall'API: " + (", ".join(f"{t} ({m})" for t, m in self.removed_by_api) or "-"),
            f"Voci finali: {self.kept} | chiamate {self.calls} | token in/out {self.input_tokens}/{self.output_tokens}",
        ]
        lines += [f"Attenzione: {w}" for w in self.warnings]
        return "\n".join(lines)


class GlossaryTidier:
    def __init__(self, campaign: Campaign, client: Any = None, model: str | None = None, effort: str | None = "low"):
        self.campaign = campaign
        self.model = model or os.environ.get("MASTER_TIDY_MODEL") or os.environ.get("MASTER_MODEL") or DEFAULT_MODEL
        self.effort = effort
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    # --- passo 1: allineamento ai file (senza API) ------------------------------

    def sync_files(self, report: TidyReport | None = None) -> TidyReport:
        report = report or TidyReport()
        glossary = self.campaign.glossary
        gname = glossary.file.name
        existing_names = {f.name for f in self.campaign.files()}

        # voci che puntano a file spariti
        for e in glossary.entries():
            if e.file and e.file not in existing_names:
                glossary.remove(e.term)
                report.removed_missing.append(e.term)

        # una voce per ogni file
        for f in self.campaign.files():
            if f.name == gname:
                continue
            title = self._title_of(f.read()) or f.path.stem.replace("-", " ").capitalize()
            heads = f.outline()
            desc = f"File: {', '.join(heads[:8])}" + (", ..." if len(heads) > 8 else "") if heads else "File della campagna"
            current = next((e for e in glossary.entries() if e.kind == "file" and e.file == f.name), None)
            if current is None:
                if not any(e.file == f.name for e in glossary.entries()):
                    glossary.upsert(GlossaryEntry(title, "file", desc, f.name))
                    report.files_added.append(f.name)
            elif current.description != desc:
                glossary.upsert(GlossaryEntry(current.term, "file", desc, f.name))
                report.files_updated.append(f.name)

        # voci sospette: il termine non compare nel file indicato
        cache: dict[str, str] = {}
        for e in glossary.entries():
            if e.kind in {"file", "fonte", "cronaca"} or not e.file:
                continue
            text = cache.setdefault(e.file, self.campaign.file(e.file).read().lower())
            if e.term.lower() not in text:
                report.suspicious.append(e.term)
        log.info("riordino: file aggiunti=%s aggiornati=%s rimossi(file inesistente)=%s sospetti=%s",
                 report.files_added or "-", report.files_updated or "-", report.removed_missing or "-", report.suspicious or "-")
        return report

    # --- passo 2: organizzazione (una chiamata veloce) -------------------------

    def organize(self, report: TidyReport | None = None) -> TidyReport:
        report = report or TidyReport()
        glossary = self.campaign.glossary
        entries = glossary.entries()
        if not entries:
            return report
        by_term = {e.term.lower(): e for e in entries}

        payload = "\n".join(
            [
                "## Voci attuali",
                *(f"- {e.term} | {e.kind} | {e.description} | {e.file}" for e in entries),
                "",
                "## File e sezioni",
                self.campaign.index(),
                "",
                "## Voci sospette",
                *([f"- {t}" for t in report.suspicious] or ["(nessuna)"]),
            ]
        )
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": ORGANIZE_PROMPT.format(kinds=", ".join(KIND_ORDER)),
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": payload}],
            extra_body={"output_config": {"format": {"type": "json_schema", "schema": ORGANIZE_SCHEMA}}},
        )
        if self.effort:
            kwargs["extra_body"]["output_config"]["effort"] = self.effort
        log.info("riordino api -> %s effort=%s voci=%d", self.model, self.effort or "-", len(entries))
        response = self.client.messages.create(**kwargs)
        report.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            report.input_tokens += getattr(usage, "input_tokens", 0) or 0
            report.output_tokens += getattr(usage, "output_tokens", 0) or 0
        log.info("riordino api <- %s", usage_line(response))

        text = "\n".join(b.text for b in response.content if b.type == "text")
        try:
            data = parse_json_object(text)
        except (ValueError, json.JSONDecodeError) as exc:
            report.warnings.append("risposta API non valida: glossario lasciato com'era")
            log.warning("riordino: JSON non valido (%s), inizio risposta: %s", exc, short(text, 200))
            report.kept = len(entries)
            return report

        kept: list[GlossaryEntry] = []
        seen: set[str] = set()
        for item in data.get("mantieni", []):
            e = by_term.get(str(item.get("term", "")).strip().lower())
            if e is None or e.term.lower() in seen:
                continue
            kind = str(item.get("kind", e.kind)).strip().lower()
            kept.append(GlossaryEntry(e.term, kind if kind in KIND_ORDER else e.kind, e.description, e.file))
            seen.add(e.term.lower())
        removed = {str(r.get("term", "")).strip().lower(): str(r.get("motivo", "")) for r in data.get("rimuovi", [])}
        # tutto cio' che l'API non ha nominato resta: nel dubbio si tiene
        for e in entries:
            key = e.term.lower()
            if key in seen:
                continue
            if key in removed:
                report.removed_by_api.append((e.term, removed[key]))
            else:
                kept.append(e)
                seen.add(key)

        if len(kept) < len(entries) * 0.5:
            report.warnings.append("l'API voleva cancellare piu' di meta' delle voci: glossario lasciato com'era")
            log.warning("riordino: l'API voleva tenere %d voci su %d, rifiutato", len(kept), len(entries))
            report.removed_by_api.clear()
            report.kept = len(entries)
            return report

        self._rewrite(kept)
        report.kept = len(kept)
        log.info("riordino FINITO: tenute %d voci, rimosse dall'API %s", len(kept),
                 [f"{t} ({m})" for t, m in report.removed_by_api] or "-")
        return report

    def run(self, progress: Callable[[str], None] = print) -> TidyReport:
        progress("Riordino glossario: allineo i file...")
        report = self.sync_files()
        progress(f"  file aggiunti {len(report.files_added)}, rimossi {len(report.removed_missing)}, sospetti {len(report.suspicious)}")
        progress("  organizzo con una chiamata veloce...")
        return self.organize(report)

    # --- utilita' -----------------------------------------------------------------

    @staticmethod
    def _title_of(text: str) -> str | None:
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
            if line.strip():
                break
        return None

    @staticmethod
    def _kind_rank(kind: str) -> int:
        return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)

    def _rewrite(self, entries: list[GlossaryEntry]) -> None:
        """Riscrive il glossario: preambolo intatto, tabella ordinata per tipo e termine."""
        glossary = self.campaign.glossary
        lines = glossary.file.read().splitlines()
        preamble = []
        for line in lines:
            if line.strip().startswith("|"):
                break
            preamble.append(line)
        while preamble and not preamble[-1].strip():
            preamble.pop()
        ordered = sorted(entries, key=lambda e: (self._kind_rank(e.kind), e.term.lower()))
        table = ["| Termine | Tipo | Descrizione | File |", "|---|---|---|---|", *(e.row() for e in ordered)]
        glossary.file.write("\n".join(preamble + [""] + table) + "\n")
