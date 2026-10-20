# Le relazioni dei PNG

<!-- Sezione GENERATA da `python main.py --doc --rigenera`: non modificarla a mano, cambia il codice che la produce. -->

Ogni PNG ha un atteggiamento verso il personaggio da -100 a 100, in `stato.json` sotto `png`:

```json
"png": {"Mira Sol": {"relazione": -23}}
```

Il numero lo possiede il codice (`Campaign.shift_relation`). Il narratore non lo vede mai: nel contesto
del turno ogni PNG porta l'etichetta del suo STADIO accanto al tipo (`### Mira Sol (png · Diffidente)`),
e lo scriba non scrive numeri: dice il verso e la forza, il codice applica il passo e clampa.

## La scala degli stadi

Predefinita: Nemico (-100..-81) · Ostile (-80..-41) · Diffidente (-40..-11) · Neutrale (-10..10) · Cordiale (11..40) · Amico (41..80) · Devoto (81..100).

Un manuale con i suoi nomi la sovrascrive con `meccanica/relazioni.json`:

```json
{"stadi": [{"nome": "Ostile", "da": -100, "a": -21}, {"nome": "Neutrale", "da": -20, "a": 20}, {"nome": "Amico", "da": 21, "a": 100}]}
```

Gli stadi devono tassellare -100..100 senza buchi ne' sovrapposizioni, con nomi unici
(`relazioni.stadi_problems`, la regola usata anche dalla verifica). Un file che non la rispetta viene
ignorato (valgono i predefiniti) e la verifica lo segnala come errore.

## Il passo

| forza | passo |
|---|---|
| `lieve` | 3 |
| `media` | 8 |
| `forte` | 15 |

`sale` aggiunge, `scende` toglie; il valore resta nella scala. Un PNG mai contato parte da 0 (Neutrale).
Il nome si cerca senza distinzione di maiuscole fra i PNG gia' contati.

## Chi la muove

- Lo scriba, con `shift_relationship`, solo per la riga `- relazione: <png> sale|scende (lieve|media|forte) — motivo`
  degli appunti del narratore (sezione `appunti`); mai di propria iniziativa.
- Il consolidamento della memoria (`memory.py`): per ogni PNG toccato dice `sale`, `scende` o `invariata`,
  e il codice applica un passo `media`; solo per interazioni dirette e sostanziali.
- Nessun altro: il narratore e il costruttore di schede non hanno il tool.

### `shift_relationship`

Muove l'atteggiamento di un PNG verso il personaggio di un passo, in su o in giu'. Il numero lo tiene il codice e te lo restituisce come stadio (Diffidente, Cordiale...): tu dici solo il verso e la forza. Solo per la riga `relazione` degli appunti del narratore, mai di tua iniziativa.

| parametro | tipo | obbligatorio | significato |
|---|---|---|---|
| `name` | string | si | Il PNG, con il nome esatto tra quelli che ricevi (o nuovo) |
| `verso` | string (`sale`, `scende`) | si |  |
| `forza` | string (`lieve`, `media`, `forte`) | si | lieve: un gesto; media: un aiuto o un torto concreto; forte: una svolta |
| `reason` | string | si | Perche', in una riga |

Rifiutato: nome vuoto, `verso` o `forza` fuori vocabolario (anche se lo schema li ammetterebbe come testo).

## Dove si legge

- Nel contesto del turno (`context.compose`): l'etichetta accanto a ogni PNG nominato, mai il numero.
- Nel contesto dello scriba e del consolidamento: `RELAZIONI DEI PNG: Mira Sol: Diffidente (-23); ...`.
- `Campaign.relation(nome)` -> intero o None; `relations_line()`; `relazioni.stadio(valore, stadi)`.
