"""Differential testing dei file di campagna: il codice e il validatore devono dire la stessa cosa.

Da una campagna sintetica pulita si muta un campo alla volta (tipo sbagliato, valore fuori
misura, chiave in piu', chiave mancante, file illeggibile) e per ogni mutazione si confrontano
due verdetti:

    codice      `accetta` se il programma usa il file com'e' scritto; `rifiuta` se si ferma
                (eccezione) o butta via il dato (fallback silenzioso: profilo ignorato,
                registro vuoto, orologi persi, sessione riportata a 1).
    validatore  il livello del rilievo di `master/validate.py` su quel file: errore, avviso,
                consiglio, oppure nulla.

La tabella TABELLA dichiara i due verdetti attesi; il test li verifica contro la realta' e
pretende l'invariante che conta: **dove il codice rifiuta, il validatore deve dire errore**.
Il contrario e' ammesso e dichiarato riga per riga (`oltre`): il validatore e' piu' severo del
codice dove il codice tollera in silenzio un dato sbagliato (una soglia oltre le facce del
dado, un totale scritto a mano, un percorso della scheda che i dadi speciali non trovano).
Ogni campo dei file con una forma (dadi.json, stato.json, la parte fissa della scheda) deve
avere almeno una mutazione: un campo nuovo senza mutazione e' un rosso.

E' il metodo con cui la community di Voyage tiene allineati validatore e motore
(mutazione campo per campo, zero disaccordi ammessi), portato sui nostri file.
"""

from __future__ import annotations

import copy
import itertools
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from master import Campaign, dice, modi, relazioni  # noqa: E402
from master.memory import write_scene  # noqa: E402
from master.sheet import Sheet  # noqa: E402
from master.validate import validate  # noqa: E402

DELETE = object()      # la chiave sparisce
RAW = object()         # il valore e' il testo intero del file (file illeggibile, JSON non oggetto)
DADI, STATO, SCHEDA, RELAZ, MODI = "meccanica/dadi.json", "stato.json", "schede/kael.json", "meccanica/relazioni.json", "meccanica/modi.json"

# --- la campagna sintetica: pulita per costruzione (lo prova il primo test) ----------------------

PROFILO: dict[str, Any] = {
    "sistema": "Prova",
    "note": "Il margine misura l'efficacia.",
    "tiri": [
        {"nome": "riserva", "uso": "tiri a riserva", "tipo": "successi", "dado": 10, "dadi_fissi": None, "successo_da": 6,
         "vantaggio": False, "nome_zero_successi": "fallimento totale",
         "coppie": {"faccia": 10, "successi_extra": 2, "nome": "critico"},
         "speciali": {"nome": "fame", "quanti_da": "stato.fame", "sostituiscono": True},
         "eventi": [{"nome": "bestiale", "faccia": 1, "quando": "fallito", "solo_speciali": True, "effetto": "compulsione"}],
         "esiti": [{"nome": "fallito", "da_margine": None, "a_margine": -1, "riuscito": False, "guida": "non riesce"},
                   {"nome": "vittoria costosa", "da_margine": 0, "a_margine": 0, "riuscito": True, "guida": "riesce a un prezzo"},
                   {"nome": "riuscito", "da_margine": 1, "a_margine": None, "riuscito": True, "guida": "riesce"}]},
        {"nome": "prova", "uso": "d20 piu' bonus", "tipo": "somma", "dado": 20, "dadi_fissi": 1, "successo_da": None,
         "vantaggio": True, "nome_zero_successi": "", "coppie": None, "speciali": None, "eventi": [], "esiti": []},
    ],
    "fonte": "test",
}
STATO_BASE: dict[str, Any] = {
    "pg_attivo": "kael", "sessione": 2,
    "orologi": {"Guardia": {"segmenti": 6, "pieni": 2, "conseguenza": "arrivano", "scadenza_turni": 12, "creato_turno": 0}},
    "memoria": {"kael": 2},
    "png": {"Mira Sol": {"relazione": -23}},
    "modo": "cupo",
}
MODI_BASE: dict[str, Any] = {"modi": [{"nome": "cupo", "descrizione": "x", "istruzioni": "tono cupo", "png": "diffidenti"},
                                      {"nome": "lieve", "descrizione": "y", "istruzioni": "tono lieve"}]}
STADI_BASE: dict[str, Any] = {"stadi": [{"nome": "Ostile", "da": -100, "a": -21}, {"nome": "Neutrale", "da": -20, "a": 20},
                                        {"nome": "Amico", "da": 21, "a": 100}]}
REGISTRO = [
    {"quando": "2026-09-18T16:58", "sessione": 1, "via": "sessione", "punti": 2, "motivo": "prima sessione"},
    {"quando": "2026-09-18T17:10", "sessione": 1, "via": "spesa", "punti": -1, "motivo": "Forza 2->3"},
]
FACCE = [10, 10, 1, 6, 3, 10, 1, 7]  # dadi fissi: coppie, eventi e dadi speciali scattano sempre


def _set(data: Any, path: str, value: Any) -> Any:
    """Applica una mutazione a un documento JSON per percorso con i punti; `+` appende a un elenco."""
    parts = path.split(".")
    node = data
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    if isinstance(node, list):
        if last == "+":
            node.append(value)
        elif value is DELETE:
            del node[int(last)]
        else:
            node[int(last)] = value
    elif value is DELETE:
        del node[last]
    else:
        node[last] = value
    return data


def _paths(data: Any, prefix: str = "") -> set[str]:
    """Tutti i percorsi di un documento, con `*` al posto degli indici: i campi da coprire."""
    out: set[str] = set()
    if isinstance(data, dict):
        for k, v in data.items():
            here = f"{prefix}.{k}" if prefix else k
            out.add(here)
            out |= _paths(v, here)
    elif isinstance(data, list):
        for v in data:
            out |= _paths(v, f"{prefix}.*")
    return out


def _norm(path: str) -> str:
    return ".".join("*" if p.isdigit() or p == "+" else p for p in path.split("."))


# --- come il codice usa ogni file (sola lettura; eccezione o fallback = rifiuta) -------------------

def codice_dadi(camp: Campaign) -> bool:
    profile = dice.load_profile(camp)
    if profile is None:
        return False  # il programma gioca come se il profilo non esistesse
    sheet = Sheet(camp, "kael")
    dice.prompt_section(profile)
    with patch("master.dice._die", side_effect=itertools.cycle(FACCE)):
        for kind in profile["tiri"]:
            spec = f"azione | {kind['nome']}" + (" | dadi 3" if kind.get("dadi_fissi") is None else "")
            if kind.get("tipo") == "somma":
                spec += " | bonus +1"
            dice.roll(spec + " | difficolta 2", profile, sheet)
            if kind.get("vantaggio"):
                dice.roll(spec + " | vantaggio", profile, sheet)
    return True


def codice_stato(camp: Campaign) -> bool:
    """Specchio di Campaign._state/session_number/clocks/pop_full_clocks/consolidated, senza i fallback silenziosi."""
    data = json.loads((camp.root / STATO).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return False
    camp.active_pg  # il personaggio attivo ha un fallback DISEGNATO (l'unica scheda): non e' un rifiuto
    if "sessione" in data and int(data["sessione"]) < 1:
        return False
    if "orologi" in data:
        if not isinstance(data["orologi"], dict):
            return False
        [k for k, c in data["orologi"].items() if c["pieni"] >= c["segmenti"]]  # come pop_full_clocks
        for c in data["orologi"].values():
            if "scadenza_turni" in c:
                if int(c["scadenza_turni"]) < 1 or int(c["creato_turno"]) < 0:  # KeyError = fallback a 0: un rifiuto
                    return False
        camp.clocks_line()
    if "memoria" in data:
        if not isinstance(data["memoria"], dict):
            return False
        for name, count in data["memoria"].items():
            if int(count) < 0:
                return False
            camp.consolidated(name)
    if "modo" in data:
        if not isinstance(data["modo"], str) or data["modo"].strip().lower() not in modi.nomi(camp):
            return False  # il narratore giocherebbe in `classico` in silenzio
    if "png" in data:
        if not isinstance(data["png"], dict):
            return False
        for name, entry in data["png"].items():
            value = int(entry["relazione"])  # KeyError/ValueError = il codice legge 0 o None: un fallback
            if not relazioni.MIN <= value <= relazioni.MAX:
                return False  # verrebbe clampato in silenzio
            if camp.relation(name) != value:
                return False
    return True


def codice_relazioni(camp: Campaign) -> bool:
    """Specchio di relazioni.carica: un file che non passa stadi_problems viene ignorato in silenzio."""
    data = json.loads((camp.root / RELAZ).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or relazioni.stadi_problems(data.get("stadi")):
        return False
    relazioni.stadio(0, data["stadi"])
    return True


def codice_scheda(camp: Campaign) -> bool:
    """Specchio di Sheet: lettura, registro, le tre viste del prompt, la vista Markdown."""
    raw = json.loads((camp.root / SCHEDA).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        return False
    sheet = Sheet(camp, "kael")
    if "esperienza" in raw:
        xp = raw["esperienza"]
        if not isinstance(xp, dict) or not isinstance(xp.get("registro"), list):
            return False  # il registro sparisce in silenzio
    sheet.xp()
    sheet.stable_json()
    sheet.volatile_json()
    sheet.working_json()
    sheet.markdown()
    return True


def codice_modi(camp: Campaign) -> bool:
    """Specchio di modi.carica: un file che non passa modi_problems viene ignorato in silenzio."""
    data = json.loads((camp.root / MODI).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or modi.modi_problems(data.get("modi")):
        return False
    modi.testo(data["modi"][0])
    return True


CODICE = {DADI: codice_dadi, STATO: codice_stato, SCHEDA: codice_scheda, RELAZ: codice_relazioni, MODI: codice_modi}


def accetta(fn, camp: Campaign) -> bool:
    try:
        return bool(fn(camp))
    except Exception:
        return False


# --- la tabella: (file, percorso, valore, validatore atteso, codice atteso, nota) ------------------
#
# validatore: "errore" | "avviso" | "consiglio" | None.  codice: "accetta" | "rifiuta".
# Una riga con errore + accetta e' un caso `oltre`: il validatore vede cio' che il codice tollera.

TABELLA: list[tuple[str, str, Any, str | None, str, str]] = [
    # --- meccanica/dadi.json ---
    (DADI, "", "{non json", "errore", "rifiuta", "file illeggibile: il profilo viene ignorato"),
    (DADI, "", "[1, 2]", "errore", "rifiuta", "non e' un oggetto"),
    (DADI, "sistema", 5, None, "accetta", "testo libero: va nel prompt com'e'"),
    (DADI, "note", DELETE, None, "accetta", "facoltativo"),
    (DADI, "fonte", DELETE, None, "accetta", "scritto dal programma, non letto"),
    (DADI, "extra", 1, None, "accetta", "chiave in piu' ignorata"),
    (DADI, "tiri", DELETE, "errore", "rifiuta", "senza tipi di tiro il profilo e' nullo"),
    (DADI, "tiri", [], "errore", "rifiuta", "idem"),
    (DADI, "tiri.0", "x", "errore", "rifiuta", "un tipo di tiro non oggetto"),
    (DADI, "tiri.0.nome", DELETE, "errore", "rifiuta", "il marcatore non trova il tipo"),
    (DADI, "tiri.1.nome", "riserva", "errore", "rifiuta", "il marcatore prende il primo: il secondo tipo tira con i dadi sbagliati o si ferma"),
    (DADI, "tiri.0.uso", 7, None, "accetta", "testo per il prompt"),
    (DADI, "tiri.0.tipo", "boh", "errore", "accetta", "oltre: il codice tira come somma"),
    (DADI, "tiri.0.dado", "dieci", "errore", "rifiuta", "int() fallisce"),
    (DADI, "tiri.0.dado", 1, "errore", "rifiuta", "_check rifiuta un dado a 1 faccia"),
    (DADI, "tiri.0.dadi_fissi", "tre", "errore", "rifiuta", "int() fallisce quando il marcatore non da' i dadi"),
    (DADI, "tiri.1.dadi_fissi", 0, "errore", "accetta", "oltre: 0 e' falso e il codice tira 1 dado"),
    (DADI, "tiri.0.successo_da", 11, "errore", "accetta", "oltre: nessun dado riesce mai"),
    (DADI, "tiri.0.successo_da", DELETE, "errore", "accetta", "oltre: successo solo sulla faccia massima"),
    (DADI, "tiri.0.vantaggio", "si", None, "accetta", "letto solo come vero/falso"),
    (DADI, "tiri.0.nome_zero_successi", 3, None, "accetta", "diventa testo"),
    (DADI, "tiri.0.coppie", "x", "errore", "rifiuta", ".get su una stringa"),
    (DADI, "tiri.0.coppie.faccia", 11, "errore", "accetta", "oltre: le coppie non escono mai"),
    (DADI, "tiri.0.coppie.successi_extra", "due", "errore", "rifiuta", "int() fallisce alla prima coppia"),
    (DADI, "tiri.0.coppie.nome", DELETE, None, "accetta", "senza nome la coppia conta e basta"),
    (DADI, "tiri.0.speciali", "x", "errore", "rifiuta", ".get su una stringa"),
    (DADI, "tiri.0.speciali.nome", DELETE, None, "accetta", "etichetta di default"),
    (DADI, "tiri.0.speciali.quanti_da", "stato.sete", "errore", "accetta", "oltre: i dadi speciali spariscono in silenzio"),
    (DADI, "tiri.0.speciali.quanti_da", "", "errore", "accetta", "oltre: idem"),
    (DADI, "tiri.0.speciali.sostituiscono", "no", None, "accetta", "letto solo come vero/falso"),
    (DADI, "tiri.0.eventi", "x", "errore", "rifiuta", "si itera una stringa"),
    (DADI, "tiri.0.eventi.0", "x", "errore", "rifiuta", "un evento non oggetto"),
    (DADI, "tiri.0.eventi.0.faccia", "uno", "errore", "accetta", "oltre: l'evento non scatta mai"),
    (DADI, "tiri.0.eventi.0.quando", "mai", "errore", "accetta", "oltre: l'evento non scatta mai"),
    (DADI, "tiri.0.eventi.0.nome", DELETE, None, "accetta", "nome di default"),
    (DADI, "tiri.0.eventi.0.solo_speciali", DELETE, None, "accetta", "falso per default"),
    (DADI, "tiri.0.eventi.0.effetto", DELETE, None, "accetta", "facoltativo"),
    (DADI, "tiri.0.esiti", DELETE, None, "accetta", "valgono le fasce predefinite"),
    (DADI, "tiri.0.esiti", [], None, "accetta", "idem"),
    (DADI, "tiri.0.esiti", "x", "errore", "rifiuta", "si itera una stringa"),
    (DADI, "tiri.0.esiti.0", "x", "errore", "rifiuta", "una fascia non oggetto"),
    (DADI, "tiri.0.esiti.0.nome", DELETE, "errore", "accetta", "oltre: il codice mostra 'fallito'/'riuscito' al posto del nome"),
    (DADI, "tiri.0.esiti.0.a_margine", 3, "errore", "accetta", "oltre: fasce sovrapposte, il codice prende la prima"),
    (DADI, "tiri.0.esiti.0.a_margine", "x", "errore", "rifiuta", "confronto fra testo e numero"),
    (DADI, "tiri.0.esiti.1.da_margine", 1, "errore", "accetta", "oltre: un buco fra le fasce, il margine 0 cade sulla soglia nuda"),
    (DADI, "tiri.0.esiti.2.a_margine", 5, "errore", "accetta", "oltre: la scala non e' aperta in alto"),
    (DADI, "tiri.0.esiti.1.riuscito", "si", "errore", "accetta", "oltre: letto come vero"),
    (DADI, "tiri.0.esiti.0.riuscito", True, None, "accetta", "una scala tutta riuscita resta monotona: lecita, i nomi li decide il manuale"),
    (DADI, "tiri.0.esiti.2.riuscito", False, "errore", "accetta", "oltre: una fascia fallita sopra una riuscita; il codice la applica com'e'"),
    (DADI, "tiri.0.esiti.2.guida", DELETE, None, "accetta", "la guida e' facoltativa"),
    # --- stato.json ---
    (STATO, "", "{", "errore", "rifiuta", "illeggibile: il programma riparte senza stato"),
    (STATO, "", "[1]", "errore", "rifiuta", "non e' un oggetto"),
    (STATO, "extra", 1, None, "accetta", "chiave in piu' ignorata"),
    (STATO, "pg_attivo", "nessuno", "avviso", "accetta", "fallback disegnato: l'unica scheda"),
    (STATO, "pg_attivo", 5, "avviso", "accetta", "idem"),
    (STATO, "pg_attivo", DELETE, None, "accetta", "idem"),
    (STATO, "sessione", 0, "errore", "rifiuta", "riportata a 1 in silenzio"),
    (STATO, "sessione", "due", "errore", "rifiuta", "int() fallisce, riportata a 1"),
    (STATO, "sessione", DELETE, None, "accetta", "default 1"),
    (STATO, "orologi", DELETE, None, "accetta", "nessun orologio"),
    (STATO, "orologi", "x", "errore", "rifiuta", "gli orologi spariscono"),
    (STATO, "orologi.Guardia", "x", "errore", "rifiuta", "indicizzazione di una stringa"),
    (STATO, "orologi.Guardia.segmenti", 20, "errore", "accetta", "oltre: il codice non controlla la misura"),
    (STATO, "orologi.Guardia.segmenti", "sei", "errore", "rifiuta", "confronto fra testo e numero"),
    (STATO, "orologi.Guardia.pieni", 9, "errore", "accetta", "oltre: vale come pieno"),
    (STATO, "orologi.Guardia.pieni", DELETE, "errore", "rifiuta", "KeyError nel conteggio dei pieni"),
    (STATO, "orologi.Guardia.conseguenza", "", "consiglio", "accetta", "'si compie'"),
    (STATO, "orologi.Guardia.scadenza_turni", DELETE, None, "accetta", "orologio senza scadenza"),
    (STATO, "orologi.Guardia.scadenza_turni", "dieci", "errore", "rifiuta", "int() fallisce nel conteggio"),
    (STATO, "orologi.Guardia.scadenza_turni", 0, "errore", "rifiuta", "scadrebbe subito: riportato a mai"),
    (STATO, "orologi.Guardia.creato_turno", DELETE, "errore", "rifiuta", "senza turno di partenza il conteggio parte da 0 in silenzio"),
    (STATO, "orologi.Guardia.creato_turno", "x", "errore", "rifiuta", "int() fallisce"),
    (STATO, "orologi.Guardia.creato_turno", -3, "errore", "rifiuta", "un turno negativo"),
    (STATO, "orologi.Guardia.conseguenza", DELETE, "consiglio", "accetta", "idem"),
    (STATO, "memoria", DELETE, None, "accetta", "nulla consolidato"),
    (STATO, "memoria", "x", "errore", "rifiuta", "la memoria sparisce"),
    (STATO, "memoria.kael", 99, "errore", "accetta", "oltre: il prossimo consolidamento salterebbe turni"),
    (STATO, "memoria.kael", "quattro", "errore", "rifiuta", "int() fallisce, torna 0"),
    (STATO, "memoria.kael", -1, "errore", "rifiuta", "riportato a 0"),
    (STATO, "modo", DELETE, None, "accetta", "vale `classico`"),
    (STATO, "modo", "inesistente", "errore", "rifiuta", "il narratore giocherebbe in `classico` senza dirlo"),
    (STATO, "modo", 5, "errore", "rifiuta", "non e' un nome"),
    (STATO, "png", DELETE, None, "accetta", "nessuna relazione contata"),
    (STATO, "png", "x", "errore", "rifiuta", "le relazioni spariscono"),
    (STATO, "png.Mira Sol", "x", "errore", "rifiuta", "voce non oggetto"),
    (STATO, "png.Mira Sol.relazione", "molto", "errore", "rifiuta", "int() fallisce, l'etichetta sparisce"),
    (STATO, "png.Mira Sol.relazione", 150, "errore", "rifiuta", "clampato in silenzio"),
    (STATO, "png.Mira Sol.relazione", DELETE, "errore", "rifiuta", "letto come 0 in silenzio"),
    (STATO, "png.Mira Sol.nota", "x", None, "accetta", "chiave in piu' conservata"),
    # --- meccanica/relazioni.json (la scala del manuale: o e' valida o si ignora, mai a meta') ---
    (RELAZ, "", "{", "errore", "rifiuta", "illeggibile: valgono gli stadi predefiniti"),
    (RELAZ, "", "[1]", "errore", "rifiuta", "non e' un oggetto"),
    (RELAZ, "extra", 1, None, "accetta", "chiave in piu' ignorata"),
    (RELAZ, "stadi", DELETE, "errore", "rifiuta", "senza stadi il file non vale"),
    (RELAZ, "stadi", [], "errore", "rifiuta", "idem"),
    (RELAZ, "stadi.0", "x", "errore", "rifiuta", "uno stadio non oggetto"),
    (RELAZ, "stadi.0.nome", DELETE, "errore", "rifiuta", "stadio senza nome"),
    (RELAZ, "stadi.0.nome", "Neutrale", "errore", "rifiuta", "nome doppio"),
    (RELAZ, "stadi.0.da", -90, "errore", "rifiuta", "la scala non comincia a -100"),
    (RELAZ, "stadi.0.a", -25, "errore", "rifiuta", "un buco fra gli stadi"),
    (RELAZ, "stadi.1.da", -30, "errore", "rifiuta", "stadi sovrapposti"),
    (RELAZ, "stadi.1.da", "x", "errore", "rifiuta", "non e' un intero"),
    (RELAZ, "stadi.2.a", 90, "errore", "rifiuta", "la scala non finisce a 100"),
    # --- meccanica/modi.json (i modi della campagna: o validi tutti o si ignorano) ---
    (MODI, "", "{", "errore", "rifiuta", "illeggibile: valgono i modi predefiniti (e `cupo` sparisce)"),
    (MODI, "", "[1]", "errore", "rifiuta", "non e' un oggetto"),
    (MODI, "extra", 1, None, "accetta", "chiave in piu' ignorata"),
    (MODI, "modi", DELETE, "errore", "rifiuta", "senza elenco il file non vale"),
    (MODI, "modi", [], "errore", "rifiuta", "idem"),
    (MODI, "modi.0", "x", "errore", "rifiuta", "un modo non oggetto"),
    (MODI, "modi.0.nome", DELETE, "errore", "rifiuta", "modo senza nome"),
    (MODI, "modi.1.nome", "cupo", "errore", "rifiuta", "nome doppio"),
    (MODI, "modi.0.descrizione", "", "errore", "rifiuta", "descrizione vuota"),
    (MODI, "modi.0.istruzioni", DELETE, "errore", "rifiuta", "senza istruzioni la lente e' vuota"),
    (MODI, "modi.0.png", 3, "errore", "rifiuta", "png non testo"),
    (MODI, "modi.0.png", DELETE, None, "accetta", "le inclinazioni dei PNG sono facoltative"),
    # --- schede/kael.json (la parte con una forma: nome, esperienza, e stato.fame letto dai dadi) ---
    (SCHEDA, "", "{", "errore", "rifiuta", "illeggibile: il gioco si ferma"),
    (SCHEDA, "", "[]", "errore", "rifiuta", "non e' un oggetto"),
    (SCHEDA, "nome", DELETE, "avviso", "accetta", "dedotto dallo slug"),
    (SCHEDA, "nome", "", "avviso", "accetta", "idem"),
    (SCHEDA, "esperienza", "x", "errore", "rifiuta", "il registro sparisce"),
    (SCHEDA, "esperienza.registro", DELETE, "errore", "rifiuta", "idem"),
    (SCHEDA, "esperienza.registro", "x", "errore", "rifiuta", "idem"),
    (SCHEDA, "esperienza.disponibili", 7, "errore", "accetta", "oltre: il totale a mano viene ignorato, ma mente a chi legge il file"),
    (SCHEDA, "esperienza.registro.0", "x", "errore", "rifiuta", ".get su una stringa"),
    (SCHEDA, "esperienza.registro.0.punti", "due", "errore", "rifiuta", "int() fallisce"),
    (SCHEDA, "esperienza.registro.0.punti", DELETE, "errore", "accetta", "oltre: contato come 0"),
    (SCHEDA, "esperienza.registro.0.via", "regalo", "avviso", "accetta", "via sconosciuta"),
    (SCHEDA, "esperienza.registro.0.via", "spesa", "errore", "accetta", "oltre: punti positivi con via spesa"),
    (SCHEDA, "esperienza.registro.1.via", "narratore", "errore", "accetta", "oltre: punti negativi senza via spesa"),
    (SCHEDA, "esperienza.registro.0.motivo", "", "avviso", "accetta", "senza motivo"),
    (SCHEDA, "esperienza.registro.0.sessione", "uno", None, "accetta", "letto solo per confronto"),
    (SCHEDA, "esperienza.registro.0.quando", DELETE, None, "accetta", "solo per la vista"),
    (SCHEDA, "esperienza.registro.+", {"quando": "x", "sessione": 1, "via": "sessione", "punti": 1, "motivo": "c"},
     "errore", "accetta", "oltre: punti di sessione due volte (il codice lo impedisce solo all'assegnazione)"),
    (SCHEDA, "stato.fame", "due", "errore", "accetta", "oltre: i dadi speciali spariscono in silenzio"),
    (SCHEDA, "stato.fame", DELETE, "errore", "accetta", "oltre: idem"),
    (SCHEDA, "stato", "x", "errore", "accetta", "oltre: idem"),
]
LIVELLI = {"errore": "error", "avviso": "warning", "consiglio": "recommendation"}
COPERTURA_SCHEDA = ("nome", "esperienza", "stato.fame")  # il resto della scheda e' libero per disegno (vedi --doc scheda)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "campaigns" / "esempio", self.tmp / "camp", ignore=shutil.ignore_patterns("sessioni", "preparazione", "stato.json"))
        self.camp = Campaign(self.tmp / "camp")
        self.write(DADI, PROFILO)
        self.write(STATO, STATO_BASE)
        self.write(RELAZ, STADI_BASE)
        self.write(MODI, MODI_BASE)
        sheet = json.loads((self.camp.root / SCHEDA).read_text(encoding="utf-8"))
        sheet["stato"]["fame"] = 2
        sheet["esperienza"] = {"registro": copy.deepcopy(REGISTRO)}
        self.write(SCHEDA, sheet)
        for i in range(2):
            self.camp.append_turn("kael", f"azione {i}", f"narrazione {i}")
        write_scene(self.camp, {"luogo": "Velmora", "quando": "notte", "presenti": ["Mira Sol"], "appena_successo": "x",
                                "domanda_aperta": "y", "da_richiamare": []})
        self.camp.file("ambientazione/avventura.md").set_section("Fili aperti di Kael", "- un filo")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name: str, data: Any) -> None:
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
        (self.camp.root / name).write_text(text, encoding="utf-8")

    def read(self, name: str) -> Any:
        return json.loads((self.camp.root / name).read_text(encoding="utf-8"))

    def applica(self, name: str, path: str, value: Any) -> None:
        if not path:
            self.write(name, value)
        else:
            self.write(name, _set(self.read(name), path, value))

    def verdetti(self, name: str) -> tuple[set[str], bool]:
        """(livelli del validatore sul file, il codice accetta?)"""
        report = validate(self.camp)
        own = {f.level for f in report.findings if f.path.split(":")[0].split(" § ")[0] == name
               or (name == SCHEDA and f.path.startswith(DADI) and "quanti_da" in f.path)}
        return own, accetta(CODICE[name], self.camp)


class TestMutazioni(Base):
    def test_la_base_e_pulita_e_il_codice_la_accetta(self) -> None:
        self.assertEqual(validate(self.camp).findings, [])
        for name, fn in CODICE.items():
            self.assertTrue(accetta(fn, self.camp), name)

    def test_ogni_mutazione_fa_cio_che_la_tabella_dichiara(self) -> None:
        for name, path, value, atteso_val, atteso_cod, nota in TABELLA:
            with self.subTest(file=name, campo=path or "(file)", valore=value if value is not DELETE else "DELETE"):
                self.setUp()
                try:
                    self.applica(name, path, value)
                    livelli, ok = self.verdetti(name)
                    if atteso_val is None:
                        self.assertEqual(livelli, set(), f"{nota}: il validatore ha rilevato {livelli}")
                    else:
                        self.assertIn(LIVELLI[atteso_val], livelli, f"{nota}: il validatore non ha dato {atteso_val} ({livelli})")
                    self.assertEqual("accetta" if ok else "rifiuta", atteso_cod, f"{nota}: il codice {'accetta' if ok else 'rifiuta'}")
                finally:
                    self.tearDown()

    def test_dove_il_codice_rifiuta_il_validatore_dice_errore(self) -> None:
        """L'invariante, misurata sulla realta' e non sulla tabella: nessun file che ferma il gioco passa la verifica."""
        for name, path, value, _, _, nota in TABELLA:
            with self.subTest(file=name, campo=path or "(file)"):
                self.setUp()
                try:
                    self.applica(name, path, value)
                    livelli, ok = self.verdetti(name)
                    if not ok:
                        self.assertIn("error", livelli, f"{nota}: il codice rifiuta ma il validatore non da' errore")
                finally:
                    self.tearDown()

    def test_ogni_campo_con_una_forma_ha_una_mutazione(self) -> None:
        mutati: dict[str, set[str]] = {}
        for name, path, _, _, _, _ in TABELLA:
            if path:
                mutati.setdefault(name, set()).add(_norm(path))
        campi = {DADI: _paths(PROFILO), STATO: _paths(STATO_BASE), RELAZ: _paths(STADI_BASE), MODI: _paths(MODI_BASE)}
        sheet = self.read(SCHEDA)
        campi[SCHEDA] = {p for p in _paths(sheet) if p.startswith(COPERTURA_SCHEDA)}
        for name, paths in campi.items():
            scoperti = sorted(p for p in paths if not any(m == p or m.startswith(p + ".") for m in mutati[name]))
            self.assertEqual(scoperti, [], f"{name}: campi senza mutazione (aggiungi righe alla TABELLA)")

    def test_la_tabella_dichiara_i_casi_oltre_il_codice(self) -> None:
        """Il validatore puo' essere piu' severo del codice, mai il contrario; e ogni riga `oltre` lo dice nella nota."""
        for name, path, _, atteso_val, atteso_cod, nota in TABELLA:
            with self.subTest(file=name, campo=path or "(file)"):
                if atteso_cod == "rifiuta":
                    self.assertEqual(atteso_val, "errore", nota)
                if atteso_val == "errore" and atteso_cod == "accetta":
                    self.assertTrue(nota.startswith("oltre"), f"{nota}: dichiara il caso con 'oltre:'")


if __name__ == "__main__":
    unittest.main()
