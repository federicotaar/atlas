# atlas-core

Backend ATLAS: FastAPI + SQLAlchemy, Postgres/pgvector in produzione, SQLite
(sqlite-vec) per sviluppo e test locali. Panoramica del progetto nel README
principale del repository.

## Struttura

- `app/` — codice: `main.py` (assembly FastAPI), `routers/` (API),
  `services/` (logica: orchestratore, retrieval, embeddings, agenti,
  previsioni), `models.py` (tabelle), `schemas.py` (validazione I/O),
  `cli.py` (CLI `atlas`).
- `alembic/` — migrazioni dello schema (generiche, escluse dal lint).
- `tests/` — suite pytest (262 test raccolti).
- `scripts/` — benchmark di recupero e strumenti di verifica.

## Avvio locale (senza Docker)

```bash
pip install -e ".[dev]"
export ATLAS_DATABASE_URL=sqlite:///$(pwd)/atlas.db
export ATLAS_JWT_SECRET=cambia-questa-stringa
uvicorn app.main:app --reload
# API su http://127.0.0.1:8000, documentazione interattiva su /docs
```

## Test

```bash
# Suite completa su SQLite (nessun servizio esterno):
ATLAS_DATABASE_URL=sqlite:////tmp/atlas_test.db pytest -q

# Lint (stessa selezione di percorsi della CI):
ruff check app tests
```

In CI la suite gira su Postgres+pgvector reale (service container) e le
migrazioni Alembic fanno upgrade/downgrade/upgrade su un database pulito.
