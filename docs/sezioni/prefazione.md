# La prefazione delle regole

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

Un manuale smontato in sezioni dice le regole, non come orientarsi fra loro. La sezione `## Come usare queste regole`
in testa a `meccanica/regole.md` lo dice, in tre liste con titoli fissi; il prompt di sistema la copia (blocco in cache),
quindi il narratore la legge a ogni turno senza che costi. L'instradatore delle regole non la propone (e' gia' li').

## Le tre liste

- `### Precedenza`: elenco numerato, da chi vince a chi cede, quando due fonti dicono cose diverse.
- `### Cerca nei file, non a memoria`: una riga per rimando, nella forma esatta `- <argomento>: `<file>` § <sezione>`: cio' che va riletto
  nei file e non ricordato (numeri, costi, tabelle). La verifica rilegge queste righe e segnala come avviso un
  rimando a un file o a una sezione che non esiste.
- `### Se una regola non si trova`: cosa fare in gioco quando una regola non si trova (quale tiro generico, quando decidere senza tiro).

Al massimo 12 voci per lista. Si puo' scrivere a mano, rispettando i tre titoli e la forma dei rimandi.
La verifica consiglia di scriverla quando le regole hanno almeno 5 sezioni: sotto, si leggono intere.

## Da dove viene

`extract_preface` (pulsante 'Scrivi la prefazione' nel tavolo, `POST /api/preface/extract`; alla fine della
preparazione di un manuale, in cache per contenuto dell'indice): una chiamata con l'indice dei file di regole, i tipi
di tiro e le tabelle che il programma gestisce, schema forzato:

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `precedenza` | elenco di string | si | In ordine, da chi vince a chi cede: quale fonte conta quando due dicono cose diverse (scheda, un file, una sezione, le regole generali) |
| `cercare` | elenco di object | si | Cio' che il master deve rileggere nei file invece di ricordare: numeri, tabelle, costi, liste Ogni elemento: `argomento`, `file` (Un file dell'indice, esatto), `sezione` (Un titolo di sezione di quel file, esatto). |
| `riserva` | elenco di string | si | Cosa fare in gioco quando una regola non si trova nei file: la lettura di riserva, nei termini di questo gioco |

Il codice tiene nei rimandi solo file e sezioni che esistono davvero (titolo ricopiato esatto), scarta il resto
(registrato nel log) e scrive la sezione in testa alle regole, sostituendo quella precedente.

Nel codice: `prefazione.leggi(campaign)`, `prefazione.prompt_section(campaign)`, `prefazione.broken_lookups(campaign)`,
`prefazione.scrivi(campaign, corpo)`.
