# Il glossario

`glossario.md` e' la tabella di collegamento fra i termini e i file: dice in una frase cos'e'
un'entita' e in quale file stanno i dettagli. E' cio' che fa restare piccolo il contesto del
turno: entrano solo le sezioni delle entita' nominate.

## Il file

```markdown
| Termine | Tipo | Descrizione | File |
|---|---|---|---|
| Mira Sol | png | Locandiera della Lanterna Blu, sa tutto di tutti | ambientazione/npc.md |
```

- Una riga per termine; il `|` dentro una cella si scrive `\|`.
- `Tipo`, i valori che il riordino conosce e con cui ordina la tabella:
  `regola, fonte, file, pg, png, creatura, fazione, luogo, razza, classe, oggetto, evento, cronaca, altro`.
  Lo scriba puo' usarne altri; il riordino li normalizza.
- `File` e' il percorso dentro la campagna. Il contesto del turno cerca il termine in quel file
  e porta la sezione che lo contiene; se il termine non compare nel file la voce e' "sospetta"
  (il riordino lo segnala, la verifica lo da' come avviso).
- Le voci di tipo `file`, `fonte` e `cronaca` non vengono cercate nel file: descrivono il file stesso.

## Come entra nel prompt

- **Nel gioco** il glossario non viaggia nel prompt: `context.compose` cerca ogni termine (almeno
  4 caratteri, non di tipo `pg`) prima nelle parole del giocatore, poi nella scena, poi nell'ultima
  narrazione, e porta nel contesto al massimo 8 entita' (700 caratteri l'una) e 3 regole (2.500).
  Per i PNG, se `png-memoria.md` ha una sezione con quel nome, entra quella invece del file.
  Con lo stesso criterio entrano le righe di `da-non-perdere.md` che il turno tocca (sezione `campagna`).
- **Nel costruttore di schede** entra intero sotto gli 8.000 caratteri; sopra, solo un indice
  compatto (termini per tipo e file, senza descrizioni) e le descrizioni si chiedono con
  `lookup_glossary`, i dettagli con `read_file(file, sezione)`.

## Chi lo scrive

- Lo scriba con `upsert_glossary` per i "nuovo" degli appunti (al massimo due a turno).
- Il costruttore con `save_sheet` (voce di tipo `pg` verso `schede/<slug>.md`).
- La preparazione da manuale per ogni blocco estratto; una voce definita da piu' manuali tiene
  descrizione e file del piu' vecchio; le voci che c'erano gia' restano.
- Il riordino (`--tidy-glossary`): senza API aggiunge una voce `file` per ogni file senza voce,
  aggiorna quelle dei file gia' indicizzati, toglie le voci verso file spariti, segnala le sospette;
  poi una chiamata veloce decide cosa tenere fra doppioni, voci vecchie e sospette (se volesse
  cancellare piu' di meta' delle voci, il glossario resta com'era).
- "Sanifica doppioni" unisce le voci equivalenti tenendo la descrizione piu' completa.

Nel codice: `Glossary.entries()`, `get(term)`, `upsert(GlossaryEntry)`, `remove(term)`,
`prompt_text(only_files)`, `lookup(query, limit)`.
