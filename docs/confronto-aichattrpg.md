# Confronto con AiChatTrpg: parser dei manuali e profilo dei dadi

Data: 2026-10-20. Studio 7 della roadmap. Letto il repository (`github.com/Leehow/AiChatTrpg`,
Apache-2.0, 9 stelle, ultimo push 2026-05-28, "pre-alpha, active development"): README,
`CLAUDE.md`, il parser `backend/agents/trpg/rule_parser_v6/`, il motore dei tiri
`backend/agents/trpg/check_ir/`, `check_engines.py`, `check_fallbacks.py`, `dice_roller.py`,
`check_spec_extractor.py`, i marcatori `framework/markers/`. Nessun codice eseguito: il loro
parser vuole PyMuPDF (AGPL) o MinerU e una chiave API per ogni stadio; il confronto e' sul
disegno, non su una corsa sul manuale di Vampiri.

**In una riga.** E' l'unico progetto open che fa la nostra stessa cosa (manuale PDF -> dati ->
master che tira i dadi col codice). Sul parser siamo piu' solidi (blocchi, cache, paternita',
piu' manuali); sul profilo dei dadi loro coprono famiglie che noi non esprimiamo (roll-under con
soglie derivate, "tieni il piu' alto", piu' assi per tiro) e hanno un'idea da prendere subito:
**il profilo si valida eseguendo campioni**. Da qui una fase nuova, la 1b.

---

## 1. Cos'e' AiChatTrpg

- Master AI per giochi da tavolo, "bring your own LLM" (OpenAI-compatibili, Gemini, Claude),
  FastAPI + PostgreSQL + React, locale per default. Un turno = `preprocess -> generate ->
  postprocess`; **niente function calling**: il modello emette marcatori testuali
  (`[ROLL expr=...]`, `[SET_SCENE]`, `[UPDATE_MEMORY bucket= op= key=]`, `[CREATE_NPC]`,
  `[UPDATE_PC]`, `[ADVANCE_TIME]`...) che il codice interpreta a fine turno.
- Memoria: stato JSON strutturato (scene, PNG, fili) + narrativa compressa dal modello; recupero
  "scene-aware" (la scena decide quali regole e sezioni del modulo entrano nel prompt).
- Il "dice IR" e i motori dei tiri sono dichiarati "hard requirement, cannot be bypassed".
- Lo sviluppo e' guidato da agenti (Codex + Claude Code, "Team Lead Mode"): i prompt sono lunghi e
  curati, il codice e' giovane (nello stadio 8 del parser un `logger.info` cita una variabile
  `audit` mai definita: `NameError` dopo una compilazione riuscita).

## 2. Il parser dei manuali

### 2.1 Come fanno loro (`rule_parser_v6`)

Otto stadi su due "viste" del libro (testo piano; testo con righe numerate `[L000123]`):

| stadio | ingresso | uscita | chiamate |
|---|---|---|---|
| 1 `core_rules` | **tutto il libro** in un messaggio | `core_rules.md`: un "resident core" fisso in 15 sezioni (termini, risoluzione, confronti, tempo, valori derivati, creazione, progressione, combattimento, esplorazione, sociale, abilita', recupero, downtime, eccezioni, mini-tabelle) scritto come **manuale operativo per un GM-LLM**, con "Lookup required" dove un catalogo e' troppo lungo | 1 |
| 2 `companion` | tutto il libro | un file di ambientazione/tono | 1 |
| 3 `discovery` | libro numerato + core + companion | `knowledge_manifest.json`: elenco di *pacchetti di regole dettagliate*, *famiglie di parametri* (cataloghi, tabelle casuali, listini, tabelle di generazione), scheda-personaggio si'/no, e per ognuno `line_hints` (righe del libro da rileggere) | 1 (JSON stretto, un retry) |
| 4 `packages`, 5 `families` | solo le righe indicate dai `line_hints` | un file per pacchetto / per famiglia; le famiglie hanno uno **schema per voce** (`id, name, aliases, family, summary, tags, rule_context` + campi di tabella: `roll_notation, roll_min, roll_max, result`, `price, currency`...), tetto ~250 voci, altrimenti `should_remain_markdown` | 1 per elemento, in parallelo |
| 6 `character_sheet` | righe indicate | template + guida di compilazione | 1 |
| 7 `final_manifest` | tutto | riduttore puro, indice "on demand" dentro il core | 0 |
| 8 `dice_ir` | tutto il libro | le procedure di tiro compilate (vedi §3) | 1 + fino a 3 per procedura |

Regole di fedelta' nei prompt: nessuna invenzione, nessuna frase di incertezza ("probably",
"source unclear"), discrezione esplicita ("GM judgment. No strict formula is defined."), **nessuna
citazione di pagina o riga nell'uscita** (le righe numerate servono solo a cercare), tabelle
riscritte in punti. Checkpoint per sotto-passo, un contatore di token condiviso, nessuna cache
per contenuto visibile.

### 2.2 Come facciamo noi (`prepare.py`, `lineage.py`, `resolve.py`, `sanitize.py`, `tidy.py`)

Blocchi di ~36.000 caratteri -> per blocco un JSON con schema forzato (sezioni destinate a
`meccanica/` e `ambientazione/`, voci di glossario, riassunto), in batch a meta' prezzo, con
**cache condivisa per impronta del manuale** (lo stesso manuale non si ripaga mai); unione nello
spazio del manuale; consolidamento solo delle sezioni spezzate su piu' blocchi; composizione in
ordine canonico fra piu' manuali con **paternita' per sezione e per voce**, rifiuto di un'altra
edizione, risoluzione "quale testo vale" dove due manuali si toccano (con controlli del codice e
quarantena); `fonte-<manuale>.md` con l'indice per pagine; sanificazione dei doppioni; riordino
del glossario; profilo dei dadi (§3).

### 2.3 Cosa fanno meglio loro, cosa facciamo meglio noi

| tema | loro | noi | giudizio |
|---|---|---|---|
| unita' di lavoro | il **libro intero** in una chiamata per core e companion (serve contesto lungo; nessuna ripresa a meta'; costo pieno a ogni rilancio) | blocchi, batch, cache per contenuto, ripresa da dove era | **noi**: e' la parte piu' matura del nostro progetto |
| cosa esce | un **manuale riscritto per il GM** (15 sezioni fisse, procedure in ordine di esecuzione, "lookup required") | **le sezioni del manuale** com'erano, ripulite; il master legge la regola, non una parafrasi | diverso per scelta: loro ottimizzano il prompt, noi la fedelta'. La loro Sezione 0 ("precedenza delle regole, cosa va cercato, fallback in gioco") e' un'idea buona anche per noi come *prefazione generata* di `meccanica/regole.md` |
| indice a due livelli | `discovery` + `line_hints`: il modello pianifica cosa rileggere | glossario come tabella di collegamento + `read_file(sezione)` + instradatore minuscolo | pari; il loro e' pianificato una volta, il nostro scelto a ogni turno |
| tabelle e cataloghi | **famiglie di parametri** con schema per voce (`roll_min/roll_max/result`, `price/currency`): le tabelle casuali diventano dati interrogabili | tabelle come prosa dentro le sezioni (`oggetti.md`, `creature.md`); il master le legge | **loro**: e' il pezzo da prendere per le tabelle casuali e i listini (vedi §4) |
| piu' manuali | niente: un libro per volta, nessuna paternita', nessuna edizione | spazio per manuale, ordine canonico, risoluzione dei conflitti, quarantena | **noi**, nettamente |
| provenienza | vietata nell'uscita per disegno | `fonte-*.md` per pagine, paternita' per sezione | **noi** |
| scheda | template + guida generati dal libro | costruttore a conversazione sulle regole di `creazione-pg.md`, dati JSON | pari; il loro template generato potrebbe dare al nostro costruttore i *gruppi* della scheda in anticipo |
| estrazione dei dadi | stadio dedicato (§3), con campioni | una chiamata, profilo dati | vedi §3 |

## 3. Il profilo dei dadi contro il "dice IR"

### 3.1 Loro: due strati

**Motori fissi** (`check_engines.py`): `coc_7e_d100` (soglie *derivate dal valore*: difficile =
skill//2, estremo = skill//5, fumble 96+ sotto 50 altrimenti 100; gradi ordinati con rango),
`brp_d100` (speciale = skill//5), `d20_vs_dc` (20 naturale critico, 1 fumble, totale >= CD),
`pbta_2d6` (10+ pieno, 7-9 parziale, altrimenti miss), `pool_count_n` (successi = dadi >= soglia
per dado, successi richiesti, critico opzionale), `custom_llm` (**non risolve**: "resolution
deferred to narration"). Un estrattore a parse-time sceglie il motore e le manopole leggendo il
core; un router per alias (`check_fallbacks.py`) manda "san/sanity" a una procedura di Sanita'.

**IR generico** (`check_ir/`, `generic_resolution_v1`): una procedura e' un programma dichiarativo:
`input_contract` (argomenti richiesti/facoltativi), `steps` (`read_rolls` NdS, `tally/count`,
`sum_values`, `max_value`, `formula`, `conditional_value`, `select_keep` tieni i piu' alti/bassi,
`change_die_to_value`, `remove_matches`, `weighted_success_count` con pesi per successo/critico/
complicazione, `beat_count`, `degree_count` = gradi per passo oltre la soglia, `resolve_dice_value`
tiri ausiliari come la perdita di Sanita'), `result_axes` (assi: `pass_fail`, `tier` con esiti
ordinati e un default, `flags`, `resource_delta/complication/value`), `labels`. Esempi nel repo:
CoC (1d100 sotto la skill con soglie derivate), d20 vs CD, PbtA 2d6, Forged in the Dark (N d6,
**decide il dado piu' alto**: 6 = pieno, 4-5 = con conseguenze, due 6 = critico), 2d20 (roll-under
a due dadi, successi pesati, complicazione sul 20, "momentum" dai successi in eccesso).

**Come nasce**: il compilatore (`compile_direct.py`) chiede al modello l'elenco delle procedure del
libro, poi per ciascuna l'IR **insieme a dei campioni** (`samples`: `args` + `expected`:
`primary_tier`, `passed`, effetti); il codice **esegue l'IR sui campioni** e se il risultato non
coincide rimanda errore e storia al modello, fino a 3 tentativi; chi non passa finisce in
`unsupported_procedures` e si dichiara. Il `DiceRoller` ha seme, `kh/kl`, dadi bonus/malus di CoC,
traccia per dado; "manual resolution required" quando serve una scelta del giocatore.

### 3.2 Noi: dati, non programmi

`dadi.json`: tipi di tiro `somma` (NdS + bonus contro soglia, `vantaggio`) o `successi` (conta i
dadi >= `successo_da`), `dadi_fissi`, `coppie` che valgono successi in piu', `speciali` presi
dalla scheda al posto dei normali (la Fame), `eventi` per faccia condizionati all'esito e ai soli
dadi speciali, `nome_zero_successi`, e da oggi `esiti` (fasce per margine con guida). Una chiamata
lo ricava; `roll` lo applica; `esiti_problems` e la verifica ne controllano la forma. Niente
interprete, niente programma scritto da un modello: il profilo e' un *dato* con pochi campi, e
cio' che non si esprime si scrive per esteso (`[[tiro: x | 2d6+1 | difficolta 8]]`).

### 3.3 Cosa il loro IR esprime e il nostro profilo no

1. **Roll-under con soglie derivate dal valore** (CoC/BRP/Delta Green/WFRP/Mythras): riesce se il
   dado e' <= valore della scheda, con gradi a frazioni del valore (1/2, 1/5) e fumble oltre una
   soglia. Il nostro `somma` confronta `>=` una difficolta': non si esprime senza riscrivere la
   regola a mano a ogni tiro.
2. **Tieni il piu' alto / il piu' basso** oltre al nostro `vantaggio` a due dadi: FitD (decide il
   dado piu' alto, l'esito e' *per valore del dado*, non per margine), 2d20kh1, pool "il migliore".
3. **Piu' assi per tiro**: successo + complicazione (20 sul d20 in 2d20), risorsa generata
   (momentum). Noi abbiamo un solo verdetto + `eventi` per faccia: la complicazione si puo' gia'
   scrivere come evento, il "momentum" no.
4. **Tiri ausiliari** nella stessa procedura (la perdita di Sanita' dopo il tiro).
5. **Gradi per passo** (`degree_count`: ogni 5 punti oltre la CD un grado) e' il nostro `esiti` a
   fasce, che pero' e' piu' espressivo (nomi e guida).
6. **Campioni di validazione** eseguiti dal codice: non e' una regola in piu', e' il metodo.

Cosa noi esprimiamo e loro no (ne' nei motori ne' negli esempi): dadi speciali presi da un valore
della scheda che sostituiscono i normali, con eventi solo su quelli; coppie che valgono successi
extra; eventi per faccia condizionati a riuscito/fallito/critico; fasce con guida narrativa;
`nome_zero_successi`. Nessuno dei due ha i **dadi esplosivi** (Savage Worlds, L5R) ne' i **tiri
contrapposti** come primitivo (loro `contest_engine.py` esiste ma e' fuori dall'IR; noi lo
lasciamo alla nota del profilo: "la difficolta' sono i successi dell'avversario").

### 3.4 Giudizio

Il loro IR e' piu' potente perche' e' un linguaggio: ma e' un programma scritto da un modello e
interpretato a runtime, con un motore `custom_llm` che "rimanda la risoluzione alla narrazione".
E' esattamente il confine che noi non attraversiamo (il narratore non inventa esiti; i dati non
sono codice). Il valore da portare a casa non e' l'interprete: sono **tre forme di tiro** che
mancano al profilo e **il controllo per campioni**, che da noi costa zero chiamate in piu'
(i campioni escono dalla stessa chiamata che ricava il profilo) e rende il profilo verificabile
offline come tutto il resto.

## 4. Cosa importare (in ordine)

1. **Campioni nel profilo** (`esempi`): per ogni tipo di tiro il modello da' 2-4 casi
   `{marcatore, dadi: [facce fisse], atteso: {valore, esito, riuscito, eventi}}`; `extract_profile`
   li esegue con `dice.roll` a dadi fissi prima di salvare e, se uno non torna, rifa' la chiamata
   con l'errore (una volta), poi salva comunque **segnando i campioni falliti**; la verifica li
   riesegue (zero chiamate) e li segnala come errore. Il profilo smette di essere "quello che il
   modello ha capito" e diventa "quello che il codice ha confermato".
2. **`tipo: "sotto"`** (roll-under): riesce se il dado <= `valore` (dalla scheda, per percorso, o
   dal marcatore), con `soglie` derivate (`[{"nome": "estremo", "frazione": 0.2}, {"nome":
   "difficile", "frazione": 0.5}]`) e `fallimento_critico_da` (96 sotto 50, altrimenti 100: lo
   dice il manuale). Copre CoC, BRP, Delta Green, WFRP, Mythras, RuneQuest.
3. **`tieni`** sul tipo `somma` e `successi`: `{"quanti": 1, "quale": "alto"|"basso"}`
   generalizza `vantaggio`; con `esiti_su: "dado"` (le fasce si leggono sul dado tenuto, non sul
   margine) si esprime Forged in the Dark e il 2d20 di base.
4. **`esplode_su`**: una faccia che fa ritirare e sommare (Savage Worlds, L5R, Cyberpunk RED sul
   10). Cinque righe in `_roll_sum`.
5. **Tabelle casuali come dati** (dal parser, famiglie di parametri): `meccanica/tabelle.json`
   con `{"nome", "dado", "righe": [{"da", "a", "risultato"}]}` ricavate dalla preparazione dove il
   manuale ha tabelle "1d100: 01-05 ..."; un marcatore `[[tabella: <nome>]]` tirato dal codice.
   Vale per incontri casuali, tesori, effetti collaterali: oggi il master le legge in prosa e le
   interpreta.
6. **Prefazione generata** di `meccanica/regole.md` (la loro Sezione 0): precedenza fra regole,
   cosa va cercato nei file e non ricordato, fallback in gioco. Una chiamata alla fine della
   preparazione, una sezione nuova in testa alle regole.
7. **Alias dei tipi di tiro** (`alias: ["percezione", "test"]`) per far atterrare marcatori scritti
   con il nome che il manuale usa in quel passo; piccolo.

### Cosa non importare

- L'interprete di procedure (`steps`/`result_axes`) e il motore `custom_llm`: dati, non programmi;
  il narratore non risolve mai.
- Il libro intero in una chiamata per il core: costo, nessuna ripresa, nessuna provenienza.
- I marcatori liberi `[UPDATE_PC]`/`[UPDATE_MEMORY]` senza validazione dei campi: lo scriba con
  percorsi chiusi e i gate (fase 0.4) sono gia' piu' stretti.
- PyMuPDF (AGPL) come dipendenza di default: pypdf ci basta e il nostro `--mode pdf` copre le
  tabelle difficili.

## 5. Dove finisce nella roadmap

Una **Fase 1b, "Profilo dei dadi v2"**: i punti 1-4 (campioni, `sotto`, `tieni`, `esplode_su`) in
`dice.py` + `PROFILE_SCHEMA` + `PROFILE_PROMPT`, con righe nella tabella di mutazione e la
verifica che riesegue i campioni; i punti 5-6 sono della preparazione (`prepare.py`) e vanno in una
fase a parte quando si tornera' sui manuali. Il manuale di Vampiri e' il banco di prova: oggi il
suo profilo si esprime per intero; con la v2 si prova un manuale a d100 (Delta Green o CoC) e uno
a "dado piu' alto" (Blades).
