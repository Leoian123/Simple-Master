"""Classi per leggere e modificare i file di campagna in modo sicuro.

Ogni file e' un semplice Markdown. Le modifiche sono atomiche (scrittura su
file temporaneo + rename) e le operazioni tipiche del master (aggiungere in
coda, aggiornare una sezione, sostituire un frammento) sono metodi dedicati,
cosi' l'AI non deve mai riscrivere l'intero file.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def split_sections(content: str) -> dict[str, str]:
    """Mappa titolo -> corpo per ogni `## Titolo` di un testo Markdown ("" = preambolo)."""
    result: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(content))
    preamble = content[: matches[0].start()] if matches else content
    if preamble.strip():
        result[""] = preamble.strip("\n")
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        result[m.group(1)] = content[m.end() : end].strip("\n")
    return result


class CampaignFile:
    """Un file Markdown dentro la cartella della campagna."""

    def __init__(self, path: Path, root: Path):
        self.path = Path(path)
        self.root = Path(root)

    # --- identita' -----------------------------------------------------

    @property
    def name(self) -> str:
        """Nome relativo alla campagna, con slash in stile POSIX."""
        return self.path.relative_to(self.root).as_posix()

    def exists(self) -> bool:
        return self.path.is_file()

    def __repr__(self) -> str:  # pragma: no cover
        return f"CampaignFile({self.name!r})"

    # --- lettura / scrittura -------------------------------------------

    def read(self) -> str:
        if not self.exists():
            raise FileNotFoundError(self.name)
        return self.path.read_text(encoding="utf-8")

    def write(self, text: str) -> None:
        """Scrive l'intero contenuto in modo atomico."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def append(self, text: str) -> None:
        """Aggiunge testo in coda, garantendo una riga vuota di separazione."""
        current = self.read() if self.exists() else ""
        text = text.strip("\n")
        if not text:
            return
        if current and not current.endswith("\n"):
            current += "\n"
        if current and not current.endswith("\n\n"):
            current += "\n"
        self.write(current + text + "\n")

    def replace(self, old: str, new: str) -> int:
        """Sostituisce la prima occorrenza di `old`. Ritorna 1 se trovata, 0 altrimenti."""
        content = self.read()
        if old not in content:
            return 0
        self.write(content.replace(old, new, 1))
        return 1

    # --- sezioni markdown (## Titolo) ----------------------------------

    def sections(self) -> dict[str, str]:
        """Mappa titolo -> corpo per ogni sezione `## Titolo`.

        Il testo prima della prima sezione e' sotto la chiave vuota "".
        """
        return split_sections(self.read() if self.exists() else "")

    def get_section(self, title: str) -> str | None:
        return self.sections().get(title)

    def set_section(self, title: str, body: str) -> str:
        """Crea o sostituisce la sezione `## title`. Ritorna "created" o "updated"."""
        content = self.read() if self.exists() else ""
        body = body.strip("\n")
        block = f"## {title}\n\n{body}\n" if body else f"## {title}\n"
        for m in _SECTION_RE.finditer(content):
            if m.group(1).strip().lower() == title.strip().lower():
                nxt = _SECTION_RE.search(content, m.end())
                end = nxt.start() if nxt else len(content)
                new_content = content[: m.start()] + block + ("\n" if nxt else "") + content[end:]
                self.write(new_content)
                return "updated"
        if content and not content.endswith("\n"):
            content += "\n"
        if content and not content.endswith("\n\n"):
            content += "\n"
        self.write(content + block)
        return "created"

    def remove_section(self, title: str) -> bool:
        """Toglie la sezione `## title` (titolo e corpo). Ritorna True se c'era."""
        content = self.read() if self.exists() else ""
        for m in _SECTION_RE.finditer(content):
            if m.group(1).strip().lower() == title.strip().lower():
                nxt = _SECTION_RE.search(content, m.end())
                end = nxt.start() if nxt else len(content)
                self.write((content[: m.start()].rstrip("\n") + "\n\n" + content[end:]).rstrip("\n") + "\n")
                return True
        return False

    def outline(self) -> list[str]:
        """Titoli delle sezioni, utile per un indice a basso costo di token."""
        return [t for t in self.sections() if t]
