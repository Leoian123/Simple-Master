"""Misura la preparazione da piu' manuali sul golden dataset e stampa il risultato.

    .venv/Scripts/python tests/misura_golden.py

Tutti gli stadi qui sono gratuiti (nessuna chiamata). Ogni fase del lavoro aggiunge la sua colonna:

    partenza     la fusione com'era: ogni blocco accodato direttamente nella campagna
    paternita'   uno spazio di lavoro per manuale, campagna composta in ordine canonico,
                 manuale di un'altra edizione rifiutato all'ingresso

Con `--vero` si aggiunge lo stadio a pagamento: la risoluzione con il modello vero (pochi centesimi; le
decisioni gia' prese stanno in cache e non si ripagano finche' il prompt non cambia).

In fondo, la ricerca dei candidati allo scontro: quante coppie da confrontare trova tra quelle attese, e
quante in piu' (ognuna costera' un confronto).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from golden import (  # noqa: E402
    apply_staged, apply_today, found_candidates, load_cases, load_manual, predicted_relations, score_candidates,
    score_declared, score_relations, score_state, snapshot,
)
from master import Campaign  # noqa: E402
from master.prepare import EditionMismatch  # noqa: E402

STAGES: dict[str, Callable] = {"partenza": apply_today, "paternita'": apply_staged}


def prepare(tmp: Path, apply: Callable, order: list[dict]) -> Campaign:
    campaign = Campaign.create(tmp / "-".join(m["nome"] for m in order))
    for manual in order:
        apply(campaign, manual)
    return campaign


def measure(apply: Callable, base: dict, expansion: dict, cases: dict) -> tuple[dict[str, list[str]], bool]:
    """Esito per caso con i manuali nell'ordine naturale, e se l'ordine inverso da' lo stesso stato."""
    tmp = Path(tempfile.mkdtemp())
    try:
        forward = prepare(tmp, apply, [base, expansion])
        result = score_state(forward, cases, base)
        same = snapshot(forward) == snapshot(prepare(tmp, apply, [expansion, base]))
        for name in cases["manuali_rifiutati"]:  # per ultimi: se non vengono rifiutati sporcano la campagna
            try:
                apply(forward, load_manual(name))
                result[name] = ["manuale di un'altra edizione fuso nella campagna"]
            except EditionMismatch:
                result[name] = []
        return result, same
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def live(base: dict, expansion: dict, cases: dict, category: dict[str, str]) -> None:
    """Lo stadio a pagamento: la risoluzione con il modello vero. Le decisioni gia' prese stanno in cache."""
    import os

    import anthropic

    from master.models import Settings, cost_usd
    from master.prepare import Preparer

    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
        key, sep, value = line.partition("=")
        if sep and not line.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    settings = Settings.load(ROOT / "impostazioni.json")
    model, effort = settings.model_for("prepare"), settings.effort_for("prepare")
    tmp = Path(tempfile.mkdtemp())
    try:
        campaign = Campaign.create(tmp / "vero")
        preparer = Preparer(campaign, client=anthropic.Anthropic(), model=model, effort=effort, batch=False)
        apply_staged(campaign, base, preparer, resolve=True)
        report = apply_staged(campaign, expansion, preparer, resolve=True)
        solved = report.resolution
        result = score_state(campaign, cases, base)
        predicted = predicted_relations(campaign, solved.steps, cases, expansion)
        relations = score_relations({i: p.get("rapporto") for i, p in predicted.items()}, cases)
        declared = score_declared(predicted, cases)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nStadio a pagamento: risoluzione con {model} (effort {effort or 'predefinito'})")
    names = list(dict.fromkeys(category.values()))
    width = max(len(n) for n in names)
    for name in names:
        ids = [i for i, c in category.items() if c == name and i in result]
        if ids:
            print(f"  {name.ljust(width)}  {sum(1 for i in ids if not result[i])}/{len(ids)}")
    print(f"  {'TOTALE'.ljust(width)}  {sum(1 for why in result.values() if not why)}/{len(result)}")
    print(f"  rapporti classificati bene: {relations['giusti']}/{relations['casi']}"
          + (f" (sbagliati: {relations['sbagliati']})" if relations["sbagliati"] else ""))
    print(f"  correzioni dichiarate o no, riconosciute: {declared['giusti']}/{declared['casi']}"
          + (f" (sbagliati: {', '.join(declared['sbagliati'])})" if declared["sbagliati"] else "")
          + (f"; spareggi non segnati: {', '.join(declared['spareggi_non_segnati'])}" if declared["spareggi_non_segnati"] else ""))
    print("  " + solved.summary())
    print(f"  chiamate {solved.calls}, token in/out {solved.input_tokens}/{solved.output_tokens}, "
          f"costo ~${cost_usd(model, solved.input_tokens, solved.output_tokens):.2f} a tariffa piena")
    for step in solved.steps:
        if step["esito"] == "quarantena":
            print(f"  QUARANTENA {step['titolo']} <- {step['sezione']}: {step.get('perche')}")
    print("  casi che non passano:")
    for case_id, why in result.items():
        if why:
            print(f"    {case_id} ({category[case_id]}): " + "; ".join(why))


def main() -> int:
    base, expansion, cases = load_manual("base"), load_manual("espansione"), load_cases()
    category = {c["id"]: c["categoria"] for c in cases["casi"]} | {g["id"]: "glossario conteso" for g in cases["glossario"]}
    category |= {name: "altra edizione" for name in cases["manuali_rifiutati"]}
    results = {name: measure(apply, base, expansion, cases) for name, apply in STAGES.items()}

    names = list(dict.fromkeys(category.values()))
    width = max(len(n) for n in names + ["stesso esito cambiando ordine"])
    print(" " * (width + 2) + "".join(name.rjust(12) for name in results))
    for name in names:
        ids = [i for i, c in category.items() if c == name]
        row = "".join(f"{sum(1 for i in ids if not result[i])}/{len(ids)}".rjust(12) for result, _ in results.values())
        print(f"  {name.ljust(width)}{row}")
    total = "".join(f"{sum(1 for why in result.values() if not why)}/{len(result)}".rjust(12) for result, _ in results.values())
    print(f"\n  {'TOTALE'.ljust(width)}{total}")
    print(f"  {'stesso esito cambiando ordine'.ljust(width)}" + "".join(("si" if same else "NO").rjust(12) for _, same in results.values()))

    tmp = Path(tempfile.mkdtemp())
    try:
        staged = prepare(tmp, apply_staged, [base, expansion])
        found = score_candidates(found_candidates(staged, expansion), cases)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nCandidati allo scontro (nessuna chiamata): trovati {found['trovati']}/{found['attesi']}"
          + (f", mancanti {', '.join(found['mancanti'])}" if found["mancanti"] else "")
          + f"; {found['in_piu']} in piu', di cui {found['falsi_allarmi_presi']} falsi allarmi previsti")

    last, (result, _) = list(results.items())[-1]
    print(f"\nCasi che non passano ({last}):")
    for case_id, why in result.items():
        if why:
            print(f"  {case_id} ({category[case_id]}): " + "; ".join(why))
    if "--vero" in sys.argv:
        live(base, expansion, cases, category)
    return 0


if __name__ == "__main__":
    sys.exit(main())
