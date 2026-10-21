# La scheda del personaggio

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

La scheda di un personaggio giocante e' `schede/<slug>.json` (lo slug e' il nome in minuscolo con `-`).
E' l'unica fonte: ogni valore di gioco e' un numero. `schede/<slug>.md` e' la vista leggibile rigenerata
a ogni salvataggio: non si scrive a mano e gli strumenti di testo rifiutano di toccarla. Una scheda
solo testo (`.md` senza `.json`) funziona ancora e diventa dati la prima volta che il costruttore la salva.

## Come e' fatta (dal prompt del costruttore di schede, parola per parola)

- Chiavi in minuscolo, senza accenti ne' punti, parole unite da `_`
  (`forza_di_volonta`, non "Forza di Volonta'"). Usa i nomi dei tratti del manuale.
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

La chiave `nome` sta in cima, messa dal programma al salvataggio (`Sheet.replace`).

## I percorsi (`update_sheet`, `Sheet.get` / `Sheet.set`)

- Con i punti: `stato.stress`, `stato.salute.attuale`, `tratti.abilita.rissa`, `equipaggiamento.2`.
- In un elenco: un indice (`condizioni.0`), oppure `+` per aggiungere in coda (`stato.condizioni.+`).
- `value` `null` cancella la voce. I livelli intermedi mancanti vengono creati; un valore non si puo' usare come gruppo.
- Un percorso che comincia con `esperienza` e' rifiutato: i punti passano solo dal registro.
- Le modifiche di una chiamata sono tutte o nessuna; il risultato elenca `percorso: prima -> dopo`.

## L'esperienza: un registro, non un numero

```json
"esperienza": {"registro": [{"quando": "2026-09-18T16:58", "sessione": 1, "via": "sessione", "punti": 1, "motivo": "..."}]}
```

Guadagnati, spesi e disponibili si calcolano dal registro a ogni lettura; nessun totale e' salvato
(la verifica segnala come errore qualunque altra chiave sotto `esperienza`). Le spese hanno punti
negativi e via `spesa`. Le vie di ingresso:

| via | quando |
|---|---|
| `creazione` | punti concessi dalle regole di creazione e non ancora spesi |
| `sessione` | chiusura di una sessione di gioco, secondo le regole del manuale (una volta per sessione) |
| `storia` | conclusione di una storia o di un arco narrativo |
| `regola` | una regola del manuale che concede punti in gioco (citala nel motivo) |
| `narratore` | premio eccezionale del narratore, fuori dalle regole (raro) |

La via `sessione` si concede una volta per numero di sessione. `spend_xp` rifiuta se i
disponibili non bastano e applica insieme spesa e crescita del tratto.

## Le due velocita' nel prompt

- Nel prompt di sistema, in cache, la parte stabile: tutti i gruppi tranne `stato`, `esperienza` (`Sheet.stable_json`).
- Nel contesto di ogni turno la parte volatile: `stato`, `esperienza`, con l'esperienza gia' calcolata
  (`guadagnati`, `spesi`, `disponibili`, `ultimi_movimenti`: gli ultimi 5) (`Sheet.volatile_json`).
- Allo scriba: `stato`, `equipaggiamento`, `esperienza` per intero e i soli nomi degli altri gruppi (`Sheet.working_json`).

## Il personaggio attivo

`stato.json` -> `pg_attivo` (slug). Se manca o non esiste piu': l'unica scheda presente, altrimenti nessuno
(e la sezione Gioca e' disattivata). Il giocato di ogni personaggio sta in `sessioni/<slug>.jsonl`.
