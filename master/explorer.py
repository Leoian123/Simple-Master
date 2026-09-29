"""Scelta di un file sul PC per la preparazione da manuale.

Due vie:
- `native_pick`: apre la finestra "Apri" di Windows (Esplora risorse) tramite
  PowerShell e ritorna il percorso scelto. Funziona perche' il server gira sullo
  stesso PC del browser.
- `list_dir` / `roots`: un esploratore minimo servito alla pagina, per gli
  altri sistemi o se la finestra nativa non si apre.
"""

from __future__ import annotations

import os
import string
import subprocess
import sys
import threading
from pathlib import Path

SOURCE_SUFFIXES = {".pdf", ".md", ".txt"}
_dialog_lock = threading.Lock()

_PS_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'Scegli il manuale'
$d.Filter = 'Manuali (*.pdf;*.md;*.txt)|*.pdf;*.md;*.txt|PDF (*.pdf)|*.pdf|Tutti i file (*.*)|*.*'
$d.Multiselect = $false
if ($env:SM_INITIAL_DIR -and (Test-Path $env:SM_INITIAL_DIR)) { $d.InitialDirectory = $env:SM_INITIAL_DIR }
$owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true; ShowInTaskbar = $false; Opacity = 0 }
$owner.Show(); $owner.Activate()
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.FileName }
$owner.Close()
"""


def native_available() -> bool:
    return sys.platform == "win32"


def native_pick(initial: str | None = None, timeout: int = 600) -> str | None:
    """Apre la finestra di Windows e ritorna il file scelto, None se annullata."""
    if not native_available():
        raise RuntimeError("La finestra di Esplora risorse e' disponibile solo su Windows")
    if not _dialog_lock.acquire(blocking=False):
        raise RuntimeError("Una finestra di scelta e' gia' aperta")
    try:
        env = dict(os.environ)
        if initial:
            env["SM_INITIAL_DIR"] = initial
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"PowerShell ha risposto con errore: {result.stderr.strip()[:200]}")
        chosen = result.stdout.strip().splitlines()
        return chosen[-1].strip() if chosen and chosen[-1].strip() else None
    finally:
        _dialog_lock.release()


# --- esploratore nella pagina ---------------------------------------------------------


def roots() -> list[dict[str, str]]:
    """Punti di partenza: unita' e cartelle utili dell'utente."""
    out: list[dict[str, str]] = []
    home = Path.home()
    for label, p in (("Home", home), ("Desktop", home / "Desktop"), ("Documenti", home / "Documents"), ("Download", home / "Downloads")):
        if p.is_dir():
            out.append({"label": label, "path": str(p)})
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:/")
            if drive.exists():
                out.append({"label": f"{letter}:", "path": str(drive)})
    else:
        out.append({"label": "/", "path": "/"})
    return out


def list_dir(path: str | None) -> dict:
    """Cartelle e file sorgente (pdf, md, txt) di una cartella, ordinati."""
    if not path:
        return {"path": "", "parent": None, "dirs": [], "files": [], "roots": roots()}
    p = Path(path).expanduser()
    if not p.is_dir():
        raise FileNotFoundError(f"Cartella non trovata: {path}")
    p = p.resolve()
    dirs: list[dict] = []
    files: list[dict] = []
    try:
        entries = sorted(p.iterdir(), key=lambda e: e.name.lower())
    except PermissionError as exc:
        raise PermissionError(f"Accesso negato a {p}") from exc
    for e in entries:
        if e.name.startswith((".", "$")):
            continue
        try:
            if e.is_dir():
                dirs.append({"name": e.name, "path": str(e)})
            elif e.suffix.lower() in SOURCE_SUFFIXES:
                files.append({"name": e.name, "path": str(e), "size": e.stat().st_size})
        except OSError:
            continue
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "dirs": dirs, "files": files, "roots": roots()}
