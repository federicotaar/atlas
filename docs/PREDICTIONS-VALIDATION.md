# Validazione del Prediction Engine

Risponde alla nota "Prediction Engine: implementato, non validato": qui
l'estimatore viene misurato con uno script ripetibile
(`scripts/validate_predictions.py`), non solo eseguito nei test.

## Metodo (dichiarato)

Per ogni regime sintetico lo script semina una storia nota di completamenti
settimanali in un database SQLite temporaneo, chiede la stima al vero
`estimate_completion`, poi confronta il tempo reale di completamento (da una
velocita' futura dichiarata del regime) con la forcella stimata.

Limiti dichiarati: i regimi sono sintetici. La validazione misura che il
meccanismo calcoli bene cio' che dichiara di calcolare e dove il suo modello
lineare si rompe; NON misura l'accuratezza su dati reali, che richiede mesi
di utilizzo vero.

## Risultati (2026-11-21)

| Regime sintetico | Stima (bassa-centrale-alta) | Reale | Errore | Forcella copre |
|---|---|---|---|---|
| costante 3/settimana | 3.0-3.0-3.0 sett. | 3.0 sett. | 0.0 | si |
| lenta 1/settimana | 4.0-4.0-4.0 sett. | 4.0 sett. | 0.0 | si |
| a singhiozzo (1/5) | 1.8-3.0-9.0 sett. | 3.0 sett. | 0.0 | si |
| in crescita (futuro piu' veloce) | 1.9-2.9-5.6 sett. | 1.4 sett. | 1.5 | **no** |
| in calo (futuro piu' lento) | 1.9-2.9-5.6 sett. | 10.0 sett. | 7.1 | **no** |
| troppo poca storia | rifiutata (dati troppo pochi) | — | — | — |

Sintesi: 5 regimi stimabili, forcella che copre il reale in 3/5, errore
medio della stima centrale 1.72 settimane. Regole base rispettate: rifiuto
esplicito con pochi dati, forcelle sempre ordinate.

## Cosa dicono i fallimenti (onesto)

Il modello assume velocita' futura simile alla media osservata: quando il
ritmo cambia (accelerazione o rallentamento) la stima centrale sbaglia e la
forcella non copre il reale. E' il limite dichiarato del modello lineare:
le stime vanno lette con le assunzioni registrate in ogni previsione, e la
verifica alla scadenza (`evaluate_due`) misura l'errore reale invece di
nasconderlo.
