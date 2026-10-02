"""Misura la preparazione da piu' manuali sul golden dataset e stampa il risultato.

    .venv/Scripts/python tests/misura_golden.py

Tutti gli stadi qui sono gratuiti (nessuna chiamata). Ogni fase del lavoro aggiunge la sua colonna:

    partenza     la fusione com'era: ogni blocco accodato direttamente nella campagna
    paternita'   uno spazio di lavoro per manuale, campagna composta in ordine canonico,
                 manuale di un'altra edizione rifiutato all'ingresso
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

from golden import apply_staged, apply_today, load_cases, load_manual, score_state, snapshot  # noqa: E402
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

    last, (result, _) = list(results.items())[-1]
    print(f"\nCasi che non passano ({last}):")
    for case_id, why in result.items():
        if why:
            print(f"  {case_id} ({category[case_id]}): " + "; ".join(why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
