# Roadmap di Simple Master — cosa prendere da Voyage, in che ordine

Data: 2026-10-20. Deriva dalla gap analysis `DCC/docs/dm-ai/08-gap-analysis-voyage.md`
(§5, voci P1 e P3, più le voci P2 che hanno senso senza un motore di combattimento). DCC
riceverà le stesse idee più avanti, sul suo piano `07`; qui si lavora solo su Simple Master.

## I vincoli che ogni fase rispetta (sono quelli del README)

- **I dadi li tira il programma**, con il profilo ricavato dal manuale: nessuna fase cambia
  le regole del gioco, ne cambia solo la *resa* o il *conteggio*.
- **Il narratore non scrive file**; lo scriba scrive per percorsi chiusi; l'esperienza è un
  registro. Ogni dato nuovo nasce con un proprietario (codice o scriba) e un percorso chiuso.
- **Il costo del turno resta piatto**: nessuna fase aggiunge una chiamata al turno di gioco.
  Ciò che cambia spesso va nel contesto del turno, ciò che è stabile nel prompt in cache.
- **Il ruleset è un input** (il manuale), non una costante: dove Voyage fissa una tabella nel
  motore, qui la tabella ha un default *e* un posto in `meccanica/` da cui il manuale può
  sovrascriverla.
- **Tutto offline nei test** (client finto o risposte preparate). Una fase = un commit.

Lezioni di Voyage che NON entrano: numeri scelti dall'AI (`impact`), regole in prosa che
nessuno applica, condizioni semantiche giudicate dall'AI a ogni turno, script nel contenuto,
embeddings per la lore, riscrittura delle parole del giocatore.

---

## Fase 0 — Processo: validare, documentare a sezioni, testare in modo avversariale

Nessun cambio al gioco. È la parte che migliora il lavoro con Claude Code da subito.

### 0.1 `--validate`: la campagna passa o non passa, con tre livelli — FATTO (2026-10-20)
> `master/validate.py`, `--validate [--json]`, pulsante "Verifica" nel menu, hook
> `SessionStart`, `tests/test_validate.py` (28 test). Sulla campagna di Vampiri ha trovato
> 25 voci di glossario che puntano a PNG mai scritti in `npc.md`: lo scriba indicizza senza
> scrivere la scheda del PNG (un caso per la fase 0.4, lo scriba ostile).
- **Lezione.** Il wiki community di Voyage espone `POST /validate` che ritorna `errors`
  (il mondo non carica), `warnings` (può rompersi in gioco), `recommendations` (opzionali),
  ciascuno con `path`, `title`, `fix`. Gli agenti lavorano a ciclo «edita → valida → correggi».
- **Dove.** Nuovo `master/validate.py`; comando `python main.py --campaign X --validate`
  (JSON con `--json`, testo altrimenti); pulsante «Verifica campagna» accanto a «Sanifica
  doppioni» in `master/ui.py`; voce nell'hook `SessionStart` di `.claude/settings.json`
  dopo `--logs diagnose`, muta se tutto è verde.
- **Cosa controlla (riuso di ciò che c'è).** `tidy.py` (voci di glossario che puntano a file
  spariti, termini assenti dal file) → *warnings*; `lineage.py` (sezioni senza paternità,
  quarantene aperte) → *warnings*; `dice.load_profile` (profilo che non rispetta
  `PROFILE_SCHEMA`, `quanti_da` che non esiste nella scheda attiva) → *errors*;
  `sheet.py` (registro XP con totali scritti a mano, percorsi `esperienza` toccati) →
  *errors*; `stato.json` (orologi fuori 2..12, `consolidato` oltre i turni esistenti) →
  *errors*; `scena.md` assente con turni giocati, sezione «Fili aperti di <pg>» mancante →
  *recommendations*.
- **Lucchetti.** `tests/test_validate.py`: una campagna d'esempio pulita è verde; ogni
  controllo ha un caso che lo fa scattare; il JSON ha sempre `counts` + le tre liste.
- **Fatto quando.** `esempio` e `Vampire` passano senza errori; il report dice in una riga
  cosa correggere e dove.

### 0.2 Doc a sezioni per l'agente — FATTO (2026-10-20)
> `master/docs.py`, `--doc [sezione] [--rigenera]`, `docs/sezioni/` (otto sezioni: quattro
> generate da `PROFILE_SCHEMA`, `TOOLS` e dai prompt, quattro a mano), `CLAUDE.md` con la
> tabella "se tocchi X leggi Y", `tests/test_docs.py` (12 test). Trovato leggendo
> `campaigns/Vampire/ambientazione/scena.md`: lo scriba ha passato `presenti` come stringa e
> `write_scene` l'ha spezzata in lettere ("J, o, n, a, t, h, a, n"): un caso per la 0.4.
- **Lezione.** `agent-setup.md` + `/sections/<key>.md`: l'agente carica solo la sezione che
  gli serve e vale la regola *«se un campo non è nella doc della sezione, non esiste»*. Il
  nostro README è 690 righe e si legge intero.
- **Dove.** `docs/sezioni/*.md`, una per ogni dato con uno schema: `scheda.md` (il JSON di
  `schede/<pg>.json` e i percorsi ammessi da `update_sheet`), `dadi.md` (generato da
  `PROFILE_SCHEMA` in `dice.py`, mai scritto a mano), `appunti.md` (le righe che il
  narratore può lasciare e chi le esegue), `strumenti-scriba.md` (da `tools.TOOLS`),
  `stato.md` (`stato.json`: pg attivo, sessione, orologi, consolidato), `campagna.md`
  (le tre cartelle e i file fissi). Comando `python main.py --doc <sezione>` che le stampa;
  le due generate si rigenerano con `--doc --rigenera` e un test le confronta col codice.
- **Lucchetti.** `tests/test_docs.py`: le sezioni generate coincidono con gli schemi; ogni
  tool di `TOOLS` ha una voce; ogni sezione citata in `CLAUDE.md` esiste.
- **Fatto quando.** `CLAUDE.md` di Simple Master dice «prima di toccare X leggi
  `--doc X`» e le sezioni coprono scheda, dadi, appunti, strumenti, stato, campagna.

### 0.3 Differential testing dei file di campagna — FATTO (2026-10-20)
> `tests/test_mutazioni.py`: 81 mutazioni su dadi.json, stato.json e scheda, tabella con i due
> verdetti attesi (codice / validatore), invariante "rifiuta ⇒ errore" misurata sulla realta',
> copertura dei campi, casi `oltre` dichiarati. Una sola previsione smentita e corretta: due
> tipi di tiro omonimi fermano il secondo marcatore, il codice rifiuta davvero.
- **Lezione.** Il wiki Voyage costruisce un mondo sintetico, muta un campo alla volta e
  pretende che decoder e validatore concordino (1.820 mutazioni, 0 disaccordi).
- **Dove.** `tests/test_mutazioni.py`: dalla campagna d'esempio, per ogni campo di
  `schede/*.json`, `dadi.json`, `stato.json` applica una mutazione tipica (tipo sbagliato,
  valore fuori range, chiave in più, chiave mancante) e verifica che caricamento
  (`Sheet`, `load_profile`, `Campaign._state`) e `--validate` diano lo stesso verdetto.
- **Fatto quando.** Nessun disaccordo; un campo nuovo senza mutazione è un rosso.

### 0.4 Lo scriba ostile — FATTO (2026-10-20)
> `tests/test_scriba_ostile.py`: 26 tentativi schema-validi in un solo giro, tutti respinti,
> nessuna traccia nei file, turno valido; piu' la controprova con le stesse scritture fatte
> bene. Tre gate nuovi nel codice: `upsert_glossary` rifiuta un termine che non compare nel
> file (i 25 PNG fantasma di Vampire), `set_scene` rifiuta `presenti`/`da_richiamare` non
> elenco (la scena a lettere), `award_xp`/`spend_xp` passano solo se gli appunti hanno la
> riga `pe:` (e mai per la via `creazione`).
- **Lezione.** `trpg-drama-engine` testa i contratti LLM con un modello finto *ostile ma
  schema-valido*: rispetta lo schema e prova a violare le regole.
- **Dove.** `tests/test_scriba_ostile.py`: un client finto che risponde con `tool_use`
  validi ma scorretti — `update_sheet` su `esperienza.*`, `award_xp` senza riga `pe`
  negli appunti, `tick_clock` su un orologio inesistente, `append_file` sul `.md` di una
  scheda, `set_section` fuori da `avventura.md`, due `set_scene`, `set_scene` con `presenti`
  stringa invece di elenco (visto su Vampire: la scena mostrava le lettere del nome),
  `upsert_glossary` verso un file in cui il termine non viene scritto (i 25 avvisi di
  Vampire) — e la verifica che ogni
  tentativo torni `is_error` e lasci i file intatti (`ToolExecutor` in `tools.py`,
  `_run_scribe` in `engine.py`).
- **Fatto quando.** Ogni violazione elencata è respinta e il turno resta valido.

---

## Fase 1 — Esiti graduati: dal RIUSCITO/FALLITO alla scala con guida narrativa — FATTO (2026-10-20)
> `tiri[i].esiti` nel profilo (schema, prompt di estrazione), `DEFAULT_ESITI` per riserve e
> somme, `esiti_problems` condivisa fra dadi e verifica, il `[Tiro]` chiude con il nome della
> fascia e la guida, il prompt elenca le fasce per tipo. Scelta: le fasce sono per tipo di
> tiro, non globali, perche' un margine di 3 successi non vale un margine di 3 su un d20.

- **Lezione.** Voyage mappa il punteggio su 7 tier (`critical failure … critical
  success`), ciascuno con un `prompt` che arriva al narratore (con `{{characterName}}`), e
  un `firstSuccessfulTier` che dice da dove comincia il successo. Oggi `dice.roll` dà
  `RIUSCITO (margine N)` / `FALLITO (mancano N)` più eventi in maiuscolo: il margine c'è,
  la scala no, e la guida narrativa è lasciata al narratore.
- **Dove.** `dice.py`: `PROFILE_SCHEMA` guadagna `esiti` (lista di fasce: `nome`,
  `da_margine`, `a_margine`, `guida` una frase, `riuscito: bool`); `extract_profile` le
  ricava dal manuale (per V5: fallimento totale / fallito / vittoria costosa (≥1 successo
  ma sotto la difficoltà) / riuscito / margine ampio / critico) con un **default** quando
  il manuale tace (fallito / riuscito di misura / riuscito / riuscito bene); `roll` mappa
  il margine (o i successi senza difficoltà) sulla fascia e il testo `[Tiro]` diventa
  `… -> 3 successi contro difficoltà 2: RIUSCITO DI MISURA (margine 1). Guida: …`.
  `prompt_section` aggiunge una riga: «l'esito ha un nome e una guida: narra quello».
- **Chi è proprietario.** Il codice (fascia e guida vengono dal profilo su disco). Zero
  chiamate: la fascia viaggia nel messaggio `[Tiro]`, non nel prompt.
- **Lucchetti.** `test_dice.py`: le fasce tassellano i margini senza buchi né
  sovrapposizioni (stessa regola di validazione di Voyage); profilo senza `esiti` → default;
  un tiro senza difficoltà non ha fascia; `--validate` segnala fasce non tassellanti come
  *error*.
- **Fatto quando.** Sul profilo di Vampiri il tiro con 1 successo e difficoltà 3 dice
  «VITTORIA COSTOSA possibile» invece di «FALLITO (mancano 2)», e la guida è quella del
  manuale.

---

## Fase 2 — Relazioni dei PNG: un numero per PNG, stadi nominati, delta a enum — FATTO (2026-10-20)
> `master/relazioni.py` (scala, passo, override da `meccanica/relazioni.json`), `stato.json` ->
> `png`, tool `shift_relationship` (enum, mai un numero), etichetta nel contesto del turno,
> consolidamento che muove di un passo medio, verifica + 20 righe di mutazione + 4 tentativi
> ostili, sezione generata `--doc relazioni`.

- **Lezione.** Ogni PNG di Voyage ha un punteggio −100..100 verso il giocatore, stadi
  nominati che tassellano la scala (Nemesi … Devoto), delta solo su interazione diretta,
  e la **label** dello stadio passata all'AI. Oggi `png-memoria.md` ha sa/crede/vuole, e
  l'atteggiamento vive solo nella prosa.
- **Dove.**
  - Dato: `stato.json` → `png: {"<nome>": {"relazione": int}}` (proprietario: codice).
  - Scala: `master/relazioni.py` con la tabella default degli stadi (vocabolario chiuso,
    tassellante); `meccanica/relazioni.json` opzionale per sovrascriverla da manuale (un
    gioco con i «Legami» o la «Fama» ha i suoi nomi), validata come le fasce dei dadi.
  - Scriba: tool nuovo `shift_relationship(name, verso: sale|scende, forza:
    lieve|media|forte)`: **enum, non numero**; il codice mappa in ±3/±8/±15 e clampa.
    Riga di appunti del narratore: `- relazione: <png> sale (media) — motivo`. Regola nel
    `SCRIBE_PROMPT`: mai di propria iniziativa, solo con la riga; regola nel
    `MASTER_PROMPT`: solo per interazione diretta e sostanziale, non per complimenti.
  - Contesto: `context.compose` scrive accanto a ogni PNG in gioco `(png · Diffidente)`;
    il consolidatore (`memory.py`) può proporre un movimento per PNG toccato, con lo stesso
    enum, nello schema `png` (campo `relazione: sale|scende|invariata`).
- **Lucchetti.** `test_relazioni.py`: clamp, tassellatura, stadio per ogni valore; lo
  scriba ostile (0.4) prova `shift_relationship` con un numero e viene respinto; il
  contesto mostra la label.
- **Fatto quando.** Un PNG aiutato tre volte cambia stadio senza che nessuno abbia scritto
  un numero, e la label compare nel contesto del turno dopo.

---

## Fase 3 — Fili e orologi che non restano eterni — FATTO (2026-10-20)
> Orologi con `scadenza_turni`/`creato_turno` (`set_clock(..., deadline)`, `expire_clocks` in
> `_table_state`, "(il tempo e' passato)", resto visibile in `clocks_line`); fili aperti con
> `[sN]` messi da `master/fili.py` a ogni scrittura (scriba e consolidamento), `stale_threads`
> consegnati all'apertura e al consolidamento, consiglio nella verifica; 6 righe di mutazione,
> 2 tentativi ostili, `tests/test_scadenze.py`.

- **Lezione.** Gli eventi narrativi di Voyage hanno `targetTurns`, force-complete a 2× e
  uno stato `sospeso` con ripresa che conserva il contatore; le quest scadono per tick,
  per uscita dal luogo o morte del committente. I nostri orologi avanzano solo se il
  narratore li tocca; i fili aperti sono prosa senza data.
- **Dove.**
  - Orologi (`campaign.py`): campo opzionale `scadenza_turni`; `set_clock` lo accetta;
    a ogni turno `_table_state` (in `engine.py`) conta i turni dalla creazione e, superata
    la scadenza senza che l'orologio sia pieno, lo **riempie da sé** e consegna
    `[OROLOGIO PIENO: … (il tempo è passato)]`. Appunti: `- orologio: nuovo "<nome>", 6
    segmenti, entro 12 turni, conseguenza: …`.
  - Fili aperti: restano prosa in `avventura.md` (non si rifanno in JSON), ma il
    consolidatore riceve per ogni riga il turno in cui è comparsa (`[t12]`, come la
    cronaca) e lo schema `fili_aperti` guadagna la voce `stantii`: fili non toccati da
    N sessioni vengono proposti come «da chiudere o pagare dal destino»; l'apertura della
    sessione successiva li riceve come `[FILI STANTII: …]` una volta sola.
- **Lucchetti.** `test_master.py`: un orologio con scadenza si riempie da solo al turno
  giusto e una sola volta; senza scadenza nulla cambia (regressione); `test_memory.py`:
  un filo marcato `[t3]` non toccato per due sessioni finisce fra gli stantii.
- **Fatto quando.** Nessun filo sopravvive silenziosamente più di due sessioni; gli
  orologi con scadenza scattano anche se il narratore se ne dimentica.

---

## Fase 4 — Modi di gioco come lente (non come regole)

- **Lezione.** I Game Modes di Voyage sono una «lente»: istruzioni al narratore +
  istruzioni ai PNG + difficoltà, scelti dal giocatore, che non cambiano il contenuto. Da
  noi la difficoltà sta nel manuale e non si tocca; la lente sì.
- **Dove.** `impostazioni.json` → `modo` per campagna (`stato.json.modo`): `nome`,
  `istruzioni` (tono, ritmo, quanto concedere alle soluzioni improbabili, quanto sono
  letali gli errori), `png` (inclinazioni: diffidenti, generosi…). Tre modi di default
  (quieto / classico / spietato). Entra come blocco **in cache** del prompt di sistema
  (`MasterEngine.system_blocks`), dopo le regole dei dadi; cambia solo quando il
  giocatore lo cambia. Selettore nella UI accanto al modello.
- **Lucchetti.** `test_settings.py`: il modo persiste; `test_fast.py`: il prefisso del
  prompt resta byte-identico tra due turni con lo stesso modo.
- **Fatto quando.** Due campagne sullo stesso manuale si giocano con toni diversi senza
  toccare `meccanica/`.

---

## Fase 5 — Memoria: «cosa non perdere» e cronaca più fedele

- **Lezione.** `summarization.custom` di Voyage è una lista di fatti che la condensa
  non deve perdere (promesse, gradi, ferite, evoluzioni), scelti pensando a «cosa sarebbe
  stonato se un PNG lo dimenticasse».
- **Dove.** `ambientazione/da-non-perdere.md` (facoltativo, poche righe); `memory.py`
  lo appende a `MEMORY_PROMPT` e lo passa anche al narratore nel contesto quando il
  turno nomina uno dei suoi termini (stessa selezione del glossario in `context.py`). Il
  consolidatore può proporre aggiunte (campo `da_non_perdere` nello schema, al massimo
  due per corsa), che il codice accoda; la rimozione è solo a mano.
- **Lucchetti.** `test_memory.py`: il file entra nel prompt; una proposta oltre il tetto è
  ignorata; senza file nulla cambia.
- **Fatto quando.** Una promessa fatta nella sessione 1 è nel contesto della sessione 4
  anche se il glossario non la indicizza.

---

## Fase 6 — Trigger come dati: le regole del manuale che il codice può contare

- **Lezione.** Il vero linguaggio del World Engine sono i trigger: condizioni strutturali
  sui fatti (risorsa, relazione, stadio, tick) → effetti tipizzati (istruzione one-shot al
  narratore, modifica di un contatore). Niente AI, niente script: dati.
- **Perché dopo 1-3.** Le condizioni utili leggono la scheda (percorsi di `Sheet`), gli
  stadi di relazione (fase 2), gli orologi (fase 3) e le fasce degli esiti (fase 1).
- **Dove.** `meccanica/trigger.json`: lista di `{nome, quando: [{percorso|orologio|
  relazione|esito, op, valore}], effetto: {avviso: "…"} | {orologio: nome, +1} |
  {relazione: png, sale|scende, forza}, ricorrente: bool}`. Valutati dal codice a ogni
  turno in `_table_state`, **prima** della chiamata del narratore: l'avviso arriva come
  `[TRIGGER <nome>: …]` nella testa del messaggio (come gli orologi pieni), gli altri
  effetti si applicano e si registrano nel log. Esempi su Vampiri, scritti a mano: «fame
  ≥ 4 → avvisa: rischio di frenesia secondo le regole»; «umanità scesa → orologio "La
  Bestia" +1». In una fase successiva `extract_profile` potrà proporre trigger dal
  manuale, come oggi fa per i dadi; prima si scrivono a mano.
- **Vincoli.** AND-only, al massimo 5 condizioni e 3 effetti, nessuna condizione sul
  testo, nessuno script; `--validate` controlla percorsi e nomi.
- **Lucchetti.** `test_trigger.py`: un trigger non ricorrente scatta una volta; uno
  ricorrente ogni turno in cui vale; percorso inesistente = errore di validazione; senza
  file nulla cambia.
- **Fatto quando.** La regola della frenesia arriva al narratore dal contatore della
  scheda, non dalla sua memoria.

---

## Fase 7 — Studio: AiChatTrpg a confronto (un pomeriggio, nessun codice)

- `Leehow/AiChatTrpg` (Apache-2.0) fa la nostra stessa cosa: manuale PDF → JSON
  strutturato, «Dice IR» con motori intercambiabili (d100, d20 vs DC, PbtA 2d6, pool).
  Provarlo sul manuale di Vampiri e annotare in `docs/confronto-aichattrpg.md`: cosa
  estrae meglio di `prepare.py`, cosa il suo Dice IR copre che `dadi.json` non copre
  (pool con soglie variabili? PbtA 6-/7-9/10+?), cosa importare in `PROFILE_SCHEMA`.
- Esito atteso: due o tre voci da aggiungere alla fase 1 o al profilo, oppure la
  conferma che il nostro profilo basta.

---

## Ordine, dipendenze, stima

```
Fase 0 (processo)  ──►  Fase 1 (esiti)  ──┐
                        Fase 2 (relazioni) ├──►  Fase 6 (trigger)
                        Fase 3 (scadenze) ─┘
                        Fase 4 (modi)      indipendente
                        Fase 5 (memoria)   indipendente
Fase 7 (studio)  in qualunque momento, meglio prima della 1
```

| Fase | File nuovi / toccati | Chiamate per turno | Stima |
|---|---|---|---|
| 0.1 validate | `validate.py`, `ui.py`, `main.py`, hook | 0 | 1 sessione |
| 0.2 doc a sezioni | `docs/sezioni/`, `main.py`, `CLAUDE.md` | 0 | mezza sessione |
| 0.3 mutazioni | `tests/test_mutazioni.py` | 0 | mezza sessione |
| 0.4 scriba ostile | `tests/test_scriba_ostile.py` | 0 | mezza sessione |
| 1 esiti | `dice.py`, `validate.py` | 0 (profilo: la stessa chiamata di oggi) | 1 sessione |
| 2 relazioni | `relazioni.py`, `tools.py`, `engine.py`, `context.py`, `memory.py` | 0 | 1-2 sessioni |
| 3 scadenze | `campaign.py`, `engine.py`, `memory.py` | 0 | 1 sessione |
| 4 modi | `models.py`, `engine.py`, `ui.py` | 0 | mezza sessione |
| 5 memoria | `memory.py`, `context.py` | 0 | mezza sessione |
| 6 trigger | `trigger.py`, `engine.py`, `validate.py` | 0 | 1-2 sessioni |
| 7 studio | `docs/` | — | mezza giornata |

Metriche da tenere a ogni fase, lette da `sessioni/spese.jsonl` e dai log: costo medio per
turno invariato (8-17 centesimi con Opus su Vampiri), zero `LETTURA DI RISERVA` in più,
suite verde e offline.

## Cosa resta fuori di proposito
- Multiplayer e DM seat (Voyage): fuori dall'identità di Simple Master.
- Generazione di PNG/luoghi a runtime con schema: il narratore già li inventa negli
  appunti e lo scriba li registra; non serve un secondo canale.
- Un motore di combattimento: le regole le applica il narratore col manuale; l'eventuale
  motore è il lavoro di DCC.
- Mods e layering di mondi: `preparazione/fonti/<manuale>/` con la composizione in ordine
  canonico è già il nostro modo di sovrapporre fonti.
