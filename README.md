# ATLAS — Personal Intelligence System

*English: ATLAS is a personal intelligence system that stores authorized
information in a verifiable, source-backed memory, retrieves it by meaning,
and answers questions citing the memories it used. This repository currently
contains **phase 0 (the working core)**. Later phases (knowledge graph,
agents, predictions, actions, time machine) are on the roadmap and are NOT
implemented yet. See `docs/ROADMAP-STATUS.md`.*

## Cos'è

Un sistema di intelligenza personale che:

1. raccoglie informazioni **autorizzate** dall'utente;
2. le conserva in una memoria strutturata dove **ogni informazione ha una fonte**;
3. distingue ciò che è **dichiarato** da ciò che è **dedotto** (e da ciò che non sa);
4. recupera per **significato** (ricerca ibrida: vettori + full-text + recenza + confidenza);
5. risponde **citando le memorie usate** — e dice «Non lo so» quando non ha dati.

Gli eventi sono **append-only**: non si modificano né si cancellano; le
correzioni sono nuove memorie che sostituiscono le precedenti
(`superseded_by`). Le cancellazioni vere avvengono solo su richiesta
dell'utente (`DELETE /me`, diritto all'oblio).

## Stato del progetto

**Fase 0 (nucleo funzionante)** — implementata in questo repository:

- Backend FastAPI: auth (argon2 + JWT), projects, events, memories, sources, `/ask`, export/cancellazione
- PostgreSQL 16 + pgvector, migrazioni Alembic
- Recupero ibrido (semantico + testuale + recenza + confidenza), pesi configurabili
- Risposte con citazioni e risposta onesta «Non lo so»
- Model router: provider intercambiabili (Ollama locale oppure provider deterministico integrato, gratis e offline)
- CLI (`atlas`) e chat web React minima
- Test automatici: API, persistenza, recupero, isolamento utenti, logica di scoring
- Docker Compose per l'ambiente completo

Le fasi 1–6 del documento di specifica (grafo, agenti, previsioni, azioni,
time machine, dashboard) **non sono implementate**: vedi
[`docs/ROADMAP-STATUS.md`](docs/ROADMAP-STATUS.md) per lo stato onesto di
ogni fase.

## Avvio rapido (Docker Compose)

```bash
cp .env.example .env   # modifica ATLAS_JWT_SECRET
docker compose up --build
# API su http://localhost:8000 (docs: /docs)
```

Modelli locali opzionali (embeddings/risposte migliori via Ollama):

```bash
docker compose --profile models up -d ollama
docker exec atlas-ollama-1 ollama pull nomic-embed-text
docker exec atlas-ollama-1 ollama pull llama3.2
# poi in .env: ATLAS_EMBED_PROVIDER=ollama, ATLAS_LLM_PROVIDER=ollama
```

Senza Ollama ATLAS funziona comunque: gli embeddings lessicali integrati e il
generatore estrattivo sono gratuiti e offline, con qualità inferiore ma
comportamento identico (citazioni incluse).

## CLI

```bash
cd backend && pip install -e .
atlas register io@example.com password-lunga
atlas note "Il server di produzione è su Oracle Cloud"
atlas ask "Dove gira il server di produzione?"
atlas search "server"
```

## Frontend (chat web minima)

```bash
cd frontend && npm install && npm run dev
# http://localhost:5173 (proxy /api -> localhost:8000)
```

## Test

```bash
cd backend
# serve Postgres+pgvector (docker compose up db)
ATLAS_DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test pytest
cd frontend && npm test
```

## Struttura

```
backend/   FastAPI app, Alembic, test pytest
frontend/  chat React minima (Vite + TS)
docs/      architettura, stato roadmap, sicurezza, benchmark
scripts/   benchmark di recupero ripetibile
```

## Licenza

MIT — vedi [LICENSE](LICENSE).
