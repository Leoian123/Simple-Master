# I trigger

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

`meccanica/trigger.json` e' un elenco di regole `condizioni -> effetti` che il programma valuta a ogni turno,
prima della chiamata del narratore, in testa al messaggio del turno: dopo le scadenze degli orologi e prima
della consegna degli orologi pieni (un trigger puo' riempirne uno nello stesso turno). Le condizioni leggono
solo cio' che il programma possiede; gli effetti sono tipizzati; nessun testo da giudicare, nessuno script,
nessuna chiamata. Un file che non rispetta la forma viene ignorato intero, e la verifica lo dice.

```json
{"trigger": [
  {"nome": "rischio di crollo", "ricorrente": true,
   "quando": [{"scheda": "stato.stress", "op": ">=", "valore": 4}],
   "effetti": [{"avviso": "Stress a 4 o piu': a ogni tentazione serve una prova, secondo le regole"}]},
  {"nome": "la ferita si infetta",
   "quando": [{"scheda": "stato.punti_ferita.attuale", "op": "<", "valore": 5}],
   "effetti": [{"orologio": "L'infezione", "avanza": 1, "segmenti": 6, "conseguenza": "Kael perde una notte di riposo"}]}
]}
```

## Una regola

| campo | significato |
|---|---|
| `nome` | unico; e' cio' che il narratore legge in `[TRIGGER nome: ...]` |
| `quando` | da 1 a 5 condizioni, tutte vere insieme (AND); non esiste OR: due regole |
| `effetti` | da 1 a 3 effetti, applicati in ordine |
| `ricorrente` | `true`: scatta a ogni turno in cui vale; `false` (default): una volta sola, poi il nome finisce in `stato.json` -> `trigger_scattati` |

Al massimo 100 regole per campagna.

## Le condizioni (`quando[j]`: una sola chiave fra queste, piu' `op` e `valore`)

| chiave | legge |
|---|---|
| `scheda` | un valore della scheda del personaggio attivo, per percorso (es. `stato.stress`); se il percorso non esiste la condizione e' falsa |
| `orologio` | i segmenti pieni di un orologio attivo, per nome; se l'orologio non esiste la condizione e' falsa |
| `relazione` | il numero della relazione di un PNG (-100..100); se non e' mai stata contata, falsa |
| `stadio` | lo stadio della relazione di un PNG, per nome (es. `Ostile`), confronto solo con `==` / `!=` |
| `esito` | il nome della fascia dell'ultimo tiro, solo nel turno in cui l'esito arriva (es. `fallito`), `==` / `!=` |
| `tabella` | la riga uscita da una tabella, per nome della tabella, solo nel turno in cui l'esito arriva: `==` / `!=` / `contiene` con un testo |
| `contatore` | `turni` (giocati dal personaggio) o `sessione` (in corso) |

`op`: `==`, `!=`, `<`, `<=`, `>`, `>=`, `contiene`. Con `<`, `<=`, `>`, `>=` il valore e' un numero; `contiene` cerca un
elemento in un elenco (o un pezzo in un testo); `stadio` ed `esito` ammettono solo `==` / `!=` con un nome; `tabella`
anche `contiene`, sul testo della riga uscita (`[Tabella] <nome> — ... -> N: <riga>`), solo in quel turno.
Un confronto fra tipi diversi e' falso, mai un errore: una regola scritta male non scatta.

## Gli effetti (`effetti[j]`: una sola chiave fra queste)

| chiave | fa |
|---|---|
| `avviso` | una riga al narratore in testa al messaggio del turno: `[TRIGGER <nome>: avviso]` |
| `orologio` | fa avanzare l'orologio di `avanza` segmenti (anche negativi); se non esiste e c'e' `segmenti`, lo crea (con `conseguenza`) |
| `relazione` | muove la relazione del PNG di un passo: `verso` sale|scende, `forza` lieve|media|forte |

L'avviso arriva al narratore come `[TRIGGER <nome>: testo]`, come gli orologi pieni; gli altri effetti
cambiano `stato.json` e si vedono dove si vedono gli orologi e le relazioni. Nessun effetto tocca la scheda:
i numeri del personaggio li muove solo lo scriba, su indicazione del narratore.

## Verifica

La verifica segnala come errore un file illeggibile o malformato (con il percorso della regola) e un percorso
`scheda` che nella scheda attiva non esiste (la regola non scatterebbe mai). Nel codice: `trigger.problems(data)`,
`trigger.carica(campaign)`, `trigger.valuta(campaign, messaggio_del_turno)`.

## Da dove vengono

Si scrivono a mano, oppure `trigger.extract_triggers(campaign, client, model, sheet)` (pulsante 'Ricava i trigger' nel
tavolo, `POST /api/triggers/extract`; alla fine della preparazione se c'e' una scheda attiva, in cache per contenuto):
una chiamata sulle sezioni di `meccanica/` che legano un numero a una conseguenza (`trigger.threshold_sections`: una
parola di soglia a meno di 60 caratteri da un numero vale un punto, servono almeno 2 punti, le sezioni
entrano dalla piu' densa fino a 36k caratteri), con i percorsi della scheda attiva
come unici percorsi ammessi (`trigger.sheet_paths`), schema forzato in forma piatta (`tipo`, `soggetto`, `op`, `valore`
per le condizioni; `tipo`, `testo` e i campi dell'effetto). Il codice ritraduce nella forma del file, passa ogni regola
per `problems` e `sheet_path_problems`, scarta cio' che non quadra (registrato nel log) e aggiunge le regole nuove a
quelle gia' scritte, senza toccarle (vince il nome gia' in uso).
