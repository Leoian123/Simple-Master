"""Il glossario: primo riferimento che l'AI consulta a ogni turno.

E' una tabella Markdown con una riga per entita' (personaggio, luogo, oggetto,
fazione, regola...). Ogni riga dice in una frase cos'e' e in quale file stanno
i dettagli. L'AI legge il glossario nel system prompt e da li' decide quali
file aprire. Restare brevi qui e' essenziale: il glossario e' un indice, non
un'enciclopedia.
"""

from __future__ import annotations

from dataclasses import dataclass

from .files import CampaignFile

HEADER = ["Termine", "Tipo", "Descrizione", "File"]

DEFAULT_TEXT = """# Glossario

Indice delle entita' della campagna. Una riga per termine, descrizione in una
frase, e il file dove si trovano i dettagli. L'AI lo consulta per primo.

| Termine | Tipo | Descrizione | File |
|---|---|---|---|
"""


@dataclass
class GlossaryEntry:
    term: str
    kind: str
    description: str
    file: str

    def row(self) -> str:
        cells = [self.term, self.kind, self.description, self.file]
        return "| " + " | ".join(c.replace("|", "\\|").strip() for c in cells) + " |"


def _split_row(line: str) -> list[str]:
    inner = line.strip().strip("|")
    cells, cur, escaped = [], [], False
    for ch in inner:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur).strip())
    return cells


class Glossary:
    """Vista strutturata sul file `glossario.md`."""

    def __init__(self, file: CampaignFile):
        self.file = file
        if not file.exists():
            file.write(DEFAULT_TEXT)

    # --- parsing --------------------------------------------------------

    def _lines(self) -> list[str]:
        return self.file.read().splitlines()

    @staticmethod
    def _is_row(line: str) -> bool:
        s = line.strip()
        return s.startswith("|") and s.endswith("|")

    @staticmethod
    def _is_separator(line: str) -> bool:
        if not Glossary._is_row(line):
            return False
        s = line.strip().strip("|")
        for ch in "|-: ":
            s = s.replace(ch, "")
        return s == ""

    def entries(self) -> list[GlossaryEntry]:
        out: list[GlossaryEntry] = []
        seen_header = False
        for line in self._lines():
            if not self._is_row(line) or self._is_separator(line):
                continue
            if not seen_header:
                seen_header = True  # prima riga della tabella = intestazione
                continue
            cells = _split_row(line)
            cells += [""] * (4 - len(cells))
            out.append(GlossaryEntry(*cells[:4]))
        return out

    def get(self, term: str) -> GlossaryEntry | None:
        key = term.strip().lower()
        return next((e for e in self.entries() if e.term.lower() == key), None)

    # --- modifica -------------------------------------------------------

    def upsert(self, entry: GlossaryEntry) -> str:
        """Aggiorna la riga se il termine esiste, altrimenti la aggiunge.

        Ritorna "created" o "updated".
        """
        lines = self._lines()
        key = entry.term.strip().lower()
        last_row = -1
        seen_header = False
        for i, line in enumerate(lines):
            if not self._is_row(line):
                continue
            last_row = i
            if self._is_separator(line):
                continue
            if not seen_header:
                seen_header = True
                continue
            if _split_row(line)[0].lower() == key:
                lines[i] = entry.row()
                self.file.write("\n".join(lines) + "\n")
                return "updated"
        if last_row == -1:  # nessuna tabella: la creiamo
            lines += ["", "| " + " | ".join(HEADER) + " |", "|---|---|---|---|"]
            last_row = len(lines) - 1
        lines.insert(last_row + 1, entry.row())
        self.file.write("\n".join(lines) + "\n")
        return "created"

    def remove(self, term: str) -> bool:
        lines = self._lines()
        key = term.strip().lower()
        seen_header = False
        for i, line in enumerate(lines):
            if not self._is_row(line) or self._is_separator(line):
                continue
            if not seen_header:
                seen_header = True
                continue
            if _split_row(line)[0].lower() == key:
                del lines[i]
                self.file.write("\n".join(lines) + "\n")
                return True
        return False

    def text(self) -> str:
        return self.file.read()

    # --- due livelli: indice corto nel prompt, dettaglio su richiesta -------------------------

    COMPACT_ABOVE_CHARS = 8_000  # sotto questa soglia il glossario intero costa poco: va nel prompt

    def prompt_text(self, only_files: tuple[str, ...] | None = None) -> str:
        """Cio' che entra nel system prompt a ogni turno.

        Glossario piccolo: la tabella intera. Glossario grande (un manuale da
        centinaia di voci): solo i termini, raggruppati per tipo e file, senza
        descrizioni. Le descrizioni si chiedono con il tool `lookup_glossary`,
        i dettagli con `read_file(file, sezione)`.

        `only_files`: prefissi dei file che interessano. Il costruttore di schede
        passa meccanica/creazione-pg.md e schede/: le centinaia di voci
        dell'ambientazione non gli servono a ogni chiamata (restano a portata di
        `lookup_glossary`).
        """
        full = self.text()
        if len(full) <= self.COMPACT_ABOVE_CHARS:
            return full
        groups: dict[tuple[str, str], list[str]] = {}
        skipped = 0
        for e in self.entries():
            if only_files is not None and not (e.file or "").startswith(only_files):
                skipped += 1
                continue
            groups.setdefault((e.kind or "altro", e.file or "?"), []).append(e.term)
        lines = [
            "Indice compatto: solo i termini, per tipo e file. Per la descrizione di un termine usa",
            "`lookup_glossary`; per i dettagli `read_file(file, sezione)`: spesso la sezione ha il nome del termine.",
            "",
        ]
        for (kind, file), terms in sorted(groups.items()):
            lines.append(f"**{kind}** in `{file}`: " + "; ".join(sorted(terms, key=str.lower)))
        if skipped:
            lines.append(f"\n(Altre {skipped} voci su mondo, personaggi e regole generali: cercale con `lookup_glossary` solo se servono.)")
        return "\n".join(lines)

    def lookup(self, query: str, limit: int = 25) -> list[GlossaryEntry]:
        """Voci il cui termine o descrizione contiene una delle parole cercate (prima i termini esatti)."""
        words = [w for w in query.lower().replace(",", " ").split() if len(w) > 1]
        if not words:
            return []
        q = query.strip().lower()
        scored: list[tuple[int, GlossaryEntry]] = []
        for e in self.entries():
            term, desc = e.term.lower(), e.description.lower()
            if term == q:
                score = 0
            elif q in term:
                score = 1
            elif all(w in term for w in words):
                score = 2
            elif any(w in term for w in words):
                score = 3
            elif all(w in desc for w in words):
                score = 4
            else:
                continue
            scored.append((score, e))
        scored.sort(key=lambda s: (s[0], s[1].term.lower()))
        return [e for _, e in scored[:limit]]
