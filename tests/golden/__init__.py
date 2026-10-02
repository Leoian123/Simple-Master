"""Golden dataset della preparazione da piu' manuali: un manuale base e un'espansione inventati,
con casi piantati di cui si conosce la risposta giusta.

Serve a misurare, non a sperare: quanti scontri tra manuali vengono trovati, quanti rapporti
classificati bene, quante sezioni escono come il master deve leggerle. Tutto senza rete: i manuali
sono gia' in forma di blocchi estratti, cioe' il punto in cui comincia la fusione.

    base.md, espansione.md, altra-edizione.md   i manuali: `## [pagine] file | titolo`
    casi.json                                   che cosa fa ogni sezione dell'espansione e cosa ci si aspetta
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from master import Campaign  # noqa: E402
from master.campaign import MECHANICS_DIR  # noqa: E402
from master.context import ENTITY_CHARS, RULE_CHARS  # noqa: E402
from master.files import split_sections  # noqa: E402

GOLDEN = Path(__file__).resolve().parent
GLOSSARY_SECTION = "glossario"
GROW_LIMIT = 1.25  # una ripresa senza novita' non deve allungare la sezione oltre questo fattore

_META = re.compile(r"<!--(.*?)-->", re.S)
_HEAD = re.compile(r"^\[(\d+)-(\d+)\]\s+(\S+)\s+\|\s+(.+)$")

Key = tuple[str, str]  # (file, titolo)


# --- lettura ------------------------------------------------------------------------


def load_manual(name: str) -> dict[str, Any]:
    """Un manuale del dataset nella forma che l'estrazione produce: blocchi di pagine con
    `sezioni` e `glossario`, piu' i dati della pubblicazione (`meta`)."""
    text = (GOLDEN / f"{name}.md").read_text(encoding="utf-8")
    meta: dict[str, Any] = {"titolo": text.splitlines()[0].lstrip("# ").strip()}
    found = _META.search(text)
    for pair in (found.group(1) if found else "").split("|"):
        key, _, value = pair.partition(":")
        if value.strip():
            meta[key.strip()] = value.strip()
    meta["anno"], meta["edizione"] = int(meta["anno"]), int(meta["edizione"])

    blocks: dict[tuple[int, int], dict[str, Any]] = {}

    def block(pages: tuple[int, int]) -> dict[str, Any]:
        return blocks.setdefault(pages, {"pagine": pages, "riassunto": f"{meta['titolo']}, pagine {pages[0]}-{pages[1]}.",
                                         "sezioni": [], "glossario": []})

    for title, body in split_sections(text).items():
        head = _HEAD.match(title)
        if head:
            pages = (int(head.group(1)), int(head.group(2)))
            block(pages)["sezioni"].append({"file": head.group(3), "titolo": head.group(4).strip(), "testo": body.strip()})
        elif title == GLOSSARY_SECTION:
            for line in body.splitlines()[2:]:  # salta intestazione e separatore
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) == 5:
                    a, _, b = cells[4].partition("-")
                    block((int(a), int(b)))["glossario"].append(
                        {"term": cells[0], "kind": cells[1], "description": cells[2], "file": cells[3]})
    return {"nome": name, "meta": meta, "blocchi": [blocks[k] for k in sorted(blocks)]}


def load_cases() -> dict[str, Any]:
    return json.loads((GOLDEN / "casi.json").read_text(encoding="utf-8"))


def sections_of(manual: dict[str, Any]) -> dict[Key, str]:
    """(file, titolo) -> testo, per tutte le sezioni di un manuale."""
    return {(s["file"], s["titolo"]): s["testo"] for b in manual["blocchi"] for s in b["sezioni"]}


def key_of(ref: dict[str, str] | None) -> Key | None:
    return (ref["file"], ref["titolo"]) if ref else None


# --- confronto ----------------------------------------------------------------------


def norm(text: str) -> str:
    """Testo confrontabile: minuscole, segni tipografici ridotti a quelli di tastiera, spazi singoli.
    Una sezione riscritta dal modello cambia l'impaginazione, non deve cambiare i fatti."""
    text = text.casefold().replace("−", "-").replace("–", "-").replace("×", "x")
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def cut_for(file: str) -> int:
    """Quanti caratteri di una sezione arrivano al master in un turno (vedi context.py)."""
    return RULE_CHARS if file.startswith(MECHANICS_DIR + "/") else ENTITY_CHARS


def _section(campaign: Campaign, ref: dict[str, str]) -> str | None:
    f = campaign.file(ref["file"])
    if not f.exists():
        return None
    wanted = ref["titolo"].lower()
    return next((body for title, body in f.sections().items() if title.lower() == wanted), None)


def check_case(campaign: Campaign, case: dict[str, Any], base_sections: dict[Key, str]) -> list[str]:
    """Perche' il caso non passa sullo stato attuale della campagna. Lista vuota = passa."""
    target = case["vigente"]
    raw = _section(campaign, target)
    if raw is None:
        return [f"manca la sezione {target['file']} § {target['titolo']}"]
    cut = cut_for(target["file"])
    whole, top = norm(raw), norm(raw[:cut])
    mechanics = target["file"].startswith(MECHANICS_DIR + "/")
    why: list[str] = []
    for pattern in case.get("deve", []):
        if not re.search(pattern, top if case.get("entro_taglio") else whole):
            where = f" nei primi {cut} caratteri" if case.get("entro_taglio") else ""
            why.append(f"non c'e' «{pattern}»{where}")
    for pattern in case.get("non_deve", []):
        if re.search(pattern, whole if mechanics else top):
            why.append(f"c'e' ancora «{pattern}»")
    for first, second in case.get("ordine", []):
        a, b = re.search(first, whole), re.search(second, whole)
        if a and b and a.start() > b.start():
            why.append(f"«{second}» viene prima di «{first}»")
    if case.get("non_cresce"):
        before = len(base_sections[key_of(case["esistente"])])
        if len(raw) > before * GROW_LIMIT:
            why.append(f"la sezione e' cresciuta da {before} a {len(raw)} caratteri senza contenuto nuovo")
    for other in case.get("altrove", []):
        text = _section(campaign, other)
        for pattern in other.get("non_deve", []):
            if text is not None and re.search(pattern, norm(text)):
                why.append(f"«{pattern}» e' finito in {other['file']} § {other['titolo']}")
    return why


def check_glossary(campaign: Campaign, expected: dict[str, str]) -> list[str]:
    entry = campaign.glossary.get(expected["term"])
    if entry is None:
        return [f"manca la voce {expected['term']}"]
    why: list[str] = []
    if entry.file != expected["file"]:
        why.append(f"la voce punta a {entry.file} invece che a {expected['file']}")
    if not re.search(expected["descrizione"], norm(entry.description)):
        why.append(f"la descrizione ha perso «{expected['descrizione']}»: {entry.description!r}")
    return why


def score_state(campaign: Campaign, cases: dict[str, Any], base: dict[str, Any]) -> dict[str, list[str]]:
    """id del caso -> motivi per cui non passa (lista vuota = passa), sezioni e glossario insieme."""
    base_sections = sections_of(base)
    result = {c["id"]: check_case(campaign, c, base_sections) for c in cases["casi"]}
    result.update({g["id"]: check_glossary(campaign, g) for g in cases["glossario"]})
    return result


def score_candidates(found: set[tuple[Key, Key]], cases: dict[str, Any]) -> dict[str, Any]:
    """Quanti scontri veri stanno tra le coppie (sezione nuova, sezione esistente) proposte.
    Un candidato in piu' costa una chiamata; uno in meno e' un conflitto che nessuno vedra' mai."""
    wanted = {c["id"]: (key_of(c["nuova"]), key_of(c["esistente"])) for c in cases["casi"] if c.get("esistente")}
    missing = sorted(i for i, pair in wanted.items() if pair not in found)
    false_alarms = {(key_of(c["nuova"]), key_of(f)) for c in cases["casi"] for f in c.get("candidati_falsi", [])}
    return {"attesi": len(wanted), "trovati": len(wanted) - len(missing), "mancanti": missing,
            "in_piu": len(found - set(wanted.values())), "falsi_allarmi_presi": len(found & false_alarms)}


def score_relations(predicted: dict[str, str], cases: dict[str, Any]) -> dict[str, Any]:
    """Quanti rapporti (nuova, identica, estende, sostituisce, variante, contraddice) sono giusti."""
    wrong = {c["id"]: predicted.get(c["id"], "-") for c in cases["casi"] if predicted.get(c["id"]) not in c["tipi"]}
    return {"casi": len(cases["casi"]), "giusti": len(cases["casi"]) - len(wrong), "sbagliati": wrong}


def snapshot(campaign: Campaign) -> dict[str, Any]:
    """Lo stato della campagna in forma confrontabile: preparare due manuali in un ordine o
    nell'altro deve dare la stessa fotografia."""
    files = {f.name: {t: norm(b) for t, b in f.sections().items()} for f in campaign.files()
             if f.name.startswith(("meccanica/", "ambientazione/"))}
    glossary = sorted((e.term.lower(), e.kind, e.description, e.file) for e in campaign.glossary.entries())
    return {"file": files, "glossario": glossary}


# --- gli stadi della preparazione, senza chiamate -----------------------------------


def apply_staged(campaign: Campaign, manual: dict[str, Any]) -> None:
    """Fonde un manuale del dataset con la preparazione vera, dal punto in cui l'estrazione ha
    gia' prodotto i blocchi: spazio di lavoro del manuale, composizione della campagna, paternita'."""
    from master.prepare import Preparer, PrepReport

    meta = manual["meta"]
    blocks = [(f"blocco_{i:03d}_p{b['pagine'][0]}-{b['pagine'][1]}.json", f"pagine {b['pagine'][0]}-{b['pagine'][1]}", b)
              for i, b in enumerate(manual["blocchi"], start=1)]
    Preparer(campaign).assimilate(meta["chiave"], manual["nome"] + ".md", blocks, PrepReport(source=meta["titolo"]), meta=meta)


def apply_today(campaign: Campaign, manual: dict[str, Any]) -> None:
    """La fusione com'era prima della paternita': `merge` blocco per blocco direttamente nella
    campagna, poi la sanificazione gratuita. Resta come termine di confronto: e' il punto di partenza."""
    from master.prepare import Preparer, PrepReport
    from master.sanitize import Sanitizer

    prep = Preparer(campaign)  # il client non serve: merge non chiama nessuno
    report = PrepReport(source=manual["meta"]["titolo"])
    for block in manual["blocchi"]:
        prep.merge(block, report)
    Sanitizer(campaign).run_free()
