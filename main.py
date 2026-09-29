"""Avvio di Simple Master.

Di norma si usa SimpleMaster.bat. Da terminale:

    python main.py                          # UI web sulla campagna di esempio
    python main.py --campaign campaigns/mia # altra campagna
    python main.py --cli                    # in terminale
    python main.py --new campaigns/mia      # crea una campagna (copia dell'esempio)
    python main.py --campaign campaigns/mia --prepare manuale.pdf   # dal manuale ai file di campagna
    python main.py --campaign campaigns/mia --tidy-glossary         # solo riordino del glossario

Ogni esecuzione scrive un registro in log/ (vedi master/logbook.py).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from master import Campaign, MasterEngine, logbook  # noqa: E402


def load_dotenv(path: Path) -> None:
    """Carica un file .env minimale (CHIAVE=valore) senza dipendenze."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def run_cli(engine: MasterEngine) -> None:
    print(f"Campagna: {engine.campaign.name} | modello: {engine.model} | 'exit' per uscire\n")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit", "esci"}:
            break
        if not text:
            continue
        result = engine.play(text)
        print(f"\n{result.text}\n")
        print(f"  [letti: {', '.join(result.files_read) or '-'} | modificati: {', '.join(result.files_changed) or '-'}]\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simple Master: master di gioco di ruolo con Claude")
    parser.add_argument("--campaign", default=None,
                        help="cartella della campagna (UI: la apre subito saltando il menu; CLI/preparazione: obbligatoria, default esempio)")
    parser.add_argument("--new", metavar="CARTELLA", help="crea una nuova campagna copiando quella di esempio")
    parser.add_argument("--empty", action="store_true", help="con --new: crea file vuoti invece di copiare l'esempio")
    parser.add_argument("--cli", action="store_true", help="usa il terminale invece della UI web")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="non aprire il browser all'avvio")
    parser.add_argument("--model", help="ID modello (default: MASTER_MODEL o claude-opus-5)")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="livello di ragionamento")
    prep = parser.add_argument_group("preparazione", "trasforma un manuale nei documenti della campagna")
    prep.add_argument("--prepare", metavar="FONTE", help="PDF, file di testo o URL http(s) da cui partire")
    prep.add_argument("--mode", choices=["testo", "pdf"], default="testo",
                      help="testo: estrae il testo (economico); pdf: invia le pagine originali (tabelle, scansioni)")
    prep.add_argument("--pages", metavar="A-B", help="solo queste pagine del PDF, es. 10-120")
    prep.add_argument("--no-consolidate", action="store_true", help="salta il riordino finale dei file")
    prep.add_argument("--no-batch", action="store_true", help="estrazione immediata a prezzo pieno invece del Batch API a meta' prezzo")
    prep.add_argument("--no-tidy", action="store_true", help="salta il riordino del glossario dopo la preparazione")
    prep.add_argument("--tidy-glossary", action="store_true",
                      help="solo riordino del glossario: allinea ai file e pulisce con una chiamata veloce")
    prep.add_argument("--sanitize", action="store_true",
                      help="toglie l'informazione duplicata: titoli equivalenti, blocchi ripetuti, voci di glossario doppie (gratuito)")
    prep.add_argument("--deep", action="store_true",
                      help="con --sanitize: riconosce anche le sezioni sullo stesso soggetto con nomi diversi (usa l'API)")
    logs = parser.add_argument_group("registri", "gestione ordinata dei file in log/")
    logs.add_argument("--logs", choices=["list", "clean", "dismiss", "purge", "diagnose", "examined"],
                      help="list: elenca con errori e interruzioni; clean: rotazione (archivia vecchi, cancella scaduti); "
                           "dismiss: archivia i file indicati o piu' vecchi di --log-days; purge: cancella l'archivio; "
                           "diagnose: interruzioni non ancora esaminate (usato dall'hook SessionStart); "
                           "examined: segna esaminati i file indicati o tutti (--log-all) e li archivia")
    logs.add_argument("--log-files", nargs="*", default=[], metavar="FILE", help="con dismiss: nomi dei registri da archiviare")
    logs.add_argument("--log-days", type=int, metavar="N", help="con clean/dismiss/purge: soglia in giorni")
    logs.add_argument("--log-all", action="store_true", help="con purge: cancella anche i registri correnti, tranne oggi")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")

    if args.logs:
        return _manage_logs(args)

    process = ("nuova" if args.new else "preparazione" if args.prepare else "riordino" if args.tidy_glossary
               else "sanifica" if args.sanitize else "gioco")
    if args.campaign is None and (args.cli or args.prepare or args.tidy_glossary or args.sanitize):
        args.campaign = str(ROOT / "campaigns" / "esempio")
    campaign_name = Path(args.new or args.campaign).name if (args.new or args.campaign) else "menu"
    log_path = logbook.setup(process, campaign_name)
    log = logging.getLogger("master.main")
    print(f"[registro: {log_path.relative_to(ROOT)}]")

    try:
        return _run(args, log)
    except KeyboardInterrupt:
        log.warning("%s INTERROTTO dall'utente (Ctrl+C)", process)
        print("\nInterrotto.")
        return 130
    except Exception as exc:
        log.critical("%s INTERROTTO: %s", process, logbook.describe_error(exc), exc_info=True)
        print(f"\nErrore: {logbook.describe_error(exc)}\nDettagli nel registro: {log_path}", file=sys.stderr)
        return 1
    finally:
        log.info("FINE %s", process)


def _manage_logs(args: argparse.Namespace) -> int:
    """Comandi sui registri: non aprono un nuovo registro, cosi' non sporcano cio' che puliscono."""
    if args.logs == "list":
        print(logbook.listing())
        return 0
    if args.logs == "diagnose":
        text = logbook.diagnosis_text()
        if text:
            print(text)
        return 0
    if args.logs == "examined":
        if not args.log_files and not args.log_all:
            print("examined: indica --log-files NOME... oppure --log-all", file=sys.stderr)
            return 2
        marked = logbook.mark_examined(files=args.log_files, everything=args.log_all)
        for name in marked:
            print(f"  esaminato   {name}")
        print(f"segnati {len(marked)}; quelli non di oggi sono in archivio")
        return 0
    if args.logs == "clean":
        report = logbook.rotate(keep_days=args.log_days)
    elif args.logs == "dismiss":
        if not args.log_files and args.log_days is None:
            print("dismiss: indica --log-files NOME... oppure --log-days N", file=sys.stderr)
            return 2
        report = logbook.dismiss(files=args.log_files, older_than_days=args.log_days)
    else:
        report = logbook.purge(older_than_days=args.log_days, everything=args.log_all)
    for name in report.archived:
        print(f"  archiviato  {name}")
    for name in report.deleted:
        print(f"  cancellato  {name}")
    print(report.summary())
    return 0


def _run(args: argparse.Namespace, log: logging.Logger) -> int:
    if args.new:
        template = None if args.empty else ROOT / "campaigns" / "esempio"
        camp = Campaign.create(args.new, template)
        log.info("campagna creata in %s (%s)", camp.root, "vuota" if args.empty else "copia dell'esempio")
        print(f"Campagna creata in {camp.root}")
        return 0

    if args.sanitize and not args.deep:  # il passo gratuito non ha bisogno della chiave
        from master.sanitize import Sanitizer

        print("\n" + Sanitizer(Campaign(args.campaign)).run(deep=False).summary())
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        log.error("INTERROTTO: manca ANTHROPIC_API_KEY (ne' variabile d'ambiente ne' .env)")
        print("Manca ANTHROPIC_API_KEY: mettila nell'ambiente o in un file .env (vedi .env.example).", file=sys.stderr)
        return 1

    if args.prepare:
        from master.prepare import Preparer

        from master.models import Settings

        # stesse scelte del menu (impostazioni.json), salvo opzioni esplicite da riga di comando
        s = Settings.load(ROOT / "impostazioni.json")
        preparer = Preparer(Campaign(args.campaign), model=args.model or s.model_for("prepare"),
                            effort=args.effort or s.effort_for("prepare"), batch=s.prep_batch and not args.no_batch)
        report = preparer.run(args.prepare, mode=args.mode, pages=args.pages,
                              consolidate=not args.no_consolidate, tidy=not args.no_tidy)
        print("\n" + report.summary())
        return 0

    if args.sanitize:
        from master.models import Settings
        from master.sanitize import Sanitizer

        s = Settings.load(ROOT / "impostazioni.json")
        report = Sanitizer(Campaign(args.campaign), model=args.model or s.model_for("prepare")).run(deep=True)
        print("\n" + report.summary())
        return 0

    if args.tidy_glossary:
        from master.tidy import GlossaryTidier

        report = GlossaryTidier(Campaign(args.campaign), model=args.model, effort=args.effort or "low").run()
        print("\n" + report.summary())
        return 0

    if args.cli:
        run_cli(MasterEngine(Campaign(args.campaign), model=args.model, effort=args.effort))
        return 0

    from master.ui import App, serve

    campaigns_dir, initial = ROOT / "campaigns", None
    if args.campaign:
        campaign_path = Path(args.campaign).resolve()
        if campaign_path.is_dir():
            campaigns_dir, initial = campaign_path.parent, campaign_path.name
    app = App(campaigns_dir, initial, model=args.model, effort=args.effort)
    serve(app, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
