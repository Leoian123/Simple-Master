# Simple Master

Master minimalista per campagne di gioco di ruolo, guidato da Claude.
Python puro, dipendenze minime (l'SDK `anthropic` e `pypdf` per i manuali). Lo
stato della storia vive in file Markdown e JSON; il programma compone da quei
file il contesto di ogni turno, e un modello economico li aggiorna.

## Il ciclo di un turno

```
giocatore ──▶ MasterEngine ──▶ context.py compone il CONTESTO DEL TURNO dai documenti
                                 (scheda, scena, fili aperti, chi e cosa e' nominato,
                                  regole pertinenti, ultimi 3 scambi parola per parola)
                                 │
                                 ▼
                              narratore (1 chiamata, solo strumenti di lettura, di riserva)
                                 │  narrazione + <appunti> che il giocatore non vede
                                 ▼
                              scriba (modello economico): diario, scheda, glossario, scena
                                 │
giocatore ◀── narrazione ◀───────┘   turno salvato in sessioni/<personaggio>.jsonl
```

1. Il programma compone il contesto del turno dai documenti della campagna
   (`master/context.py`). Il **glossario** fa da tabella di collegamento: dice in
   quale file e sezione sta ogni termine nominato, e quella sezione entra nel contesto.
2. Il narratore risponde in una chiamata. Se gli manca qualcosa di indispensabile
   lo legge con `read_file` / `lookup_glossary` (lettura di riserva, una chiamata in piu').
3. Lo scriba trascrive gli appunti del narratore negli archivi e aggiorna la scena.
4. Torna la narrazione; il turno viene salvato in `sessioni/<personaggio>.jsonl`.

Il glossario e' una tabella con una riga per entita' (chi/cosa e', in una frase,
e in quale file stanno i dettagli): cosi' il contesto resta piccolo e i file
entrano solo quando servono.

**Glossario a due livelli.** Dove il glossario viaggia nel prompt (il costruttore
di schede) vale questa regola: finche' e' piccolo (sotto gli 8.000 caratteri) entra
intero; quando un manuale lo porta a centinaia di voci entra solo un indice compatto,
i termini raggruppati per tipo e file senza descrizioni, e le descrizioni si chiedono
con `lookup_glossary`, i dettagli con `read_file(file, sezione)`. Sul manuale di
Vampiri: da circa 21.600 a circa 2.500 token. Il file `glossario.md` resta completo.

## Meccanica, ambientazione, schede

Ogni campagna separa le informazioni in tre cartelle:

```
campaigns/mia/
  glossario.md
  meccanica/       REGOLE: numeri, tiri, tabelle. Il master le applica alla lettera.
    regole.md          come si gioca
    creazione-pg.md    come si crea un personaggio (passi, caratteristiche, opzioni)
    oggetti.md         statistiche di armi e oggetti
    creature.md        statistiche di mostri e creature
  ambientazione/   MONDO: cio' che il master narra e puo' inventare.
    mondo.md           luoghi, fazioni, storia
    npc.md             personaggi non giocanti
    avventura.md       trame, ganci, segreti
    diario.md          cronologia di cio' che succede in gioco
  schede/          una scheda per personaggio giocante (kael.md, ...)
```

La preparazione da manuale rispetta la stessa divisione: una creatura finisce
con i numeri in `meccanica/creature.md` e con la descrizione in
`ambientazione/npc.md`, stessa sezione.

### Scheda del personaggio

Dentro una campagna la sezione **Personaggio** ha due colonne. A sinistra i
personaggi della campagna, con "Vedi scheda" (anteprima impaginata: titoli,
tabelle, elenchi), "Gioca con questo" e "Modifica". A destra la creazione guidata:
il costruttore legge solo le regole in `meccanica/creazione-pg.md`, guida una
domanda alla volta con le opzioni e i numeri del manuale, calcola i valori
derivati, mostra la scheda completa e, dopo conferma, la salva come dati in
`schede/<nome>.json` registrandola nel glossario. Tre avvii rapidi evitano la
pagina bianca: passo passo, tre concetti adatti all'ambientazione, personaggio
pronto da rivedere.

**Il personaggio e' legato alla campagna.** Ogni campagna ricorda il suo
personaggio attivo in `stato.json`: la prima scheda creata lo diventa da sola,
l'unica scheda presente viene fissata all'ingresso, e con piu' schede si sceglie
con "Gioca con questo" (la conversazione riparte, lo stato resta nei file).
Senza un personaggio attivo la sezione Gioca e' disattivata e la campagna si
apre sulla creazione. Durante il gioco il master ha sempre la scheda del
personaggio attivo senza doverla rileggere: tratti, identita' e storia nel prompt
(in cache), stato ed esperienza nel contesto di ogni turno. La trascrizione della
sessione porta il suo nome.

**La scheda e' fatta di dati, non di frasi.** `schede/<nome>.json` e' l'unica
fonte: ogni valore di gioco e' un numero (`tratti.attributi.mentali.fermezza: 4`,
`stato.salute: {attuale, massimo}`), cosi' il master non deve interpretare
"Salute: 9 / 9 — nessun danno" per sapere quanti dadi tirare, e non deve
ritrovare e riscrivere una frase per togliere un punto. Lo scriba la modifica con
`update_sheet`, per percorso (`stato.fame` -> 3), tutte le modifiche del turno in
una chiamata, o tutte o nessuna. `schede/<nome>.md` resta come vista leggibile
(e' cio' che mostra "Vedi scheda"), rigenerata dai dati a ogni salvataggio: non
e' una seconda copia da tenere allineata, e gli strumenti di testo rifiutano di
scriverci sopra. Una vecchia scheda solo testo continua a funzionare; diventa
dati la prima volta che la si fa modificare al costruttore.

### Esperienza: le vie e il contatore

I punti esperienza non sono un numero che il master ricorda: sono un registro di
movimenti dentro la scheda (`esperienza.registro`: quando, sessione, via, punti,
motivo). Guadagnati, spesi e disponibili si calcolano dal registro ogni volta;
nessun totale viene salvato ne' scritto a mano (`update_sheet` rifiuta il
percorso `esperienza`). Il contatore compare accanto al nome del personaggio.

I punti entrano solo con `award_xp`, per una via dichiarata:

| Via | Quando |
|---|---|
| `creazione` | punti delle regole di creazione non ancora spesi (li indica il costruttore) |
| `sessione` | alla chiusura della sessione; il codice la concede una volta sola per sessione |
| `storia` | si chiude una storia o un arco |
| `regola` | una regola del manuale li concede in gioco (va citata nel motivo) |
| `narratore` | premio eccezionale, raro |

Escono solo con `spend_xp`: costo calcolato dalle regole del manuale, controllo
che i punti bastino, tratto aumentato e spesa registrata insieme, o niente.

**Chiudi sessione.** Dopo almeno tre turni giocati il pulsante "Nuova sessione"
diventa "Chiudi sessione": il narratore ferma la scena e mette negli appunti i
punti per la via `sessione` (le regole sull'esperienza trovate in `meccanica/`
viaggiano gia' nel messaggio, senza giri di lettura), lo scriba li registra con
`award_xp`. Il totale in coda lo scrive il programma dal registro. Riepilogo e
fili aperti non li scrive il narratore: subito dopo la pagina chiede il
consolidamento della memoria (vedi sotto, livello lento). Poi la conversazione
viene archiviata e il numero di sessione (in `stato.json`) avanza: la sessione
dopo non si apre con il preludio ma con "Sessione N", che riparte da scena,
diario e fili aperti.
Con meno di tre turni e' un rifare da capo: si archivia e basta, senza chiamate,
senza punti e senza far avanzare la sessione.

## Contatore di spesa a schermo

Accanto al modello, in alto, c'e' sempre `media $X/turno · sessione $Y · oggi $Z`;
passandoci sopra: ultimo turno, media di campagna e ripartizione per processo
(gioco, scriba, memoria, scheda, dadi, preparazione). Ogni chiamata lascia una
riga in `sessioni/spese.jsonl` con il costo calcolato dai token che l'API
restituisce in ogni risposta (ingresso, uscita, cache letta a un decimo, cache
scritta al doppio) per il prezzo del modello: e' immediato e per singolo turno.
La media per turno conta narrazione e scriba; memoria, schede e preparazione
entrano nei totali. L'alternativa ufficiale, la Usage & Cost API, richiede una
chiave di amministrazione dell'organizzazione e restituisce costi a giornate:
utile per una verifica a fine mese, non per un contatore in gioco. La console
somma anche cio' che altri programmi spendono con la stessa chiave.

## Narratore e scriba: due chiamate separate

Scrivere sui file costa quanto narrare, parola per parola, e sul modello che narra
l'uscita e' la voce piu' cara: in un preludio reale l'85% del testo prodotto erano
scritture (diario, PNG, glossario, scheda), non racconto. Per questo il turno di
gioco e' diviso in due chiamate che non si parlano:

1. **Il narratore** (il modello scelto per il gioco) ha solo strumenti di lettura.
   Narra, e in coda lascia un blocco `<appunti>` di poche righe telegrafiche:
   diario, cambi di stato con i numeri, PNG e luoghi nuovi, segreti, punti
   esperienza. Il giocatore non lo vede e nel salvataggio non finisce.
2. **Lo scriba** (Haiku 4.5, cinque volte meno caro in uscita; si cambia nelle
   impostazioni) riceve narrazione, appunti, la parte della scheda JSON che cambia
   in gioco (stato, equipaggiamento, esperienza), la scena precedente, gli orologi e
   i fili aperti, e fa tutte le scritture in un messaggio solo. Non porta con se' ne'
   la conversazione ne' il glossario: poche migliaia di token, circa un centesimo.

Lo scriba parte a ogni turno, anche quando gli appunti dicono `nulla`: aggiorna
comunque `scena.md` (`set_scene`), il documento da cui riparte il turno dopo. Se
fallisce il turno resta valido e gli appunti partono con quelli del turno dopo.
Sotto ogni risposta il costo riporta la quota dello scriba. Il costruttore di schede
non usa lo scriba: salva lui, alla conferma del giocatore.

## Due livelli di narrazione: il veloce gioca, il lento ricorda

Il giocato grezzo viene gia' copiato dal programma a ogni turno in
`sessioni/<pg>.jsonl` (archivi compresi): e' la memoria di base, senza chiamate.
Sopra ci sono due livelli che si parlano solo attraverso i documenti della campagna.

- **Veloce** (`master/context.py`): a ogni turno UNA chiamata del narratore, senza
  conversazione alle spalle. Il contesto lo compone il codice come insieme di
  documenti: scheda JSON, `scena.md`, fili aperti, orologi, chi e cosa e' nominato
  (il glossario fa da tabella di collegamento termine -> file e sezione, e per i PNG
  vale la loro memoria), le regole pertinenti e gli ultimi tre scambi parola per
  parola (mai riassunti). Le regole si trovano per titolo anche nelle sottosezioni
  (`### Soggezione` dentro una Disciplina); se l'azione non nomina nessuna regola
  ("salto dal tetto") un instradatore minuscolo sul modello dello scriba sceglie tra
  i titoli di `meccanica/` (frazioni di centesimo, elenco in cache). Il prefisso
  del prompt e' fisso e non si invalida mai; l'indice del glossario non viaggia piu'
  a ogni turno; il costo non cresce con la durata della sessione. La scheda viaggia a due
  velocita': tratti, identita' e storia stanno in un secondo blocco del prompt, in cache
  (si riscrive solo quando il personaggio cresce); stato ed esperienza nel contesto del
  turno. Lo scriba riceve solo stato, equipaggiamento ed esperienza. Al narratore resta
  la lettura di riserva: costa una chiamata in piu' e nel registro compare come
  `LETTURA DI RISERVA`, cosi' si vede quanto spesso la selezione non basta. Lo scriba
  aggiorna `scena.md` a ogni turno (`set_scene`) e lascia nel diario solo appunti
  provvisori, sotto `## Appunti`.
- **Lento** (`master/memory.py`): alla chiusura di una sessione, o dal pulsante
  "Consolida memoria" quando ci sono almeno tre turni non consolidati, un modello
  piu' robusto (Sonnet 5, si cambia nelle impostazioni) rilegge il giocato grezzo
  non ancora consolidato e, in una chiamata con uscita strutturata, produce:
  - la **cronaca** nel diario: unita' di evento di una frase con il turno d'origine
    (`- [t12] ...`), che prendono il posto degli appunti. Non riassunti: comprimere
    fa perdere per primo cio' che il giocatore ha detto e fatto;
  - i **fili aperti** aggiornati;
  - `ambientazione/png-memoria.md`: per ogni PNG cosa sa, cosa crede (anche il
    falso) e cosa vuole;
  - `ambientazione/scena.md`: dove si e' rimasti, chi c'e', la domanda aperta, i
    dettagli da richiamare. L'apertura della sessione dopo la riceve gia' nel
    messaggio, senza giri di lettura;
  - le **incoerenze** tra giocato e documenti, con la lettura da tenere (il giocato
    fa fede), annotate in `avventura.md`.

`stato.json` ricorda fino a che turno si e' consolidato: nulla si ripaga. Se la
chiamata fallisce la sessione si chiude lo stesso e il giocato resta in attesa.
Un inizio rifatto da capo (meno di tre turni, poi "Nuova sessione") finisce in
archivio come `-scartata` e non entra nella memoria. Lo schema segue la ricerca
su agenti a due velocita' e consolidamento a riposo (Talker-Reasoner, sleep-time
compute, Generative Agents) e il risultato di un benchmark su narratori LLM: i
conflitti di fatti sono l'errore piu' comune, e la memoria compressa peggiora
proprio la fedelta' a cio' che ha fatto il giocatore.

## Dadi: li tira il programma, con le regole del manuale

Il master non genera numeri casuali credibili e non deve decidere lui se un'azione
riesce. `master/dice.py` non conosce nessun gioco: sa sommare dadi o contare
successi, e applica un **profilo** in `meccanica/dadi.json` che la preparazione
ricava dalle regole estratte dal manuale (una chiamata a fine preparazione, poi
resta nella cache del manuale: lo stesso manuale non la ripaga). Il profilo dice
che dado si usa, da che valore e' un successo, quanto valgono le coppie di una
faccia, se ci sono dadi speciali il cui numero viene da un valore della scheda,
e quali facce fanno scattare un evento con un nome. Vampiri diventa cosi' riserve
di d10 con i dadi Fame presi da `stato.fame`, critici, critici caotici e
fallimenti bestiali; l'esempio diventa d20 + modificatore contro una soglia. Anche
la parte del prompt che spiega al master come chiedere un tiro e' generata dal
profilo. Per una campagna preparata prima di questa funzione, in pagina compare
"Ricava i dadi dalle regole" (una chiamata, pochi centesimi, una volta).

Il master chiude la narrazione con `[[tiro: Intelligenza + Investigare | riserva |
dadi 5 | difficolta 3]]`; la pagina lo mostra come pulsante; il programma tira e
l'esito (`[Tiro] ... 5d10: 7, 6, 4 | fame: 2, 9 -> 3 successi ... RIUSCITO`)
diventa il messaggio del giocatore. Tirare non costa chiamate. `/tira 2d6+1` nel
campo di testo fa un tiro libero.

## Orologi: i fili che avanzano li conta il programma

Una minaccia o un progetto che matura nel tempo e' un orologio a segmenti in
`stato.json` ("La guardia indaga 3/6", con la conseguenza). Il narratore li
riceve in testa al messaggio del turno (non nel prompt, che resta in cache), li
crea e li fa avanzare con una riga negli appunti, lo scriba esegue con
`set_clock` / `tick_clock`. Quando uno si riempie la conseguenza arriva al
narratore al turno dopo, una volta sola, e l'orologio sparisce.

## Quanto costa un turno, e perche' costa poco

Un turno di gioco e' una chiamata del narratore su un contesto composto dal
codice, piu' lo scriba e, quasi sempre, l'instradatore delle regole. Niente
conversazione che cresce: il costo non dipende da quanto e' lunga la sessione.

- **Cache di un'ora sul prefisso fisso.** Il prompt del narratore (con le regole
  dei dadi) e la parte stabile della scheda sono due blocchi in cache: tra un
  turno e l'altro un giocatore impiega minuti, e con i 5 minuti predefiniti la
  cache scadeva. Il blocco della scheda si riscrive solo quando il personaggio
  cresce; ferite ed esperienza stanno nel contesto del turno e non lo toccano.
- **Il contesto del turno non va in cache**: cambia a ogni turno, scriverlo
  costerebbe il doppio per nulla. E' piccolo per costruzione: al massimo 3 regole
  (2.500 caratteri l'una), 8 entita' (700), gli ultimi 3 scambi.
- **Un file grande non si legge mai intero.** Oltre 24.000 caratteri `read_file`
  senza sezione restituisce l'elenco delle sezioni: un `regole.md` da manuale
  letto intero sarebbero 130.000 token.
- **Il costo reale di ogni turno** compare sotto la risposta e nel registro, con
  token a prezzo pieno, letti dalla cache e scritti in cache.

Misurato sulla campagna di Vampiri con Opus (`sessioni/spese.jsonl`, 2026-09-18):
un turno di narrazione costa da 8 a 17 centesimi di dollaro (17 quando serve una
lettura di riserva), lo scriba circa 1 centesimo, l'instradatore circa 0,2.

### Il costruttore di schede: il ciclo a conversazione

La creazione della scheda e' un dialogo, e usa il ciclo a conversazione
(`master/conversation.py`): il modello chiede le regole con `read_file`, le legge
e salva con `save_sheet`. Li' la conversazione cresce, e tre accorgimenti tengono
fermo il prefisso in cache:

- **Il prompt resta fermo per 8 turni** (`SNAPSHOT_TURNS`): se una scrittura cambia
  glossario o schede a meta' turno, il prompt non cambia e la cache della
  conversazione non si invalida. Prima ogni scrittura faceva riscrivere in cache
  decine di migliaia di token a prezzo doppio.
- **Risposta e scritture nello stesso messaggio** chiudono il turno: l'esito delle
  scritture apre il messaggio del turno dopo. Niente giro in piu' solo per dire "fatto".
- **Lo storico si dimezza, non si lima**, e le letture lunghe dei turni passati
  diventano una riga ("rileggilo se serve").

Il costruttore non porta l'indice del mondo: solo le voci di
`meccanica/creazione-pg.md` e le schede, il resto a portata di `lookup_glossary`.
Prefisso fisso da circa 9.100 a 3.500 token.

## Preludio: la prima scena nasce dal background

Una campagna senza turni giocati non apre su una scena qualunque: nella scheda
Gioca compare la carta "Preludio di <personaggio>", con un campo facoltativo per
tono e desideri. Il master non riceve una scena da copiare ma un metodo
(`PRELUDE_BRIEF` in `master/engine.py`): trovare nel background la **rottura**
(il fatto che ha cambiato la vita del personaggio), scegliere il **punto
d'ingresso** piu' carico subito dopo, renderlo **concreto** (luogo, ora, corpo,
oggetti, nomi presi dalla scheda), rispettare **regole e mondo** del manuale,
lasciare in scena un **motore** (almeno due pressioni che costringono a
scegliere) e **annotare** tutto negli appunti: diario, stato della scheda e i
"Fili aperti di <personaggio>" con cio' che e' successo davvero e chi si muovera'.
Lo scriba li registra (i fili in `ambientazione/avventura.md`), cosi' i turni
successivi hanno una direzione invece di improvvisare dal nulla. Lo stesso principio vale per ogni
scena successiva: nasce dall'ultima, dai fili aperti o dal background.

## Persistenza: la campagna continua da dove era

Il giocato vive in `sessioni/<personaggio>.jsonl`, una riga per turno, solo in
aggiunta: e' insieme la trascrizione completa e cio' da cui si riparte, senza una
seconda copia in Markdown. Chiudendo il programma, tornando al menu o riavviando
il PC non si perde nulla:

- al rientro in una campagna la conversazione ricompare nella pagina (ultimi 40
  turni, con il totale in archivio). Nel gioco il narratore non ha conversazione
  da ricaricare: dal primo turno dopo il rientro il contesto contiene, come
  sempre, scena, fili aperti e gli ultimi 3 scambi parola per parola. Il
  costruttore di schede, che e' un dialogo, ricarica invece gli ultimi 12 turni
  come testo semplice. In nessuno dei due casi tornano tool o ragionamenti dei
  turni passati: lo stato vive nei file, e legherebbero il salvataggio a un
  modello o a una versione;
- ogni personaggio ha la sua conversazione: cambiando personaggio si riprende la sua;
- anche la creazione di una scheda si riprende a meta' (`sessioni/_scheda.jsonl`),
  e riparte pulita dopo il salvataggio della scheda;
- un turno fallito non viene salvato; una riga troncata da una chiusura forzata
  viene ignorata senza impedire la ripresa;
- "Nuova sessione" archivia la conversazione in `sessioni/archivio/` e ne apre una
  vuota: diario, schede e mondo restano;
- nel menu ogni campagna mostra quanti turni sono stati giocati e l'ultima data.

## Sanificazione dei doppioni

Un manuale letto a blocchi lascia sezioni sullo stesso soggetto con nomi
varianti ("Tremere" e "Clan Tremere"), voci di glossario equivalenti e blocchi
ripetuti. Il pulsante "Sanifica doppioni" sulla scheda della campagna (o
`--sanitize [--deep]`) li toglie in due passi:

1. **Gratuito**, eseguito anche da solo alla fine di ogni preparazione: fonde le
   sezioni con titolo uguale a meno di articolo, maiuscole e accenti; toglie i
   blocchi di testo identici nello stesso file; unisce le voci di glossario
   equivalenti tenendo la descrizione piu' completa. Ripetibile: una seconda
   passata non trova nulla.
2. **Approfondito**, con l'API: per ogni file viaggia solo l'elenco dei titoli, il
   modello indica quali trattano lo stesso soggetto (con regole strette: mai un
   argomento con un suo sotto-argomento) e il codice fa la fusione, scartando i
   gruppi non validi. Le sezioni fuse passano poi dal consolidamento per
   sezioni, con stima del costo e cache per contenuto.

## Avvio a un clic: `Avvia.bat`

Doppio clic e si gioca. Lo script controlla Python, crea l'ambiente virtuale
`.venv` e ci installa le dipendenze se mancano (tutto resta dentro la cartella
del progetto), al primo avvio crea il `.env` e apre il Blocco note per la chiave API,
ricorda l'ultima campagna usata (o chiede quale, se ce n'e' piu' d'una) e apre la
UI nel browser. Chiudere la finestra ferma tutto. Da terminale accetta anche
`Avvia.bat NOME` per una campagna precisa e `Avvia.bat NOME --cli` per il terminale.

## Menu completo: `SimpleMaster.bat`

Stessi controlli iniziali, poi un menu:

1. **Gioca**: avvia la UI e apre il browser su http://127.0.0.1:8765 sul
   **menu principale**. Qui si gestisce: l'elenco delle campagne con "Entra" e
   "Prepara da manuale" (il pulsante "Sfoglia" apre Esplora risorse per scegliere
   il PDF; su altri sistemi compare un esploratore nella pagina), la creazione di
   una campagna nuova, le impostazioni di modello ed effort. L'avanzamento di una
   preparazione compare in cima al menu. "Entra" porta al **tavolo di gioco**:
   da dentro una campagna non si modifica nulla della campagna, si gioca
   ("Gioca") o si compila la scheda ("Scheda PG"); il pulsante "Menu" riporta
   fuori.
2. **Prepara da un manuale**: chiede il file (si puo' trascinare nella finestra)
   o un URL, la campagna, la modalita' e le pagine, poi costruisce i documenti.
3. **Riordina il glossario**.
4. **Nuova campagna**: copia dell'esempio o file vuoti.
5. **Installa le dipendenze** (`pip install -r requirements.txt`).

Scorciatoia: trascina un PDF sopra `SimpleMaster.bat` e parte subito la preparazione.

**Modello e ragionamento** si scelgono dalla UI, dal pulsante col nome del
modello in alto: un modello per tutto (Opus 5 predefinito, Sonnet 5, Haiku 4.5,
Fable 5.1, con prezzo indicativo) e un livello di ragionamento (effort:
predefinito, low, medium, high, max) separato per gioco, scheda PG, preparazione
e riordino del glossario. Le scelte si salvano in `impostazioni.json` e valgono
anche ai prossimi avvii. Haiku non accetta livelli di ragionamento: per lui
vengono ignorati. `MASTER_MODEL` e `MASTER_EFFORT` nel `.env` restano come
valori iniziali quando il file delle impostazioni non esiste.

Per chi preferisce il terminale, `python main.py --help` elenca le stesse
funzioni come opzioni (`--cli`, `--prepare`, `--tidy-glossary`, `--new`).

## Preparazione: dal manuale ai documenti

Dato un manuale (PDF anche di 500+ pagine, file di testo o URL), costruisce i
file di campagna e il glossario:

```bash
python main.py --campaign campaigns/mia --prepare manuale.pdf
```

Come funziona (map -> reduce -> consolida):

1. **Blocchi.** Il manuale viene spezzato in blocchi di circa 36.000 caratteri
   (una dozzina di pagine): abbastanza piccoli perche' la trascrizione completa
   delle regole stia nella risposta. In modalita' `testo` (default, economica) si
   estrae il testo con pypdf; con `--mode pdf` si inviano le pagine originali,
   utile per tabelle complesse o PDF scansionati.
   Le pagine senza valore (vuote o quasi solo immagini, indici e sommari) vengono
   scartate prima di spendere token. Prima di partire viene stampata una stima di
   token e costo, calibrata con il conteggio gratuito dei token.
2. **Estrazione.** Per ogni blocco l'API restituisce un JSON con schema forzato:
   sezioni destinate ai file di `meccanica/` e `ambientazione/`, voci di glossario
   e un riassunto. I blocchi mancanti partono insieme con il **Batch API a meta'
   prezzo** (impostazione predefinita; l'esito arriva in minuti, a volte ore, e se
   il processo viene interrotto rilanciandolo si riprende lo stesso batch senza
   ripagarlo). Con `--no-batch` o dall'impostazione si torna alle chiamate
   immediate a prezzo pieno. Il livello di ragionamento predefinito per la
   preparazione e' `medium`: trascrivere non richiede di piu'. Dalle
   impostazioni si puo' scegliere un modello diverso solo per la preparazione,
   per esempio Sonnet 5 a meno della meta' del costo.
   Ogni blocco riuscito viene salvato in una **cache condivisa** alla radice del
   progetto, `cache/manuali/<impronta del file>/…/blocco_NN_pA-B.json`, legata
   al contenuto del manuale e non alla campagna: lo stesso manuale preparato per
   una seconda campagna riusa tutti i blocchi senza chiamate API. Se il processo
   si interrompe, riparte da dove era; un blocco fallito (risposta troncata o
   non valida) non viene salvato e si rifa' al lancio dopo. Le chiamate lunghe
   usano lo streaming, come richiede l'SDK, con un limite di 15 minuti per
   blocco: uno stream che resta muto diventa un blocco da rifare, non un'attesa
   infinita. I blocchi validi lasciati da versioni precedenti nella cartella
   `preparazione/` di una campagna vengono importati nella cache condivisa
   abbinandoli per pagine.
3. **Unione.** Le sezioni entrano nei file con le classi di `files.py` (sezioni con
   lo stesso titolo si accodano), le voci nel glossario. Nasce anche
   `fonte-<nome>.md`: indice per pagine del manuale, per tornare all'originale.
4. **Consolidamento per sezioni.** Solo le sezioni il cui titolo compare in piu'
   blocchi hanno testo accodato da ripulire: vengono inviate a gruppi di circa
   45.000 caratteri (in batch quando i gruppi sono almeno due), e l'esito
   sostituisce solo quelle sezioni. Il resto del file non viaggia e non si paga:
   sul manuale di Vampiri sono 20 sezioni su 355, circa 82.000 caratteri invece
   di 877.000. Ogni sezione ripulita deve conservare almeno un terzo della
   lunghezza, altrimenti resta com'era. Esito in cache per contenuto
   (`cache/manuali/consolidati/`). `--no-consolidate` per saltarlo del tutto.
   La fusione e' ripetibile: rilanciando sulla stessa campagna il testo gia'
   presente non si accoda di nuovo. I titoli `#` e `##` dentro il testo di una
   sezione vengono abbassati a `###`, perche' la sezione e' l'unita' che il
   master legge e aggiorna.

5. **Riordino del glossario.** Un sotto-tool parte alla fine (o da solo con
   `--tidy-glossary`). Prima, senza API: aggiunge una voce per ogni file della
   campagna che non ne ha una, aggiorna quelle dei file gia' indicizzati, toglie
   le voci che puntano a file spariti e segnala quelle il cui termine non compare
   nel file indicato. Poi una chiamata veloce (effort basso) decide cosa tenere e
   cosa cancellare tra doppioni, voci vecchie e sospette. Le descrizioni non
   vengono riscritte, la tabella viene riordinata per tipo. Se l'API volesse
   cancellare piu' di meta' delle voci, il glossario resta com'era.
   `--no-tidy` per saltarlo; `MASTER_TIDY_MODEL` nel `.env` per usare un modello
   diverso solo qui.

Opzioni: `--pages 10-120` per una parte del manuale, `--effort low` per
risparmiare. A fine corsa stampa blocchi, chiamate e token usati.

## Registri (log): dove e perche' si ferma qualcosa

Ogni esecuzione scrive un file in `log/`, uno per giorno, processo e campagna:

```
log/2026-09-17-gioco-esempio.log
log/2026-09-17-preparazione-mia.log
log/2026-09-17-riordino-mia.log
```

Ogni riga ha data, ora, livello e modulo. Dentro ci sono: avvio con versione e
argomenti, ogni chiamata API con modello, effort, esito e token, ogni tool
eseguito con esito, ogni blocco della preparazione, le richieste HTTP dell'SDK
(utili per 429 e 529), e ogni errore con traceback. Quando un processo si
ferma c'e' sempre una riga `INTERROTTO ...` che dice a che punto era (turno e
chiamata, oppure blocco e pagine) e perche' (tipo di errore, codice HTTP,
request-id). Per trovarla: cercare `INTERROTT` o `ERROR` nell'ultimo file.
Una corsa chiusa a forza (finestra chiusa, processo ucciso) non lascia FINE:
all'avvio successivo viene riconosciuta e segnata come `INTERROTTA` con
l'ultima attivita' registrata, cosi' compare nella diagnosi.
Il menu del .bat ha la voce 7 per aprire la cartella e la 8 per elenco e pulizia.

### Rotazione

- Un file che supera 5 MB viene spezzato in `.log.1`, `.log.2`... (fino a 5).
- A ogni avvio i registri piu' vecchi di 7 giorni (o oltre i 40 file) finiscono in
  `log/archivio/AAAA-MM/`; l'archivio oltre i 30 giorni viene cancellato.
  Il file del giorno corrente non viene mai toccato.
- Soglie modificabili nel `.env`: `MASTER_LOG_KEEP_DAYS`, `MASTER_LOG_ARCHIVE_DAYS`,
  `MASTER_LOG_MAX_FILES`, `MASTER_LOG_MAX_MB`.

### Automatismi per Claude Code

Sono configurati in `.claude/settings.json` e `.claude/skills/diagnosi-registri/`:

- **All'apertura di una sessione** di Claude Code gira `--logs diagnose`: le
  interruzioni e gli errori non ancora esaminati vengono messi davanti a Claude,
  con file, numero di riga e contesto. Se non c'e' nulla di nuovo, silenzio.
- **Alla chiusura della sessione** gira la rotazione (`--logs clean`).
- **La skill `diagnosi-registri`** guida Claude quando qualcosa si e' fermato:
  leggere le righe indicate, spiegare la causa, correggere, poi segnare i
  registri come esaminati (`--logs examined`), che li archivia e li toglie
  dalla diagnosi. Il segno di lettura e' in `log/.esaminati.json`, cosi' del
  file di oggi tornano solo le righe nuove.

Comandi sottostanti, per chi vuole usarli a mano (non aprono un nuovo registro):

```bash
python main.py --logs list
```

elenca ogni file con dimensione, righe, numero di errori e interruzioni e, se
fermo, l'ultima riga. Poi:

```bash
python main.py --logs clean
```

esegue la rotazione (con `--log-days N` cambia la soglia);

```bash
python main.py --logs dismiss --log-files 2026-09-10-gioco-esempio.log
```

archivia i file indicati, oppure `--logs dismiss --log-days 3` archivia tutto
cio' che ha piu' di 3 giorni;

```bash
python main.py --logs purge
```

cancella l'archivio (`--log-days N` solo la parte piu' vecchia di N giorni,
`--log-all` anche i registri correnti tranne quelli di oggi).

## Struttura

```
master/
  files.py        CampaignFile: lettura, scrittura atomica, append, sezioni "## Titolo", replace
  glossary.py     Glossary: tabella Markdown con upsert/remove per voce, indice compatto, lookup
  campaign.py     Campaign: cartella sandbox, indice file, giocato, stato.json, spese, orologi
  tools.py        definizione dei tool per l'API e ToolExecutor che li esegue sui file
  sheet.py        Sheet: la scheda come dati JSON, modifiche per percorso, registro dell'esperienza
  conversation.py ConversationEngine: il ciclo a conversazione (tool_use, cache, storico, ripresa)
  character.py    CharacterBuilder: il ciclo a conversazione con prompt e strumenti da costruttore
  engine.py       MasterEngine: il gioco (narratore, scriba, instradatore, preludio, chiusura)
  context.py      compose(): il contesto di ogni turno di gioco, composto dai documenti
  memory.py       Consolidator: il livello lento (cronaca, fili aperti, memoria dei PNG, scena)
  dice.py         tiri e profilo dei dadi ricavato dal manuale
  prepare.py      Preparer: manuale -> blocchi -> JSON strutturato -> file di campagna + glossario
  sanitize.py     Sanitizer: fusione dei doppioni, gratuita e approfondita
  tidy.py         GlossaryTidier: allinea il glossario ai file, poi lo pulisce e ordina con una chiamata veloce
  models.py       modelli, prezzi, calcolo dei costi, impostazioni salvate
  explorer.py     scelta del PDF da Esplora risorse o dalla pagina
  logbook.py      registro con marca temporale in log/: chiamate API, tool, blocchi, errori con traceback
  ui.py           pagina web minimale (http.server) e API JSON
main.py           avvio, .env, CLI
campaigns/esempio/   glossario.md + meccanica/, ambientazione/, schede/kael.json
tests/               un file per area, tutti senza rete (client finto o risposte preparate)
```

## Test

Con il Python del `.venv` (i `.bat` lo creano; a mano: `python -m venv .venv`
poi `.venv\Scripts\python -m pip install -r requirements.txt`):

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```
