"""La campagna: una cartella di file Markdown piu' il glossario.

Struttura:
    glossario.md        indice, primo riferimento dell'AI
    meccanica/          regole del sistema: come si gioca, come si crea un personaggio,
                        statistiche di oggetti e creature (numeri, tabelle, tiri)
    ambientazione/      il mondo: luoghi, fazioni, personaggi non giocanti, trame, diario
    schede/             una scheda per personaggio giocante: <pg>.json i dati (vedi sheet.py),
                        <pg>.md la vista leggibile generata dai dati
    sessioni/           il giocato: <personaggio>.jsonl, trascrizione e ripresa insieme (non indicizzato)
    stato.json          personaggio attivo e numero della sessione in corso
    preparazione/       cache della preparazione da manuale (non indicizzata)
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path

from .files import CampaignFile
from .glossary import Glossary

ALLOWED_SUFFIXES = {".md", ".txt"}
GLOSSARY_NAME = "glossario.md"
MECHANICS_DIR = "meccanica"
SETTING_DIR = "ambientazione"
SHEETS_DIR = "schede"
SESSION_DIR = "sessioni"
PREP_DIR = "preparazione"
HIDDEN_DIRS = {SESSION_DIR, PREP_DIR}
DIARY = f"{SETTING_DIR}/diario.md"
STATE_FILE = "stato.json"  # legame campagna <-> personaggio attivo

TEMPLATE_FILES = {
    f"{MECHANICS_DIR}/regole.md": "# Regole\n\n## Sistema\n\nDescrivi qui il sistema di gioco.\n",
    f"{MECHANICS_DIR}/creazione-pg.md": "# Creazione del personaggio\n\n## Passi\n\nDescrivi qui come si crea un personaggio.\n",
    f"{MECHANICS_DIR}/oggetti.md": "# Oggetti\n",
    f"{MECHANICS_DIR}/creature.md": "# Creature\n",
    f"{SETTING_DIR}/mondo.md": "# Mondo\n",
    f"{SETTING_DIR}/npc.md": "# Personaggi non giocanti\n",
    f"{SETTING_DIR}/avventura.md": "# Avventura\n",
    f"{SETTING_DIR}/diario.md": "# Diario della campagna\n",
    f"{SHEETS_DIR}/.gitkeep": "",
}


class Campaign:
    """Accesso sicuro ai file di una campagna (tutto resta dentro `root`)."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Cartella campagna non trovata: {self.root}")
        self.glossary = Glossary(self.file(GLOSSARY_NAME))

    @property
    def name(self) -> str:
        return self.root.name

    # --- risoluzione sicura dei nomi -----------------------------------

    def resolve(self, name: str) -> Path:
        """Trasforma un nome relativo in un percorso dentro la campagna.

        Rifiuta percorsi assoluti, `..`, e suffissi non testuali.
        """
        clean = name.strip().replace("\\", "/").lstrip("/")
        if not clean or clean.startswith(".."):
            raise ValueError(f"Nome file non valido: {name!r}")
        path = (self.root / clean).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError(f"Il file deve stare dentro la campagna: {name!r}")
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"Sono ammessi solo file {sorted(ALLOWED_SUFFIXES)}: {name!r}")
        return path

    def file(self, name: str) -> CampaignFile:
        return CampaignFile(self.resolve(name), self.root)

    def files(self) -> list[CampaignFile]:
        """Tutti i file testuali della campagna, escluse trascrizioni e cache di preparazione."""
        out = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            if p.name.startswith(".") or HIDDEN_DIRS & set(p.relative_to(self.root).parts):
                continue
            out.append(CampaignFile(p, self.root))
        return out

    def index(self, max_titles: int = 12, folders: set[str] | None = None) -> str:
        """Elenco compatto `file: sezione, sezione...` per il system prompt, raggruppato per cartella.

        `folders` limita alle cartelle indicate ("" = radice): il costruttore di schede non ha
        bisogno dell'ambientazione intera a ogni chiamata.
        """
        groups: dict[str, list[str]] = {}
        for f in self.files():
            folder = f.name.split("/")[0] if "/" in f.name else ""
            if folders is not None and folder not in folders:
                continue
            titles = f.outline()
            heads = ", ".join(titles[:max_titles]) + (f", ... (+{len(titles) - max_titles})" if len(titles) > max_titles else "")
            groups.setdefault(folder, []).append(f"- {f.name}" + (f": {heads}" if heads else ""))
        labels = {
            "": "Radice",
            MECHANICS_DIR: "Meccanica (regole, numeri, tabelle)",
            SETTING_DIR: "Ambientazione (mondo, personaggi, trame, diario)",
            SHEETS_DIR: "Schede dei personaggi giocanti",
        }
        lines = []
        for folder in sorted(groups, key=lambda k: (k != "", k)):
            lines.append(f"{labels.get(folder, folder)}:")
            lines += groups[folder]
        return "\n".join(lines)

    # --- personaggi giocanti: legati alla campagna ---------------------------------

    def sheets(self) -> list[dict[str, str]]:
        """Le schede in `schede/`: slug, nome (dal titolo `# ...`), file, riassunto (dal glossario)."""
        out = []
        summaries = {e.file: e.description for e in self.glossary.entries() if e.kind == "pg"}
        for f in self.files():
            if not f.name.startswith(SHEETS_DIR + "/"):
                continue
            slug = f.path.stem
            name = slug.replace("-", " ").title()
            for line in f.read().splitlines():
                if line.startswith("# "):
                    name = line[2:].strip()
                    break
            out.append({"slug": slug, "name": name, "file": f.name, "summary": summaries.get(f.name, "")})
        return out

    def _state(self) -> dict:
        try:
            data = json.loads((self.root / STATE_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def active_pg(self) -> str | None:
        """Lo slug del personaggio con cui si gioca questa campagna.

        Quello salvato se esiste ancora; altrimenti l'unica scheda presente; altrimenti nessuno.
        """
        slugs = [s["slug"] for s in self.sheets()]
        saved = self._state().get("pg_attivo")
        if saved in slugs:
            return saved
        return slugs[0] if len(slugs) == 1 else None

    def _save_state(self, state: dict) -> None:
        (self.root / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    def set_active_pg(self, slug: str) -> dict[str, str]:
        sheet = next((s for s in self.sheets() if s["slug"] == slug), None)
        if sheet is None:
            raise ValueError(f"Nessuna scheda '{slug}' in questa campagna")
        state = self._state()
        state["pg_attivo"] = slug
        self._save_state(state)
        return sheet

    @property
    def session_number(self) -> int:
        """La sessione in corso, contata dal codice: 1 finche' non se ne chiude una."""
        try:
            return max(1, int(self._state().get("sessione", 1)))
        except (TypeError, ValueError):
            return 1

    def next_session(self) -> int:
        state = self._state()
        state["sessione"] = self.session_number + 1
        self._save_state(state)
        return state["sessione"]

    def active_sheet(self) -> dict[str, str] | None:
        slug = self.active_pg
        return next((s for s in self.sheets() if s["slug"] == slug), None) if slug else None

    # --- spese: ogni chiamata lascia una riga, il contatore a schermo le somma ------------------
    #
    # Il costo si calcola dai token che l'API restituisce a ogni risposta (ingresso, uscita, cache
    # letta e scritta) per il prezzo del modello: e' immediato e per singolo turno. Il resoconto
    # ufficiale (Usage & Cost API) richiede una chiave di amministrazione ed e' a giornate.

    def spend_path(self) -> Path:
        return self.root / SESSION_DIR / "spese.jsonl"

    def record_spend(self, process: str, model: str, usd: float, **extra: object) -> None:
        if usd <= 0:
            return
        path = self.spend_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"t": _dt.datetime.now().isoformat(timespec="seconds"), "sessione": self.session_number,
               "processo": process, "modello": model, "usd": round(float(usd), 5), **extra}
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def spend_summary(self) -> dict:
        """Media per turno e totali: sessione in corso, oggi, campagna, per processo."""
        rows: list[dict] = []
        try:
            for line in self.spend_path().read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("usd"), (int, float)):
                    rows.append(row)
        except OSError:
            pass
        today = _dt.date.today().isoformat()
        session = self.session_number
        in_turns = ("gioco", "scriba")  # cio' che costa un turno giocato: narrazione e trascrizione

        def average(subset: list[dict]) -> tuple[float, int]:
            turns = sum(1 for r in subset if r["processo"] == "gioco")
            spent = sum(r["usd"] for r in subset if r["processo"] in in_turns)
            return (round(spent / turns, 4) if turns else 0.0), turns

        here = [r for r in rows if r.get("sessione") == session]
        avg_session, turns_session = average(here)
        avg_all, turns_all = average(rows)
        last = max((i for i, r in enumerate(rows) if r["processo"] == "gioco"), default=None)
        last_turn = 0.0 if last is None else round(rows[last]["usd"] + sum(r["usd"] for r in rows[last + 1:] if r["processo"] == "scriba"), 4)
        by_process: dict[str, float] = {}
        for r in rows:
            by_process[r["processo"]] = round(by_process.get(r["processo"], 0.0) + r["usd"], 4)
        return {"media_turno_sessione": avg_session, "turni_sessione": turns_session,
                "media_turno_campagna": avg_all, "turni_campagna": turns_all,
                "ultimo_turno": last_turn,
                "sessione": round(sum(r["usd"] for r in here), 4),
                "oggi": round(sum(r["usd"] for r in rows if str(r.get("t", "")).startswith(today)), 4),
                "campagna": round(sum(r["usd"] for r in rows), 4),
                "per_processo": by_process}

    # --- orologi: i fili che avanzano li conta il codice ------------------------------------
    #
    # Un orologio e' una minaccia o un progetto a segmenti ("La guardia indaga" 2/6).
    # Quando si riempie la sua conseguenza viene consegnata al narratore, una volta sola.

    def clocks(self) -> dict[str, dict]:
        data = self._state().get("orologi")
        return data if isinstance(data, dict) else {}

    def _save_clocks(self, clocks: dict[str, dict]) -> None:
        state = self._state()
        state["orologi"] = clocks
        self._save_state(state)

    def set_clock(self, name: str, segments: int, consequence: str = "", filled: int = 0) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("Un orologio ha bisogno di un nome")
        clocks = self.clocks()
        if int(segments) <= 0:
            clocks.pop(name, None)
            self._save_clocks(clocks)
            return {}
        if not 2 <= int(segments) <= 12:
            raise ValueError("Un orologio ha da 2 a 12 segmenti")
        old = clocks.get(name, {})
        clock = {"segmenti": int(segments), "pieni": max(0, min(int(filled or old.get("pieni", 0)), int(segments))),
                 "conseguenza": consequence.strip() or old.get("conseguenza", "")}
        clocks[name] = clock
        self._save_clocks(clocks)
        return clock

    def tick_clock(self, name: str, amount: int = 1) -> dict:
        clocks = self.clocks()
        key = next((k for k in clocks if k.lower() == name.strip().lower()), None)
        if key is None:
            raise ValueError(f"Nessun orologio '{name}'. Esistono: {list(clocks) or 'nessuno'}")
        clock = clocks[key]
        clock["pieni"] = max(0, min(clock["segmenti"], clock["pieni"] + int(amount)))
        self._save_clocks(clocks)
        return {"nome": key, **clock}

    def clocks_line(self) -> str:
        return "; ".join(f"{k} {c['pieni']}/{c['segmenti']}" for k, c in self.clocks().items())

    def pop_full_clocks(self) -> list[str]:
        """Gli orologi pieni: si consegnano una volta e si tolgono."""
        clocks = self.clocks()
        full = {k: c for k, c in clocks.items() if c["pieni"] >= c["segmenti"]}
        if full:
            self._save_clocks({k: c for k, c in clocks.items() if k not in full})
        return [f"{k}: {c.get('conseguenza') or 'si compie'}" for k, c in full.items()]

    # --- il giocato: un solo archivio per conversazione, trascrizione e ripresa insieme -----------
    #
    # `sessioni/<nome>.jsonl`, una riga JSON per turno, solo in aggiunta. E' la trascrizione
    # completa (leggibile) e insieme cio' da cui si riparte: niente seconda copia in Markdown.
    # <nome> e' lo slug del personaggio per il gioco, `_scheda` per la creazione delle schede.

    def save_path(self, name: str) -> Path:
        clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip().lower()) or "senza-pg"
        return self.root / SESSION_DIR / f"{clean}.jsonl"

    def append_turn(self, name: str, player: str, master: str, **extra: object) -> None:
        path = self.save_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"t": _dt.datetime.now().isoformat(timespec="seconds"), "player": player.strip(), "master": master.strip(), **extra}
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_turns(self, name: str, limit: int | None = None) -> list[dict]:
        path = self.save_path(name)
        if not path.is_file():
            return []
        turns: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # una riga troncata da una chiusura forzata non deve impedire la ripresa
            if isinstance(row, dict) and row.get("player") and row.get("master"):
                turns.append(row)
        return turns[-limit:] if limit else turns

    def turn_count(self, name: str) -> int:
        return len(self.read_turns(name))

    def archive_turns(self, name: str, discarded: bool = False) -> Path | None:
        """Chiude la conversazione corrente: il file passa in `sessioni/archivio/` con data e ora.

        `discarded`: un inizio rifatto da capo. Resta in archivio ma non e' storia della campagna.
        """
        path = self.save_path(name)
        if not path.is_file():
            return None
        target_dir = path.parent / "archivio"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{path.stem}-{_dt.datetime.now():%Y%m%d-%H%M%S}{'-scartata' if discarded else ''}.jsonl"
        path.replace(target)
        return target

    # --- il giocato grezzo come memoria: tutto cio' che e' stato giocato, in ordine -----------------

    def all_turns(self, name: str) -> list[dict]:
        """Sessioni archiviate (non quelle scartate) piu' quella in corso. E' la fonte del consolidamento."""
        current = self.save_path(name)
        files = sorted(p for p in (current.parent / "archivio").glob(f"{current.stem}-*.jsonl")
                       if not p.stem.endswith("-scartata") and p.stem[len(current.stem) + 1:][:8].isdigit())
        turns: list[dict] = []
        for path in [*files, current]:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("player") and row.get("master"):
                    turns.append(row)
        return turns

    def consolidated(self, name: str) -> int:
        """Fino a che turno il giocato di questo personaggio e' gia' diventato memoria."""
        data = self._state().get("memoria")
        try:
            return max(0, int(data.get(name, 0))) if isinstance(data, dict) else 0
        except (TypeError, ValueError):
            return 0

    def set_consolidated(self, name: str, count: int) -> None:
        state = self._state()
        memory = state.get("memoria") if isinstance(state.get("memoria"), dict) else {}
        memory[name] = int(count)
        state["memoria"] = memory
        self._save_state(state)

    # --- creazione -----------------------------------------------------

    @classmethod
    def create(cls, root: str | Path, template: str | Path | None = None) -> "Campaign":
        """Crea una nuova campagna, da un modello se indicato, altrimenti vuota."""
        root = Path(root)
        if root.exists():
            raise FileExistsError(root)
        if template:
            shutil.copytree(template, root, ignore=shutil.ignore_patterns(*HIDDEN_DIRS, ".*"))
        else:
            root.mkdir(parents=True)
            for name, text in TEMPLATE_FILES.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        return cls(root)
