"""Catalogo dei modelli e dei livelli di ragionamento (effort) selezionabili dalla UI.

Prezzi indicativi per milione di token (ingresso / uscita), API Anthropic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODELS: list[dict[str, str]] = [
    {"id": "claude-opus-5", "name": "Opus 5", "note": "predefinito: il piu' adatto a narrare e applicare regole", "price": "$5 / $25"},
    {"id": "claude-sonnet-5", "name": "Sonnet 5", "note": "veloce ed economico, buono per preparazione e schede", "price": "$2 / $10"},
    {"id": "claude-haiku-4-5", "name": "Haiku 4.5", "note": "il piu' economico; nessun livello di ragionamento", "price": "$1 / $5"},
    {"id": "claude-fable-5-1", "name": "Fable 5.1", "note": "il piu' capace; costoso, per campagne complesse", "price": "$10 / $50"},
]

EFFORTS: list[dict[str, str]] = [
    {"id": "", "name": "predefinito", "note": "lascia decidere al modello (equivale a high)"},
    {"id": "low", "name": "low", "note": "risposte rapide ed economiche, ragionamento minimo"},
    {"id": "medium", "name": "medium", "note": "buon compromesso per il gioco quotidiano"},
    {"id": "high", "name": "high", "note": "ragionamento pieno, piu' lento e costoso"},
    {"id": "max", "name": "max", "note": "massimo ragionamento: per trame intricate o regole difficili"},
]

MODEL_IDS = {m["id"] for m in MODELS}

# Prezzo per milione di token (ingresso, uscita), API Anthropic, tariffa piena. Il Batch dimezza.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}


CACHE_READ_FACTOR = 0.1   # una lettura dalla cache costa un decimo dell'ingresso
CACHE_WRITE_FACTOR = 2.0  # scrittura con durata di un'ora (1.25 con i 5 minuti predefiniti)


def cost_usd(model: str, input_tokens: float, output_tokens: float, batch: bool = False,
             cache_read: float = 0, cache_write: float = 0) -> float:
    """Costo in dollari. `input_tokens` sono quelli NON in cache, come li riporta l'API."""
    pin, pout = PRICES.get(model, (5.0, 25.0))
    total = (input_tokens + cache_read * CACHE_READ_FACTOR + cache_write * CACHE_WRITE_FACTOR) / 1e6 * pin
    total += output_tokens / 1e6 * pout
    return total / 2 if batch else total
EFFORT_IDS = {e["id"] for e in EFFORTS} | {"xhigh"}


def supports_effort(model: str) -> bool:
    """Haiku 4.5 rifiuta il parametro effort: per lui si omette sempre."""
    return not model.startswith("claude-haiku")


def effective_effort(model: str, effort: str | None) -> str | None:
    return effort if effort and supports_effort(model) else None


@dataclass
class Settings:
    """Modello ed effort per ciascun processo. Vuoto = predefinito."""

    model: str = "claude-opus-5"
    model_prepare: str = ""  # vuoto = lo stesso del gioco
    model_memory: str = "claude-sonnet-5"  # il livello lento: rilegge il giocato e lo consolida, una volta a sessione
    model_scribe: str = "claude-haiku-4-5"  # lo scriba trascrive appunti: non serve un modello costoso
    # "" = predefinito del modello (high). Il ragionamento e' circa meta' dell'uscita di un turno, ma e' anche cio' che
    # tiene insieme regole e trama: abbassarlo e' una scelta di qualita' che spetta al giocatore, dalle impostazioni.
    effort_play: str = ""
    effort_sheet: str = ""
    effort_prepare: str = "medium"  # trascrivere non richiede ragionamento profondo
    effort_tidy: str = "low"
    prep_batch: bool = True  # Batch API: meta' prezzo, esito in minuti o ore
    path: Path | None = field(default=None, repr=False, compare=False)

    # --- persistenza ------------------------------------------------------------

    @classmethod
    def load(cls, path: Path, defaults: dict[str, str | None] | None = None) -> "Settings":
        s = cls(path=path)
        for key, value in (defaults or {}).items():
            if value and hasattr(s, key):
                setattr(s, key, value)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            s.update(data, save=False)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        if self.path is None:
            return
        self.path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    def as_dict(self) -> dict[str, str]:
        d = asdict(self)
        d.pop("path", None)
        return d

    def update(self, data: dict, save: bool = True) -> None:
        if not isinstance(data, dict):
            raise ValueError("Impostazioni non valide")
        model = str(data.get("model", self.model)).strip()
        if model not in MODEL_IDS:
            raise ValueError(f"Modello sconosciuto: {model}")
        self.model = model
        if "model_prepare" in data:
            mp = str(data["model_prepare"] or "").strip()
            if mp and mp not in MODEL_IDS:
                raise ValueError(f"Modello sconosciuto per la preparazione: {mp}")
            self.model_prepare = mp
        if "model_memory" in data:
            mm = str(data["model_memory"] or "").strip() or "claude-sonnet-5"
            if mm not in MODEL_IDS:
                raise ValueError(f"Modello sconosciuto per la memoria: {mm}")
            self.model_memory = mm
        if "model_scribe" in data:
            ms = str(data["model_scribe"] or "").strip() or "claude-haiku-4-5"
            if ms not in MODEL_IDS:
                raise ValueError(f"Modello sconosciuto per lo scriba: {ms}")
            self.model_scribe = ms
        if "prep_batch" in data:
            self.prep_batch = bool(data["prep_batch"])
        for key in ("effort_play", "effort_sheet", "effort_prepare", "effort_tidy"):
            if key in data:
                value = str(data[key] or "").strip().lower()
                if value not in EFFORT_IDS:
                    raise ValueError(f"Livello non valido per {key}: {value}")
                setattr(self, key, value)
        if save:
            self.save()

    # --- valori effettivi per i motori ------------------------------------------

    def model_for(self, process: str) -> str:
        return self.model_prepare if process in ("prepare", "tidy") and self.model_prepare else self.model

    def effort_for(self, process: str) -> str | None:
        return effective_effort(self.model_for(process), getattr(self, f"effort_{process}", ""))
