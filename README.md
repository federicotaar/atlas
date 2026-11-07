<div align="center">

# ATLAS

<img src="assets/atlas-logo.png" alt="ATLAS logo" width="180">

**Un sistema di intelligenza personale che ricorda, ragiona e prevede, con fonti verificabili, limiti dichiarati e zero costi.**

![status](https://img.shields.io/badge/status-fase%203%20di%206-orange)
![cost](https://img.shields.io/badge/costo-0%E2%82%AC%20tutto%20locale-brightgreen)
![tests](https://img.shields.io/badge/test-114%20verdi-brightgreen)

</div>

---

ATLAS conserva le informazioni autorizzate in una memoria dove **ogni dato ha
una fonte**, distingue ciò che è dichiarato da ciò che è dedotto, recupera
per significato e risponde **citando le memorie usate**, oppure dice
«Non lo so». Gli eventi sono **append-only**: le correzioni sono nuove
memorie che sostituiscono le precedenti, niente viene riscritto.

Su questa base ATLAS costruisce un grafo di entità e relazioni (con conferma
esplicita), una pipeline di agenti con limiti applicati e un motore di
previsioni che stima con intervalli e registra l'esito reale alla data di
verifica. Tutto gira in locale: nessuna chiamata a pagamento, nessun dato che
esce dalla macchina senza permesso.

## Cosa fa oggi

- **Memoria verificabile**: fonti, confidenza, date, sostituzioni invece di sovrascritture; rilevamento di contraddizioni.
- **Recupero ibrido**: ricerca per similarità + testuale + recenza + confidenza, con pesi calibrati su un benchmark ripetibile. Sul set sintetico di 8 domande in `scripts/benchmark.py`: 88% (7/8) con il provider lessicale integrato (hash n-gram, baseline), 100% (8/8) con Ollama (embeddings semantici reali). Set piccolo e sintetico: indica che il meccanismo funziona, non l'accuratezza su dati reali.
- **Knowledge Graph assistito**: entità e relazioni proposte dal testo nascono `pending` e diventano reali solo dopo conferma.
- **Agenti Researcher / Planner / Critic**: messaggi strutturati, limiti di passi, timeout e budget applicati (non solo registrati), ogni esecuzione tracciata. *Baseline rule-based dichiarata, non prova di capacità generale.*
- **Prediction Engine**: stime a intervallo dalla velocità reale di completamento, rifiuto esplicito quando i dati sono troppo pochi, confronto con l'esito alla data di verifica (`GET /predictions/due` elenca le scadenze; `POST /predictions/evaluate-due` verifica automaticamente le stime automatiche misurando lo stato reale delle attività, mentre le stime manuali restano da verificare a mano), errore e punteggio di Brier, metriche di calibrazione. La verifica automatica è un meccanismo su dati sintetici: l'accuratezza longitudinale reale si misura solo con mesi di utilizzo reale.
- **Interfacce**: API FastAPI, chat web React, CLI `atlas`, Docker Compose.

Lo stato onesto di ogni fase è in
[`docs/ROADMAP-STATUS.md`](docs/ROADMAP-STATUS.md): le fasi 0–3 sono
implementate e testate in CI; connettori/azioni (fase 4), time machine e
dashboard (fase 5) e il Counterfactual Engine **non sono ancora implementati**.

## Avvio rapido (Docker Compose)

```bash
cp .env.example .env   # modifica ATLAS_JWT_SECRET
docker compose up --build
# API su http://localhost:8000 (docs interattive: /docs)
```

Modelli locali opzionali (embeddings e risposte migliori via Ollama):

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

## Frontend (chat web)

```bash
cd frontend && npm install && npm run dev
# http://localhost:5173 (proxy /api -> localhost:8000)
```

## Documentazione

| Documento | Contenuto |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Componenti, flussi e scelte tecniche per fase |
| [`docs/ROADMAP-STATUS.md`](docs/ROADMAP-STATUS.md) | Stato onesto di ogni fase della specifica |
| [`docs/BENCHMARK.md`](docs/BENCHMARK.md) | Benchmark di recupero: metodo, risultati, calibrazione |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Minacce considerate e contromisure implementate |

## Limiti dichiarati

- Gli agenti di fase 2 sono rule-based: struttura e limiti reali, intelligenza generale no (richiede un modello locale via Ollama).
- Le previsioni di fase 3 verificano il meccanismo (stima, scadenza, errore, calibrazione); l'accuratezza su dati longitudinali reali va dimostrata con l'uso.
- Il benchmark misura il recupero sul set preparato, non l'utilità percepita da utenti reali.
- Le memorie dedotte sono sempre marcate come tali; la risoluzione delle contraddizioni spetta all'utente.
