<div align="center">

# ATLAS

<img src="assets/atlas-logo.png" alt="ATLAS logo" width="180">

**Un sistema di intelligenza personale che ricorda, ragiona e prevede, con fonti verificabili, limiti dichiarati e zero costi.**

![status](https://img.shields.io/badge/status-fase%205%20di%206-orange)
![cost](https://img.shields.io/badge/costo-0%E2%82%AC%20tutto%20locale-brightgreen)
![tests](https://img.shields.io/badge/test-223%20verdi-brightgreen)

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

## Agente ATLAS (stage 1-14)

L'agente personale con provider LLM intercambiabili: un'astrazione unica
`LLMProvider`, configurazione in `config/providers.yaml`, chiavi API solo in
`.env`, fallback automatico al provider successivo se quello attivo fallisce
o va in rate limit.

### Setup da checkout pulito

1. **Python 3.12+** e dipendenze: `pip install ./backend` (include CLI,
   canali Telegram/WhatsApp, web UI e scheduler).
2. **Ollama**: installa da <https://ollama.com/download>, poi scarica un
   modello: `ollama pull llama3.2:3b`. Scegli un modello CON tool-calling
   nativo (llama3.2, qwen2.5 e simili): con un modello senza, la chat
   funziona in modalita' solo testo ma gli strumenti dell'agente non sono
   disponibili. Il modello si cambia anche dal pannello ⚙ della web UI
   (campo "modello") senza toccare file.
3. **Chiavi provider** (opzionali, per i cloud): crea `.env` dal root del
   repo (vedi `.env.example`) con ad esempio `GROQ_API_KEY=...`. Le chiavi
   non finiscono mai nel codice, nei log o nei messaggi.
4. **Avvio chat**: `python main.py` dal root del repo. In alternativa la
   web UI locale: `python main.py --web` (solo 127.0.0.1, porta 8321).

Comandi: `/model list` (provider configurati e modelli disponibili),
`/model use <nome[:modello]>` (cambio a runtime), `/mode ask|auto-safe|yolo`,
`/remember`, `/skills`, `/tools`, `/help`, `/quit`.

### Canali di messaggistica

Configurazione in `config/channels.yaml` (ogni canale disabilitato non
parte); segreti e allowlist SOLO in `.env`.

- **Telegram**: crea un bot con @BotFather, metti
  `TELEGRAM_BOT_TOKEN=...` e `ALLOWED_TELEGRAM_IDS=<il tuo id numerico>` in
  `.env`, abilita il canale in `channels.yaml` e avvia con
  `python main.py --headless`. I mittenti sconosciuti ricevono silenzio;
  l'associazione di nuovi dispositivi passa da `/pairing` solo via CLI
  locale.
- **WhatsApp**: il canale non ufficiale usa il sidecar Go `wa-bridge`
  (solo loopback + token condiviso); il collegamento avviene scansionando
  il QR / inserendo il codice dal telefono del proprietario al primo avvio.
  Stato: codice, test e smoke locali; non ancora verificato end-to-end su
  account reale. Il canale ufficiale (Cloud API) richiede l'app Meta del
  proprietario e un tunnel avviato da lui.

### App desktop

L'app desktop (sezione sotto) avvia anche l'agente: stesso server locale,
pagina raggiungibile dal link "Agente ATLAS" nella dashboard (montata a
`/agent`). Provider e chiavi si configurano dalla pagina stessa (ingranaggio
in alto): la chiave viene salvata in `.env` nella data dir (permessi 0600)
e non viene mai mostrata. Una sola istanza dell'agente per workspace tra
CLI, web UI e app desktop (lock con PID).

### Tool auto-generati (fail-closed)

L'agente puo' PROPORRE nuovi tool, ma restano inattivi finche' il
proprietario non li approva esplicitamente. Test, controllo interfaccia ed
esecuzione a runtime avvengono SOLO dentro bubblewrap (bwrap): niente rete,
filesystem di sistema in sola lettura, ambiente pulito dai segreti. Su host
senza bwrap (es. macOS) l'esecuzione dei tool generati e' DISABILITATA: le
proposte restano registrate ma non vengono eseguite ne' attivate.

## Cosa fa oggi

- **Memoria verificabile**: fonti, confidenza, date, sostituzioni invece di sovrascritture; rilevamento di contraddizioni.
- **Recupero ibrido**: ricerca per similarità + testuale + recenza + confidenza, con pesi calibrati su un benchmark ripetibile. Sul set sintetico di 8 domande in `scripts/benchmark.py`: 88% (7/8) con il provider lessicale integrato (hash n-gram, baseline), 100% (8/8) con Ollama (embeddings semantici reali). Set piccolo e sintetico: indica che il meccanismo funziona, non l'accuratezza su dati reali.
- **Knowledge Graph assistito**: entità e relazioni proposte dal testo nascono `pending` e diventano reali solo dopo conferma.
- **Agenti Researcher / Planner / Critic**: messaggi strutturati, limiti di passi, timeout e budget applicati (non solo registrati), ogni esecuzione tracciata. *Baseline rule-based dichiarata, non prova di capacità generale.*
- **Prediction Engine**: stime a intervallo dalla velocità reale di completamento, rifiuto esplicito quando i dati sono troppo pochi, confronto con l'esito alla data di verifica (`GET /predictions/due` elenca le scadenze; `POST /predictions/evaluate-due` verifica automaticamente le stime automatiche misurando lo stato reale delle attività, mentre le stime manuali restano da verificare a mano), errore e punteggio di Brier, metriche di calibrazione. La verifica automatica è un meccanismo su dati sintetici: l'accuratezza longitudinale reale si misura solo con mesi di utilizzo reale.
- **Action Engine**: unica porta per le azioni (gli agenti non eseguono mai direttamente). Livelli 0/1/2: lettura con permesso per connettore, modifiche reversibili eseguite e annullabili, azioni verso altri con anteprima esatta e conferma esplicita ogni volta. Chiave di idempotenza contro doppie esecuzioni, esito registrato, annullamento quando possibile. Connettori fittizi per i test (calendario sintetico, bozze email) e connettori Google reali opzionali (calendario in sola lettura, bozze Gmail; OAuth gratuito, token cifrati, setup in [`docs/CONNECTORS-GOOGLE.md`](docs/CONNECTORS-GOOGLE.md)): una bozza non è un invio e non esiste alcuna operazione di invio. I connettori reali sono validati solo con fixture sintetiche: nessun account reale è mai stato collegato.
- **Connettore Telegram**: bot ufficiale creato con @BotFather; il token resta cifrato sul dispositivo dell'utente. L'accoppiamento è sempre avviato dall'utente dalla sua app Telegram, con QR o codice monouso valido 15 minuti (rigenerabile); ATLAS legge e risponde solo ai messaggi del proprietario accoppiato. Le risposte passano dalla stessa pipeline della chat web, citazioni incluse.
- **Connettore WhatsApp**: rotta **non ufficiale** (bridge locale `wa-bridge`, Go/whatsmeow, protocollo multi-dispositivo): comporta rischio di restrizioni o ban del numero e l'avviso resta visibile in ogni stato dell'interfaccia. L'accoppiamento avviene **solo** con scansione QR avviata dall'utente, mai in background; ATLAS legge e risponde solo nella chat con se stessi, con dedup persistita contro replay e doppie risposte e anti-eco per id preassegnato. Sessione e credenziali restano sul dispositivo. *Non ancora collegato a un account reale: validato solo con fixture sintetiche e test locali.*
- **Time Machine**: ricostruisce lo stato a una data rileggendo i dati registrati fino a quel momento (gli eventi sono solo in aggiunta): cronologia, stato a una data, confronto tra due periodi, obiettivi abbandonati, attività ricorrenti. Le lacune (periodi senza dati) sono sempre mostrate, mai riempite.
- **Dashboard web**: pannelli su progetti, scadenze, eventi recenti, previsioni, agenti, automazioni, memorie importanti e richieste di autorizzazione; ogni elemento si apre e mostra gli id dei dati da cui deriva.
- **Interfacce**: API FastAPI, chat web React con dashboard, app desktop Tauri (Linux e macOS), CLI `atlas`, Docker Compose.

Lo stato onesto di ogni fase è in
[`docs/ROADMAP-STATUS.md`](docs/ROADMAP-STATUS.md): le fasi 0–6 sono
implementate e testate in CI (242 test backend, frontend, job Ollama reale,
compose e smoke desktop end-to-end). La fase 6 si chiude solo con la beta
con persone reali, che **resta aperta**; il Counterfactual Engine (ricerca
facoltativa) **non è implementato**.

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

## App desktop (Linux)

L'app desktop include il backend: all'avvio parte un processo locale con
database SQLite incorporato, in ascolto solo su 127.0.0.1. Non servono
Docker, un server o un indirizzo email: il profilo si crea con una password
locale e resta solo sul dispositivo (il login con email e codice resta per
l'installazione server con Docker Compose). Prerequisiti: Ubuntu 22.04 o
24.04 (se mancanti: `sudo apt install libwebkit2gtk-4.1-0 libegl1`).

```bash
chmod +x ATLAS_*.AppImage
./ATLAS_*.AppImage   # al primo avvio: "Crea profilo locale"
```

Il flusso è verificato in CI a ogni commit: build del pacchetto, avvio
reale dell'app, registrazione locale, salvataggio e richiamo con citazione,
persistenza dopo riavvio.

La [release v0.1.0](https://github.com/federicotaar/atlas/releases/tag/v0.1.0)
pubblicata è precedente a queste modifiche e richiede ancora il backend
Docker (istruzioni in [`docs/DESKTOP.md`](docs/DESKTOP.md)); gli artefatti
aggiornati arrivano con la prossima release.

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
| [`docs/DESKTOP.md`](docs/DESKTOP.md) | Involucro desktop Tauri della dashboard |
| [`docs/CONNECTORS-GOOGLE.md`](docs/CONNECTORS-GOOGLE.md) | Connettori Google reali (OAuth gratuito, token cifrati) |
| [`docs/BENCHMARK.md`](docs/BENCHMARK.md) | Benchmark di recupero: metodo, risultati, calibrazione |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Minacce considerate e contromisure implementate |
| [`docs/API.md`](docs/API.md) | Riferimento endpoint HTTP |

## Limiti dichiarati

- Gli agenti di fase 2 sono rule-based: struttura e limiti reali, intelligenza generale no (richiede un modello locale via Ollama).
- Le previsioni di fase 3 verificano il meccanismo (stima, scadenza, errore, calibrazione); l'accuratezza su dati longitudinali reali va dimostrata con l'uso.
- La Time Machine ricostruisce dallo stato registrato (eventi append-only, memorie con sostituzioni datate); il record progetto non ha storia propria e lo stato attuale viene marcato come tale.
- Il benchmark misura il recupero sul set preparato, non l'utilità percepita da utenti reali.
- Le memorie dedotte sono sempre marcate come tali; la risoluzione delle contraddizioni spetta all'utente.
