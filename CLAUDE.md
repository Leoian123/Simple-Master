# Simple Master — come si lavora qui

Master minimalista per campagne di gioco di ruolo, guidato da Claude. Python puro, stato in
file Markdown/JSON dentro `campaigns/<nome>/`. Il README e' la descrizione completa; la
roadmap e' `ROADMAP.md` (una fase = un commit, nell'ordine scritto li').

## Prima di toccare un dato, leggi la sua sezione

`python main.py --doc` elenca le sezioni; `--doc <sezione>` ne stampa una. **Se un campo non e'
nella sezione, non esiste**: non inventare chiavi, percorsi o strumenti.

| se tocchi... | leggi |
|---|---|
| `schede/*.json`, `sheet.py`, il costruttore di schede, `update_sheet` | `--doc scheda` |
| gli appunti del narratore, `MASTER_PROMPT`, `SCRIBE_PROMPT`, lo scriba | `--doc appunti` |
| `tools.py`, `ToolExecutor`, un tool nuovo | `--doc strumenti-scriba` |
| `dadi.json`, `dice.py`, il marcatore `[[tiro: ...]]` | `--doc dadi` |
| `stato.json`, `sessioni/`, orologi e scadenze, fili aperti e la loro eta' (`fili.py`), memoria consolidata, `campaign.py` | `--doc stato` |
| le relazioni dei PNG, `relazioni.py`, `shift_relationship`, `meccanica/relazioni.json` | `--doc relazioni` |
| i modi di gioco, `modi.py`, il selettore 'Modo', `meccanica/modi.json` | `--doc modi` |
| `glossario.md`, `glossary.py`, `context.py`, `tidy.py` | `--doc glossario` |
| le cartelle e i file di una campagna, `files.py`, i fatti da non perdere (`fatti.py`) | `--doc campagna` |
| `validate.py`, i livelli della verifica | `--doc verifica` |

Le sezioni `scheda`, `appunti`, `strumenti-scriba`, `dadi`, `relazioni` e `modi` sono generate dal codice
(`master/docs.py`): se cambi uno schema, un tool o un prompt lancia `--doc --rigenera` e
committa i file in `docs/sezioni/` insieme al codice (`tests/test_docs.py` lo pretende).

## Invarianti (non si rinegoziano)

- I dadi li tira il programma con il profilo del manuale; il narratore non inventa esiti.
- Il narratore non scrive file: lascia appunti. Lo scriba scrive per percorsi chiusi; l'esperienza
  e' un registro, mai un totale. Ogni dato nuovo nasce con un proprietario (codice o scriba).
- Il costo del turno resta piatto: nessuna chiamata in piu' per turno; cio' che cambia spesso va
  nel contesto del turno, cio' che e' stabile nel prompt in cache.
- Il ruleset e' un input (il manuale), non una costante del codice: default + override in `meccanica/`.
- La chiave API vive solo in `.env`/ambiente: mai in argomenti, log, prompt, documenti.

## Verifica

```bash
.venv/Scripts/python -m unittest discover -s tests
```

Tutti i test sono offline (client finto o risposte preparate): un test che chiama la rete e'
un errore. Un campo nuovo in `dadi.json`, `stato.json` o nella parte fissa della scheda vuole
una riga nella tabella di `tests/test_mutazioni.py` (cosa fa il codice, cosa dice il
validatore) e, se ha una forma, un controllo in `validate.py`. Prima di committare: suite verde, `python main.py --validate` muto,
`python main.py --doc` senza avviso di sezioni non aggiornate. All'apertura di una sessione
girano da soli `--logs diagnose` (interruzioni nei registri, skill `diagnosi-registri`) e
`--validate` su tutte le campagne.

## Stile

Italiano, apostrofo ASCII al posto dell'accento (`e'`, `perche'`, `citta'`) nel codice, nei
prompt e nei documenti. Docstring che dicono il perche', non il cosa. Nessuna dipendenza nuova.
Le campagne ricavate da manuali commerciali restano locali: nel repo c'e' solo `campaigns/esempio/`.
