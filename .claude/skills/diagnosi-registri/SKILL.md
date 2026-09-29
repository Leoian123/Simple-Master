---
name: diagnosi-registri
description: Capire dove e perche' un processo di Simple Master (gioco, scheda PG, preparazione manuale, riordino glossario) si e' fermato, leggendo i registri in log/, e poi scartare in modo ordinato i registri gia' esaminati. Usare quando l'utente dice che qualcosa si e' bloccato, non ha funzionato, si e' interrotto, o quando l'hook di inizio sessione segnala interruzioni non esaminate.
---

# Diagnosi dei registri di Simple Master

I processi scrivono in `log/AAAA-MM-GG-<processo>-<campagna>.log`. Ogni riga ha
data, ora, livello, modulo. Le righe chiave contengono `INTERROTT` (dove si e'
fermato: turno e chiamata API nel gioco, blocco e pagine nella preparazione),
`ERROR`/`CRITICAL` con traceback, `api ->`/`api <-` (modello, effort, stop_reason,
token) e le righe `httpx` con gli status HTTP (429, 529...).

## Procedura

1. Elenca cio' che non e' ancora stato esaminato:
   `python main.py --logs diagnose`
   (all'inizio di ogni sessione gira da solo tramite hook; se non stampa nulla,
   non ci sono interruzioni nuove: allora `python main.py --logs list` mostra
   tutti i file con errori e interruzioni).
2. Leggi le righe indicate con lo strumento Read (offset alla riga, ~40 righe):
   il contesto prima della riga `INTERROTT` dice cosa stava facendo; il traceback
   dopo dice perche'.
3. Spiega la causa all'utente in poche frasi e correggi il codice se e' un bug
   (poi `python -m unittest discover -s tests`). Se e' un errore esterno (429,
   529, rete, chiave mancante) dillo e indica cosa fare.
4. Scarta i registri esaminati, cosi' non tornano in diagnosi:
   `python main.py --logs examined --log-files <nome>...` oppure `--log-all`.
   Il file di oggi resta in uso (avanza solo il segno di lettura); gli altri
   finiscono in `log/archivio/`.

## Regole

- Non cancellare mai registri a mano: usa `examined` (archivia) o `purge`
  (svuota l'archivio) cosi' resta traccia ordinata.
- Il file del giorno corrente e' in uso dai processi: non spostarlo.
- Il codice del registro e della rotazione e' in `master/logbook.py`;
  la rotazione automatica gira da sola a ogni avvio dei processi e alla
  chiusura della sessione di Claude Code (hook SessionEnd).
