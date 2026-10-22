# Il profilo dei dadi

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

Il profilo sta in `meccanica/dadi.json`. Lo ricava una chiamata (`extract_profile`, dal pulsante
'Ricava i dadi dalle regole' o alla fine della preparazione) leggendo le sezioni di `meccanica/` che
parlano di tiri; poi resta su disco e `master/dice.py` lo applica senza conoscere il gioco.
Il modulo sa fare due cose: sommare dadi e un bonus contro una soglia (`somma`), o contare i dadi
che superano un valore (`successi`). Tutto il resto e' nel profilo.

## Il file

| campo | tipo | significato |
|---|---|---|
| `sistema` | testo | il nome del sistema di gioco |
| `note` | testo | Regole sui tiri che il narratore deve ricordare (ritiri, margine...), 2-3 frasi |
| `tiri` | elenco | un elemento per ogni modo di tirare (i campi qui sotto) |
| `fonte` | testo | scritto dal programma: da quali sezioni e' stato ricavato |

## Un tipo di tiro (`tiri[i]`)

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `nome` | string | si | Una parola minuscola, come la usa il manuale (una per ogni modo di tirare) |
| `alias` | elenco di string | si | Altri nomi con cui il manuale chiama lo stesso tiro ('test', 'prova di abilita''); [] se nessuno |
| `uso` | string | si | Quando si usa, in una frase |
| `tipo` | string (`successi`, `somma`, `sotto`) | si | successi = si contano i dadi riusciti; somma = si sommano i dadi e un bonus contro una difficolta'; sotto = si riesce con un totale fino al valore della scheda (d100 roll-under) |
| `dado` | integer | si | Facce del dado |
| `dadi_fissi` | integer o null | si | Numero di dadi se e' sempre lo stesso; null se dipende dai tratti |
| `successo_da` | integer o null | si | Per `successi`: un dado riesce da questo valore in su |
| `vantaggio` | boolean | si | Per `somma`: esiste il tirare due volte e tenere il migliore/peggiore |
| `nome_zero_successi` | string | si | Nome dell'esito con zero successi, o stringa vuota |
| `coppie` | object o null | si | Se le coppie di una faccia valgono successi in piu' |
| `speciali` | object o null | si | Dadi di tipo diverso dentro il tiro, il cui numero viene da un valore della scheda |
| `eventi` | elenco di object | si | Ogni elemento: `nome` (Il nome che il manuale da' all'evento), `faccia` (La faccia del dado che lo fa scattare), `quando` (sempre; solo se il tiro e' riuscito; solo se e' fallito; solo se c'e' un critico (coppie)), `solo_speciali` (true se conta solo quando la faccia esce su un dado speciale), `effetto` (Promemoria breve dell'effetto, o stringa vuota). |
| `soglie` | elenco di object | si | Solo per `sotto`: i gradi di successo come frazioni del valore (es. difficile 0.5, estremo 0.2); [] altrimenti Ogni elemento: `nome` (Il nome del grado nel manuale (es. 'successo estremo')), `frazione` (Il grado vale se il totale e' fino a questa frazione del valore (es. 0.5 = meta', 0.2 = un quinto)), `guida` (Come narrare questo grado, in una frase; stringa vuota se il manuale non lo dice). |
| `fallimento_critico` | object o null | si | Solo per `sotto`: da quale totale il tiro e' un fallimento critico; `sotto_valore`/`altrimenti_da` se la soglia dipende dal valore (es. 96 sotto 50, altrimenti 100) |
| `successo_critico_fino_a` | integer o null | si | Solo per `sotto`: un totale fino a questo e' un successo critico (es. 1); null altrimenti |
| `tieni` | object o null | si | Per `somma`: si tirano N dadi e se ne tengono `quanti`, i piu' alti o i piu' bassi (es. Forged in the Dark: dadi <N>, tieni 1 alto; due dadi a venti facce, tieni 1). null se si sommano tutti |
| `esiti_su` | string (`margine`, `dado`) | si | Per `somma`: `margine` = le fasce di `esiti` si leggono sul margine contro la difficolta'; `dado` = si leggono sul dado tenuto, senza difficolta' (es. 6 pieno, 4-5 con conseguenze, 1-3 fallito) |
| `esplode_su` | integer o null | si | Una faccia da questo valore in su si ritira e si somma (somma) o aggiunge un dado (successi); null se i dadi non esplodono |
| `esempi` | elenco di object | si | Da 2 a 4 casi con dadi fissi ed esito atteso SECONDO LE REGOLE: il programma li esegue e rifiuta un profilo che non li rispetta Ogni elemento: `marcatore` (Un marcatore completo, come lo scriverebbe il narratore: `<nome> | <tipo> | dadi N | difficolta D` (per i dadi speciali forzali: `| <nome dei dadi speciali> 2`)), `dadi` (Le facce uscite, in ordine, tante quante il tiro ne usa), `atteso`. |
| `esiti` | elenco di object | si | Le fasce che il manuale da' all'esito in base al MARGINE (successi o totale oltre la difficolta', negativo se sotto): devono coprire tutti i margini senza buchi, la piu' bassa aperta in basso (da_margine null) e la piu' alta in alto (a_margine null). [] se il manuale non gradua l'esito: valgono fasce predefinite Ogni elemento: `nome` (Il nome che il manuale da' a questo esito (es. 'vittoria costosa')), `da_margine` (Margine minimo compreso; null = aperta in basso), `a_margine` (Margine massimo compreso; null = aperta in alto), `riuscito` (true se in questa fascia l'azione riesce), `guida` (Come narrare questo esito secondo il manuale, in una frase). |

`coppie`: `faccia`; `successi_extra` Successi IN PIU' per ogni coppia, oltre a quelli dei due dadi; `nome`.
`speciali`: `nome` Una parola minuscola; `quanti_da` Percorso nella scheda JSON del valore che dice quanti sono; `sostituiscono` true se prendono il posto di dadi normali, false se si aggiungono.

## Un evento (`tiri[i].eventi[j]`)

Una faccia che, quando esce, fa scattare qualcosa con un nome (critico esplosivo, fallimento rovinoso).

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `nome` | string | si | Il nome che il manuale da' all'evento |
| `faccia` | integer | si | La faccia del dado che lo fa scattare |
| `quando` | string (`sempre`, `riuscito`, `fallito`, `critico`) | si | sempre; solo se il tiro e' riuscito; solo se e' fallito; solo se c'e' un critico (coppie) |
| `solo_speciali` | boolean | si | true se conta solo quando la faccia esce su un dado speciale |
| `effetto` | string | si | Promemoria breve dell'effetto, o stringa vuota |

`quando` vale uno tra: `sempre`, `riuscito`, `fallito`, `critico`.

## Tiri sotto il valore (`tipo: "sotto"`)

Per i sistemi in cui si riesce tirando FINO AL valore della scheda (d100 di CoC, BRP, Delta Green, WFRP,
Mythras). Il marcatore porta il valore: `[[tiro: <nome> | <tipo> | valore <V>]]` (`| bonus <+K>` modifica il
valore). I gradi sono `soglie` a frazioni del valore (es. difficile 0.5, estremo 0.2), il `fallimento_critico`
un totale alto (`{da, sotto_valore, altrimenti_da}`: 96 con valore sotto 50, altrimenti 100), il
`successo_critico_fino_a` un totale basso. L'esito e' il nome del grado o uno fra successo critico / riuscito /
fallito / fallimento critico, con la guida del grado se il manuale la da'. `esiti` non si applica a `sotto`.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `nome` | string | si | Il nome del grado nel manuale (es. 'successo estremo') |
| `frazione` | number | si | Il grado vale se il totale e' fino a questa frazione del valore (es. 0.5 = meta', 0.2 = un quinto) |
| `guida` | string | si | Come narrare questo grado, in una frase; stringa vuota se il manuale non lo dice |

## Tieni ed esplosioni

`tieni: {quanti, quale}` (per `somma`): si tirano N dadi e se ne tengono i `quanti` piu' alti o piu' bassi;
con `esiti_su: "dado"` le fasce di `esiti` sono valori del dado tenuto e il marcatore non porta una
difficolta' (Forged in the Dark: `dadi <N>`, tieni 1 alto, 6 pieno / 4-5 con conseguenze / 1-3 fallito;
`coppie` sulla faccia 6 da' la nota CRITICO quando due dadi la mostrano). `esplode_su`: una faccia da quel
valore in su si ritira e si somma (`somma`: 6+6+2=14) o aggiunge un dado alla riserva (`successi`, il
"10 again"); al massimo 10 esplosioni per dado. Nei campioni le facce vanno nell'ordine in cui il
programma le consuma: ogni dado seguito dalle sue esplosioni.

## I campioni (`tiri[i].esempi[j]`)

Da 2 a 4 casi per tipo di tiro, ricavati insieme al profilo: un marcatore completo, le facce uscite e l'esito
che le regole prevedono. `dice.verifica_esempi` li esegue a dadi fissi: `extract_profile` rifa' la chiamata una
volta se non tornano e poi salva comunque segnando `campioni_falliti`; la verifica li riesegue a zero chiamate
e segnala ogni campione che non torna come errore. Il gioco non li legge.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `marcatore` | string | si | Un marcatore completo, come lo scriverebbe il narratore: `<nome> \| <tipo> \| dadi N \| difficolta D` (per i dadi speciali forzali: `\| <nome dei dadi speciali> 2`) |
| `dadi` | elenco di integer | si | Le facce uscite, in ordine, tante quante il tiro ne usa |
| `atteso` | object | si |  |

## Le fasce dell'esito (`tiri[i].esiti[j]`)

Con una difficolta', il margine (successi o totale oltre la difficolta', negativo se sotto) cade in una
fascia con un nome e una guida per il narratore. Le fasce devono coprire tutti i margini senza buchi,
la piu' bassa aperta in basso e la piu' alta in alto (`dice.esiti_problems` e' la regola, usata anche
dalla verifica). Se il manuale non gradua l'esito (`esiti: []` o assente) valgono le fasce predefinite:

- `successi`: fallito (fino a -1) · riuscito di misura (0) · riuscito (1..2) · riuscito bene (da 3)
- `somma`: fallito malamente (fino a -10) · fallito (-9..-1) · riuscito di misura (0..4) · riuscito (5..9) · riuscito bene (da 10)

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `nome` | string | si | Il nome che il manuale da' a questo esito (es. 'vittoria costosa') |
| `da_margine` | integer o null | si | Margine minimo compreso; null = aperta in basso |
| `a_margine` | integer o null | si | Margine massimo compreso; null = aperta in alto |
| `riuscito` | boolean | si | true se in questa fascia l'azione riesce |
| `guida` | string | si | Come narrare questo esito secondo il manuale, in una frase |

## Il marcatore che il narratore scrive

Uno per messaggio, su una riga sua, in coda alla narrazione. La pagina lo mostra come pulsante.

```
[[tiro: <nome> | <tipo di tiro del profilo> | dadi N | bonus +K | difficolta D]]
[[tiro: <nome> | <tipo> | dadi N | <nome dei dadi speciali> N]]      per forzare i dadi speciali
[[tiro: <nome> | <tipo> | bonus +K | difficolta D | vantaggio]]      o svantaggio, se il tipo lo ammette
[[tiro: <nome> | <tipo sotto> | valore <V>]]                       roll-under: riesce fino al valore
[[tiro: <nome> | <tipo con tieni e esiti sul dado> | dadi N]]        decide il dado tenuto, nessuna difficolta'
[[tiro: <nome> | 2d6+1 | difficolta 8]]                             senza profilo: dadi per esteso
[[tiro: <nome> | 6d10 | successi 6+ | difficolta 3]]                senza profilo: riserva a successi
```

`dadi` e' obbligatorio se il tipo non ha `dadi_fissi`; `difficolta` e' facoltativa (senza, nessun verdetto).
Il tipo vale per nome, per alias, o scritto a meta' se e' uno solo a contenerlo (`nomi.risolvi`); due candidati
fermano il tiro nominandoli; una notazione come `2d6+1` non si confonde mai con un tipo.
I dadi speciali li conta il programma leggendo `quanti_da` dalla scheda del personaggio attivo.
`/tira 2d6+1` nel campo di testo della pagina fa un tiro libero.

## L'esito che torna al narratore

Tirare non costa chiamate. L'esito e' il messaggio del giocatore del turno dopo:

```
[Tiro] <nome> — 5d10: 7, 6, 4 | <speciali>: 2, 9 -> 3 successi contro difficolta' 2: RIUSCITO (margine 1). CRITICO. FALLIMENTO ROVINOSO (effetto). Guida: ...
[Tiro] <nome> — 1d20: 14 +3 = 17 contro difficolta' 15: RIUSCITO DI MISURA (margine 2). Guida: ...
```

Gli eventi compaiono in MAIUSCOLO con l'effetto tra parentesi; `nome_zero_successi` compare quando
nessun dado riesce; con `difficolta` il verdetto e' il NOME DELLA FASCIA in maiuscolo con il margine
(o quanto manca), e in coda la guida della fascia.
Nel codice: `dice.roll(spec, profile, sheet) -> {name, target, success, value, rolls, special_rolls, notes, esito, guida, text}`.
