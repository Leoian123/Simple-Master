# La verifica della campagna

`python main.py --campaign campaigns/mia --validate [--json]`, oppure il pulsante "Verifica"
sulla scheda della campagna nel menu (`POST /api/validate {campaign}`). Sola lettura, nessuna
chiamata. Codice in `master/validate.py`; i criteri sono quelli dei moduli che possiedono i
dati, le correzioni restano ai loro comandi.

## I tre livelli

| livello | significa | uscita del comando |
|---|---|---|
| `errors` | la campagna non funziona come deve: un JSON che il codice non legge, un profilo dei dadi malformato o con un `quanti_da` assente dalla scheda, totali dell'esperienza scritti a mano, punti di sessione doppi, orologi fuori misura, memoria consolidata oltre il giocato | esce con 2 |
| `warnings` | funziona ma puo' sbagliare in gioco: voce di glossario verso un file sparito, termine assente dal file, voce doppia, scheda senza nome, sezione in quarantena o registrata ma assente, via dell'esperienza sconosciuta | esce con 0 |
| `recommendations` | migliorie: campagna da preparare, dadi/tabelle/trigger da ricavare, prefazione delle regole, scheda solo testo, scena o fili aperti assenti, memoria da consolidare, orologio senza conseguenza | esce con 0 |

Ogni rilievo: `path` (file; dopo `:` la chiave JSON, dopo ` § ` la sezione o la voce), `title`,
`message`, `fix`. A terminale: `- path | title: message -> fix`.

## Il JSON (`--json`)

```json
{"campaign": "mia", "validatorVersion": 1,
 "counts": {"errors": 0, "warnings": 1, "recommendations": 0},
 "errors": [], "warnings": [{"level": "warning", "path": "glossario.md § Jolyne", "title": "termine assente dal file", "message": "...", "fix": "..."}],
 "recommendations": []}
```

Per un agente: modifica, valida, correggi finche' `counts.errors` e' 0; leggi gli avvisi, decidi
sui consigli. Una verifica pulita prova la forma, non la qualita' del contenuto.

## Il validatore e il codice dicono la stessa cosa (test di mutazione)

`tests/test_mutazioni.py` parte da una campagna sintetica pulita e muta un campo alla volta di
`dadi.json`, `stato.json` e della parte fissa della scheda (`nome`, `esperienza`, il percorso
letto dai dadi speciali): tipo sbagliato, valore fuori misura, chiave in piu', chiave
mancante, file illeggibile. Per ogni mutazione confronta due verdetti: quello del **codice**
(`accetta` se usa il file com'e' scritto; `rifiuta` se si ferma o butta via il dato in
silenzio) e quello del **validatore** (errore, avviso, consiglio, nulla). La tabella del test
dichiara entrambi; l'invariante e' **dove il codice rifiuta, il validatore dice errore**. Il
contrario e' ammesso solo se dichiarato riga per riga (`oltre:`): il validatore vede cio' che
il codice tollera (una soglia oltre le facce del dado, un totale scritto a mano, un percorso
che i dadi speciali non trovano). Un campo nuovo in uno di quei file senza una riga nella
tabella e' un rosso: quando aggiungi un campo, aggiungi la sua mutazione.

## Tutte le campagne e l'hook

Senza `--campaign` controlla ogni cartella di `campaigns/` e stampa solo chi ha errori o
avvisi, al massimo 6 righe per livello (`... e altri N`), nulla se tutto e' in ordine. E' il
secondo hook `SessionStart` di Claude Code (`.claude/settings.json`), dopo `--logs diagnose`.
Non apre un registro in `log/`.
