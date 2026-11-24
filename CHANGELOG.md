# Changelog

Modifiche rilevanti di ATLAS, dalle piu recenti.

## 2026-11-24

- Backend: helper date condivisi `days_between`, `start_of_day`, `week_start` e `to_iso_z`.
- Backend: nuova relazione `collabora_con` nel grafo; saluti ("Ciao", "Grazie", ...) esclusi dalle entita candidate.
- Test unitari aggiunti per embeddings, generatori (Ollama simulato), predizioni, contraddizioni, router e credenziali.

## 2026-11-19

- Backend: nuovi pattern relazionali nel grafo (`studia_a`, `abita_a`, `fa_parte_di`).
- Backend: `extract_relations()` estratta come funzione pura e coperta da test.
- Backend: piu avverbi e indicazioni temporali esclusi dalle entita candidate
  ("Forse ...", "Ecco ...", "Stasera ...").
- Backend: "Progetto X" non viene piu classificato come persona.
- Backend: i promemoria ("ricordami di ...", "non dimenticare ...") si salvano
  come nuova informazione invece di essere classificati come azioni.
- Backend: il classifier riconosce "dimmi ..." come domanda.
- Backend: numeri con punto migliaia ("1.000") letti bene nel rilevamento
  contraddizioni: "1.000 euro" e "1000 euro" non sono piu un falso conflitto.
- Test: copertura unitaria pura per entities, contradictions ed embeddings.
- Backend: matematica delle stime (`_interval`) e curva di calibrazione
  (`calibration_buckets`) estratte come funzioni pure e coperte da test
  (brier_score, chiave settimana ISO incluse).
- Backend: `_choose_bucket_days` e `_gap_runs` della time machine estratte
  come funzioni pure e testate.
- Backend: nuovo endpoint `GET /graph/stats` con i conteggi del grafo
  (entita e relazioni per tipo/stato), coperto da test su DB reale.
- Backend: validazione delle memorie estratta in `validate_memory_input`
  (pura) e coperta da test.
- Backend: `LocalHashEmbedder` rifiuta dimensioni non positive invece di
  fallire con una divisione per zero al primo testo.
- Test: copertura pura per LocalHashEmbedder ed ExtractiveGenerator.
- Backend: il helper `_aware` (naive = UTC), copiato in tre servizi, vive
  ora in `app/services/dates.py` come `aware()` condiviso e testato.
- Backend: anche l'Action Engine usa il helper condiviso `aware()`.
- Test: fallback locale del ModelRouter e forma d'errore API stabile.
