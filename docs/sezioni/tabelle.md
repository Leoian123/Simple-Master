# Le tabelle casuali

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

Le tabelle casuali del manuale (incontri, tesori, effetti: "1d100: 01-05 ...") stanno in `meccanica/tabelle.json`
come dati, e le tira il programma. Lasciate in prosa il narratore sceglierebbe lui la riga.

```json
{"tabelle": [{"nome": "incontri nella palude", "uso": "ogni ora di marcia", "dado": "1d100",
              "righe": [{"da": 1, "a": 30, "risultato": "Nulla"}, {"da": 31, "a": 100, "risultato": "..."}],
              "fonte": "meccanica/creature.md: Incontri casuali"}]}
```

## Una tabella

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `nome` | string | si | Il nome della tabella, minuscolo, come la chiama il manuale |
| `uso` | string | si | Quando si tira, in una frase |
| `dado` | string | si | La notazione del dado che la tabella usa: 1d100, 2d6, 1d20... |
| `righe` | elenco di object | si | Tutte le righe, con gli intervalli del manuale: devono coprire tutto il dado senza buchi Ogni elemento: `da`, `a`, `risultato` (Il testo della riga, breve, com'e' nel manuale), `tabella` (Se la riga manda a un'altra tabella ('tira sulla tabella dei tesori'), il suo nome esatto; altrimenti null). |
| `fonte` | string | si | File e sezione da cui viene |

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `da` | integer | si |  |
| `a` | integer | si |  |
| `risultato` | string | si | Il testo della riga, breve, com'e' nel manuale |
| `tabella` | string o null | si | Se la riga manda a un'altra tabella ('tira sulla tabella dei tesori'), il suo nome esatto; altrimenti null |

Regole (`tabelle.problems`, usate anche dalla verifica): nomi unici; `dado` e' una notazione NdS+K; le righe coprono
tutto l'intervallo del dado (dal minimo al massimo) senza buchi ne' sovrapposizioni; al massimo 60 tabelle e
200 righe per tabella. Un file che non le rispetta si ignora intero e la verifica lo segnala come errore.
I testi che il programma ricopia hanno la regola di `testi.py`: il nome senza `|`, `[`, `]`, backtick ne' a capo
(finisce in un marcatore); `uso` e `risultato` su una riga sola, senza segnali del programma (`[[`, `[Tabella]`,
`[Tiro]`, `[TRIGGER`), entro 300 caratteri. L'estrazione scarta le tabelle che non la rispettano.

Una riga puo' mandare a un'altra tabella dello stesso file (`"tabella": "tesori"`: 'tira sui tesori'): il
programma tira anche quella, fino a 3 tabelle in fila, una riga di `[Tabella]` per ciascuna. Il nome deve
esistere nel file e non essere la tabella stessa (`problems`).

## Il marcatore e l'esito

Il narratore chiude la narrazione con `[[tabella: <nome>]]` (uno per messaggio, in alternativa a un tiro); la pagina
lo mostra come pulsante; `POST /api/table {name}` tira il dado della tabella, trova la riga e manda l'esito al
narratore come messaggio del giocatore. Il nome vale esatto, o scritto a meta' se e' uno solo a contenerlo
(`nomi.risolvi`: 'incontri' per 'incontri nella palude'); due candidati fermano il tiro nominandoli. La verifica
avvisa se l'ultima narrazione ha un marcatore che il programma non saprebbe tirare.

```
[Tabella] incontri nella palude — 1d100: 37 -> 37: Un branco di cani selvatici
```

Tirare non costa chiamate. Il prompt di sistema elenca le tabelle con il loro uso (blocco in cache).

Un modificatore al tiro ("+10 di notte") sta nel marcatore: `[[tabella: <nome> | +10]]`; il totale si ferma ai capi
del dado (`1d100: 95 +10 (fermato a 100) -> 100: ...`). La riga uscita e' un fatto per i trigger: la condizione
`{"tabella": "<nome>", "op": "contiene", "valore": "..."}` vale solo nel turno in cui arriva (`--doc trigger`).
Nel codice: `tabelle.parse(spec) -> (nome, modificatore)`, `tabelle.tira(spec, tabelle)`, `tabelle.outcome(messaggio)`.

Quando una sezione di regole entra nel contesto del turno e contiene una tabella del file (il nome della tabella
nel titolo, o il titolo nella `fonte`), il programma appende sotto la sezione il promemoria `> Questa tabella la
tira il programma: ... chiudi con [[tabella: <nome>]]` (`tabelle.promemoria`, `TurnContext.tables`): letta in prosa,
la riga la sceglierebbe il narratore.

## Da dove vengono

`extract_tables` (pulsante 'Ricava le tabelle' nel tavolo, `POST /api/tables/extract`; alla fine della preparazione
di un manuale, in cache per contenuto delle sezioni): una chiamata sulle sezioni
di `meccanica/` e `ambientazione/` che hanno la forma di una tabella (almeno 3 righe con un intervallo
come `01-05` e una notazione di dado nel testo), con schema forzato; il codice controlla ogni tabella e scarta
quelle che non quadrano (registrate nel log). Si possono scrivere anche a mano. La verifica consiglia di ricavarle
quando ci sono sezioni con quella forma e il file manca; `python main.py --campaign <cartella> --ricava tabelle` lo fa
dalla riga di comando (anche `dadi`, `prefazione`, `trigger`). Il tavolo mostra la stima di costo accanto al pulsante.
