# Gli strumenti dell'API

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

Tutti i tool sono definiti in `master/tools.py` ed eseguiti da `ToolExecutor` sui file della
campagna: l'AI chiede cosa leggere o cosa cambiare, il codice esegue e non riscrive mai un file intero.
Chi ha quali strumenti e' deciso dal codice: il narratore del gioco ha solo quelli di lettura e
scrive gli appunti; lo scriba ha quelli di scrittura e non legge; il costruttore legge e salva la scheda.

## Lettura (narratore, costruttore di schede)

### `read_file`

Legge un file della campagna. Usalo dopo aver consultato il glossario per aprire SOLO i file necessari a questo turno. Puoi indicare una sezione (titolo `## ...`) per leggere solo quella.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si | Nome del file con cartella, es. ambientazione/npc.md |
| `section` | string | no | Titolo di sezione opzionale |

### `lookup_glossary`

Cerca nel glossario completo e ritorna le voci (termine, tipo, descrizione, file) che corrispondono. Usalo quando il glossario nel prompt e' in forma di indice compatto e ti serve la descrizione di un termine o il file giusto, prima di aprire file interi.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `query` | string | si | Termine o parole chiave, es. 'Frenesia' o 'clan Ventrue' |

### `list_files`

Elenca i file della campagna con le loro sezioni. Raramente necessario: il glossario basta.

(nessun parametro)

## Scrittura (solo lo scriba)

### `append_file`

Aggiunge testo in coda a un file (lo crea se non esiste). Usalo per il diario (ambientazione/diario.md): una voce breve per ogni evento rilevante.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si |  |
| `text` | string | si | Testo Markdown da aggiungere |

### `set_section`

Crea o sostituisce per intero una sezione `## Titolo` di un file. Usalo per aggiornare un PNG, un luogo, i fili aperti. Non per la scheda del personaggio: quella e' update_sheet.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si |  |
| `section` | string | si | Titolo della sezione (senza ##) |
| `text` | string | si | Nuovo corpo completo della sezione |

### `replace_text`

Sostituisce la prima occorrenza esatta di un frammento con un altro. Usalo per piccole modifiche puntuali in un file di testo (es. lo stato di un PNG).

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si |  |
| `old` | string | si |  |
| `new` | string | si |  |

### `upsert_glossary`

Aggiunge o aggiorna una voce del glossario. Chiamalo ogni volta che introduci un'entita' nuova (PNG, luogo, oggetto, fazione) o quando la sua descrizione in una frase cambia. Il campo file indica dove stanno i dettagli.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `term` | string | si |  |
| `kind` | string | si | pg, png, luogo, oggetto, fazione, regola, evento... |
| `description` | string | si | Una frase sola |
| `file` | string | si | File con i dettagli, es. ambientazione/npc.md |

### `update_sheet`

Cambia i dati della scheda del personaggio (il JSON in PERSONAGGIO ATTIVO) per percorso: danni, risorse, condizioni, equipaggiamento. Una sola chiamata con tutte le modifiche del turno. Non tocca l'esperienza: per quella ci sono award_xp e spend_xp.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `changes` | elenco di object | si | Modifiche ai dati della scheda, applicate tutte insieme Ogni elemento: `path` (Percorso con i punti, es. 'stato.fame', 'stato.salute.attuale', 'tratti.abilita.rissa', 'equipaggiamento.+' per aggiungere a un elenco), `value` (Nuovo valore JSON (numero, testo, elenco, oggetto). null cancella la voce). |
| `pg` | string | no | Personaggio, solo se diverso da quello attivo |

### `award_xp`

Assegna punti esperienza al personaggio e li scrive nel registro della scheda. Il conto lo tiene il codice: non sommare a mente, il risultato ti dice guadagnati, spesi e disponibili. Vie: `sessione` = chiusura di una sessione di gioco, secondo le regole del manuale (una volta per sessione); `storia` = conclusione di una storia o di un arco narrativo; `regola` = una regola del manuale che concede punti in gioco (citala nel motivo); `narratore` = premio eccezionale del narratore, fuori dalle regole (raro).

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `amount` | integer, min 1 | si |  |
| `route` | string (`sessione`, `storia`, `regola`, `narratore`) | si |  |
| `reason` | string | si | Perche', in una riga (per `regola`: quale regola) |
| `pg` | string | no | Personaggio, solo se diverso da quello attivo |

### `spend_xp`

Spende punti esperienza per far crescere un tratto: controlla che bastino, applica le modifiche alla scheda e registra la spesa, tutto insieme. Il costo lo calcoli tu dalle regole in meccanica/ (leggile se non le hai davanti); se i punti non bastano la spesa viene rifiutata.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `amount` | integer, min 1 | si |  |
| `reason` | string | si | Cosa si compra e il calcolo del costo, es. 'Fermezza 2->3: 5 x 3' |
| `changes` | elenco di object | si | Modifiche ai dati della scheda, applicate tutte insieme Ogni elemento: `path` (Percorso con i punti, es. 'stato.fame', 'stato.salute.attuale', 'tratti.abilita.rissa', 'equipaggiamento.+' per aggiungere a un elenco), `value` (Nuovo valore JSON (numero, testo, elenco, oggetto). null cancella la voce). |
| `pg` | string | no | Personaggio, solo se diverso da quello attivo |

### `set_clock`

Crea o aggiorna un orologio: una minaccia o un progetto che avanza a segmenti (es. 'La guardia indaga', 6 segmenti). `consequence` e' cio' che accade quando si riempie. segments 0 lo toglie.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si |  |
| `segments` | integer, min 0, max 12 | si | 4 breve, 6 normale, 8 lungo; 0 = togli |
| `consequence` | string | no | Cosa succede quando e' pieno, in una frase |
| `filled` | integer, min 0 | no | Segmenti gia' pieni (di solito 0) |

### `tick_clock`

Fa avanzare (o arretrare, con un numero negativo) un orologio esistente. Il conto lo tiene il codice.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si |  |
| `amount` | integer | no | Di solito 1; 2-3 per un fatto grave |

### `shift_relationship`

Muove l'atteggiamento di un PNG verso il personaggio di un passo, in su o in giu'. Il numero lo tiene il codice e te lo restituisce come stadio (Diffidente, Cordiale...): tu dici solo il verso e la forza. Solo per la riga `relazione` degli appunti del narratore, mai di tua iniziativa.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si | Il PNG, con il nome esatto tra quelli che ricevi (o nuovo) |
| `verso` | string (`sale`, `scende`) | si |  |
| `forza` | string (`lieve`, `media`, `forte`) | si | lieve: un gesto; media: un aiuto o un torto concreto; forte: una svolta |
| `reason` | string | si | Perche', in una riga |

### `set_scene`

Riscrive il documento della scena in corso, com'e' ALLA FINE di questo turno. Va chiamato a ogni turno.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `luogo` | string | si |  |
| `quando` | string | si | Giorno/notte e ora, come risulta dalla narrazione |
| `presenti` | elenco di string | si | I PNG in scena, con il nome usato nella narrazione |
| `appena_successo` | string | si | 2-4 frasi: i fatti di questo turno, comprese le azioni del giocatore |
| `domanda_aperta` | string | si | Cio' che il giocatore deve decidere adesso |
| `da_richiamare` | elenco di string | si | Fino a 6 dettagli concreti da tenere vivi: frasi dette, oggetti, promesse. Tieni quelli ancora utili, togli i superati |

## Salvataggio della scheda (solo il costruttore)

### `save_sheet`

Salva la scheda completa di un personaggio giocante come dati in schede/<nome>.json (la vista leggibile schede/<nome>.md viene generata da sola) e la registra nel glossario. Chiamalo solo dopo la conferma del giocatore.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si | Nome del personaggio |
| `summary` | string | si | Una frase per il glossario, es. 'Ladra elfa di Velmora' |
| `data` | object | si | I dati della scheda, secondo le convenzioni indicate nel prompt |
| `starting_xp` | integer, min 0 | no | Punti esperienza concessi dalla creazione e NON ancora spesi (di solito 0) |

## Cosa viene rifiutato (il tool torna un errore, nulla viene scritto)

- `read_file` senza `section` su un file oltre 24.000 caratteri: ritorna l'elenco delle sezioni. non il testo.
- Scrivere sulla vista `schede/<pg>.md` di una scheda che ha il JSON: la vista e' generata, i dati si cambiano con `update_sheet`.
- `update_sheet` su un percorso che comincia con `esperienza`: i punti entrano solo da `award_xp` e `spend_xp`.
- `update_sheet` su un percorso che non esiste (il messaggio elenca le chiavi disponibili), un indice fuori dall'elenco, o un gruppo usato come valore. Le modifiche sono tutte o nessuna.
- `award_xp` e `spend_xp` dallo scriba quando gli appunti del narratore non hanno una riga `pe:` (il motore apre il permesso solo per quel giro); `award_xp` con la via `creazione` (e' del costruttore, via `save_sheet`).
- `award_xp` con una via sconosciuta, con punti non positivi, senza motivo, o per la via `sessione` quando la sessione in corso li ha gia' ricevuti.
- `spend_xp` se i punti disponibili non bastano, o se una delle modifiche al tratto fallisce (spesa e crescita insieme o niente).
- `upsert_glossary` con un termine vuoto, o con un termine che non compare nel file indicato (salvo i tipi `file`, `fonte`, `cronaca`): prima la sezione, poi la voce.
- `set_scene` con `presenti` o `da_richiamare` che non sono elenchi di testi, o con un campo di testo che non e' un testo: la scena di prima resta.
- `set_clock` con segmenti fuori da 2..12 (0 toglie l'orologio); `tick_clock` su un nome che non esiste (il messaggio elenca quelli attivi).
- Lo scriba che chiama uno strumento di lettura o `save_sheet`: 'Lo scriba non legge e non crea schede: trascrive soltanto.'

`tests/test_scriba_ostile.py` prova ognuno di questi rifiuti con un modello finto che rispetta lo schema e cerca la scappatoia.
- Qualunque nome di file fuori dalla campagna, con `..`, o con estensione diversa da `.md`/`.txt`.
