# Lo stato della campagna

Cio' che il programma conta al posto del narratore. Tutto in file, dentro la campagna;
solo il codice li scrive (`Campaign`), mai uno strumento dell'AI.

## `stato.json`

```json
{
 "pg_attivo": "jonathan-harker",
 "sessione": 2,
 "orologi": {
  "Rosa Ruiz e la polizia": {"segmenti": 6, "pieni": 1, "conseguenza": "Agenti al portone con mandato"}
 },
 "memoria": {"jonathan-harker": 9},
 "png": {"Rosa Ruiz": {"relazione": -23}}
}
```

| chiave | tipo | significato |
|---|---|---|
| `pg_attivo` | slug | il personaggio con cui si gioca. Se manca o la scheda non esiste piu': l'unica scheda presente, altrimenti nessuno |
| `sessione` | intero >= 1 | la sessione in corso. Avanza solo con "Chiudi sessione" dopo almeno 3 turni giocati; con meno e' un rifare da capo |
| `orologi` | oggetto | nome -> `{segmenti (2..12), pieni (0..segmenti), conseguenza}`, piu' `scadenza_turni` (da 1) e `creato_turno` (i turni giocati quando e' nato) se ha una scadenza. `set_clock` con `segments` 0 lo toglie; quando `pieni >= segmenti` il narratore lo riceve una volta come `[OROLOGIO PIENO: nome: conseguenza]` e l'orologio sparisce. Passata la scadenza senza che nessuno lo abbia riempito, il programma lo riempie da solo (`scaduto: true`) e la conseguenza arriva con "(il tempo e' passato)" |
| `memoria` | oggetto | slug -> fino a che turno del giocato il consolidamento e' arrivato. Nulla si ripaga; non puo' superare i turni esistenti |
| `png` | oggetto | nome del PNG -> `{relazione}` da -100 a 100, l'atteggiamento verso il personaggio contato dal codice e letto come stadio (sezione `relazioni`) |

Un `stato.json` illeggibile vale come vuoto: il programma riparte senza personaggio attivo,
orologi e contatore (la verifica lo segnala come errore).

## I fili aperti e la loro eta'

La sezione "Fili aperti di <pg>" in `ambientazione/avventura.md` resta prosa, una riga per filo,
ma ogni riga porta `[sN]`: la sessione in cui e' nata. Le etichette le mette il codice
(`master/fili.py`) a ogni scrittura della sezione, dallo scriba (`set_section`) o dal
consolidamento: una riga che c'era gia' tiene la sua, una nuova prende la sessione in corso.
Un filo fermo da almeno 2 sessioni e' **stantio**: il narratore lo riceve all'apertura della
sessione come `[FILI STANTII: ...]` e il consolidamento lo riceve fra i documenti, con l'invito
a chiuderlo (un fatto accaduto nel frattempo) o a rilanciarlo. La verifica lo segnala come consiglio.

## `sessioni/<slug>.jsonl`: il giocato

Una riga JSON per turno, solo in aggiunta. E' insieme la trascrizione completa e cio' da cui
si riparte (la conversazione ricompare in pagina: ultimi 40 turni).

```json
{"t": "2026-09-18T16:58:03", "player": "Apro gli occhi.", "master": "Ti svegli...", "pg": "Kael", "read": [], "changed": ["ambientazione/diario.md"], "tokens": [1200, 300]}
```

- `player` e' cio' che e' stato mandato: le parole del giocatore, un esito `[Tiro] ...`, o
  un'istruzione del tavolo tra parentesi quadre (`[Apertura della campagna]`, `[Apertura della
  sessione]`, `[Chiusura della sessione]`), che non conta come turno giocato.
- `master` e' la narrazione senza il blocco `<appunti>`.
- Una riga troncata da una chiusura forzata viene ignorata. Un turno fallito non viene salvato.
- "Nuova sessione" sposta il file in `sessioni/archivio/<slug>-AAAAMMGG-HHMMSS.jsonl`
  (`-scartata` se con meno di 3 turni: non e' storia della campagna e il consolidamento la ignora).
- `Campaign.all_turns(slug)` = archivi non scartati + file corrente, in ordine: la fonte della memoria.
- `sessioni/_scheda.jsonl` e' la conversazione del costruttore di schede (ultimi 12 turni ricaricati).

## `sessioni/spese.jsonl`: il contatore di spesa

Una riga per chiamata API: `t`, `sessione`, `processo` (gioco, scriba, instradamento, memoria,
scheda, dadi, preparazione, riordino, sanifica), `modello`, `usd` (dai token che l'API
restituisce: ingresso, uscita, cache letta a un decimo, cache scritta al doppio) e campi extra
(`turno`, `chiamate`, `nota`). La media per turno conta `gioco` + `scriba`.

## `impostazioni.json` (radice del progetto, non della campagna)

`model`, `model_prepare`, `model_memory`, `model_scribe`, `effort_play`, `effort_sheet`,
`effort_prepare`, `effort_tidy`, `prep_batch`. Si cambiano dalla UI (pulsante col nome del
modello); `MASTER_MODEL` e `MASTER_EFFORT` nel `.env` valgono solo se il file non esiste.
