# La cartella di una campagna

Una campagna e' una cartella sotto `campaigns/`. Tutto cio' che il master sa sta li' dentro;
il programma non tiene stato altrove, salvo `impostazioni.json` (modelli ed effort, alla
radice del progetto) e la cache dei manuali (`cache/manuali/`, condivisa fra campagne).

```
campaigns/<nome>/
  glossario.md          indice: una riga per entita', con il file dove stanno i dettagli (sezione `glossario`)
  stato.json            personaggio attivo, sessione, orologi, memoria consolidata (sezione `stato`)
  meccanica/            REGOLE: numeri, tiri, tabelle. Il master le applica alla lettera.
    regole.md             come si gioca (sotto 80 caratteri la campagna e' "da preparare")
    creazione-pg.md       come si crea un personaggio (il costruttore legge solo questo)
    oggetti.md            statistiche di armi e oggetti
    creature.md           statistiche di mostri e creature
    dadi.json             il profilo dei dadi ricavato dalle regole (sezione `dadi`)
  ambientazione/        MONDO: cio' che il master narra e puo' inventare.
    mondo.md              luoghi, fazioni, storia
    npc.md                personaggi non giocanti
    avventura.md          trame, ganci, segreti; la sezione "Fili aperti di <pg>"
    diario.md             cronologia: cronaca `- [t12] ...` dal consolidamento, `## Appunti` provvisori dello scriba
    scena.md              dove si e' rimasti (riscritta dallo scriba a ogni turno)
    png-memoria.md        per ogni PNG: cosa sa, cosa crede, cosa vuole (dal consolidamento)
  schede/               una scheda per personaggio giocante: <slug>.json i dati, <slug>.md la vista generata (sezione `scheda`)
  sessioni/             il giocato: <slug>.jsonl una riga per turno, archivio/, spese.jsonl, _scheda.jsonl (non indicizzato)
  preparazione/         cache della preparazione da manuale: paternita.json, fonti/<manuale>/ (non indicizzato)
```

## Regole dei file

- Sono ammessi solo `.md` e `.txt`, dentro la cartella: percorsi assoluti, `..` e altre estensioni
  vengono rifiutati (`Campaign.resolve`). I JSON (`stato.json`, `dadi.json`, le schede) li legge
  e scrive solo il codice, mai uno strumento dell'AI per nome.
- Un file Markdown e' fatto di sezioni `## Titolo`: la sezione e' l'unita' che il master legge
  (`read_file` con `section`), che lo scriba aggiorna (`set_section`) e che la preparazione
  compone. I titoli `#` e `##` dentro il corpo di una sezione vengono abbassati a `###`.
- Le scritture sono atomiche (file temporaneo + rename). `append_file` crea il file se manca.
- `sessioni/` e `preparazione/` non entrano nell'indice dei file ne' nel glossario.
- `Campaign.files()` elenca i file testuali in ordine di percorso; `Campaign.index()` da' per ogni
  file le prime 12 sezioni (e' cio' che il costruttore e il riordino vedono).

## Cosa indicizza il glossario

Ogni file della campagna ha una voce di tipo `file` (il riordino la aggiunge da solo); le entita'
(PNG, luoghi, oggetti, fazioni, regole) hanno la loro voce con il file dove stanno. Vedi `glossario`.

## Creare una campagna

`python main.py --new campaigns/mia` copia l'esempio (Velmora); con `--empty` crea i file
vuoti del modello (`campaign.TEMPLATE_FILES`). Dal menu: "Nuova campagna". Le campagne
ricavate da manuali commerciali restano locali (`.gitignore`); nel repository c'e' solo `esempio/`.
