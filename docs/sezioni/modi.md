# I modi di gioco

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

Un modo di gioco e' una LENTE sul narratore: tono, ritmo, quanto il mondo concede alle soluzioni
improbabili, quanto costano gli errori, come si inclinano i PNG. Non e' una regola: le difficolta' restano
quelle del manuale, i dadi li tira il programma, la scheda non cambia. Si sceglie dalla pagina (selettore
'Modo' nella testata) o scrivendo `modo` in `stato.json`; vale dal turno dopo. Entra nel prompt di sistema
come parte del primo blocco, in cache: si riscrive solo quando il giocatore lo cambia. Zero chiamate in piu'.

## I modi predefiniti (`classico` se non si sceglie)

### `quieto`

Tono caldo, ritmo disteso: si esplora e si parla, gli errori costano tempo, non la vita.

Blocco nel prompt, parola per parola:

```
## Modo di gioco: quieto
Tono caldo, ritmo disteso. Lascia respirare le scene di dialogo e di scoperta. Una soluzione improbabile ma ben raccontata puo' riuscire: nel dubbio scegli la difficolta' piu' bassa che le regole ammettono. Un fallimento costa tempo, una risorsa o una complicazione; la morte arriva solo per una scelta consapevole e annunciata.
I PNG in questo modo: I PNG sono inclini ad ascoltare: un estraneo parte dalla curiosita', non dal sospetto; chi e' ostile lo dice prima di colpire.
Il modo e' una lente: cambia tono, ritmo e quanto il mondo concede. Le regole, le difficolta' previste dal manuale, i dadi e la scheda non cambiano con il modo.
```

### `classico`

Il tono del manuale: difficolta' senza sconti ne' rincari, conseguenze proporzionate.

Blocco nel prompt, parola per parola:

```
## Modo di gioco: classico
Tono e ritmo del manuale. Le difficolta' seguono le regole senza sconti ne' rincari; un fallimento ha conseguenze proporzionate a cio' che si rischiava; la morte e' possibile quando le regole la prevedono e il giocatore ha accettato il rischio.
I PNG in questo modo: I PNG seguono i loro interessi: aiutano chi gli conviene, diffidano degli sconosciuti, ricordano i torti.
Il modo e' una lente: cambia tono, ritmo e quanto il mondo concede. Le regole, le difficolta' previste dal manuale, i dadi e la scheda non cambiano con il modo.
```

### `spietato`

Tono asciutto, ritmo serrato: ogni errore lascia un segno, la morte e' sul tavolo.

Blocco nel prompt, parola per parola:

```
## Modo di gioco: spietato
Tono asciutto, ritmo serrato. Nel dubbio scegli la difficolta' piu' alta che le regole ammettono. Ogni fallimento lascia un segno concreto: una ferita, una perdita, un nemico. La morte e' sul tavolo a ogni scontro e non viene annunciata. Nessuna soluzione riesce per il solo fatto di essere ingegnosa: serve il tiro.
I PNG in questo modo: I PNG non fanno sconti: mentono se gli conviene, pretendono qualcosa in cambio, e chi e' stato tradito non dimentica.
Il modo e' una lente: cambia tono, ritmo e quanto il mondo concede. Le regole, le difficolta' previste dal manuale, i dadi e la scheda non cambiano con il modo.
```

## `meccanica/modi.json`: i modi della campagna

```json
{"modi": [{"nome": "cupo", "descrizione": "una riga per il selettore", "istruzioni": "cosa cambia per il narratore", "png": "come si inclinano i PNG (facoltativo)"}]}
```

Se il file c'e' ed e' valido SOSTITUISCE i predefiniti (tutti); `nome`, `descrizione` e `istruzioni` sono
testi obbligatori, `png` facoltativo, nomi unici (`modi.modi_problems`, la regola usata anche dalla verifica).
Un file malformato viene ignorato e la verifica lo segnala come errore; un `modo` in `stato.json` che non
esiste piu' e' un errore (il narratore giocherebbe in `classico` senza dirlo).

Nel codice: `modi.carica(campaign)`, `modi.attivo(campaign)`, `modi.testo(modo)`, `Campaign.set_mode(nome)`,
`POST /api/mode {name}`; lo stato della pagina espone `mode` e `modes`.
