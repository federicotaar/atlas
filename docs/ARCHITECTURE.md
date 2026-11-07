# Architettura (fase 0)

```
UTENTE (CLI / chat React / API)
        |
   ATLAS Core (POST /ask)
        |  1. classifica (question | new_info | action_request | analysis)
        |  2. recupera memorie (retrieval ibrido)
        |  3. risponde citando, oppure "Non lo so"
        v
 Memory Engine ---- Model Router (embed/generate)
        |                |-- LocalHash (offline, default)
        |                |-- Ollama (opzionale)
        v
 PostgreSQL 16 + pgvector
 (users, sources, projects, events append-only, memories, action_logs)
```

## Recupero ibrido

Punteggio finale = `w_semantic*semantico + w_text*testuale + w_recency*recenza + w_confidence*confidenza`

- **semantico**: `1 - cosine_distance` (pgvector `<=>`)
- **testuale**: `ts_rank` di PostgreSQL (config `simple`), normalizzato con
  `min(1, rank*4)`: su frasi brevi il rank grezzo è piccolo (tipicamente
  < 0.25); il fattore 4 porta una corrispondenza piena vicino a 1. Costante
  empirica da calibrare sul benchmark (vedi `scripts/benchmark.py`).
- **recenza**: decadimento esponenziale con emivita configurabile (default 30g)
- **confidenza**: valore 0..1 memorizzato

La **rilevanza** (`w_semantic + w_text`, senza prior di qualità) guida il gate
«Non lo so»: se nessun risultato supera `ATLAS_ASK_MIN_RELEVANCE` (default
0.12), il sistema risponde «Non lo so». Recenza e confidenza riordinano ma non
possono mai rendere "pertinente" una memoria che non c'entra.

I pesi sono configurabili via env (`ATLAS_W_*`).

## Sostituzione, non sovrascrittura

Quando un'informazione cambia si crea una nuova memoria e la vecchia riceve
`superseded_by`. Le memorie sostituite escono dal recupero normale ma restano
nel database: sono la base della Time Machine (fase 5).

## Eventi append-only

Nessun endpoint di update/delete su `/events`. La cancellazione esiste solo
come diritto all'oblio completo (`DELETE /me`).

## Model Router

`embed()` e `generate()` con provider intercambiabili:

| Provider | Uso | Costo | Note |
|---|---|---|---|
| `local` / `extractive` | default | 0 | deterministico, offline, qualità lessicale |
| `ollama` | opzionale | 0 (locale) | richiede Ollama con i modelli scaricati |

Se Ollama non risponde, il router fa fallback sui provider locali e lo
registra in `action_logs` (modello, durata, token stimati, costo).

## Isolamento utenti

Ogni query filtra per `user_id`. `tests/test_isolation.py` verifica che
l'utente B non possa leggere, cercare, citare, sostituire o esportare dati
dell'utente A.
