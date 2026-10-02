"""Misura la preparazione da piu' manuali sul golden dataset e stampa il risultato.

    .venv/Scripts/python tests/misura_golden.py

Per ora c'e' un solo stadio, gratuito: la preparazione com'e' oggi (fusione e sanificazione, senza
chiamate). E' il punto di partenza contro cui si confrontano le fasi successive: ogni fase aggiunge
qui il suo stadio e la sua riga.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from golden import apply_today, load_cases, load_manual, score_state, snapshot  # noqa: E402
from master import Campaign  # noqa: E402


def run_today(tmp: Path, order: list[dict]) -> Campaign:
    campaign = Campaign.create(tmp / "-".join(m["nome"] for m in order))
    for manual in order:
        apply_today(campaign, manual)
    return campaign


def main() -> int:
    base, expansion, cases = load_manual("base"), load_manual("espansione"), load_cases()
    category = {c["id"]: c["categoria"] for c in cases["casi"]} | {g["id"]: "glossario conteso" for g in cases["glossario"]}
    tmp = Path(tempfile.mkdtemp())
    try:
        forward = run_today(tmp, [base, expansion])
        result = score_state(forward, cases, base)
        same_order = snapshot(forward) == snapshot(run_today(tmp, [expansion, base]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("Stadio: preparazione di oggi (fusione + sanificazione gratuita, nessuna chiamata)\n")
    names = list(dict.fromkeys(category.values()))
    width = max(len(n) for n in names)
    for name in names:
        ids = [i for i, c in category.items() if c == name]
        passed = [i for i in ids if not result[i]]
        print(f"  {name.ljust(width)}  {len(passed)}/{len(ids)}")
    total = sum(1 for why in result.values() if not why)
    print(f"\n  {'TOTALE'.ljust(width)}  {total}/{len(result)}")
    print(f"  {'stesso risultato cambiando ordine'.ljust(width)}  {'si' if same_order else 'NO'}")

    print("\nCasi che non passano:")
    for case_id, why in result.items():
        if why:
            print(f"  {case_id} ({category[case_id]}): " + "; ".join(why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
