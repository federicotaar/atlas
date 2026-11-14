# Come ATLAS viene testato

Riepilogo onesto di cosa gira dove, cosi un contributore sa cosa aspettarsi
prima di aprire una PR. Fonte: `.github/workflows/ci.yml` (aggiornato al
2026-11-14).

## In CI (a ogni push che tocca codice)

1. **Backend su Postgres reale**: service container `pgvector/pgvector:pg16`,
   `pytest -q` con provider modelli locali (deterministici, gratuiti).
2. **Lint**: `ruff check backend/app backend/tests` (le migrazioni Alembic
   sono escluse: file generati).
3. **Migrazioni**: `alembic upgrade head`, `downgrade base`, di nuovo
   `upgrade head` su un database pulito.
4. **Benchmark di recupero**: `scripts/benchmark.py` su database dedicato.
5. **Job Ollama reale**: installa Ollama e verifica i provider semantici
   (embeddings + LLM) contro un servizio vero, non un mock.
6. **Frontend**: test Vitest e build TypeScript (`tsc -b && vite build`).
7. **Compose e smoke desktop end-to-end**.

## In locale, senza servizi esterni

Il backend gira anche su SQLite (sqlite-vec al posto di pgvector):

```bash
cd backend
pip install -e ".[dev]"
ATLAS_DATABASE_URL=sqlite:////tmp/atlas_test.db pytest -q
```

Verificato il 2026-11-14: 249 test verdi, 13 saltati (richiedono Postgres
o un modello Ollama locale). I test saltano da soli con un messaggio chiaro
quando il database non e' raggiungibile, quindi una suite rossa in locale
quasi mai significa codice rotto: prima controllare `ATLAS_DATABASE_URL`.

## Cosa NON e' coperto

- I connettori Google e WhatsApp reali: validati solo con fixture sintetiche.
- L'accuratezza longitudinale delle previsioni: serve utilizzo reale di mesi.
