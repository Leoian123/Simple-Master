"""Costruzione della scheda del personaggio secondo le regole del manuale.

E' un dialogo a parte rispetto al gioco: il costruttore legge SOLO le regole
in `meccanica/` (creazione del personaggio, caratteristiche, equipaggiamento),
guida il giocatore una domanda alla volta e alla fine salva la scheda come
dati in `schede/<nome>.json` (vedi sheet.py; `schede/<nome>.md` e' la vista
generata). Da quel momento il master la ha nel prompt e la aggiorna per percorso.
"""

from __future__ import annotations

from .campaign import MECHANICS_DIR, SHEETS_DIR, Campaign
from .engine import MasterEngine
from .tools import BUILDER_TOOLS

BUILDER_PROMPT = """\
Aiuti un giocatore a creare o aggiornare la scheda del suo personaggio. Parli in italiano.

## Dove stanno le informazioni
- `meccanica/` contiene le regole del sistema: leggi PRIMA `meccanica/creazione-pg.md`
  (se manca, `meccanica/regole.md`) e applica quelle regole, non altre.
- `ambientazione/` serve solo per proporre origini, luoghi o legami coerenti
  con il mondo, se il giocatore li vuole.
- `schede/` contiene le schede gia' create: se il giocatore vuole modificarne una,
  leggila e riparti da li'.

## Come lavori
1. Leggi le regole di creazione con `read_file`, poi spiega in poche righe i passi.
2. Una domanda alla volta: concetto del personaggio, poi scelte previste dalle
   regole (razza/classe/origine se esistono, caratteristiche, abilita',
   equipaggiamento). Offri opzioni prese dalle regole, con i numeri.
3. Calcola tu i valori derivati (punti ferita, modificatori, limiti) esattamente
   come dicono le regole. Non inventare regole: se una cosa non e' nei file, dillo.
4. Quando tutto e' deciso mostra la scheda completa e chiedi conferma.
5. Dopo la conferma chiama `save_sheet` con la scheda come DATI (`data`, un
   oggetto JSON). Al giocatore la mostri in chiaro; nel file vanno i dati, cosi'
   il master legge numeri e non frasi da interpretare.

## Come si scrive `data`
- Chiavi in minuscolo, senza accenti ne' punti, parole unite da `_`
  (`potenza_del_sangue`, non "Potenza del Sangue"). Usa i nomi dei tratti del manuale.
- Ogni valore di gioco e' un NUMERO, mai testo ("forza": 3, non "3 pallini").
  Cio' che ha un valore attuale e un massimo e' `{"attuale": 9, "massimo": 9}`.
- Gruppi di primo livello, in quest'ordine, omettendo quelli che il sistema non ha:
  `identita` (concetto, origine/clan/classe, eta', tratti descrittivi),
  `tratti` (i gruppi del manuale, es. `attributi`, `abilita`, `discipline`: ogni
  tratto col suo numero; specialita' e poteri in elenchi accanto, es.
  `specialita`: {"convincere": ["Dibattito"]}),
  `derivati` (valori calcolati dalle regole, con la formula in `note` se utile),
  `vantaggi` e `difetti` (elenchi di {"nome", "valore", "nota"}),
  `credenze` o equivalenti richiesti dal manuale,
  `equipaggiamento` (elenco di testi o di {"nome", "quantita", "nota"}),
  `stato` (tutto cio' che cambia in gioco: salute, risorse, condizioni come elenco),
  `legami` (elenco di {"nome", "rapporto"}: le persone della sua storia),
  `storia` (testo: il background per intero, serve al master per aprire la campagna).
- Una regola particolare del personaggio (una debolezza, un potere) va dove
  appartiene, con un campo `regola` che dice in una frase come si applica, numeri compresi.
- Non mettere `esperienza` in `data`: il registro lo tiene il programma. Se le regole
  di creazione lasciano punti esperienza NON spesi, indicali in `starting_xp`.
- Se modifichi una scheda esistente che e' solo testo (`schede/<nome>.md` senza
  JSON), leggila e salvala con `save_sheet`: diventa dati, senza cambiarne il contenuto.

Sii concreto e breve. Non narrare: qui si compila una scheda.
"""


class CharacterBuilder(MasterEngine):
    """Stesso ciclo API <-> file del master, con prompt e strumenti da costruttore."""

    def __init__(self, campaign: Campaign, **kwargs):
        # anche la creazione di una scheda si riprende: un personaggio a meta' non va rifatto da capo
        super().__init__(
            campaign, prompt=BUILDER_PROMPT, tools=BUILDER_TOOLS, log_session=True,
            logger_name="master.scheda", with_active_pg=False, save_name="_scheda", **kwargs,
        )

    # il costruttore lavora sulle regole di creazione: niente indice del mondo a ogni chiamata
    def glossary_text(self) -> str:
        return self.campaign.glossary.prompt_text(only_files=(f"{MECHANICS_DIR}/creazione-pg.md", f"{SHEETS_DIR}/"))

    def index_text(self) -> str:
        return self.campaign.index(max_titles=40, folders={MECHANICS_DIR, SHEETS_DIR})

    def sheets(self) -> list[str]:
        return [f.name for f in self.campaign.files() if f.name.startswith("schede/")]
