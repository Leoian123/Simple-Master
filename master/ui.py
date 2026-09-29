"""UI web servita dalla libreria standard, nessuna dipendenza.

Due schermate:
- MENU PRINCIPALE: elenco delle campagne (entra, prepara da manuale), nuova
  campagna, impostazioni di modello ed effort. Qui si gestisce, non si gioca.
- CAMPAGNA: il tavolo di gioco, con due sezioni. "Personaggio": i PG della
  campagna, l'anteprima della scheda e la creazione guidata dalle regole in
  meccanica/. "Gioca": la conversazione con il master. Il personaggio e' legato
  alla campagna (stato.json): senza un PG attivo non si gioca, e il master ne
  riceve la scheda a ogni turno. Da dentro non si modifica la campagna: si
  torna al menu.

Lo stato vive in `App`: campagna aperta, motori, preparazione in corso.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import dice, explorer
from .campaign import DIARY, MECHANICS_DIR, SHEETS_DIR, Campaign
from .character import CharacterBuilder
from .engine import MasterEngine
from .logbook import describe_error
from .sheet import Sheet
from .models import EFFORTS, MODELS, Settings

log = logging.getLogger("master.ui")


# --- stato dell'applicazione -----------------------------------------------------


class App:
    """Campagna aperta, motori, preparazione e impostazioni. Un'istanza per server."""

    def __init__(
        self,
        campaigns_dir: str | Path,
        campaign: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        client: Any = None,
        settings_path: Path | None = None,
    ):
        self.campaigns_dir = Path(campaigns_dir).resolve()
        self.campaigns_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings.load(
            settings_path or self.campaigns_dir.parent / "impostazioni.json",
            {"model": model, "effort_play": effort, "effort_sheet": effort, "effort_prepare": effort},
        )
        self.client = client
        self.lock = threading.Lock()
        self.campaign: Campaign | None = None
        self.engine: MasterEngine | None = None
        self.builder: CharacterBuilder | None = None
        self.prep: dict[str, Any] = {"running": False, "lines": [], "done": False, "error": None, "campaign": None}
        if campaign:
            self.enter(campaign)

    # --- campagne ---------------------------------------------------------------------

    def list_campaigns(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.campaigns_dir.iterdir()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            camp = Campaign(p)
            files = camp.files()
            rules = camp.file(f"{MECHANICS_DIR}/regole.md")
            diary = camp.file(DIARY)
            out.append(
                {
                    "name": p.name,
                    "files": len(files),
                    "sheets": [s["name"] for s in camp.sheets()],
                    "pg": (camp.active_sheet() or {}).get("name", ""),
                    "turns": camp.turn_count(camp.active_pg) if camp.active_pg else 0,
                    "last_played": ((camp.read_turns(camp.active_pg, 1) or [{}])[-1].get("t", "") if camp.active_pg else ""),
                    "entries": len(camp.glossary.entries()),
                    "has_rules": rules.exists() and len(rules.read()) > 80,
                    "diary_sections": len(diary.outline()) if diary.exists() else 0,
                    "current": self.campaign is not None and self.campaign.root == p.resolve(),
                    "preparing": bool(self.prep["running"]) and self.prep["campaign"] == p.name,
                }
            )
        return out

    def _resolve(self, name: str) -> Path:
        clean = name.strip()
        if not re.fullmatch(r"[\w][\w .-]{0,60}", clean) or clean.startswith("."):
            raise ValueError("Nome campagna non valido: usa lettere, numeri, spazi, trattini")
        return self.campaigns_dir / clean

    def enter(self, name: str) -> Campaign:
        """Apre il tavolo di gioco di una campagna."""
        path = self._resolve(name)
        if not path.is_dir():
            raise FileNotFoundError(f"Campagna inesistente: {name}")
        if self.prep["running"] and self.prep["campaign"] == path.name:
            raise RuntimeError("Questa campagna e' in preparazione: aspetta che finisca")
        with self.lock:
            self.campaign = Campaign(path)
            # narrazione e scrittura sono due chiamate separate: il narratore narra, lo scriba trascrive
            self.engine = MasterEngine(self.campaign, client=self.client, model=self.settings.model, scribe=True,
                                       scribe_model=self.settings.model_scribe, memory=True, fast=True,
                                       memory_model=self.settings.model_memory)
            self.builder = CharacterBuilder(self.campaign, client=self.client, model=self.settings.model)
            self.apply_settings()
            # l'unica scheda presente e' il personaggio della campagna: lo si fissa, cosi' una
            # seconda scheda creata dopo non lo fa "dimenticare"
            slug = self.campaign.active_pg
            if slug and not self.campaign._state().get("pg_attivo"):
                self.campaign.set_active_pg(slug)
        sheet = self.campaign.active_sheet()
        log.info("campagna aperta: %s | personaggio: %s", self.campaign.name, sheet["name"] if sheet else "nessuno")
        return self.campaign

    def leave(self) -> None:
        """Torna al menu principale: la conversazione della sessione si chiude."""
        with self.lock:
            if self.campaign is not None:
                log.info("campagna chiusa: %s", self.campaign.name)
            self.campaign = self.engine = self.builder = None

    def create(self, name: str, empty: bool = False) -> str:
        path = self._resolve(name)
        template = None if empty else self.campaigns_dir / "esempio"
        if template is not None and not template.is_dir():
            template = None
        Campaign.create(path, template)
        log.info("campagna creata: %s (%s)", path.name, "vuota" if template is None else "copia dell'esempio")
        return path.name

    # --- impostazioni: modello ed effort -------------------------------------------------------

    def apply_settings(self) -> None:
        s = self.settings
        for engine, process in ((self.engine, "play"), (self.builder, "sheet")):
            if engine is not None:
                engine.model = s.model
                engine.effort = s.effort_for(process)
        if self.engine is not None:
            self.engine.scribe_model = s.model_scribe
            self.engine.memory_model = s.model_memory

    def update_settings(self, data: dict[str, Any]) -> dict[str, str]:
        with self.lock:
            self.settings.update(data)
            self.apply_settings()
        log.info("impostazioni: modello=%s (preparazione: %s, batch=%s) effort gioco=%s scheda=%s preparazione=%s riordino=%s",
                 self.settings.model, self.settings.model_prepare or "stesso", self.settings.prep_batch,
                 self.settings.effort_play or "-", self.settings.effort_sheet or "-",
                 self.settings.effort_prepare or "-", self.settings.effort_tidy or "-")
        return self.settings.as_dict()

    def settings_payload(self) -> dict[str, Any]:
        return {"current": self.settings.as_dict(), "models": MODELS, "efforts": EFFORTS,
                "path": str(self.settings.path) if self.settings.path else ""}

    # --- preparazione da manuale, in un thread, dal menu -------------------------------------------

    def start_prepare(self, name: str, source: str, mode: str = "testo", pages: str | None = None) -> None:
        path = self._resolve(name)
        if not path.is_dir():
            raise FileNotFoundError(f"Campagna inesistente: {name}")
        if self.prep["running"]:
            raise RuntimeError("Una preparazione e' gia' in corso")
        if not source.strip():
            raise ValueError("Indica il file del manuale o un URL")
        from .prepare import Preparer

        campaign = Campaign(path)
        preparer = Preparer(campaign, client=self.client, model=self.settings.model_for("prepare"),
                            effort=self.settings.effort_for("prepare"), batch=self.settings.prep_batch)
        tidy_effort = self.settings.effort_for("tidy")
        self.prep = {"running": True, "lines": [], "done": False, "error": None, "campaign": path.name, "source": source.strip()}

        def work() -> None:
            try:
                report = preparer.run(source.strip(), mode=mode, pages=pages or None,
                                      tidy_effort=tidy_effort, progress=self.prep["lines"].append)
                self.prep["lines"].extend(report.summary().splitlines())
                from .models import cost_usd
                batched = bool(report.chunks_batched)
                Campaign(path).record_spend("preparazione", preparer.model, cost_usd(preparer.model, report.input_tokens, report.output_tokens, batch=batched),
                                            chiamate=report.calls, nota="stima dai token" + (", tariffa batch" if batched else ""))
            except Exception as exc:  # gia' nel registro con traceback
                self.prep["error"] = describe_error(exc)
            finally:
                self.prep["running"] = False
                self.prep["done"] = True
                if self.campaign is not None and self.campaign.name == path.name:
                    self.enter(path.name)  # ricarica glossario e file nei motori

        threading.Thread(target=work, name="preparazione", daemon=True).start()

    def start_sanitize(self, name: str, deep: bool = True) -> None:
        """Toglie i doppioni da una campagna, dal menu: stesso canale di avanzamento della preparazione."""
        path = self._resolve(name)
        if not path.is_dir():
            raise FileNotFoundError(f"Campagna inesistente: {name}")
        if self.prep["running"]:
            raise RuntimeError("Un'altra elaborazione e' gia' in corso")
        from .sanitize import Sanitizer

        sanitizer = Sanitizer(Campaign(path), client=self.client, model=self.settings.model_for("prepare"))
        self.prep = {"running": True, "lines": [], "done": False, "error": None, "campaign": path.name, "kind": "Sanificazione"}

        def work() -> None:
            try:
                report = sanitizer.run(deep=deep, progress=self.prep["lines"].append)
                self.prep["lines"].extend(report.summary().splitlines())
            except Exception as exc:
                log.error("sanificazione INTERROTTA: %s", describe_error(exc), exc_info=True)
                self.prep["error"] = describe_error(exc)
            finally:
                self.prep["running"] = False
                self.prep["done"] = True

        threading.Thread(target=work, name="sanificazione", daemon=True).start()

    # --- stato per la pagina ------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        base = {"model": self.settings.model, "settings": self.settings.as_dict(), "campaigns": self.list_campaigns(), "prep": self.prep}
        if self.campaign is None or self.engine is None:
            return {**base, "campaign": None, "intro": "", "sheets": [], "pg": None}
        diary = self.campaign.file(DIARY)
        sections = diary.sections() if diary.exists() else {}
        last = next(reversed(sections), "") if sections else ""
        return {
            **base,
            "campaign": self.campaign.name,
            "intro": f"{last}\n\n{sections[last]}" if last else "",
            "sheets": self.campaign.sheets(),
            "pg": self.campaign.active_sheet(),
            "xp": self._xp(),
            "session": self.campaign.session_number,
            "can_close": self.engine.can_close(),
            "memory_pending": self._memory_pending(),
            "spend": self.campaign.spend_summary(),
            "dice": [k.get("nome") for k in (dice.load_profile(self.campaign) or {}).get("tiri", [])],
            "has_creation_rules": self.campaign.file(f"{MECHANICS_DIR}/creazione-pg.md").exists()
            and len(self.campaign.file(f"{MECHANICS_DIR}/creazione-pg.md").read()) > 120,
        }

    # --- personaggi: legati alla campagna aperta ------------------------------------------------

    def _open_campaign(self) -> Campaign:
        if self.campaign is None:
            raise ValueError("Entra prima in una campagna dal menu principale")
        return self.campaign

    def _xp(self) -> dict[str, int] | None:
        """Il contatore dell'esperienza del personaggio attivo, calcolato dal registro della scheda."""
        active = self.campaign.active_sheet() if self.campaign else None
        if active is None:
            return None
        sheet = Sheet(self.campaign, active["slug"])
        return sheet.xp() if sheet.exists else None

    def select_pg(self, slug: str) -> dict[str, str]:
        sheet = self._open_campaign().set_active_pg(slug.strip())
        if self.engine is not None:
            self.engine.reset_session()  # nuovo personaggio, nuova conversazione: lo stato resta nei file
        log.info("personaggio attivo: %s (%s)", sheet["name"], sheet["file"])
        return sheet

    def sheet_text(self, slug: str) -> dict[str, str]:
        camp = self._open_campaign()
        sheet = next((s for s in camp.sheets() if s["slug"] == slug.strip()), None)
        if sheet is None:
            raise ValueError(f"Nessuna scheda '{slug}' in questa campagna")
        return {**sheet, "markdown": camp.file(sheet["file"]).read()}

    def play(self, text: str) -> Any:
        camp = self._open_campaign()
        if camp.active_sheet() is None:
            raise ValueError("Crea o scegli prima un personaggio nella sezione Personaggio")
        with self.lock:
            return self.engine.play(text)

    def roll(self, spec: str) -> dict[str, Any]:
        """I dadi li tira il codice: l'esito diventa il messaggio del giocatore e il narratore ne racconta le conseguenze."""
        self._open_campaign()
        camp = self._open_campaign()
        active = camp.active_sheet()
        sheet = Sheet(camp, active["slug"]) if active else None
        rolled = dice.roll(spec, dice.load_profile(camp), sheet if sheet is not None and sheet.exists else None)
        log.info("tiro: %s", rolled["text"])
        turn = self.play(rolled["text"])
        return {**turn.__dict__, "roll_text": rolled["text"], "roll_success": rolled["success"]}

    def consolidate(self) -> dict[str, Any]:
        """Il livello lento, a comando: il giocato non ancora consolidato diventa memoria."""
        self._open_campaign()
        with self.lock:
            report = self.engine.consolidate()
        return {**report.__dict__, "summary": report.summary()}

    def _memory_pending(self) -> int:
        slug = self.campaign.active_pg
        if not slug:
            return 0
        return max(0, len(self.campaign.all_turns(slug)) - self.campaign.consolidated(slug))

    def make_dice_profile(self) -> dict[str, Any]:
        """Ricava `meccanica/dadi.json` dalle regole gia' estratte: una chiamata, poi resta su disco."""
        camp = self._open_campaign()
        active = camp.active_sheet()
        sheet = Sheet(camp, active["slug"]) if active else None
        with self.lock:
            profile = dice.extract_profile(camp, self.client or self.engine.client, self.settings.model_for("prepare"),
                                           sheet.prompt_json() if sheet is not None and sheet.exists else "")
            if self.engine is not None:
                self.engine.reset_snapshot()
        if profile is None:
            raise ValueError("Nelle regole di questa campagna non ho trovato sezioni che parlino di tiri")
        return {"kinds": [k["nome"] for k in profile["tiri"]]}

    def prelude(self, notes: str = "") -> Any:
        """Apre la campagna per il personaggio attivo: la prima scena nasce dal suo background."""
        camp = self._open_campaign()
        if camp.active_sheet() is None:
            raise ValueError("Crea o scegli prima un personaggio nella sezione Personaggio")
        with self.lock:
            return self.engine.prelude(notes)

    def build(self, text: str) -> Any:
        camp = self._open_campaign()
        with self.lock:
            result = self.builder.play(text)
        saved = [f for f in result.files_changed if f.startswith(SHEETS_DIR + "/")]
        if saved and not camp._state().get("pg_attivo"):
            slug = Path(saved[-1]).stem
            camp.set_active_pg(slug)  # la prima scheda creata diventa il personaggio della campagna
            log.info("personaggio attivo (prima scheda della campagna): %s", slug)
        if saved:
            self.builder.new_session()  # scheda conclusa: la prossima creazione parte pulita
        return result

    def history(self, which: str, limit: int = 40) -> dict[str, Any]:
        self._open_campaign()
        engine = self.engine if which == "play" else self.builder
        name = engine.save_name
        total = self.campaign.turn_count(name) if name else 0
        return {"turns": engine.history(limit), "total": total}

    def new_session(self, which: str) -> dict[str, Any]:
        self._open_campaign()
        with self.lock:
            if which != "play":
                return {"archived": self.builder.new_session(), "closing": None}
            # chiudere una sessione giocata e' la via `sessione` dell'esperienza: riepilogo, fili, punti
            had_turns = bool(self.engine.history(1))
            closing = self.engine.close_session(consolidate=False)  # la memoria la chiede la pagina, a parte
        return {"archived": had_turns, "closing": closing.__dict__ if closing else None,
                "xp": self._xp(), "session": self.campaign.session_number}


# --- pagina ---------------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Simple Master</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,500&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{
  --ground:#15130f; --surface:#1d1a14; --raised:#27221a; --line:#3a3226; --line-soft:#2b261d;
  --ink:#e9dfc8; --ink-2:#b7a98c; --ink-3:#7f7259;
  --gold:#c9a24c; --gold-ink:#1a1408; --moss:#8fb08a; --moss-ink:#0f170f; --ember:#c65a48;
  --player:#2a3444;
  --display:"Cormorant Garamond",Georgia,"Times New Roman",serif;
  --body:"Source Serif 4",Georgia,serif;
  --mono:"IBM Plex Mono",Consolas,monospace;
  --radius:10px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--ground);color:var(--ink);font:17px/1.55 var(--body);display:flex;flex-direction:column}
button{font:inherit;cursor:pointer}
:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
[hidden]{display:none!important}
.mono{font-family:var(--mono)}
.lbl{font:500 11px/1 var(--mono);color:var(--ink-3);text-transform:uppercase;letter-spacing:.09em}
.btn{background:var(--raised);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:8px 14px;font-size:15px}
.btn:hover{border-color:var(--gold)}
.btn.gold{background:var(--gold);color:var(--gold-ink);border-color:var(--gold);font-weight:600}
.btn.quiet{background:transparent;color:var(--ink-2)}
.btn:disabled{opacity:.5;cursor:default}
input,select{background:var(--ground);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:8px 10px;font:15px var(--body);min-width:0}
input:focus,select:focus{border-color:var(--gold);outline:none}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.row input{flex:1}
.err{color:var(--ember);font-size:14px;margin:8px 0 0;min-height:1.2em}
.hint{font:12px/1.5 var(--mono);color:var(--ink-3);margin:8px 0 0}

/* ===== MENU PRINCIPALE ===== */
#home{flex:1;overflow-y:auto;padding:32px 20px 48px}
#home .wrap{max-width:900px;margin:0 auto;display:grid;gap:28px}
.masthead{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.masthead h1{margin:0;font:600 40px/1 var(--display);color:var(--gold);letter-spacing:.01em}
.masthead p{margin:0;color:var(--ink-2)}
section.block h2{margin:0 0 12px;font:600 24px/1.1 var(--display);text-wrap:balance}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.card{background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.card:hover{border-color:var(--line)}
.card .top{display:flex;align-items:baseline;gap:10px}
.card .n{font:600 22px/1.15 var(--display);flex:1;overflow-wrap:anywhere}
.card .tag{font:500 11px/1 var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--gold);white-space:nowrap}
.card .tag.warn{color:var(--ember)}
.card .d{font:12px/1.5 var(--mono);color:var(--ink-3)}
.card .acts{display:flex;gap:8px;margin-top:4px;flex-wrap:wrap}
.card .acts .btn{flex:1;text-align:center}
.card .prepform{border-top:1px solid var(--line-soft);padding-top:10px;display:grid;gap:8px}
.card .prepform .row input{width:100%}
.progress{background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:12px 16px;display:grid;gap:4px}
.progress .lines{font:12px/1.6 var(--mono);color:var(--ink-2);max-height:180px;overflow-y:auto;white-space:pre-wrap}
.progress .lines .e{color:var(--ember)}
.newc{background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:14px 16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}
.grid label{display:flex;flex-direction:column;gap:4px}
.grid select{width:100%}
.grid .note{font:12px/1.4 var(--mono);color:var(--ink-3);min-height:1.4em}
details.settings{background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:12px 16px}
details.settings summary{cursor:pointer;font:600 24px/1.1 var(--display);list-style:none;display:flex;align-items:center;gap:10px}
details.settings summary::after{content:"\25BE";color:var(--ink-3);font-size:14px;margin-left:auto}
details.settings[open] summary::after{content:"\25B4"}
details.settings summary .cur{font:12px/1 var(--mono);color:var(--ink-3);font-weight:400}
details.settings .body{margin-top:14px}
.explorer{margin-top:4px;border:1px solid var(--line-soft);border-radius:8px;background:var(--ground);overflow:hidden}
.crumbs{display:flex;flex-wrap:wrap;gap:4px;padding:6px 8px;border-bottom:1px solid var(--line-soft);font:12px/1.4 var(--mono)}
.crumbs button{background:transparent;border:0;color:var(--gold);padding:2px 6px;border-radius:4px;font:inherit}
.crumbs button:hover{background:var(--raised)}
.crumbs .sep{color:var(--ink-3);align-self:center}
.entries{max-height:200px;overflow-y:auto;padding:4px}
.entries button{display:flex;gap:8px;width:100%;text-align:left;background:transparent;border:0;color:var(--ink);padding:5px 8px;border-radius:4px;font:14px/1.3 var(--body)}
.entries button:hover{background:var(--raised)}
.entries .ico{color:var(--ink-3);font:12px/1.3 var(--mono);width:3ch;flex:none}
.entries .file .ico{color:var(--gold)}
.entries .sz{margin-left:auto;font:11px/1.3 var(--mono);color:var(--ink-3)}
.entries .empty{padding:8px;color:var(--ink-3);font:12px var(--mono)}

/* ===== CAMPAGNA ===== */
#play{flex:1;display:flex;flex-direction:column;min-height:0}
header{display:flex;align-items:center;gap:16px;padding:10px 20px;border-bottom:1px solid var(--line-soft);background:var(--surface)}
header .back{background:transparent;border:1px solid var(--line);color:var(--ink-2);border-radius:999px;padding:5px 14px 5px 10px;font-size:14px}
header .back:hover{border-color:var(--gold);color:var(--ink)}
header .title{display:flex;flex-direction:column;gap:2px;min-width:0}
header .title .n{font:600 21px/1.1 var(--display);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
header .title .m{font:11px/1 var(--mono);color:var(--ink-3);letter-spacing:.03em}
nav{margin-left:auto;display:flex;gap:4px;background:var(--ground);padding:3px;border-radius:999px;border:1px solid var(--line-soft)}
nav button{background:transparent;color:var(--ink-2);border:0;border-radius:999px;padding:6px 16px;font-size:15px}
nav button.on{background:var(--raised);color:var(--ink)}
body.sheet nav button.on{color:var(--moss)}
.view{flex:1;display:none;flex-direction:column;min-height:0}
.view.on{display:flex}
.pgview.on{display:grid;grid-template-columns:minmax(320px,440px) 1fr;min-height:0}
.pgside{border-right:1px solid var(--line-soft);overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:14px;background:var(--surface)}
.pghead h2{margin:0;font:600 22px/1.15 var(--display);text-wrap:balance}
.pghead .sub{margin:4px 0 0;color:var(--ink-2);font-size:14px}
.pglist{display:grid;gap:8px}
.pgcard{background:var(--raised);border:1px solid var(--line-soft);border-radius:8px;padding:10px 12px;display:grid;gap:6px}
.pgcard.viewing{border-color:var(--gold)}
.pgcard.active{border-color:var(--moss);box-shadow:inset 3px 0 0 var(--moss)}
.pgcard .top{display:flex;align-items:baseline;gap:8px}
.pgcard .n{font:600 19px/1.2 var(--display);flex:1;overflow-wrap:anywhere}
.pgcard .tag{font:500 11px/1 var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--moss);white-space:nowrap}
.pgcard .d{font-size:14px;color:var(--ink-2)}
.pgcard .acts{display:flex;gap:6px;flex-wrap:wrap}
.pgcard .acts .btn{padding:5px 10px;font-size:14px}
.btn.moss{background:var(--moss);color:var(--moss-ink);border-color:var(--moss);font-weight:600}
.pgsheet{background:var(--ground);border:1px solid var(--line-soft);border-radius:8px;padding:14px 16px;font-size:15px;line-height:1.5;overflow-wrap:anywhere}
.pgsheet h1{font:600 24px/1.1 var(--display);margin:0 0 8px;color:var(--gold)}
.pgsheet h2{font:600 12px/1 var(--mono);text-transform:uppercase;letter-spacing:.09em;color:var(--ink-3);margin:16px 0 6px;border-top:1px solid var(--line-soft);padding-top:10px}
.pgsheet h3{font:600 16px/1.2 var(--display);margin:10px 0 4px}
.pgsheet p{margin:0 0 8px}
.pgsheet ul{margin:0 0 8px;padding-left:18px}
.pgsheet code{font:13px var(--mono);color:var(--ink-2)}
.pgsheet .tw{overflow-x:auto}
.pgsheet table{border-collapse:collapse;width:100%;font-size:14px;margin:0 0 8px;font-variant-numeric:tabular-nums}
.pgsheet th,.sheet td{border-bottom:1px solid var(--line-soft);padding:4px 8px;text-align:left}
.pgsheet th{font:500 11px/1.2 var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
.pgchat{display:flex;flex-direction:column;min-height:0}
.chips{display:flex;gap:6px;flex-wrap:wrap;padding:0 20px 10px}
.chips button{background:transparent;border:1px solid var(--line);color:var(--ink-2);border-radius:999px;padding:5px 12px;font-size:14px}
.chips button:hover{border-color:var(--moss);color:var(--ink)}
.pgchip{display:flex;flex-direction:column;gap:3px;padding-left:16px;border-left:1px solid var(--line-soft)}
.pgchip .n{font:600 17px/1.1 var(--display);color:var(--moss)}
.btn.small{padding:4px 10px;font-size:13px}
.prelude{max-width:72ch;background:var(--surface);border:1px solid var(--gold);border-radius:var(--radius);padding:18px 20px;display:grid;gap:10px}
.prelude h3{margin:0;font:600 24px/1.1 var(--display);color:var(--gold);text-wrap:balance}
.prelude .lead{margin:0;color:var(--ink-2);font-size:16px}
.prelude textarea{height:auto;min-height:56px;font-size:15px;resize:vertical}
.prelude .hint{margin:0}
.meta.divider{align-self:center;color:var(--ink-3);padding:4px 0}
nav button:disabled{opacity:.4;cursor:default}
@media (max-width:820px){.pgview.on{grid-template-columns:1fr;grid-template-rows:auto 1fr;overflow-y:auto}.pgside{border-right:0;border-bottom:1px solid var(--line-soft);max-height:46vh}.pgchip{display:none}}
.log{flex:1;overflow-y:auto;padding:24px 20px;display:none;flex-direction:column;gap:14px}
.log.on{display:flex}
.msg{max-width:72ch;padding:12px 18px;border-radius:var(--radius);white-space:pre-wrap;line-height:1.6}
.gm{background:var(--surface);border:1px solid var(--line-soft);align-self:flex-start}
.gm.hint{color:var(--ink-2);font-style:italic;border-style:dashed}
.me{background:var(--player);align-self:flex-end;border-bottom-right-radius:3px}
.meta{font:12px/1.5 var(--mono);color:var(--ink-3);padding:0 4px;max-width:72ch}
form.composer{display:flex;gap:10px;padding:14px 20px 18px;border-top:1px solid var(--line-soft);background:var(--surface)}
textarea{flex:1;resize:none;background:var(--ground);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:10px 12px;font:17px/1.5 var(--body);height:70px}
textarea:focus{border-color:var(--gold);outline:none}
button.go{background:var(--gold);color:var(--gold-ink);border:0;border-radius:8px;padding:0 22px;font:600 16px var(--body);min-width:110px}
button.go:disabled{opacity:.5;cursor:default}
body.sheet button.go{background:var(--moss);color:var(--moss-ink)}
body.sheet textarea:focus{border-color:var(--moss)}
@media (max-width:640px){#home{padding:20px 16px 32px}.masthead h1{font-size:32px}.grid{grid-template-columns:1fr}header{gap:10px;padding:8px 12px}.log{padding:16px 12px}form.composer{padding:10px 12px 14px}}
@media (prefers-reduced-motion:no-preference){.msg{animation:in .18s ease-out}@keyframes in{from{opacity:.4;transform:translateY(4px)}to{opacity:1;transform:none}}}
</style></head><body>

<!-- ===== MENU PRINCIPALE ===== -->
<div id="home">
  <div class="wrap">
    <div class="masthead"><h1>Simple Master</h1><p>Scegli una campagna per sederti al tavolo. Qui fuori si gestisce; dentro si gioca.</p></div>
    <div class="progress" id="progress" hidden><span class="lbl">Preparazione in corso</span><div class="lines"></div></div>
    <section class="block"><h2>Campagne</h2><div class="cards" id="cards"></div></section>
    <section class="block"><h2>Nuova campagna</h2>
      <div class="newc"><div class="row"><input id="newname" placeholder="Nome, es. Vampiri" aria-label="Nome nuova campagna"><select id="newkind"><option value="empty">file vuoti, da preparare da un manuale</option><option value="copy">copia dell'esempio (Velmora)</option></select><button class="btn gold" id="create" type="button">Crea</button></div>
      <p class="err" id="newerr"></p></div>
    </section>
    <details class="settings" id="settings"><summary>Modello e ragionamento <span class="cur" id="setcur"></span></summary><div class="body" id="setbody"></div></details>
  </div>
</div>

<!-- ===== CAMPAGNA ===== -->
<div id="play" hidden>
  <header>
    <button class="back" id="back" type="button" title="Torna al menu principale">&larr; Menu</button>
    <div class="title"><span class="n" id="camp-name"></span><span class="m" id="model"></span><span class="m spend" id="spend" hidden></span></div>
    <div class="pgchip" id="pgchip" hidden><span class="lbl">In gioco</span><span class="n" id="pgchip-name"></span></div>
    <button class="btn quiet small" id="memory" type="button" hidden></button>
    <button class="btn quiet small" id="newsession" type="button" title="Archivia la conversazione corrente e ne apre una nuova. La storia nei file della campagna resta.">Nuova sessione</button>
    <nav><button id="tab-sheet" type="button">Personaggio</button><button id="tab-play" class="on" type="button">Gioca</button></nav>
  </header>

  <!-- sezione PERSONAGGIO: i PG della campagna, la scheda, la creazione guidata -->
  <div id="view-sheet" class="view pgview">
    <aside class="pgside">
      <div class="pghead"><h2>Personaggi di questa campagna</h2><p class="sub" id="pgsub"></p></div>
      <div class="pglist" id="pglist"></div>
      <article class="pgsheet" id="sheetview" hidden></article>
    </aside>
    <section class="pgchat">
      <div id="log-sheet" class="log on"></div>
      <div class="chips" id="chips"></div>
      <form id="f-sheet" class="composer"><textarea id="t-sheet" placeholder="Descrivi il personaggio che vuoi creare, rispondi alle domande, o chiedi di modificare una scheda"></textarea>
      <button id="b-sheet" class="go" type="submit">Compila</button></form>
    </section>
  </div>

  <!-- sezione GIOCA -->
  <div id="view-play" class="view">
    <div id="log-play" class="log on"></div>
    <form id="f-play" class="composer"><textarea id="t-play" placeholder="Cosa fa il tuo personaggio? (Invio per mandare, Shift+Invio a capo)"></textarea>
    <button id="b-play" class="go" type="submit">Gioca</button></form>
  </div>
</div>

<template id="card-tpl">
  <div class="card"><div class="top"><span class="n"></span><span class="tag"></span></div><span class="d"></span>
    <div class="acts"><button class="btn gold enter" type="button">Entra</button><button class="btn prep-toggle" type="button">Prepara da manuale</button><button class="btn sanitize" type="button" title="Fonde le sezioni sullo stesso soggetto, toglie i blocchi ripetuti e le voci di glossario doppie">Sanifica doppioni</button></div>
    <div class="prepform" hidden>
      <div class="row"><input class="src" placeholder="Percorso del PDF o URL" aria-label="Fonte"><button class="btn browse" type="button" title="Apre Esplora risorse">Sfoglia&hellip;</button></div>
      <div class="explorer" hidden><div class="crumbs"></div><div class="entries"></div></div>
      <div class="row"><select class="mode"><option value="testo">testo (economica)</option><option value="pdf">pdf (tabelle, scansioni)</option></select><input class="pages" placeholder="Pagine, es. 10-120 (vuoto = tutte)" aria-label="Pagine"><button class="btn gold prep" type="button">Avvia</button></div>
      <p class="hint">Molte chiamate API e parecchi minuti per un manuale grande. L'avanzamento compare in cima al menu; se si interrompe, rilanciando riparte da dove era.</p>
      <p class="err"></p>
    </div>
  </div>
</template>

<template id="settings-tpl">
  <p class="hint" style="margin:0 0 12px">Il modello vale per tutto. Il livello di ragionamento (effort) si regola per ogni processo: piu' alto e' piu' accurato, lento e costoso.</p>
  <div class="grid">
    <label><span class="lbl">Modello</span><select class="s-model"></select><span class="note s-model-note"></span></label>
    <label><span class="lbl">Gioco</span><select class="s-play"></select><span class="note">ogni turno della narrazione</span></label>
    <label><span class="lbl">Scheda PG</span><select class="s-sheet"></select><span class="note">creazione e modifica delle schede</span></label>
    <label><span class="lbl">Preparazione</span><select class="s-prepare"></select><span class="note">estrazione dal manuale: trascrizione, basta medium</span></label>
    <label><span class="lbl">Riordino glossario</span><select class="s-tidy"></select><span class="note">la chiamata veloce di pulizia</span></label>
    <label><span class="lbl">Modello per la preparazione</span><select class="s-model-prepare"></select><span class="note">Sonnet 5 trascrive bene a meno della meta' del costo</span></label>
    <label><span class="lbl">Modello della memoria</span><select class="s-model-memory"></select><span class="note">Rilegge il giocato e lo consolida in cronaca, fili, memoria dei PNG: una chiamata a sessione</span></label>
    <label><span class="lbl">Modello dello scriba</span><select class="s-model-scribe"></select><span class="note">Scrive diario, scheda e glossario dagli appunti del narratore, in una chiamata a parte: Haiku basta</span></label>
    <label><span class="lbl">Batch API</span><select class="s-batch"><option value="1">si': meta' prezzo, esito in minuti o ore</option><option value="0">no: subito, prezzo pieno</option></select><span class="note">i blocchi partono insieme e si attende l'esito; si riprende se interrotto</span></label>
  </div>
  <div class="row" style="margin-top:12px"><span class="hint s-status" style="margin:0 auto 0 0"></span><button class="btn gold s-save" type="button">Salva</button></div>
  <p class="err"></p>
</template>

<script>
const $=s=>document.querySelector(s);
const logs={play:$('#log-play'),sheet:$('#log-sheet')}, tabs={play:$('#tab-play'),sheet:$('#tab-sheet')};
const views={play:$('#view-play'),sheet:$('#view-sheet')};
const io={play:{t:$('#t-play'),b:$('#b-play'),f:$('#f-play'),url:'/api/play'},sheet:{t:$('#t-sheet'),b:$('#b-sheet'),f:$('#f-sheet'),url:'/api/sheet'}};
let mode='play', state=null, prepTimer=null, prepShown=0;
async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
function screen(which){$('#home').hidden=which!=='home';$('#play').hidden=which!=='play'}

/* ===== MENU PRINCIPALE ===== */
function renderHome(){
  const cards=$('#cards');cards.innerHTML='';
  if(!state.campaigns.length){cards.innerHTML='<p class="hint">Nessuna campagna ancora: creane una qui sotto.</p>'}
  for(const c of state.campaigns){
    const el=$('#card-tpl').content.firstElementChild.cloneNode(true);
    el.querySelector('.n').textContent=c.name;
    const tag=el.querySelector('.tag');const tt=c.preparing?'in preparazione':(!c.has_rules?'da preparare':'');tag.textContent=tt;tag.classList.toggle('warn',tt==='da preparare');
    el.querySelector('.d').textContent=c.files+' file \u00b7 '+c.entries+' voci di glossario \u00b7 '+(c.sheets.length?'personaggi: '+c.sheets.join(', ')+(c.pg?' (in gioco: '+c.pg+')':''):'nessun personaggio')+' \u00b7 '+c.diary_sections+' voci di diario'+(c.turns?' · giocato: '+c.turns+' turni, ultimo il '+c.last_played.slice(0,10):'');
    const err=el.querySelector('.err'), form=el.querySelector('.prepform');
    el.querySelector('.enter').disabled=!!c.preparing;
    el.querySelector('.enter').onclick=()=>enter(c.name,err);
    el.querySelector('.prep-toggle').onclick=()=>{form.hidden=!form.hidden;if(!form.hidden)el.querySelector('.src').focus()};
    el.querySelector('.sanitize').disabled=!c.has_rules||!!c.preparing;
    el.querySelector('.sanitize').onclick=async()=>{
      if(!confirm('Sanificare "'+c.name+'"? Il passo gratuito fonde i titoli equivalenti e toglie i blocchi ripetuti. Il passo approfondito usa poche chiamate API: invia solo i titoli per riconoscere le sezioni sullo stesso soggetto, poi ripulisce le sezioni fuse. La stima del costo compare nell’avanzamento.'))return;
      try{await api('/api/sanitize',{campaign:c.name,deep:true});prepShown=0;await load();pollPrep()}catch(e){err.textContent=e.message}};
    const src=el.querySelector('.src'), browse=el.querySelector('.browse'), ex=el.querySelector('.explorer');
    browse.onclick=async()=>{browse.disabled=true;err.textContent='';
      try{const r=await api('/api/browse',{initial:src.value.replace(/[\\\/][^\\\/]*$/,'')});if(r.path)src.value=r.path}
      catch(e){await showExplorer(ex,src,err,src.value.replace(/[\\\/][^\\\/]*$/,''))}
      finally{browse.disabled=false}};
    el.querySelector('.prep').onclick=async()=>{const s=src.value.trim();if(!s){err.textContent='Indica il file o l\u0027URL del manuale.';return}
      try{await api('/api/prepare',{campaign:c.name,source:s,mode:el.querySelector('.mode').value,pages:el.querySelector('.pages').value.trim()});prepShown=0;await load();pollPrep()}catch(e){err.textContent=e.message}};
    cards.appendChild(el)}
  const p=state.prep;$('#progress').hidden=!(p&&(p.running||(p.done&&p.lines.length)));
  $('#setcur').textContent=state.model+(state.settings.effort_play?' \u00b7 gioco '+state.settings.effort_play:'');
}
async function enter(name,err){try{await api('/api/campaign',{name});await load();}catch(e){if(err)err.textContent=e.message}}
$('#create').onclick=async()=>{const name=$('#newname').value.trim();const err=$('#newerr');if(!name){err.textContent='Scrivi un nome.';return}
  try{await api('/api/campaign/new',{name,empty:$('#newkind').value==='empty'});$('#newname').value='';err.textContent='';await load()}catch(e){err.textContent=e.message}};
$('#newname').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('#create').click()}});
$('#back').onclick=async()=>{await api('/api/campaign/leave',{});await load()};

/* impostazioni */
async function fillSettings(){
  const host=$('#setbody');const s=await api('/api/settings');
  host.innerHTML='';host.appendChild($('#settings-tpl').content.cloneNode(true));
  const err=host.querySelector('.err'), status=host.querySelector('.s-status');
  const opt=(sel,items,cur)=>{for(const it of items){const o=document.createElement('option');o.value=it.id;o.textContent=it.name+(it.price?' \u00b7 '+it.price+' /M token':'');o.selected=it.id===cur;sel.appendChild(o)}};
  const mSel=host.querySelector('.s-model');opt(mSel,s.models,s.current.model);
  const note=()=>{const m=s.models.find(x=>x.id===mSel.value);host.querySelector('.s-model-note').textContent=m?m.note:'';
    const noEffort=mSel.value.startsWith('claude-haiku');for(const k of ['play','sheet','prepare','tidy'])host.querySelector('.s-'+k).disabled=noEffort;
    status.textContent=noEffort?'Haiku non accetta livelli di ragionamento: verranno ignorati.':''};
  for(const k of ['play','sheet','prepare','tidy']){const sel=host.querySelector('.s-'+k);opt(sel,s.efforts,s.current['effort_'+k]);sel.title=s.efforts.map(e=>e.name+': '+e.note).join('\n')}
  const mpSel=host.querySelector('.s-model-prepare');opt(mpSel,[{id:'',name:'stesso del gioco'}].concat(s.models),s.current.model_prepare||'');
  const mmSel=host.querySelector('.s-model-memory');opt(mmSel,s.models,s.current.model_memory||'claude-sonnet-5');
  const msSel=host.querySelector('.s-model-scribe');opt(msSel,s.models,s.current.model_scribe||'claude-haiku-4-5');
  host.querySelector('.s-batch').value=s.current.prep_batch?'1':'0';
  mSel.onchange=note;note();
  host.querySelector('.s-save').onclick=async()=>{try{const body={model:mSel.value,model_prepare:mpSel.value,model_scribe:msSel.value,model_memory:mmSel.value,prep_batch:host.querySelector('.s-batch').value==='1'};for(const k of ['play','sheet','prepare','tidy'])body['effort_'+k]=host.querySelector('.s-'+k).value;
    await api('/api/settings',body);await load();status.textContent='Salvato.';err.textContent=''}catch(e){err.textContent=e.message}};
}
$('#settings').addEventListener('toggle',()=>{if($('#settings').open)fillSettings()});

/* preparazione: avanzamento in cima al menu */
async function pollPrep(){clearTimeout(prepTimer);try{const p=await api('/api/prepare/status');const box=$('#progress'),lines=box.querySelector('.lines');
  box.hidden=false;box.querySelector('.lbl').textContent=((p.kind||'Preparazione')+(p.running?' in corso':' conclusa'))+(p.campaign?' \u00b7 '+p.campaign:'');
  if(prepShown===0)lines.textContent='';
  for(;prepShown<p.lines.length;prepShown++){const d=document.createElement('div');d.textContent=p.lines[prepShown];lines.appendChild(d)}
  if(p.error&&!lines.querySelector('.e')){const d=document.createElement('div');d.className='e';d.textContent='Interrotta: '+p.error+' (dettagli nel registro in log/)';lines.appendChild(d)}
  lines.scrollTop=lines.scrollHeight;
  if(p.running){prepTimer=setTimeout(pollPrep,2000)}else{await load()}}catch(e){}}

/* esploratore di riserva */
async function showExplorer(ex,src,err,start){
  ex.hidden=false;const crumbs=ex.querySelector('.crumbs'),entries=ex.querySelector('.entries');
  async function go(p){let d;try{d=await api('/api/fs?path='+encodeURIComponent(p||''))}catch(e){err.textContent=e.message;return}
    crumbs.innerHTML='';const home=document.createElement('button');home.type='button';home.textContent='Risorse';home.onclick=()=>go('');crumbs.appendChild(home);
    if(d.path){const parts=d.path.split(/[\\\/]/).filter(Boolean);let acc='';parts.forEach((part,i)=>{acc+=(i===0&&/^[A-Za-z]:$/.test(part))?part+'\\':(i===0?'/'+part:(acc.endsWith('\\')||acc.endsWith('/')?'':'\\')+part);const full=acc;
      const sep=document.createElement('span');sep.className='sep';sep.textContent='\u203a';crumbs.appendChild(sep);const bt=document.createElement('button');bt.type='button';bt.textContent=part;bt.onclick=()=>go(full);crumbs.appendChild(bt)})}
    entries.innerHTML='';
    const item=(cls,ico,name,size,fn)=>{const bt=document.createElement('button');bt.type='button';bt.className=cls;bt.innerHTML='<span class="ico"></span><span class="nm"></span>'+(size!=null?'<span class="sz"></span>':'');bt.querySelector('.ico').textContent=ico;bt.querySelector('.nm').textContent=name;if(size!=null)bt.querySelector('.sz').textContent=(size/1048576).toFixed(size>1048576?1:2)+' MB';bt.onclick=fn;entries.appendChild(bt)};
    if(!d.path){for(const r of d.roots)item('dir','\u25b8',r.label+'  '+r.path,null,()=>go(r.path))}
    else{if(d.parent)item('dir','\u2190','..',null,()=>go(d.parent));for(const x of d.dirs)item('dir','\u25b8',x.name,null,()=>go(x.path));
      for(const fl of d.files)item('file','pdf',fl.name,fl.size,()=>{src.value=fl.path;ex.hidden=true});
      if(!d.dirs.length&&!d.files.length){const e=document.createElement('div');e.className='empty';e.textContent='Nessuna cartella o manuale (pdf, md, txt) qui.';entries.appendChild(e)}}}
  await go(start||'');
}

/* ===== CAMPAGNA ===== */
function add(cls,text,which){const l=logs[which||mode],d=document.createElement('div');d.className='msg '+cls;d.textContent=text;l.appendChild(d);l.scrollTop=l.scrollHeight;return d}
/* dadi: il narratore lascia [[tiro: nome | dadi | opzioni]], qui diventa un pulsante; tira il programma */
const ROLL_RE=/\[\[\s*tiro\s*:([\s\S]*?)\]\]/i;
function gmText(el,text,which){const m=text.match(ROLL_RE);el.textContent=text.replace(ROLL_RE,'').trim();
  const l=logs[which||mode];l.querySelectorAll('.rollbar').forEach(b=>b.remove());
  if(m&&(which||mode)==='play'){const parts=m[1].split('|').map(x=>x.trim()).filter(Boolean);
    const bar=document.createElement('div');bar.className='meta rollbar';const b=document.createElement('button');b.type='button';b.className='btn gold small';
    b.textContent='Tira: '+parts[0]+(parts.length>1?'  ('+parts.slice(1).join(', ')+')':'');b.title='I dadi li tira il programma: l\u2019esito va al master così com\u2019è';
    b.onclick=()=>doRoll(m[1]);bar.appendChild(b);l.appendChild(bar)}
  l.scrollTop=l.scrollHeight}
async function doRoll(spec){const c=io.play;logs.play.querySelectorAll('.rollbar').forEach(b=>b.remove());
  const me=add('me','Tiro i dadi…','play');c.b.disabled=true;const wait=add('gm','…','play');
  try{const j=await api('/api/roll',{spec});me.textContent=j.roll_text;gmText(wait,j.text,'play');meta(usageLine(j),'play');await load()}
  catch(e){me.remove();wait.textContent='Errore: '+e.message;wait.classList.add('hint')}finally{c.b.disabled=false;c.t.focus()}}
function meta(text,which){const l=logs[which||mode],d=document.createElement('div');d.className='meta';d.textContent=text;l.appendChild(d);l.scrollTop=l.scrollHeight}
function setMode(m){if(m==='play'&&!(state&&state.pg))m='sheet';mode=m;
  for(const k in views){views[k].classList.toggle('on',k===m);tabs[k].classList.toggle('on',k===m)}
  document.body.classList.toggle('sheet',m==='sheet');labelSession();io[m].t.focus()}
tabs.play.onclick=()=>setMode('play');tabs.sheet.onclick=()=>setMode('sheet');

/* markdown minimo per mostrare la scheda: titoli, tabelle, elenchi, grassetto */
function esc(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function inline(s){return esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>')}
function md(text){const out=[];const lines=text.split(/\r?\n/);let i=0,para=[];
  const flush=()=>{if(para.length){out.push('<p>'+para.map(inline).join('<br>')+'</p>');para=[]}};
  while(i<lines.length){const l=lines[i];const h=l.match(/^(#{1,3})\s+(.*)$/);
    if(h){flush();out.push('<h'+h[1].length+'>'+inline(h[2])+'</h'+h[1].length+'>');i++;continue}
    if(/^\s*\|.*\|\s*$/.test(l)){flush();const rows=[];while(i<lines.length&&/^\s*\|.*\|\s*$/.test(lines[i])){rows.push(lines[i].trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim()));i++}
      const body=rows.filter(r=>!r.every(c=>/^:?-{2,}:?$/.test(c)||c===''));if(body.length){out.push('<div class="tw"><table><thead><tr>'+body[0].map(c=>'<th>'+inline(c)+'</th>').join('')+'</tr></thead><tbody>'+body.slice(1).map(r=>'<tr>'+r.map(c=>'<td>'+inline(c)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>')}continue}
    if(/^\s*[-*]\s+/.test(l)){flush();const items=[];while(i<lines.length&&/^\s*[-*]\s+/.test(lines[i])){items.push('<li>'+inline(lines[i].replace(/^\s*[-*]\s+/,''))+'</li>');i++}out.push('<ul>'+items.join('')+'</ul>');continue}
    if(!l.trim()){flush();i++;continue}
    para.push(l);i++}
  flush();return out.join('')}

/* sezione PERSONAGGIO */
let viewing=null;
async function showSheet(slug){viewing=slug;const box=$('#sheetview');
  if(!slug){box.hidden=true;box.innerHTML='';renderPgList();return}
  try{const s=await api('/api/pg/sheet?slug='+encodeURIComponent(slug));box.innerHTML=md(s.markdown);box.hidden=false}catch(e){box.hidden=true}
  renderPgList()}
function renderPgList(){const list=$('#pglist');list.innerHTML='';const active=state.pg&&state.pg.slug;
  $('#pgsub').textContent=state.sheets.length?'Il personaggio in gioco è legato a questa campagna: il master ne ha sempre la scheda davanti.':'Nessun personaggio ancora. Crealo qui a destra: serve per giocare.';
  for(const s of state.sheets){const el=document.createElement('div');el.className='pgcard'+(s.slug===active?' active':'')+(s.slug===viewing?' viewing':'');
    el.innerHTML='<div class="top"><span class="n"></span><span class="tag"></span></div><span class="d"></span><div class="acts"></div>';
    el.querySelector('.n').textContent=s.name;el.querySelector('.tag').textContent=s.slug===active?'in gioco':'';el.querySelector('.d').textContent=s.summary||'';
    const acts=el.querySelector('.acts');const btn=(label,cls,fn)=>{const b=document.createElement('button');b.type='button';b.className='btn '+cls;b.textContent=label;b.onclick=fn;acts.appendChild(b)};
    btn(s.slug===viewing?'Nascondi scheda':'Vedi scheda','',()=>showSheet(s.slug===viewing?null:s.slug));
    if(s.slug!==active)btn('Gioca con questo','moss',async()=>{await api('/api/pg/select',{slug:s.slug});logs.play.innerHTML='';await load();await restore('play')});
    btn('Modifica','',()=>{io.sheet.t.value='Voglio modificare la scheda di '+s.name+': ';io.sheet.t.focus()});
    list.appendChild(el)}}
function renderChips(){const c=$('#chips');c.innerHTML='';
  const items=state.sheets.length?['Crea un nuovo personaggio, guidami passo passo','Proponi tre concetti adatti a questa ambientazione']
    :['Guidami passo passo nella creazione','Proponi tre concetti adatti a questa ambientazione','Crea un personaggio pronto, poi lo rivedo'];
  for(const text of items){const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=()=>{io.sheet.t.value=text;send('sheet')};c.appendChild(b)}}

function usageLine(j){return 'letti: '+(j.files_read.join(', ')||'–')+' · modificati: '+(j.files_changed.join(', ')||'–')+' · token '+j.input_tokens+'/'+j.output_tokens+(j.cache_read_tokens?' (cache '+j.cache_read_tokens+')':'')+(j.cost_usd?' · costo ≈ $'+j.cost_usd.toFixed(3)+(j.scribe_cost_usd?' (di cui scriba $'+j.scribe_cost_usd.toFixed(3)+')':''):'')}
function divider(text,which){const d=document.createElement('div');d.className='meta divider';d.textContent=text;logs[which].appendChild(d)}
const PRELUDE_MARK='[Apertura della campagna]',OPENING_MARK='[Apertura della sessione]',CLOSING_MARK='[Chiusura della sessione]';
function tableMark(p){return p.startsWith(PRELUDE_MARK)?'— preludio —':p.startsWith(OPENING_MARK)?'— apertura della sessione —':p.startsWith(CLOSING_MARK)?'— chiusura della sessione —':null}
/* preludio: la prima scena nasce dal background del personaggio, non da "inizia la scena" */
function showPrelude(){if(!state.pg||logs.play.querySelector('.prelude'))return;
  const c=document.createElement('div');c.className='prelude';
  c.innerHTML='<h3></h3><p class="lead"></p><textarea id="prelude-notes" rows="2"></textarea><div class="row"><button class="btn gold" type="button"></button><span class="hint"></span></div><p class="err"></p>';
  const again=state.session>1;
  c.querySelector('h3').textContent=again?'Sessione '+state.session+' di '+state.pg.name:'Preludio di '+state.pg.name;
  c.querySelector('.lead').textContent=again?'La sessione scorsa è chiusa e archiviata. Il master riparte dal diario e dai fili aperti: dice dove eravate, quanto tempo è passato, e muove qualcosa che chiede una risposta.':'La campagna non è ancora iniziata. Il master legge il background sulla scheda, trova il momento che ha spezzato la vita del personaggio e apre la scena lì, con le sue conseguenze addosso. Pianta anche i fili che porteranno avanti la storia.';
  const notes=c.querySelector('textarea');notes.placeholder='Facoltativo: indicazioni per questa apertura (tono, dove vuoi cominciare, cosa evitare)';
  const btn=c.querySelector('button'),hint=c.querySelector('.hint'),err=c.querySelector('.err');const open=again?'Apri la sessione':'Apri la campagna';btn.textContent=open;hint.textContent='Una scena sola, poi si gioca.';
  btn.onclick=async()=>{btn.disabled=true;notes.disabled=true;btn.textContent='Il master prepara la scena…';err.textContent='';
    try{const j=await api('/api/prelude',{notes:notes.value});c.remove();divider(again?'— apertura della sessione —':'— preludio —','play');gmText(add('gm','','play'),j.text,'play');meta(usageLine(j),'play');io.play.t.focus()}
    catch(e){err.textContent=e.message;btn.disabled=false;notes.disabled=false;btn.textContent=open}};
  logs.play.appendChild(c);logs.play.scrollTop=logs.play.scrollHeight}
/* ripresa: al rientro la conversazione torna com'era, dall'archivio del giocato */
async function restore(only){for(const k of (only?[only]:['play','sheet'])){try{const h=await api('/api/history?which='+k);
  if(h.turns.length){const d=document.createElement('div');d.className='meta divider';
    d.textContent='— conversazione ripresa: '+h.total+' turni in archivio'+(h.total>h.turns.length?', mostro gli ultimi '+h.turns.length:'')+' —';logs[k].appendChild(d);
    for(const t of h.turns){const tm=tableMark(t.player);if(tm)divider(tm,k);else add('me',t.player,k);gmText(add('gm','',k),t.master,k)}}
  else if(k==='play')showPrelude()}catch(e){}}}
$('#newsession').onclick=async()=>{const k=mode,b=$('#newsession');
  const ask=k!=='play'?'Archiviare la conversazione di creazione e ripartire da capo?'
    :state.can_close?'Chiudere la sessione '+state.session+'? Il master ferma la scena, scrive il riepilogo nel diario, aggiorna i fili aperti e assegna i punti esperienza secondo il manuale (una chiamata). Poi la conversazione viene archiviata.'
    :'Archiviare la conversazione e ripartire? Con così pochi turni non è una sessione da chiudere: niente riepilogo e niente esperienza. La storia nei file della campagna resta.';
  if(!confirm(ask))return;
  const label=b.textContent;b.disabled=true;if(k==='play'&&state.can_close)b.textContent='Il master chiude la sessione…';
  try{const r=await api('/api/session/new',{which:k});
    if(r.closing){divider('— chiusura della sessione —',k);add('gm',r.closing.text,k);meta(usageLine(r.closing),k)}
    else logs[k].innerHTML='';
    if(k==='play'&&r.closing){b.textContent='Consolido la memoria…';meta('Rileggo il giocato della sessione e lo consolido in memoria…',k);
      try{const m=await api('/api/memory',{});meta(m.summary+(m.inconsistencies&&m.inconsistencies.length?' Incoerenze: '+m.inconsistencies.map(i=>i.descrizione).join(' · '):''),k)}
      catch(e){meta('Memoria non consolidata ('+e.message+'): il giocato resta in attesa, riprova con "Consolida memoria".',k)}}
    divider('— nuova sessione —',k);
    if(k==='play'){await load();showPrelude()}}
  catch(e){meta('Errore: '+e.message,k)}
  finally{b.disabled=false;b.textContent=label;labelSession()}};
/* memoria lenta a comando: il giocato non ancora consolidato diventa cronaca, fili, memoria dei PNG */
$('#memory').onclick=async()=>{const b=$('#memory'),n=state.memory_pending;
  if(!confirm('Consolidare la memoria? Il programma rilegge i '+n+' turni non ancora consolidati e li converte in cronaca con il turno d\u2019origine, fili aperti, memoria dei personaggi e documento di scena. Una chiamata sola; alla chiusura della sessione avviene comunque da sé.'))return;
  b.disabled=true;b.textContent='Rileggo il giocato…';
  try{const r=await api('/api/memory',{});meta(r.summary+(r.inconsistencies&&r.inconsistencies.length?' Incoerenze: '+r.inconsistencies.map(i=>i.descrizione).join(' · '):''),'play');await load()}
  catch(e){meta('Errore: '+e.message,'play')}finally{b.disabled=false;labelSession()}};
/* contatore di spesa: media per turno della sessione, dai token di ogni risposta per il prezzo del modello */
function money(x){return '$'+(x<1?x.toFixed(3):x.toFixed(2))}
function renderSpend(){const el=$('#spend'),s=state&&state.spend;if(!el)return;if(!s||!s.campagna){el.hidden=true;return}
  el.hidden=false;el.textContent='media '+money(s.media_turno_sessione||s.media_turno_campagna)+'/turno · sessione '+money(s.sessione)+' · oggi '+money(s.oggi);
  const parts=Object.entries(s.per_processo).sort((a,b)=>b[1]-a[1]).map(([k,v])=>k+' '+money(v)).join(', ');
  el.title='Ultimo turno '+money(s.ultimo_turno)+' · media di sessione su '+s.turni_sessione+' turni · media di campagna '+money(s.media_turno_campagna)+' su '+s.turni_campagna+' turni\nCampagna in tutto '+money(s.campagna)+': '+parts+'\nCalcolato dai token di ogni risposta per il prezzo del modello (narrazione + scriba nella media). La console Anthropic somma anche altro che usa la stessa chiave.'}
function labelSession(){renderSpend();const m=$('#memory');if(m){const n=state&&mode==='play'?(state.memory_pending||0):0;m.hidden=n<3;m.textContent='Consolida memoria ('+n+')';m.title='Turni giocati non ancora consolidati: '+n}
  const b=$('#newsession');if(!b)return;const close=mode==='play'&&!!(state&&state.can_close);
  b.textContent=close?'Chiudi sessione':'Nuova sessione';
  b.title=close?'Il master riepiloga, aggiorna i fili aperti e assegna i punti esperienza; poi la conversazione viene archiviata.':'Archivia la conversazione corrente e ne apre una nuova. La storia nei file della campagna resta.'}
/* senza profilo dei dadi il master scrive i tiri per esteso: qui lo si ricava dalle regole estratte, una volta */
function diceHint(){const had=logs.play.querySelector('.dicehint');if(!state.pg||!state.has_creation_rules||(state.dice&&state.dice.length)){if(had)had.remove();return}
  if(had)return;const d=document.createElement('div');d.className='meta dicehint';
  const t=document.createElement('span');t.textContent='Questa campagna non ha ancora il profilo dei dadi: il programma può ricavarlo dalle regole estratte dal manuale (una chiamata, pochi centesimi, una volta sola). ';
  const b=document.createElement('button');b.type='button';b.className='btn small';b.textContent='Ricava i dadi dalle regole';
  b.onclick=async()=>{b.disabled=true;b.textContent='Leggo le regole sui tiri…';try{const r=await api('/api/dice/profile',{});d.textContent='Profilo dei dadi ricavato: '+r.kinds.join(', ')+'.';d.classList.remove('dicehint');await load()}catch(e){b.disabled=false;b.textContent='Ricava i dadi dalle regole';t.textContent='Errore: '+e.message+' '}};
  d.appendChild(t);d.appendChild(b);logs.play.insertBefore(d,logs.play.firstChild)}
let shownCampaign=null;
function renderPlay(){
  $('#camp-name').textContent=state.campaign;const ef=state.settings.effort_play;$('#model').textContent=state.model+(ef?' · '+ef:'');
  $('#pgchip').hidden=!state.pg;$('#pgchip-name').textContent=state.pg?state.pg.name+(state.xp?' · PE '+state.xp.disponibili:''):'';
  $('#pgchip').title=state.xp?'Punti esperienza: '+state.xp.disponibili+' disponibili, '+state.xp.guadagnati+' guadagnati, '+state.xp.spesi+' spesi. Sessione '+state.session+'. Il conto lo tiene il registro della scheda.':'';labelSession();
  tabs.play.disabled=!state.pg;tabs.play.title=state.pg?'':'Crea o scegli prima un personaggio';
  const fresh=shownCampaign!==state.campaign;
  if(fresh){shownCampaign=state.campaign;viewing=null;for(const k in logs)logs[k].innerHTML='';
    add('gm hint',!state.has_creation_rules?'Questa campagna non ha ancora regole di creazione in meccanica/creazione-pg.md: preparala da un manuale dal menu principale, oppure descrivimi il personaggio e lo imposto in forma libera.'
      :(state.sheets.length?'Qui crei un nuovo personaggio o modifichi una scheda, seguendo le regole della campagna.':'Creiamo il tuo personaggio seguendo le regole della campagna. Scegli un avvio qui sotto o descrivimi chi vuoi interpretare.'),'sheet')}
  renderPgList();renderChips();diceHint();
  if(fresh)restore();
  if(fresh){setMode(state.pg?'play':'sheet');if(state.pg)showSheet(state.pg.slug)}
  else if(mode==='play'&&!state.pg)setMode('sheet');
}
async function send(which){which=which||mode;const c=io[which];const text=c.t.value.trim();if(!text||!state||!state.campaign)return;
  if(which==='play'&&/^\/tira\s+/i.test(text)){c.t.value='';return doRoll(text.replace(/^\/tira\s+/i,''))}
  c.t.value='';add('me',text,which);c.b.disabled=true;const wait=add('gm','…',which);
  try{const j=await api(c.url,{text});gmText(wait,j.text,which);
    meta(usageLine(j),which);
    const touched=j.files_changed.filter(x=>x.startsWith('schede/'));
    if(touched.length){const hadPg=!!state.pg;await load();const slug=touched[touched.length-1].replace(/^schede\//,'').replace(/\.(md|json)$/,'');await showSheet(slug);
      if(which==='sheet')meta(!hadPg&&state.pg?'Scheda salvata: '+state.pg.name+' è ora il personaggio di questa campagna. Puoi passare a Gioca.':'Scheda salvata.','sheet')}
    else if(which==='play')await load()}
  catch(e){wait.textContent='Errore: '+e.message;wait.classList.add('hint')}finally{c.b.disabled=false;c.t.focus()}}
for(const k of ['play','sheet']){io[k].f.addEventListener('submit',e=>{e.preventDefault();send(k)});
  io[k].t.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(k)}})}

/* ===== stato ===== */
async function load(){
  state=await api('/api/state');
  if(state.campaign){renderPlay();screen('play')}else{shownCampaign=null;renderHome();screen('home');if(state.prep&&state.prep.running&&!prepTimer)pollPrep()}
}
load();
</script></body></html>
"""


# --- server -----------------------------------------------------------------------


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # silenzia il log HTTP
            pass

        def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, obj: Any) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            return data if isinstance(data, dict) else {}

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(HTTPStatus.OK, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(HTTPStatus.OK, app.state())
            elif path == "/api/campaigns":
                self._json(HTTPStatus.OK, app.list_campaigns())
            elif path == "/api/prepare/status":
                self._json(HTTPStatus.OK, app.prep)
            elif path == "/api/settings":
                self._json(HTTPStatus.OK, app.settings_payload())
            elif path == "/api/history":
                q = parse_qs(urlparse(self.path).query)
                try:
                    self._json(HTTPStatus.OK, app.history(q.get("which", ["play"])[0], int(q.get("limit", ["40"])[0])))
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            elif path == "/api/pg/sheet":
                slug = parse_qs(urlparse(self.path).query).get("slug", [""])[0]
                try:
                    self._json(HTTPStatus.OK, app.sheet_text(slug))
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            elif path == "/api/fs":
                target = parse_qs(urlparse(self.path).query).get("path", [""])[0]
                try:
                    self._json(HTTPStatus.OK, explorer.list_dir(target))
                except (FileNotFoundError, PermissionError, OSError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            started = time.monotonic()
            try:
                body = self._body()
                if path == "/api/campaign":
                    camp = app.enter(str(body.get("name", "")))
                    self._json(HTTPStatus.OK, {"campaign": camp.name})
                elif path == "/api/campaign/leave":
                    app.leave()
                    self._json(HTTPStatus.OK, {"campaign": None})
                elif path == "/api/campaign/new":
                    name = app.create(str(body.get("name", "")), bool(body.get("empty")))
                    self._json(HTTPStatus.OK, {"campaign": name})
                elif path == "/api/prepare":
                    app.start_prepare(str(body.get("campaign", "")), str(body.get("source", "")),
                                      str(body.get("mode", "testo")), str(body.get("pages", "")) or None)
                    self._json(HTTPStatus.OK, {"started": True})
                elif path == "/api/sanitize":
                    app.start_sanitize(str(body.get("campaign", "")), bool(body.get("deep", True)))
                    self._json(HTTPStatus.OK, {"started": True})
                elif path == "/api/settings":
                    self._json(HTTPStatus.OK, app.update_settings(body))
                elif path == "/api/browse":
                    chosen = explorer.native_pick(str(body.get("initial", "")) or None)
                    log.info("esplora risorse: %s", chosen or "annullato")
                    self._json(HTTPStatus.OK, {"path": chosen})
                elif path == "/api/prelude":
                    result = app.prelude(str(body.get("notes", "")))
                    log.info("%s ok in %.1fs", path, time.monotonic() - started)
                    self._json(HTTPStatus.OK, result.__dict__)
                elif path == "/api/memory":
                    out = app.consolidate()
                    log.info("%s ok in %.1fs", path, time.monotonic() - started)
                    self._json(HTTPStatus.OK, out)
                elif path == "/api/dice/profile":
                    self._json(HTTPStatus.OK, app.make_dice_profile())
                elif path == "/api/roll":
                    out = app.roll(str(body.get("spec", "")))
                    log.info("%s ok in %.1fs", path, time.monotonic() - started)
                    self._json(HTTPStatus.OK, out)
                elif path == "/api/session/new":
                    self._json(HTTPStatus.OK, app.new_session(str(body.get("which", "play"))))
                elif path == "/api/pg/select":
                    self._json(HTTPStatus.OK, app.select_pg(str(body.get("slug", ""))))
                elif path in ("/api/play", "/api/sheet"):
                    text = str(body.get("text", ""))
                    result = app.play(text) if path == "/api/play" else app.build(text)
                    log.info("%s ok in %.1fs", path, time.monotonic() - started)
                    self._json(HTTPStatus.OK, result.__dict__)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
                msg = "Esiste gia' una campagna con questo nome" if isinstance(exc, FileExistsError) else str(exc)
                log.warning("%s rifiutata: %s", path, msg)
                self._json(HTTPStatus.BAD_REQUEST, {"error": msg})
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                log.warning("%s: la pagina ha chiuso la connessione dopo %.1fs, prima di ricevere la risposta (il lavoro e' stato fatto)",
                            path, time.monotonic() - started)
            except Exception as exc:  # errori API: mostrarli in pagina e' meglio di un 500 muto
                log.error("%s FALLITA dopo %.1fs: %s (dettagli sopra)", path, time.monotonic() - started, describe_error(exc))
                self._json(HTTPStatus.BAD_GATEWAY, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(app: App, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    try:
        server = ThreadingHTTPServer((host, port), make_handler(app))
    except OSError as exc:
        log.error("UI INTERROTTA: impossibile aprire la porta %d (%s). Un'altra istanza e' gia' in esecuzione?", port, exc)
        raise
    url = f"http://{host}:{port}"
    current = app.campaign.name if app.campaign else "menu principale"
    print(f"Simple Master su {url}  ({current}, modello: {app.settings.model})")
    log.info("UI in ascolto su %s | %s | modello=%s", url, current, app.settings.model)
    if open_browser:
        import webbrowser

        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("UI chiusa dall'utente (Ctrl+C)")
    finally:
        server.server_close()
        log.info("UI FINITA")
