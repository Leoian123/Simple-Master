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
| `uso` | string | si | Quando si usa, in una frase |
| `tipo` | string (`successi`, `somma`) | si | successi = si contano i dadi riusciti; somma = si sommano i dadi e un bonus |
| `dado` | integer | si | Facce del dado |
| `dadi_fissi` | integer o null | si | Numero di dadi se e' sempre lo stesso; null se dipende dai tratti |
| `successo_da` | integer o null | si | Per `successi`: un dado riesce da questo valore in su |
| `vantaggio` | boolean | si | Per `somma`: esiste il tirare due volte e tenere il migliore/peggiore |
| `nome_zero_successi` | string | si | Nome dell'esito con zero successi, o stringa vuota |
| `coppie` | object o null | si | Se le coppie di una faccia valgono successi in piu' |
| `speciali` | object o null | si | Dadi di tipo diverso dentro il tiro, il cui numero viene da un valore della scheda |
| `eventi` | elenco di object | si | Ogni elemento: `nome` (Il nome che il manuale da' all'evento), `faccia` (La faccia del dado che lo fa scattare), `quando` (sempre; solo se il tiro e' riuscito; solo se e' fallito; solo se c'e' un critico (coppie)), `solo_speciali` (true se conta solo quando la faccia esce su un dado speciale), `effetto` (Promemoria breve dell'effetto, o stringa vuota). |

`coppie`: `faccia`; `successi_extra` Successi IN PIU' per ogni coppia, oltre a quelli dei due dadi; `nome`.
`speciali`: `nome` Una parola minuscola; `quanti_da` Percorso nella scheda JSON del valore che dice quanti sono; `sostituiscono` true se prendono il posto di dadi normali, false se si aggiungono.

## Un evento (`tiri[i].eventi[j]`)

Una faccia che, quando esce, fa scattare qualcosa con un nome (critico caotico, fallimento bestiale).

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `nome` | string | si | Il nome che il manuale da' all'evento |
| `faccia` | integer | si | La faccia del dado che lo fa scattare |
| `quando` | string (`sempre`, `riuscito`, `fallito`, `critico`) | si | sempre; solo se il tiro e' riuscito; solo se e' fallito; solo se c'e' un critico (coppie) |
| `solo_speciali` | boolean | si | true se conta solo quando la faccia esce su un dado speciale |
| `effetto` | string | si | Promemoria breve dell'effetto, o stringa vuota |

`quando` vale uno tra: `sempre`, `riuscito`, `fallito`, `critico`.

## Il marcatore che il narratore scrive

Uno per messaggio, su una riga sua, in coda alla narrazione. La pagina lo mostra come pulsante.

```
[[tiro: <nome> | <tipo di tiro del profilo> | dadi N | bonus +K | difficolta D]]
[[tiro: <nome> | <tipo> | dadi N | <nome dei dadi speciali> N]]      per forzare i dadi speciali
[[tiro: <nome> | <tipo> | bonus +K | difficolta D | vantaggio]]      o svantaggio, se il tipo lo ammette
[[tiro: <nome> | 2d6+1 | difficolta 8]]                             senza profilo: dadi per esteso
[[tiro: <nome> | 6d10 | successi 6+ | difficolta 3]]                senza profilo: riserva a successi
```

`dadi` e' obbligatorio se il tipo non ha `dadi_fissi`; `difficolta` e' facoltativa (senza, nessun verdetto).
I dadi speciali li conta il programma leggendo `quanti_da` dalla scheda del personaggio attivo.
`/tira 2d6+1` nel campo di testo della pagina fa un tiro libero.

## L'esito che torna al narratore

Tirare non costa chiamate. L'esito e' il messaggio del giocatore del turno dopo:

```
[Tiro] <nome> — 5d10: 7, 6, 4 | fame: 2, 9 -> 3 successi contro difficolta' 2: RIUSCITO (margine 1). CRITICO. FALLIMENTO BESTIALE (effetto).
[Tiro] <nome> — 1d20: 14 +3 = 17 contro difficolta' 15: RIUSCITO (margine 2).
```

Gli eventi compaiono in MAIUSCOLO con l'effetto tra parentesi; `nome_zero_successi` compare quando
nessun dado riesce; con `difficolta` il verdetto e' RIUSCITO (margine) o FALLITO (mancano).
Nel codice: `dice.roll(spec, profile, sheet) -> {name, target, success, value, rolls, special_rolls, notes, text}`.
