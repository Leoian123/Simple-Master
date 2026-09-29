"""Preparazione: da un manuale (PDF, testo o URL) ai documenti di campagna.

Un manuale da 500 pagine non entra in una chiamata, e comunque non vogliamo
il testo grezzo: vogliamo i file di campagna e il glossario. Il processo e' un
map-reduce:

    map         ogni blocco di pagine -> JSON: sezioni per file + voci glossario + riassunto
    reduce      le sezioni entrano nei file con CampaignFile.set_section,
                le voci nel glossario con Glossary.upsert
    consolida   i file in cui sezioni omonime si sono accodate vengono riordinati
                dall'API in una chiamata (unisce doppioni, toglie ripetizioni)

## Precisione e costo (pratiche documentate da Anthropic)
- Batch API per l'estrazione: i blocchi mancanti partono in un unico batch a
  meta' prezzo; il processo aspetta l'esito (minuti, a volte ore) e riprende
  lo stesso batch se viene interrotto. Disattivabile (prep_batch / --no-batch).
- Output strutturato con schema JSON: niente JSON malformato.
- Streaming e tetto alto ai token di uscita: niente risposte troncate.
- Prompt caching sul prompt di sistema, condiviso da tutti i blocchi.
- Pagine senza valore (vuote, quasi solo immagini, indici e sommari) scartate
  prima di spendere token.
- Stima del costo prima di partire, calibrata con count_tokens (gratuito).
- Consolidamento solo dei file dove qualcosa si e' davvero accodato.

## Cache condivisa: lo stesso manuale non si paga due volte
Ogni blocco riuscito viene salvato in `cache/manuali/<impronta>-<nome>/<versione>/
blocco_NN_pA-B.json` alla radice del progetto. La chiave e' l'impronta SHA-256
del file, non la campagna: preparare lo stesso manuale per un'altra campagna
riusa tutti i blocchi senza chiamate API. Anche il consolidamento di un file e'
in cache (`cache/manuali/consolidati/`), per contenuto. Se il processo si
interrompe, rilanciandolo riparte da dove era (batch compreso); i blocchi
falliti (JSON troncato o non valido) non vengono salvati.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .campaign import MECHANICS_DIR, PREP_DIR, SETTING_DIR, Campaign
from .engine import DEFAULT_MODEL
from .files import split_sections
from .glossary import GlossaryEntry
from .logbook import PROJECT_ROOT, describe_error, short, usage_line
from .models import cost_usd

log = logging.getLogger("master.preparazione")

CACHE_ROOT = PROJECT_ROOT / "cache" / "manuali"
CACHE_VERSION = "v2"  # cambia quando cambiano prompt o schema di estrazione: invalida i blocchi vecchi


def source_key(path: Path) -> str:
    """Impronta del manuale: primi 16 esadecimali dello SHA-256 + nome leggibile."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16] + "-" + re.sub(r"[^\w.-]+", "_", path.stem)[:40]


TARGET_FILES: dict[str, str] = {
    # MECCANICA: numeri, tiri, tabelle. Cio' che serve per applicare le regole.
    f"{MECHANICS_DIR}/regole.md": "regole di gioco: tiri, combattimento, magia, progressione, condizioni",
    f"{MECHANICS_DIR}/creazione-pg.md": "creazione del personaggio: passi, caratteristiche, razze, classi, abilita', punti iniziali",
    f"{MECHANICS_DIR}/oggetti.md": "equipaggiamento, armi, armature, oggetti magici: statistiche, costi, effetti",
    f"{MECHANICS_DIR}/creature.md": "creature e mostri: statistiche, attacchi, capacita' (solo i numeri e le regole)",
    # AMBIENTAZIONE: il mondo. Cio' che serve per narrare.
    f"{SETTING_DIR}/mondo.md": "geografia, luoghi, storia, fazioni, religioni, culture, razze come popoli",
    f"{SETTING_DIR}/npc.md": "personaggi non giocanti e creature come esseri del mondo: chi sono, cosa vogliono",
    f"{SETTING_DIR}/avventura.md": "trame, scenari, incontri, ganci narrativi, segreti per il master",
}

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "riassunto": {"type": "string", "description": "Cosa contiene questo blocco di pagine, 2-4 frasi"},
        "sezioni": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": list(TARGET_FILES)},
                    "titolo": {"type": "string"},
                    "testo": {"type": "string"},
                },
                "required": ["file", "titolo", "testo"],
                "additionalProperties": False,
            },
        },
        "glossario": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "kind": {"type": "string"},
                    "description": {"type": "string"},
                    "file": {"type": "string", "enum": list(TARGET_FILES)},
                },
                "required": ["term", "kind", "description", "file"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["riassunto", "sezioni", "glossario"],
    "additionalProperties": False,
}

EXTRACT_PROMPT = """\
Stai trasformando un manuale di gioco di ruolo in documenti di campagna per un
master AI. Ricevi un blocco di pagine alla volta e restituisci SOLO un JSON.

## File di destinazione
{targets}

## Separazione fondamentale: MECCANICA vs AMBIENTAZIONE
- In `meccanica/` va tutto cio' che e' numero, tiro, tabella, costo, statistica:
  come si crea un personaggio, come si combatte, cosa fa un oggetto, quanti punti
  ferita ha una creatura. Il master lo applica alla lettera.
- In `ambientazione/` va tutto cio' che e' mondo: chi e' un personaggio, cosa
  vuole, com'e' un luogo, cosa e' successo nella storia. Il master lo narra.
- Se un elemento ha entrambe le facce (una creatura, un oggetto leggendario, una
  razza), spezzalo: la scheda numerica in `meccanica/`, la descrizione e il
  ruolo nel mondo in `ambientazione/`, con lo stesso titolo di sezione.

## Regole
- Scrivi in italiano, anche se il manuale e' in un'altra lingua.
- Non riassumere le regole: trascrivile complete e precise, tabelle incluse
  (in Markdown). Il master deve poterle applicare senza il manuale.
- La creazione del personaggio va in `meccanica/creazione-pg.md` in forma di
  procedura passo-passo con tutti i valori: servira' a compilare le schede.
- Ambientazione e trame: sintetizza, ma conserva nomi, luoghi, date, relazioni.
- Ogni sezione ha un titolo stabile e riutilizzabile ("Combattimento", "Elfi",
  "Città di Velmora"), mai "Capitolo 4" o "Pagina 12". Se un argomento continua
  da un blocco precedente, usa lo stesso titolo: verra' unito.
- Salta indice, colophon, pubblicita', ringraziamenti, testo di riempimento.
- Glossario: una voce per ogni entita' rilevante (regola chiave, luogo, PNG,
  fazione, oggetto, razza, classe...). Descrizione in una frase. Il campo
  `kind` e' uno tra: regola, luogo, png, fazione, oggetto, razza, classe,
  creatura, evento, altro. Riusa i termini gia' presenti nel glossario invece
  di crearne varianti.
- Non inventare nulla che non sia nel testo.
"""

CONSOLIDATE_SECTIONS_PROMPT = """\
Sei un redattore. Ricevi alcune sezioni `## Titolo` di un file di campagna. Ogni
sezione e' stata assemblata accodando pezzi estratti da blocchi diversi di un
manuale, quindi contiene ripetizioni, frasi spezzate tra un pezzo e l'altro e
tabelle duplicate. Per ciascuna sezione:
- fondi i pezzi in un testo unico e ordinato, togliendo le ripetizioni;
- NON perdere fatti, numeri, nomi, regole, tabelle: se due pezzi dicono cose
  diverse, tieni entrambe; se una tabella compare due volte, tienine una completa;
- non aggiungere nulla che non sia nel testo.
Restituisci SOLO le stesse sezioni, con gli STESSI titoli `## ...` e nello stesso
ordine, in Markdown, senza commenti prima o dopo.
"""

CONSOLIDATE_GROUP_CHARS = 45_000  # un gruppo di sezioni per chiamata: l'uscita resta sotto il tetto


@dataclass
class Chunk:
    index: int
    first_page: int
    last_page: int
    text: str | None = None  # modalita' testo
    pdf_b64: str | None = None  # modalita' pdf (pagine originali, tabelle e immagini incluse)

    @property
    def label(self) -> str:
        return f"pagine {self.first_page}-{self.last_page}"

    @property
    def cache_name(self) -> str:
        return f"blocco_{self.index:03d}_p{self.first_page}-{self.last_page}.json"


@dataclass
class PrepReport:
    source: str
    chunks: int = 0
    chunks_from_cache: int = 0
    chunks_batched: int = 0
    consolidated_from_cache: int = 0
    consolidations_skipped: int = 0
    pages_skipped: int = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimate: str = ""
    files_touched: list[str] = field(default_factory=list)
    merged_files: list[str] = field(default_factory=list)
    glossary_entries: int = 0
    warnings: list[str] = field(default_factory=list)
    tidy: str = ""

    def summary(self) -> str:
        lines = [
            f"Fonte: {self.source}",
            f"Blocchi: {self.chunks} (di cui {self.chunks_from_cache} dalla cache condivisa, senza costo"
            + (f"; {self.chunks_batched} elaborati in batch a meta' prezzo" if self.chunks_batched else "") + ")",
            f"Chiamate API: {self.calls} | token in/out {self.input_tokens}/{self.output_tokens}"
            + (f" | consolidamenti dalla cache: {self.consolidated_from_cache}" if self.consolidated_from_cache else "")
            + (f" | consolidamenti saltati (nulla da unire): {self.consolidations_skipped}" if self.consolidations_skipped else ""),
            f"File toccati: {', '.join(self.files_touched) or '-'}",
            f"Voci glossario aggiunte o aggiornate: {self.glossary_entries}",
        ]
        if self.pages_skipped:
            lines.append(f"Pagine scartate perche' senza valore (vuote, immagini, indici): {self.pages_skipped}")
        if self.estimate:
            lines.append(self.estimate)
        if self.tidy:
            lines += ["Riordino glossario:"] + ["  " + l for l in self.tidy.splitlines()]
        lines += [f"Attenzione: {w}" for w in self.warnings]
        return "\n".join(lines)


# --- sorgente ---------------------------------------------------------------


def fetch_source(spec: str, download_dir: Path) -> Path:
    """Ritorna un percorso locale: scarica se `spec` e' un URL http(s)."""
    parsed = urllib.parse.urlparse(spec)
    if parsed.scheme in {"http", "https"}:
        name = Path(parsed.path).name or "manuale.pdf"
        download_dir.mkdir(parents=True, exist_ok=True)
        target = download_dir / name
        if not target.exists():
            req = urllib.request.Request(spec, headers={"User-Agent": "SimpleMaster/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(target, "wb") as out:
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    out.write(block)
        return target
    path = Path(spec).expanduser()
    if not path.is_file():
        raise FileNotFoundError(spec)
    return path


def _parse_pages(pages: str | None, total: int) -> tuple[int, int]:
    if not pages:
        return 1, total
    m = re.fullmatch(r"\s*(\d+)?\s*-\s*(\d+)?\s*", pages) or re.fullmatch(r"\s*(\d+)\s*", pages)
    if not m:
        raise ValueError(f"Intervallo pagine non valido: {pages!r} (usa es. 10-120)")
    first = int(m.group(1) or 1)
    last = int(m.group(2) or total) if m.lastindex and m.lastindex >= 2 else first
    return max(1, first), min(total, last)


# Un blocco deve produrre un JSON che stia comodamente sotto MAX_OUTPUT_TOKENS anche
# quando le regole vengono trascritte per intero: ~36k caratteri di manuale (~10k token)
# generano di norma 10-20k token di uscita.
DEFAULT_CHARS_PER_CHUNK = 36_000
DEFAULT_PAGES_PER_CHUNK = 12
MAX_OUTPUT_TOKENS = 32_000
MIN_PAGE_CHARS = 150  # sotto: pagina vuota o quasi solo immagine


def is_low_value_page(text: str, min_chars: int = MIN_PAGE_CHARS) -> str | None:
    """Ritorna il motivo se la pagina non vale i token: vuota/immagine, indice o sommario."""
    stripped = text.strip()
    if len(stripped) < min_chars:
        return "vuota"
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    if len(lines) >= 8:
        numbered = sum(1 for l in lines if re.search(r"(?:\.{2,}|\s)\s*\d{1,4}\s*$", l))
        if numbered / len(lines) >= 0.6:
            return "indice"
    return None


def load_chunks(
    path: Path,
    mode: str = "testo",
    pages: str | None = None,
    chars_per_chunk: int = DEFAULT_CHARS_PER_CHUNK,
    pages_per_chunk: int = DEFAULT_PAGES_PER_CHUNK,
    warnings: list[str] | None = None,
    stats: dict[str, int] | None = None,
) -> list[Chunk]:
    """Spezza la fonte in blocchi. PDF: per pagine; testo/markdown: per caratteri.

    In modalita' testo le pagine senza valore vengono scartate (conteggio in
    `stats["pages_skipped"]`), ma i numeri di pagina restano quelli del manuale.
    """
    warnings = warnings if warnings is not None else []
    stats = stats if stats is not None else {}
    stats.setdefault("pages_skipped", 0)
    if path.suffix.lower() != ".pdf":
        text = path.read_text(encoding="utf-8", errors="replace")
        chunks = []
        for i in range(0, max(len(text), 1), chars_per_chunk):
            n = len(chunks) + 1
            chunks.append(Chunk(n, n, n, text=text[i : i + chars_per_chunk]))
        return chunks

    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(path))
    total = len(reader.pages)
    first, last = _parse_pages(pages, total)
    chunks: list[Chunk] = []

    if mode == "pdf":
        for start in range(first, last + 1, pages_per_chunk):
            end = min(start + pages_per_chunk - 1, last)
            writer = PdfWriter()
            for p in range(start - 1, end):
                writer.add_page(reader.pages[p])
            buf = io.BytesIO()
            writer.write(buf)
            b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
            chunks.append(Chunk(len(chunks) + 1, start, end, pdf_b64=b64))
        return chunks

    if mode != "testo":
        raise ValueError(f"Modalita' sconosciuta: {mode!r} (usa 'testo' o 'pdf')")

    texts = [(reader.pages[p].extract_text() or "").strip() for p in range(first - 1, last)]
    # soglia relativa: una pagina e' "vuota" se e' molto sotto la mediana del manuale
    # (cosi' un PDF di prova con pagine corte non viene scartato per intero)
    lengths = sorted(len(t) for t in texts) or [0]
    median = lengths[len(lengths) // 2]
    min_chars = min(MIN_PAGE_CHARS, max(1, int(median * 0.1)))

    buf_pages: list[str] = []
    buf_len = 0
    start = first
    empty = 0
    for p, page_text in zip(range(first - 1, last), texts):
        if len(page_text) < 40:
            empty += 1
        reason = is_low_value_page(page_text, min_chars)
        if reason is None:
            buf_pages.append(f"--- pagina {p + 1} ---\n{page_text}")
            buf_len += len(page_text)
        else:
            stats["pages_skipped"] += 1
            log.debug("pagina %d scartata: %s", p + 1, reason)
        if (buf_len >= chars_per_chunk or p == last - 1) and buf_pages:
            chunks.append(Chunk(len(chunks) + 1, start, p + 1, text="\n\n".join(buf_pages)))
            buf_pages, buf_len, start = [], 0, p + 2
        elif not buf_pages:
            start = p + 2
    if empty and empty > (last - first + 1) * 0.3:
        warnings.append(
            f"{empty} pagine su {last - first + 1} senza testo estraibile: "
            "il PDF e' probabilmente scansionato, usa --mode pdf"
        )
    return chunks


# --- utilita' API e JSON ----------------------------------------------------------


BLOCK_TIMEOUT_SECONDS = 15 * 60  # oltre: lo stream e' impiantato, meglio fallire e rifare


def create_message(client: Any, max_seconds: float | None = None, **kwargs: Any) -> Any:
    """Chiamata all'API in streaming quando il client lo offre (obbligatorio per
    risposte lunghe con l'SDK 1.x), altrimenti chiamata semplice (client finti).

    `max_seconds` e' un limite di orologio sull'intera risposta: uno stream che
    resta aperto senza concludersi (successo il 2026-09-17: 51 minuti muti)
    solleva TimeoutError invece di bloccare il processo per sempre.
    """
    stream = getattr(getattr(client, "messages", None), "stream", None)
    if stream is None:
        return client.messages.create(**kwargs)
    deadline = time.monotonic() + max_seconds if max_seconds else None
    with stream(**kwargs) as s:
        if deadline is not None:
            for _ in s:  # consuma gli eventi man mano, controllando l'orologio
                if time.monotonic() > deadline:
                    raise TimeoutError(f"risposta non conclusa entro {int(max_seconds)} secondi")
        return s.get_final_message()


def supports_batches(client: Any) -> bool:
    return getattr(getattr(client, "messages", None), "batches", None) is not None


def is_empty_result(data: dict[str, Any]) -> bool:
    return not data.get("sezioni") and not data.get("glossario") and not (data.get("riassunto") or "").strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """Estrae il primo oggetto JSON dal testo, tollerando testo attorno."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Nessun JSON nella risposta")
    return json.loads(text[start : end + 1])


# --- preparatore -----------------------------------------------------------


class Preparer:
    """Esegue map (batch o sequenziale) -> reduce -> consolida su una campagna."""

    def __init__(
        self,
        campaign: Campaign,
        client: Any = None,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int = 16000,
        cache_root: Path | None = None,
        batch: bool = True,
        poll_seconds: float = 20.0,
        block_timeout: float | None = BLOCK_TIMEOUT_SECONDS,
    ):
        self.campaign = campaign
        self.model = model or os.environ.get("MASTER_MODEL") or DEFAULT_MODEL
        self.effort = effort or os.environ.get("MASTER_EFFORT") or None
        self.max_tokens = max_tokens
        self.cache_root = Path(cache_root or CACHE_ROOT)
        self.batch = batch
        self.poll_seconds = poll_seconds
        self.block_timeout = block_timeout
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    # --- ciclo completo ----------------------------------------------------

    def run(
        self,
        source: str,
        mode: str = "testo",
        pages: str | None = None,
        consolidate: bool = True,
        tidy: bool = True,
        tidy_effort: str | None = "low",
        batch: bool | None = None,
        progress: Callable[[str], None] = print,
    ) -> PrepReport:
        use_batch = self.batch if batch is None else batch
        log.info("preparazione: fonte=%s campagna=%s modalita'=%s pagine=%s modello=%s effort=%s batch=%s",
                 source, self.campaign.name, mode, pages or "tutte", self.model, self.effort or "-", use_batch)
        prep_root = self.campaign.root / PREP_DIR
        try:
            path = fetch_source(source, prep_root / "download")
        except Exception as exc:
            log.error("preparazione INTERROTTA: fonte non leggibile (%s)", describe_error(exc), exc_info=True)
            raise
        report = PrepReport(source=str(path))
        cache_dir = self.cache_root / source_key(path) / f"{CACHE_VERSION}-{mode}-c{DEFAULT_CHARS_PER_CHUNK}-p{DEFAULT_PAGES_PER_CHUNK}"
        cache_dir.mkdir(parents=True, exist_ok=True)

        stats: dict[str, int] = {}
        try:
            chunks = load_chunks(path, mode=mode, pages=pages, warnings=report.warnings, stats=stats)
        except Exception as exc:
            log.error("preparazione INTERROTTA: impossibile spezzare %s (%s)", path.name, describe_error(exc), exc_info=True)
            raise
        report.chunks = len(chunks)
        report.pages_skipped = stats.get("pages_skipped", 0)
        for w in report.warnings:
            log.warning("preparazione: %s", w)

        recovered = self.recover_legacy(chunks, cache_dir, path.stem)
        if recovered:
            progress(f"  recuperati {recovered} blocchi da una preparazione precedente: nessun costo")
        cached: dict[int, dict[str, Any]] = {}
        todo: list[Chunk] = []
        for chunk in chunks:
            data = self._load_cache(cache_dir / chunk.cache_name)
            if data is not None:
                cached[chunk.index] = data
            else:
                todo.append(chunk)
        progress(f"{path.name}: {len(chunks)} blocchi, modalita' {mode}"
                 + (f", {report.pages_skipped} pagine scartate" if report.pages_skipped else "")
                 + (f", {len(cached)} gia' in cache (nessun costo)" if cached else ""))
        log.info("preparazione: %s -> %d blocchi, %d in cache, %d da elaborare, %d pagine scartate (cache in %s)",
                 path.name, len(chunks), len(cached), len(todo), report.pages_skipped, cache_dir)

        if todo:
            report.estimate = self.estimate(todo, mode, use_batch and supports_batches(self.client))
            progress("  " + report.estimate)
            log.info("preparazione: %s", report.estimate)

        # Batch: tutti i blocchi mancanti insieme, a meta' prezzo. Altrimenti uno alla volta
        # nel ciclo sotto, cosi' ogni blocco vede il glossario aggiornato dai precedenti.
        results: dict[int, dict[str, Any]] = dict(cached)
        sequential = bool(todo)
        if todo and use_batch and supports_batches(self.client) and len(todo) >= 2:
            results.update(self._extract_batch(todo, cache_dir, report, progress))
            sequential = False

        summaries: list[tuple[Chunk, str]] = []
        seen_titles: dict[tuple[str, str], int] = {}  # (file, titolo minuscolo) -> in quanti blocchi compare
        for chunk in chunks:
            data = results.get(chunk.index)
            if data is None and sequential:
                progress(f"  blocco {chunk.index}/{len(chunks)} ({chunk.label}): estrazione...")
                log.info("blocco %d/%d (%s): estrazione, %s", chunk.index, len(chunks), chunk.label,
                         f"{len(chunk.text or '')} caratteri" if chunk.text else "pdf allegato")
                try:
                    data = self.extract(chunk, report)
                except Exception as exc:
                    log.error("preparazione INTERROTTA al blocco %d/%d (%s): %s | i blocchi gia' fatti sono in cache",
                              chunk.index, len(chunks), chunk.label, describe_error(exc), exc_info=True)
                    raise
                if data.get("_errore"):
                    progress(f"    blocco {chunk.index}: {data['_errore']} (non salvato: verra' rifatto al prossimo lancio)")
                else:
                    (cache_dir / chunk.cache_name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            if data is None:
                continue
            if chunk.index in cached:
                report.chunks_from_cache += 1
                progress(f"  blocco {chunk.index}/{len(chunks)} ({chunk.label}): dalla cache, nessun costo")
            before = report.glossary_entries
            for sec in data.get("sezioni", []):
                key = (str(sec.get("file", "")), str(sec.get("titolo", "")).strip().lower())
                seen_titles[key] = seen_titles.get(key, 0) + 1
            self.merge(data, report)
            log.info("blocco %d/%d: %d sezioni, %d voci glossario", chunk.index, len(chunks),
                     len(data.get("sezioni", [])), report.glossary_entries - before)
            summaries.append((chunk, data.get("riassunto", "")))

        self.write_source_index(path, summaries, report)
        log.info("preparazione: indice della fonte scritto")

        # sanificazione gratuita: titoli equivalenti fusi, blocchi identici tolti, glossario senza doppioni.
        # Le sezioni fuse qui entrano nel consolidamento qui sotto insieme a quelle ripetute nei blocchi.
        from .sanitize import Sanitizer

        clean = Sanitizer(self.campaign).run_free()
        if clean.merged_sections or clean.removed_blocks or clean.merged_glossary:
            progress(f"  sanificazione: {len(clean.merged_sections)} sezioni fuse per titolo equivalente, "
                     f"{clean.removed_blocks} blocchi ripetuti tolti, {len(clean.merged_glossary)} voci di glossario unite")
        for name, lows in clean.touched.items():
            for low in lows:
                seen_titles[(name, low)] = max(seen_titles.get((name, low), 0), 2)

        if consolidate:
            # Solo le sezioni comparse in piu' blocchi hanno testo accodato da ripulire: il resto
            # del file non si tocca e non si paga. Vale anche se la fusione e' avvenuta in una
            # corsa precedente, perche' il conteggio viene dai blocchi in cache.
            targets: dict[str, list[str]] = {}
            for (name, low), count in seen_titles.items():
                if count >= 2 and name in TARGET_FILES:
                    targets.setdefault(name, []).append(low)
            for name in TARGET_FILES:
                if name not in targets and self.campaign.file(name).exists() and any(k[0] == name for k in seen_titles):
                    report.consolidations_skipped += 1
                    log.info("consolidamento %s saltato: nessuna sezione ripetuta, nulla da unire", name)
            if targets:
                try:
                    self.consolidate_sections(targets, cache_dir, report, progress, use_batch)
                except Exception as exc:
                    log.error("preparazione INTERROTTA al consolidamento: %s | rilanciando riprende da dove era",
                              describe_error(exc), exc_info=True)
                    raise

        if tidy:
            from .tidy import GlossaryTidier

            try:
                tidy_report = GlossaryTidier(self.campaign, client=self.client, model=self.model, effort=tidy_effort).run(progress)
            except Exception as exc:
                log.error("preparazione INTERROTTA al riordino del glossario: %s", describe_error(exc), exc_info=True)
                raise
            report.calls += tidy_report.calls
            report.input_tokens += tidy_report.input_tokens
            report.output_tokens += tidy_report.output_tokens
            report.warnings += tidy_report.warnings
            report.tidy = tidy_report.summary()

        # a manuale smontato, la meccanica dei tiri diventa dati: i dadi li tirera' il codice.
        # Il profilo sta nella cache del manuale: lo stesso manuale su un'altra campagna non lo ripaga.
        try:
            from . import dice

            cached = cache_dir / dice.PROFILE_NAME
            profile = self._load_json(cached)
            if profile is not None:  # anche "nessun tiro trovato" resta in cache: non si ripaga la domanda
                if profile.get("tiri"):
                    dice.save_profile(self.campaign, profile)
                    progress("  dadi: profilo dei tiri ripreso dalla cache del manuale")
            elif dice.rules_about_dice(self.campaign):
                progress("  dadi: ricavo il profilo dei tiri dalle regole estratte")
                profile = dice.extract_profile(self.campaign, self.client, self.model)
                report.calls += 1
                cached.write_text(json.dumps(profile or {"tiri": []}, ensure_ascii=False, indent=1), encoding="utf-8")
                progress("  dadi: " + (", ".join(k["nome"] for k in profile["tiri"]) if profile else "nessun tipo di tiro trovato nelle regole"))
        except Exception as exc:  # la campagna resta giocabile: senza profilo il narratore scrive i dadi per esteso
            log.warning("profilo dadi non ricavato: %s", describe_error(exc))
            report.warnings.append(f"profilo dei dadi non ricavato: {exc}")

        log.info("preparazione FINITA | blocchi=%d (cache %d, batch %d) chiamate=%d token in/out=%d/%d | file=%s | avvisi=%d",
                 report.chunks, report.chunks_from_cache, report.chunks_batched, report.calls, report.input_tokens,
                 report.output_tokens, ", ".join(report.files_touched) or "-", len(report.warnings))
        return report

    # --- cache dei blocchi ------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _load_cache(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or is_empty_result(data) or data.get("_errore"):
            log.info("cache %s vuota o fallita: il blocco verra' rifatto", path.name)
            return None
        return data

    # --- stima del costo ------------------------------------------------------

    def estimate(self, todo: list[Chunk], mode: str, batch: bool) -> str:
        """Stima di token e costo massimo per i blocchi da elaborare, calibrata con count_tokens."""
        system_tokens = 1300
        if mode == "pdf":
            pages = sum(c.last_page - c.first_page + 1 for c in todo)
            in_tokens = pages * 2600 + system_tokens * len(todo)
            out_tokens = pages * 900
        else:
            chars = sum(len(c.text or "") for c in todo)
            ratio = 3.6
            counter = getattr(getattr(self.client, "messages", None), "count_tokens", None)
            if counter is not None:
                try:
                    probe = todo[0]
                    res = counter(model=self.model, system=self._system(), messages=[{"role": "user", "content": probe.text or ""}])
                    probe_tokens = int(getattr(res, "input_tokens", 0)) - system_tokens
                    if probe_tokens > 100 and probe.text:
                        ratio = len(probe.text) / probe_tokens
                except Exception as exc:  # stima comunque: e' solo informativa
                    log.info("count_tokens non disponibile (%s): stima con %.1f caratteri/token", describe_error(exc), ratio)
            in_tokens = chars / ratio + system_tokens * len(todo)
            out_tokens = in_tokens * 0.6  # trascrizione: l'uscita e' circa i due terzi dell'ingresso
        full = cost_usd(self.model, in_tokens, out_tokens, batch=False)
        num = lambda n: f"{int(n):,}".replace(",", ".")
        return (f"Stima per {len(todo)} blocchi: ~{num(in_tokens)} token in ingresso, ~{num(out_tokens)} in uscita, "
                f"costo massimo ~${full:.2f}" + (f" (in batch a meta' prezzo: ~${full / 2:.2f})" if batch else ""))

    # --- map: richiesta e risposta ---------------------------------------------

    def _system(self) -> list[dict[str, Any]]:
        targets = "\n".join(f"- {name}: {what}" for name, what in TARGET_FILES.items())
        return [
            {
                "type": "text",
                "text": EXTRACT_PROMPT.format(targets=targets),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _known_terms(self) -> str:
        entries = self.campaign.glossary.entries()
        if not entries:
            return "(vuoto)"
        return "\n".join(f"- {e.term} ({e.kind}, {e.file})" for e in entries)

    def _params(self, chunk: Chunk) -> dict[str, Any]:
        intro = f"Glossario della campagna finora:\n{self._known_terms()}\n\nBlocco: {chunk.label}.\n"
        content: list[dict[str, Any]]
        if chunk.pdf_b64:
            content = [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": chunk.pdf_b64}},
                {"type": "text", "text": intro + "Le pagine sono nel documento allegato."},
            ]
        else:
            content = [{"type": "text", "text": intro + "\n" + (chunk.text or "")}]
        params: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max(self.max_tokens, MAX_OUTPUT_TOKENS),
            system=self._system(),
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
        )
        if self.effort:
            params["output_config"]["effort"] = self.effort
        return params

    def _parse(self, chunk: Chunk, response: Any, report: PrepReport) -> dict[str, Any]:
        self._count(report, response)
        log.info("blocco %d api <- %s", chunk.index, usage_line(response))
        if response.stop_reason == "refusal":
            report.warnings.append(f"{chunk.label}: rifiutato dall'API, saltato")
            log.warning("blocco %d: rifiutato dall'API, saltato", chunk.index)
            return {"riassunto": "", "sezioni": [], "glossario": [], "_errore": "rifiutato dall'API"}
        text = "\n".join(b.text for b in response.content if b.type == "text")
        try:
            data = parse_json_object(text)
        except (ValueError, json.JSONDecodeError) as exc:
            why = "risposta troncata (max_tokens)" if response.stop_reason == "max_tokens" else f"JSON non valido ({exc})"
            report.warnings.append(f"{chunk.label}: {why}, blocco da rifare")
            log.warning("blocco %d: %s, inizio risposta: %s", chunk.index, why, short(text, 200))
            return {"riassunto": "", "sezioni": [], "glossario": [], "_errore": why}
        if response.stop_reason == "max_tokens":
            report.warnings.append(f"{chunk.label}: risposta al limite dei token, forse incompleta")
            log.warning("blocco %d: JSON valido ma stop=max_tokens, forse incompleto", chunk.index)
        return data

    def extract(self, chunk: Chunk, report: PrepReport) -> dict[str, Any]:
        params = self._params(chunk)
        log.info("blocco %d api -> %s effort=%s max_tokens=%d", chunk.index, self.model, self.effort or "-", params["max_tokens"])
        try:
            response = create_message(self.client, max_seconds=self.block_timeout, **params)
        except TimeoutError as exc:
            report.warnings.append(f"{chunk.label}: {exc}, blocco da rifare")
            log.warning("blocco %d: %s (stream impiantato), blocco da rifare", chunk.index, exc)
            return {"riassunto": "", "sezioni": [], "glossario": [], "_errore": str(exc)}
        return self._parse(chunk, response, report)

    # --- recupero di blocchi da cache precedenti (per campagna, con altri confini) --------------

    def recover_legacy(self, chunks: list[Chunk], cache_dir: Path, stem: str) -> int:
        """Importa nella cache condivisa i blocchi validi lasciati dalle versioni precedenti
        in `campaigns/*/preparazione/<manuale>/blocco_NN_pA-B.json`, abbinandoli per pagine.

        Un blocco vecchio vale per un blocco nuovo se ne copre le pagine e ne eccede di poco
        (le pagine in piu' sono quelle vuote che oggi vengono scartate).
        """
        legacy_name = re.sub(r"[^\w.-]+", "_", stem)
        candidates: list[tuple[int, int, Path]] = []
        for base in {self.campaign.root.parent, self.campaign.root}:
            for f in base.glob(f"*/{PREP_DIR}/{legacy_name}/blocco_*_p*-*.json"):
                m = re.search(r"_p(\d+)-(\d+)\.json$", f.name)
                if m:
                    candidates.append((int(m.group(1)), int(m.group(2)), f))
        if not candidates:
            return 0
        recovered = 0
        for chunk in chunks:
            target = cache_dir / chunk.cache_name
            if target.exists():
                continue
            for a, b, f in candidates:
                if a <= chunk.first_page and b >= chunk.last_page and (b - a) - (chunk.last_page - chunk.first_page) <= 4:
                    data = self._load_cache(f)
                    if data is None:
                        continue
                    target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                    log.info("blocco %d (%s): recuperato dalla cache precedente %s (pagine %d-%d), nessun costo",
                             chunk.index, chunk.label, f, a, b)
                    recovered += 1
                    break
        return recovered

    # --- map in batch: meta' prezzo, ripresa dello stesso batch -----------------------------

    def _run_batch(self, requests: dict[str, dict[str, Any]], state_file: Path, report: PrepReport,
                   progress: Callable[[str], None], what: str) -> dict[str, Any]:
        """Invia (o riprende) un batch e ne attende l'esito. Ritorna custom_id -> messaggio riuscito.

        L'id del batch sta in `state_file`: se il processo muore durante l'attesa, il
        rilancio ritrova lo stesso batch invece di inviarne (e pagarne) un altro.
        """
        batch_id = None
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if set(state.get("ids", [])) >= set(requests):
                    batch_id = state["id"]
                    progress(f"  riprendo il batch gia' inviato ({batch_id}): nessun nuovo costo")
                    log.info("batch %s: riprendo %s", what, batch_id)
            except (OSError, ValueError, KeyError):
                batch_id = None
        if batch_id is None:
            batch = self.client.messages.batches.create(
                requests=[{"custom_id": cid, "params": params} for cid, params in requests.items()])
            batch_id = batch.id
            state_file.write_text(json.dumps({"id": batch_id, "ids": list(requests), "created": time.time()}), encoding="utf-8")
            progress(f"  batch inviato: {len(requests)} {what} a meta' prezzo (id {batch_id}). Attendo l'esito...")
            log.info("batch %s inviato: %d %s, modello %s", batch_id, len(requests), what, self.model)
            report.calls += 1

        last_line = ""
        while True:
            try:
                b = self.client.messages.batches.retrieve(batch_id)
            except Exception as exc:
                log.error("preparazione INTERROTTA aspettando il batch %s: %s | rilanciando riprende lo stesso batch",
                          batch_id, describe_error(exc), exc_info=True)
                raise
            counts = getattr(b, "request_counts", None)
            done = (getattr(counts, "succeeded", 0) or 0) + (getattr(counts, "errored", 0) or 0) if counts else 0
            line = f"  batch: {done}/{len(requests)} {what} finiti, stato {b.processing_status}"
            if line != last_line:
                progress(line)
                log.info("batch %s: %s", batch_id, line.strip())
                last_line = line
            if b.processing_status == "ended":
                break
            time.sleep(self.poll_seconds)

        messages: dict[str, Any] = {}
        for item in self.client.messages.batches.results(batch_id):
            if item.custom_id not in requests:
                continue
            if item.result.type == "succeeded":
                messages[item.custom_id] = item.result.message
            else:
                err = getattr(getattr(item.result, "error", None), "message", None) or item.result.type
                report.warnings.append(f"{item.custom_id}: batch {item.result.type} ({short(str(err), 120)}), da rifare")
                log.warning("batch %s: %s %s: %s", batch_id, item.custom_id, item.result.type, err)
        state_file.unlink(missing_ok=True)
        return messages

    def _extract_batch(self, todo: list[Chunk], cache_dir: Path, report: PrepReport,
                       progress: Callable[[str], None]) -> dict[int, dict[str, Any]]:
        by_id = {chunk.cache_name[:-5]: chunk for chunk in todo}
        messages = self._run_batch({cid: self._params(c) for cid, c in by_id.items()},
                                   cache_dir / "batch-in-corso.json", report, progress, "blocchi")
        results: dict[int, dict[str, Any]] = {}
        failed: list[str] = []
        for cid, chunk in by_id.items():
            message = messages.get(cid)
            if message is None:
                failed.append(chunk.label)
                continue
            data = self._parse(chunk, message, report)
            if data.get("_errore"):
                failed.append(chunk.label)
            else:
                (cache_dir / chunk.cache_name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                report.chunks_batched += 1
            results[chunk.index] = data
        if failed:
            progress(f"  blocchi da rifare al prossimo lancio: {', '.join(failed)}")
        return results

    # --- reduce ------------------------------------------------------------------

    @staticmethod
    def normalize_body(body: str) -> str:
        """Dentro una sezione i titoli `#` e `##` diventano `###`.

        Una sezione e' l'unita' che il master legge e aggiorna: un `##` nel corpo la
        spezzerebbe in sezioni fantasma (viste nel manuale di Vampiri: 123 su 26 testi).
        """
        return re.sub(r"^#{1,2}(?=\s)", "###", body.strip(), flags=re.MULTILINE)

    def merge(self, data: dict[str, Any], report: PrepReport) -> None:
        for sec in data.get("sezioni", []):
            name, title = sec.get("file", ""), sec.get("titolo", "").strip().lstrip("#").strip()
            body = self.normalize_body(sec.get("testo", ""))
            if name not in TARGET_FILES or not title or not body:
                continue
            f = self.campaign.file(name)
            if not f.exists():
                f.write(f"# {Path(name).stem.replace('-', ' ').capitalize()}\n")
            existing = f.get_section(title)
            if existing and body in existing:
                continue  # gia' fuso in una corsa precedente: rilanciare non deve duplicare
            if existing:
                f.set_section(title, f"{existing}\n\n{body}")
                if f.name not in report.merged_files:
                    report.merged_files.append(f.name)
            else:
                f.set_section(title, body)
            self._touch(report, f.name)
        for g in data.get("glossario", []):
            try:
                entry = GlossaryEntry(
                    str(g["term"]).strip(), str(g.get("kind", "altro")).strip(),
                    str(g["description"]).strip(), str(g.get("file", "")).strip(),
                )
            except KeyError:
                continue
            if not entry.term or entry.file not in TARGET_FILES:
                continue
            self.campaign.glossary.upsert(entry)
            report.glossary_entries += 1
            self._touch(report, self.campaign.glossary.file.name)

    def write_source_index(self, path: Path, summaries: list[tuple[Chunk, str]], report: PrepReport) -> None:
        """Un file `fonte-<nome>.md`: cosa c'e' in quali pagine, per tornare al manuale."""
        name = "fonte-" + re.sub(r"[^\w-]+", "-", path.stem).strip("-").lower() + ".md"
        f = self.campaign.file(name)
        lines = [f"# Fonte: {path.name}", "", "Indice per pagine del manuale originale.", ""]
        for chunk, summary in summaries:
            if summary:
                lines += [f"## {chunk.label.capitalize()}", "", summary.strip(), ""]
        f.write("\n".join(lines))
        self.campaign.glossary.upsert(
            GlossaryEntry(path.stem, "fonte", f"Manuale originale, indice per pagine", name)
        )
        self._touch(report, f.name)

    # --- consolida ------------------------------------------------------------------

    def consolidate_sections(self, targets: dict[str, list[str]], cache_dir: Path, report: PrepReport,
                             progress: Callable[[str], None], use_batch: bool = True) -> None:
        """Ripulisce solo le sezioni con testo accodato da piu' blocchi, a gruppi che stanno in una chiamata.

        `targets`: file -> titoli (minuscoli). Ogni gruppo ha una cache per contenuto
        in `cache/manuali/consolidati/`: stesso testo in ingresso, nessuna chiamata.
        I gruppi mancanti partono in batch a meta' prezzo quando sono almeno due.
        """
        groups: list[dict[str, Any]] = []
        for name, lows in targets.items():
            f = self.campaign.file(name)
            sections = f.sections()
            by_low = {t.lower(): t for t in sections if t}
            current: list[str] = []
            size = 0

            def flush() -> None:
                nonlocal current, size
                if current:
                    text = "\n\n".join(f"## {t}\n\n{sections[t]}" for t in current)
                    digest = hashlib.sha256((name + "\n" + text).encode("utf-8")).hexdigest()[:24]
                    groups.append({"file": name, "titles": current, "text": text, "digest": digest})
                current, size = [], 0

            for low in lows:
                title = by_low.get(low)
                if title is None:
                    continue
                body = sections[title]
                if len(body) > CONSOLIDATE_GROUP_CHARS:
                    report.warnings.append(f"{name} / {title}: sezione troppo lunga per il consolidamento, lasciata com'e'")
                    log.warning("consolidamento: %s / %s saltata, %d caratteri", name, title, len(body))
                    continue
                if current and size + len(body) > CONSOLIDATE_GROUP_CHARS:
                    flush()
                current.append(title)
                size += len(body)
            flush()
        if not groups:
            return

        store = self.cache_root / "consolidati"
        store.mkdir(parents=True, exist_ok=True)
        pending: dict[str, dict[str, Any]] = {}
        for i, g in enumerate(groups, start=1):
            g["id"] = f"cons-{i:03d}-{g['digest'][:12]}"
            g["cache"] = store / f"{CACHE_VERSION}-{g['digest']}.md"
            if g["cache"].exists():
                self._apply_consolidation(g, g["cache"].read_text(encoding="utf-8"), report, from_cache=True)
            else:
                pending[g["id"]] = g
        total_sections = sum(len(g["titles"]) for g in groups)
        progress(f"  consolidamento: {total_sections} sezioni ripetute in {len(groups)} gruppi"
                 + (f", {len(groups) - len(pending)} gia' in cache (nessun costo)" if len(pending) < len(groups) else ""))
        if not pending:
            return
        chars = sum(len(g["text"]) for g in pending.values())
        # misurato sul manuale di Vampiri: l'italiano con tabelle Markdown rende ~2,2 caratteri per token,
        # e il testo ripulito esce lungo quasi quanto entra
        in_tokens = chars / 2.2 + 400 * len(pending)
        batch = use_batch and supports_batches(self.client) and len(pending) >= 2
        full = cost_usd(self.model, in_tokens, in_tokens * 0.95, batch=False)
        tokens_text = f"{int(in_tokens):,}".replace(",", ".")
        est = (f"  stima consolidamento: {len(pending)} chiamate, ~{tokens_text} token in ingresso, costo massimo ~${full:.2f}"
               + (f" (in batch a meta' prezzo: ~${full / 2:.2f})" if batch else ""))
        progress(est)
        log.info("consolidamento: %s", est.strip())

        def params(g: dict[str, Any]) -> dict[str, Any]:
            p: dict[str, Any] = dict(
                model=self.model,
                max_tokens=max(self.max_tokens, MAX_OUTPUT_TOKENS),
                system=[{"type": "text", "text": CONSOLIDATE_SECTIONS_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Sezioni di `{g['file']}`:\n\n{g['text']}"}],
            )
            if self.effort:
                p["output_config"] = {"effort": self.effort}
            return p

        if batch:
            messages = self._run_batch({cid: params(g) for cid, g in pending.items()},
                                       cache_dir / "batch-consolidamento.json", report, progress, "gruppi di sezioni")
            for cid, g in pending.items():
                if cid in messages:
                    self._finish_consolidation(g, messages[cid], report)
        else:
            for g in pending.values():
                progress(f"  consolido {g['file']}: {', '.join(g['titles'][:4])}{'...' if len(g['titles']) > 4 else ''}")
                try:
                    response = create_message(self.client, max_seconds=self.block_timeout, **params(g))
                except TimeoutError as exc:
                    report.warnings.append(f"{g['file']}: consolidamento {exc}, sezioni lasciate com'erano")
                    log.warning("consolidamento %s: %s", g["id"], exc)
                    continue
                self._finish_consolidation(g, response, report)

    def _finish_consolidation(self, g: dict[str, Any], response: Any, report: PrepReport) -> None:
        self._count(report, response)
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        log.info("consolidamento %s (%s, %d sezioni): %s | %d -> %d caratteri", g["id"], g["file"], len(g["titles"]),
                 usage_line(response), len(g["text"]), len(text))
        if response.stop_reason != "end_turn":
            report.warnings.append(f"{g['file']}: consolidamento interrotto ({response.stop_reason}), sezioni lasciate com'erano")
            log.warning("consolidamento %s scartato: stop=%s", g["id"], response.stop_reason)
            return
        if self._apply_consolidation(g, text, report, from_cache=False):
            g["cache"].write_text(text + "\n", encoding="utf-8")

    def _apply_consolidation(self, g: dict[str, Any], text: str, report: PrepReport, from_cache: bool) -> bool:
        """Sostituisce nel file le sezioni del gruppo con quelle ripulite. Ritorna True se almeno una e' stata applicata."""
        f = self.campaign.file(g["file"])
        current = f.sections()
        # l'esito puo' contenere `##` solo come titoli delle sezioni del gruppo: ogni altro `##` e' un
        # sottotitolo sfuggito e va riportato dentro la sezione che lo precede
        wanted = {t.lower() for t in g["titles"]}
        lines = []
        for line in text.splitlines():
            m = re.match(r"^##\s+(.+?)\s*$", line)
            if m and m.group(1).lower() not in wanted:
                line = "###" + line[2:]
            lines.append(line)
        cleaned = {t.lower(): body for t, body in split_sections("\n".join(lines)).items() if t}
        applied = 0
        for title in g["titles"]:
            new = cleaned.get(title.lower())
            old = current.get(title, "")
            if not new or len(new) < len(old) * 0.35:
                report.warnings.append(f"{g['file']} / {title}: esito del consolidamento non affidabile, sezione lasciata com'era")
                log.warning("consolidamento %s: sezione '%s' scartata (%d -> %d caratteri)", g["id"], title, len(old), len(new or ""))
                continue
            if new != old:
                f.set_section(title, new)
            applied += 1
        if applied:
            self._touch(report, f.name)
            if from_cache:
                report.consolidated_from_cache += 1
                log.info("consolidamento %s (%s): dalla cache condivisa, nessun costo", g["id"], g["file"])
        return applied > 0

    # --- utilita' -------------------------------------------------------------------

    @staticmethod
    def _touch(report: PrepReport, name: str) -> None:
        if name not in report.files_touched:
            report.files_touched.append(name)

    @staticmethod
    def _count(report: PrepReport, response: Any) -> None:
        report.calls += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            report.input_tokens += getattr(usage, "input_tokens", 0) or 0
            report.output_tokens += getattr(usage, "output_tokens", 0) or 0
