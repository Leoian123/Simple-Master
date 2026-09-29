"""Registro con marca temporale di tutto cio' che fanno i processi, con rotazione.

Un file per giorno, processo e campagna in `log/` alla radice del progetto:

    log/2026-09-17-gioco-esempio.log
    log/2026-09-17-preparazione-mia.log

Ogni riga: data ora, livello, modulo, messaggio. Ci finiscono: avvio con
versione e argomenti, ogni chiamata API (modello, effort, esito, token),
ogni tool eseguito, ogni blocco della preparazione, ogni errore con traceback
e con la riga "INTERROTTO ..." che dice a che punto ci si e' fermati.
Anche le eccezioni non gestite e le richieste HTTP dell'SDK passano di qui.

Per capire perche' un processo si e' fermato: aprire l'ultimo file in `log/`
e cercare "INTERROTT" o "ERROR".

## Rotazione
- un file che supera MAX_MB viene rinominato `.log.1`, `.log.2`... (fino a BACKUPS);
- a ogni avvio `rotate()` sposta in `log/archivio/AAAA-MM/` i file piu' vecchi
  di KEEP_DAYS giorni (o oltre MAX_FILES file) e cancella dall'archivio quelli
  piu' vecchi di ARCHIVE_DAYS;
- da terminale: `python main.py --logs list|clean|dismiss|purge` (vedi main.py)
  per elencare, archiviare o cancellare in modo organizzato.
Variabili d'ambiente (anche in .env): MASTER_LOG_KEEP_DAYS, MASTER_LOG_ARCHIVE_DAYS,
MASTER_LOG_MAX_FILES, MASTER_LOG_MAX_MB.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "log"
ARCHIVE_NAME = "archivio"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

KEEP_DAYS = int(os.environ.get("MASTER_LOG_KEEP_DAYS", "7"))
ARCHIVE_DAYS = int(os.environ.get("MASTER_LOG_ARCHIVE_DAYS", "30"))
MAX_FILES = int(os.environ.get("MASTER_LOG_MAX_FILES", "40"))
MAX_MB = float(os.environ.get("MASTER_LOG_MAX_MB", "5"))
BACKUPS = 5

_MARK = "_simple_master_handler"
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z]+)(?:-(.+?))?\.log(?:\.(\d+))?$")

# Senza setup() (es. nei test) i messaggi restano silenziosi invece di finire su stderr.
logging.getLogger("master").addHandler(logging.NullHandler())


# --- scrittura ------------------------------------------------------------------


def log_path(process: str, campaign: str | None = None, log_dir: Path | None = None, day: _dt.date | None = None) -> Path:
    day = day or _dt.date.today()
    name = f"{day.isoformat()}-{process}" + (f"-{campaign}" if campaign else "") + ".log"
    return Path(log_dir or LOG_DIR) / name


def setup(
    process: str,
    campaign: str | None = None,
    log_dir: Path | None = None,
    level: int = logging.INFO,
    rotate_now: bool = True,
) -> Path:
    """Attiva il registro su file per questo processo. Ritorna il percorso del file."""
    log_dir = Path(log_dir or LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_path(process, campaign, log_dir)
    unfinished = flag_unfinished_runs(log_dir)
    rotation = rotate(log_dir) if rotate_now else None

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        if getattr(h, _MARK, False):
            root.removeHandler(h)
            h.close()
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=int(MAX_MB * 1024 * 1024), backupCount=BACKUPS, encoding="utf-8"
    )
    setattr(handler, _MARK, True)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FORMAT, DATEFMT))
    root.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.INFO)  # una riga per richiesta HTTP: utile per 429/529
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.INFO)  # tentativi e attese dell'SDK
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # gli avvisi sui font non servono a nessuno
    logging.captureWarnings(True)
    _install_excepthook()

    log = logging.getLogger("master")
    log.info("=" * 60)
    log.info(
        "AVVIO %s | pid=%d | campagna=%s | python %s | %s | argv=%s",
        process, os.getpid(), campaign or "-", sys.version.split()[0], platform.platform(), sys.argv,
    )
    if rotation and (rotation.archived or rotation.deleted):
        log.info("rotazione registri: %s", rotation.summary())
    for name, last in unfinished:
        log.warning("la corsa precedente in %s si e' chiusa a forza; ultima attivita': %s", name, last)
    return path


CLOSURE_MARKERS = ("FINE ", "ERRORE NON GESTITO", "CHIUSURA FORZATA", "finestra chiusa a riposo")
# l'ultima riga di lavoro dice se il processo stava elaborando: richiesta partita senza risposta,
# estrazione, batch o consolidamento in corso
_BUSY = re.compile(r"api #?\d*\s*->|api ->|: estrazione|batch inviato|stato in_progress|consolido|organizzo|HTTP Request: POST")
_PID = re.compile(r"\bpid=(\d+)\b")
RECENT_SECONDS = 20 * 60  # senza pid (registri vecchi): un file scritto da poco puo' essere di un processo vivo


def pid_alive(pid: int) -> bool:
    """True se il processo esiste ancora. Niente os.kill su Windows: il segnale 0 li' e' CTRL+C."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def flag_unfinished_runs(log_dir: Path) -> list[tuple[str, str]]:
    """Trova, nei registri correnti, corse iniziate (AVVIO) e mai chiuse (niente FINE).

    Tre casi:
    - il processo e' ancora vivo (pid nell'AVVIO, o file scritto da poco): non si tocca;
    - il processo non c'e' piu' e stava lavorando (richiesta senza risposta, estrazione,
      batch, consolidamento): si aggiunge una riga `INTERROTTA` con l'ultima attivita',
      cosi' la diagnosi la mostra;
    - il processo non c'e' piu' ed era a riposo: e' la normale chiusura della finestra,
      si annota senza allarme.
    Ritorna [(nome file, ultima riga di attivita')] solo per le vere interruzioni.
    """
    found: list[tuple[str, str]] = []
    for info in scan(log_dir, inspect=False, include_archive=False):
        try:
            lines = info.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        starts = [i for i, line in enumerate(lines) if " AVVIO " in line]
        if not starts:
            continue
        text = "\n".join(lines)
        markers: list[str] = []
        for n, start in enumerate(starts):
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            started_at = lines[start][:19]  # data e ora dell'AVVIO
            if f"corsa avviata alle {started_at}" in text:
                continue  # gia' segnalata a un avvio precedente
            segment = lines[start + 1 : end]
            if any(any(m in l for m in CLOSURE_MARKERS) for l in segment):
                continue
            is_last = n + 1 == len(starts)
            if is_last:  # solo l'ultima corsa del file puo' essere ancora in esecuzione
                m = _PID.search(lines[start])
                if m:
                    if pid_alive(int(m.group(1))):
                        continue
                elif _dt.datetime.now().timestamp() - info.path.stat().st_mtime < RECENT_SECONDS:
                    continue
            activity = [l for l in segment if l.strip() and not l.startswith((" ", "Traceback")) and "=====" not in l]
            last = short(activity[-1], 200) if activity else "(nessuna attivita' dopo l'avvio)"
            stamp = _dt.datetime.now().strftime(DATEFMT)
            if activity and _BUSY.search(activity[-1]):
                markers.append(f"{stamp} WARNING master: corsa avviata alle {started_at} INTERROTTA senza chiusura "
                               f"(CHIUSURA FORZATA della finestra o del processo); ultima attivita': {last}")
                found.append((info.name, last))
            else:
                markers.append(f"{stamp} INFO    master: corsa avviata alle {started_at}: finestra chiusa a riposo, "
                               f"nessuna elaborazione in corso")
        if markers:
            try:
                with open(info.path, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(markers) + "\n")
            except OSError:
                continue
    return found


def _install_excepthook() -> None:
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        logging.getLogger("master").critical(
            "ERRORE NON GESTITO, processo INTERROTTO: %s\n%s",
            describe_error(exc), "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        previous(exc_type, exc, tb)

    if getattr(sys.excepthook, "__name__", "") != "hook":
        sys.excepthook = hook


# --- utilita' di formato ---------------------------------------------------------


def describe_error(exc: BaseException) -> str:
    """Una riga leggibile: tipo, codice HTTP e request-id se e' un errore API."""
    parts = [type(exc).__name__]
    status = getattr(exc, "status_code", None)
    if status is not None:
        parts.append(f"http={status}")
    request_id = getattr(exc, "request_id", None)
    if not request_id:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            request_id = headers.get("request-id")
    if request_id:
        parts.append(f"request-id={request_id}")
    message = str(exc).strip().replace("\n", " ")
    if message:
        parts.append(short(message, 300))
    return " ".join(parts)


def short(value: Any, n: int = 120) -> str:
    """Testo su una riga, troncato, per non gonfiare il registro."""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            value = repr(value)
    value = " ".join(value.split())
    return value if len(value) <= n else value[: n - 3] + "..."


def usage_line(response: Any) -> str:
    """'stop=end_turn in=1234 out=56 cache=1000' da una risposta dell'API."""
    usage = getattr(response, "usage", None)
    parts = [f"stop={getattr(response, 'stop_reason', '?')}"]
    if usage is not None:
        parts.append(f"in={getattr(usage, 'input_tokens', 0) or 0}")
        parts.append(f"out={getattr(usage, 'output_tokens', 0) or 0}")
        cache = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache:
            parts.append(f"cache={cache}")
    details = getattr(response, "stop_details", None)
    if details is not None and getattr(details, "category", None):
        parts.append(f"rifiuto={details.category}")
    return " ".join(parts)


# --- lettura e rotazione ----------------------------------------------------------


@dataclass
class LogInfo:
    path: Path
    date: _dt.date
    process: str
    campaign: str
    size: int
    archived: bool
    lines: int = 0
    errors: int = 0
    interruptions: int = 0
    last: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    def row(self) -> str:
        flag = "ARCHIVIO " if self.archived else ""
        state = f"{self.interruptions} interruz., {self.errors} errori" if (self.errors or self.interruptions) else "ok"
        return f"{flag}{self.name:<48} {self.size / 1024:7.1f} KB {self.lines:6d} righe  {state}"


def parse_name(path: Path) -> tuple[_dt.date, str, str] | None:
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    try:
        day = _dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return day, m.group(2), m.group(3) or ""


def _inspect(info: LogInfo) -> LogInfo:
    lines = errors = interruptions = 0
    last = ""
    try:
        with open(info.path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                lines += 1
                if " ERROR " in line or " CRITICAL " in line:
                    errors += 1
                if "INTERROTT" in line:
                    interruptions += 1
                if line.strip():
                    last = line.rstrip()
    except OSError:
        pass
    info.lines, info.errors, info.interruptions, info.last = lines, errors, interruptions, short(last, 160)
    return info


def scan(log_dir: Path | None = None, inspect: bool = True, include_archive: bool = True) -> list[LogInfo]:
    """Tutti i registri, i piu' recenti per primi."""
    log_dir = Path(log_dir or LOG_DIR)
    out: list[LogInfo] = []
    if not log_dir.is_dir():
        return out
    candidates: list[tuple[Path, bool]] = [(p, False) for p in log_dir.iterdir() if p.is_file()]
    archive = log_dir / ARCHIVE_NAME
    if include_archive and archive.is_dir():
        candidates += [(p, True) for p in archive.rglob("*") if p.is_file()]
    for path, archived in candidates:
        parsed = parse_name(path)
        if parsed is None:
            continue  # es. .esaminati.json
        day, process, campaign = parsed
        info = LogInfo(path, day, process, campaign, path.stat().st_size, archived)
        out.append(_inspect(info) if inspect else info)
    out.sort(key=lambda i: (i.archived, i.date, i.name), reverse=True)
    return out


@dataclass
class RotationReport:
    archived: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    kept: int = 0

    def summary(self) -> str:
        return f"archiviati {len(self.archived)}, cancellati {len(self.deleted)}, in log/ restano {self.kept}"


def _archive_file(path: Path, log_dir: Path, report: RotationReport, day: _dt.date) -> None:
    target_dir = log_dir / ARCHIVE_NAME / day.strftime("%Y-%m")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}.dup{int(path.stat().st_mtime)}{path.suffix}"
    shutil.move(str(path), str(target))
    report.archived.append(path.name)


def rotate(
    log_dir: Path | None = None,
    keep_days: int | None = None,
    archive_days: int | None = None,
    max_files: int | None = None,
    today: _dt.date | None = None,
) -> RotationReport:
    """Archivia i registri vecchi o in eccesso, cancella l'archivio scaduto. Mai il giorno corrente."""
    log_dir = Path(log_dir or LOG_DIR)
    keep_days = KEEP_DAYS if keep_days is None else keep_days
    archive_days = ARCHIVE_DAYS if archive_days is None else archive_days
    max_files = MAX_FILES if max_files is None else max_files
    today = today or _dt.date.today()
    report = RotationReport()
    if not log_dir.is_dir():
        return report

    current = [i for i in scan(log_dir, inspect=False, include_archive=False)]
    cutoff = today - _dt.timedelta(days=keep_days)
    keep: list[LogInfo] = []
    for info in current:
        if info.date < cutoff and info.date != today:
            _archive_file(info.path, log_dir, report, info.date)
        else:
            keep.append(info)
    # eccesso di file: via i piu' vecchi, ma mai quelli di oggi
    keep.sort(key=lambda i: (i.date, i.name), reverse=True)
    while len(keep) > max_files:
        victim = keep.pop()
        if victim.date == today:
            keep.append(victim)
            break
        _archive_file(victim.path, log_dir, report, victim.date)
    report.kept = len(keep)

    archive = log_dir / ARCHIVE_NAME
    if archive.is_dir():
        expiry = today - _dt.timedelta(days=archive_days)
        for path in sorted(archive.rglob("*")):
            if not path.is_file():
                continue
            parsed = parse_name(path)
            if parsed and parsed[0] < expiry:
                path.unlink()
                report.deleted.append(path.name)
        for folder in sorted(archive.glob("*"), reverse=True):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
    return report


def dismiss(
    files: Iterable[str] = (),
    before: _dt.date | None = None,
    older_than_days: int | None = None,
    log_dir: Path | None = None,
    today: _dt.date | None = None,
) -> RotationReport:
    """Sposta in archivio registri scelti per nome o per data. Il file di oggi in uso resta."""
    log_dir = Path(log_dir or LOG_DIR)
    today = today or _dt.date.today()
    if older_than_days is not None:
        before = today - _dt.timedelta(days=older_than_days)
    wanted = {Path(f).name for f in files}
    report = RotationReport()
    for info in scan(log_dir, inspect=False, include_archive=False):
        chosen = info.name in wanted or (before is not None and info.date < before)
        if chosen and info.date != today:
            _archive_file(info.path, log_dir, report, info.date)
    report.kept = len(scan(log_dir, inspect=False, include_archive=False))
    return report


def purge(
    log_dir: Path | None = None,
    older_than_days: int | None = None,
    everything: bool = False,
    today: _dt.date | None = None,
) -> RotationReport:
    """Cancella l'archivio (tutto o solo la parte piu' vecchia di N giorni).

    Con `everything=True` cancella anche i registri correnti tranne quelli di oggi.
    """
    log_dir = Path(log_dir or LOG_DIR)
    today = today or _dt.date.today()
    report = RotationReport()
    cutoff = today - _dt.timedelta(days=older_than_days) if older_than_days is not None else None
    for info in scan(log_dir, inspect=False, include_archive=True):
        if info.date == today:
            continue
        if not info.archived and not everything:
            continue
        if cutoff is not None and info.date >= cutoff:
            continue
        info.path.unlink()
        report.deleted.append(info.name)
    archive = log_dir / ARCHIVE_NAME
    if archive.is_dir():
        for folder in sorted(archive.glob("*"), reverse=True):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        if not any(archive.iterdir()):
            archive.rmdir()
    report.kept = len(scan(log_dir, inspect=False, include_archive=False))
    return report


# --- diagnosi automatica (usata dagli hook di Claude Code) --------------------------

EXAMINED_FILE = ".esaminati.json"


def _examined(log_dir: Path) -> dict[str, int]:
    """Nome file -> numero di righe gia' esaminate."""
    path = log_dir / EXAMINED_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save_examined(log_dir: Path, data: dict[str, int]) -> None:
    (log_dir / EXAMINED_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


@dataclass
class Finding:
    file: str
    line_no: int
    text: str
    context: list[str] = field(default_factory=list)


def diagnose(log_dir: Path | None = None, context_lines: int = 6, max_findings: int = 20) -> tuple[list[Finding], int]:
    """Interruzioni ed errori NON ancora esaminati nei registri correnti.

    Ritorna (trovati, file_esaminati_da_zero). Le righe gia' viste (marcate con
    `mark_examined`) non tornano piu'.
    """
    log_dir = Path(log_dir or LOG_DIR)
    examined = _examined(log_dir)
    findings: list[Finding] = []
    scanned = 0
    for info in scan(log_dir, inspect=False, include_archive=False):
        start = examined.get(info.name, 0)
        try:
            lines = info.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        scanned += 1
        for i in range(start, len(lines)):
            line = lines[i]
            if "INTERROTT" in line or " CRITICAL " in line or (" ERROR " in line and "INTERROTT" not in line):
                ctx = [l.rstrip() for l in lines[max(0, i - context_lines) : i] if l.strip()]
                findings.append(Finding(info.name, i + 1, line.rstrip(), ctx))
                if len(findings) >= max_findings:
                    return findings, scanned
    return findings, scanned


def mark_examined(files: Iterable[str] = (), everything: bool = False, log_dir: Path | None = None) -> list[str]:
    """Segna come esaminati i registri indicati (o tutti): le loro righe attuali non tornano in diagnosi.

    I file non di oggi vengono anche archiviati (dismiss); quello di oggi resta in
    uso ma con la riga di lettura avanzata.
    """
    log_dir = Path(log_dir or LOG_DIR)
    examined = _examined(log_dir)
    wanted = {Path(f).name for f in files}
    today = _dt.date.today()
    marked: list[str] = []
    for info in scan(log_dir, inspect=False, include_archive=False):
        if not (everything or info.name in wanted):
            continue
        try:
            n = sum(1 for _ in open(info.path, encoding="utf-8", errors="replace"))
        except OSError:
            continue
        examined[info.name] = n
        marked.append(info.name)
    dismiss(files=list(marked), log_dir=log_dir, today=today)
    # le voci di file archiviati o spariti non servono piu'
    present = {i.name for i in scan(log_dir, inspect=False, include_archive=False)}
    examined = {k: v for k, v in examined.items() if k in present}
    _save_examined(log_dir, examined)
    return marked


def diagnosis_text(log_dir: Path | None = None, brief: bool = False) -> str:
    """Testo per Claude Code: cosa si e' fermato e dove leggere. Vuoto = niente di nuovo."""
    log_dir = Path(log_dir or LOG_DIR)
    findings, scanned = diagnose(log_dir)
    if not findings:
        return ""
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.file, []).append(f)
    lines = [
        f"REGISTRI SIMPLE MASTER: {len(findings)} interruzioni/errori non ancora esaminati in {len(by_file)} file (cartella {log_dir}).",
    ]
    for name, items in by_file.items():
        lines.append(f"- {name}: righe " + ", ".join(str(i.line_no) for i in items))
        for it in items[: 3 if brief else 10]:
            lines.append(f"    r.{it.line_no}: {short(it.text, 220)}")
    lines.append(
        "Procedura: leggi le righe indicate (e le ~6 precedenti per il contesto), spiega la causa, correggi; "
        "poi segna come esaminati con: python main.py --logs examined --log-files <nome>... "
        "(o --log-all). Non torneranno in questa diagnosi."
    )
    return "\n".join(lines)


def listing(log_dir: Path | None = None) -> str:
    """Tabella leggibile: nome, dimensione, righe, errori e interruzioni, ultima riga se fermo."""
    infos = scan(log_dir)
    if not infos:
        return "Nessun registro."
    lines = [f"{'registro':<48} {'dim.':>10} {'righe':>6}  stato", "-" * 90]
    for info in infos:
        lines.append(info.row())
        if info.interruptions or info.errors:
            lines.append(f"    ultima riga: {info.last}")
    total = sum(i.size for i in infos)
    lines.append("-" * 90)
    lines.append(f"{len(infos)} file, {total / 1024:.1f} KB totali. Politica: tieni {KEEP_DAYS} giorni / {MAX_FILES} file, archivio {ARCHIVE_DAYS} giorni, max {MAX_MB:g} MB a file.")
    return "\n".join(lines)
