# Stato onesto della roadmap

Documento di specifica: "ATLAS — Specifiche e piano di sviluppo (v2)".
Le fasi finiscono quando passano i test di uscita, non per calendario.

| Fase | Contenuto | Stato |
|---|---|---|
| 0 | Nucleo: memoria, fonti, eventi, recupero ibrido, /ask con citazioni, auth, test | **Implementata** (questo repo) |
| 1 | Grafo entità/relazioni, tipi completi, contraddizioni, conversazioni | **Implementata** (questo repo) |
| 2 | Agenti Researcher/Planner/Critic, agent_runs | **Implementata** (questo repo) |
| 3 | Previsioni con intervalli, verifica e calibrazione | **Implementata** (questo repo) |
| 4 | Connettori, Action Engine con permessi | Non implementata |
| 5 | Time Machine, dashboard | Non implementata |
| ricerca | Counterfactual Engine | Non implementata (ricerca, richiede dati storici reali) |

## Uscita fase 0 (dalla specifica) e stato

- [x] Repository, struttura, Docker Compose con PostgreSQL
- [x] FastAPI, configurazione, gestione errori, log
- [x] Schema e migrazioni: users, projects, events, memories, sources, action_logs
- [x] Autenticazione (argon2 + JWT con scadenza)
- [x] API eventi e memorie (con fonte, date, confidenza, sostituzione)
- [x] Ricerca testuale e semantica (pgvector), recupero ibrido
- [x] Endpoint /ask con memorie citate e «Non lo so»
- [x] Test automatici su API, persistenza, recupero, isolamento utenti
- [x] Chat web minima + CLI
- [x] Benchmark di recupero ripetibile (`scripts/benchmark.py`, risultati in `docs/BENCHMARK.md`)

## Uscita fase 1 (dalla specifica) e stato

- [x] Tipi di memoria completi (fact, opinion, decision, hypothesis, task), confidenza, sostituzioni
- [x] Rilevamento di contraddizioni (euristico, dichiarato): avviso alla scrittura + vista `GET /memories/contradictions`
- [x] Recupero ibrido con pesi configurabili (`ATLAS_W_*`), benchmark di riferimento in `docs/BENCHMARK.md`
- [x] Entità e relazioni con estrazione assistita: nascono `pending`, conferma/rifiuto espliciti via API `/graph/*`
- [x] Conversazioni salvate (tabelle conversations/messages, `/ask` persiste i turni, `GET /conversations`)
- [x] Orchestratore centrale (`app/services/orchestrator.py`): classifica -> percorso -> registra
- [x] Domande contestuali sui progetti ("A che punto sono con X?": eventi e date reali, niente progressi inventati)
- [x] Gate: benchmark >= 80% (88% con provider locale, 100% con Ollama) e «Non lo so» quando manca l'informazione

## Uscita fase 2 (dalla specifica) e stato

- [x] Researcher, Planner, Critic con messaggi strutturati (schemi Pydantic in `app/agents/schemas.py`)
- [x] Limiti di esecuzione: passi (3), timeout (20s), budget costo (0, solo provider locali)
- [x] Registro `agent_runs` (input, output, passi, durata, costo, esito) + `GET /agents/runs`
- [x] Pipeline `POST /agents/run`: Researcher -> Planner -> Critic; il Critic ha l'ultima parola
- [x] Gate: il Critic individua errori inseriti di proposito (test con attivita "piantata" senza fonte)

Gli agenti sono rule-based e locali: niente chiamate a modelli esterni.
Questa e una BASELINE meccanica della struttura richiesta (messaggi
strutturati, limiti, registro, ultima parola del Critic), NON prova di
capacita generale di ricerca o pianificazione intelligente: quella richiede
un modello linguistico locale (Ollama) e valutazione su casi reali.
I limiti (passi, timeout, budget) sono APPLICATI, non solo registrati: test
dedicati verificano che la pipeline si fermi (stopped_reason) quando un
limite scatta. Il Critic controlla l'appoggio sostanziale (parole di
contenuto condivise) e le contraddizioni con i fatti, con casi avversari in
test (memoria citata ma irrilevante; attivita che contraddice un fatto).
Analyst ed Engineer (opzionali nella spec) non ancora aggiunti: la spec li
subordina a un bisogno concreto con test.

## Note di onestà

- Gli embeddings di default (`local`) sono lessicali (feature hashing), non
  semantici profondi: senza Ollama la ricerca per significato è limitata alla
  sovrapposizione di parole. Con Ollama (`nomic-embed-text`) diventa semantica.
- Il rilevamento contraddizioni è euristico (parole condivise + valori diversi
  nella stessa categoria): produce proposte, mai risoluzioni automatiche.
- L'estrazione di entità/relazioni è rule-based e richiede sempre conferma
  umana; il grafo non si popola da solo.
- La classificazione delle richieste è a regole, non a modello.
- La fase 4 usa solo connettori fittizi (calendario sintetico, bozze email): nessun account reale, nessun invio. Connettori reali non ancora implementati.

## Uscita fase 3 (dalla specifica) e stato

- [x] Registro delle previsioni e dei risultati (tabella `predictions`, `POST/GET /predictions`, `POST /predictions/{id}/verify`)
- [x] Stime con intervallo (basso/centrale/alto) dalla velocita storica di completamento delle attivita
- [x] Rifiuto esplicito quando i dati sono troppo pochi (< 2 settimane di completamenti): niente stime inventate
- [x] Metriche di errore e calibrazione (`GET /predictions/metrics`: MAE, Brier medio, bucket di calibrazione)
- [x] Assunzioni, dati usati, probabilita dichiarata e fattori di rischio sempre registrati
- [x] Gate: il sistema formula previsioni, le verifica alla scadenza (errore + Brier) e mostra l'accuratezza storica

Metodo dichiarato: la stima automatica usa solo la velocita di completamento
delle attivita (attivita chiuse per settimana ISO); non usa modelli
linguistici. Le verifiche sono oneste anche quando l'esito e sfavorevole.

## Uscita fase 4 (dalla specifica) e stato

- [x] Action Engine come unica porta: i kind di azione vivono solo qui, gli agenti non eseguono mai direttamente
- [x] Permesso una volta per connettore (livello 0): connettori `calendar_mock` e `email_mock`, stati pending/authorized/revoked
- [x] Livello 1 (modifiche reversibili su dati propri): `calendar.create_event` e `tasks.add` eseguono e riportano; annullabili via `POST /actions/{id}/cancel`
- [x] Livello 2 (riguarda altre persone): `email.create_draft` richiede anteprima esatta e conferma esplicita (`pending_confirm` -> `confirm`) ogni volta
- [x] Una bozza non e un invio: non esiste alcun kind di invio; `email.send` e rifiutato
- [x] Chiave di idempotenza per azione: stessa chiave, stessa azione, nessuna doppia esecuzione
- [x] Esito registrato (`result`, `executed_at`) solo dopo esecuzione verificata; annullamento quando tecnicamente possibile (bozza eliminata, evento annullato, attivita rimossa)
- [x] Test di sicurezza: nessuna esecuzione senza connettore autorizzato, senza conferma, o tra utenti diversi

Metodo dichiarato: connettori fittizi con dati sintetici. Il meccanismo
(permessi, anteprima, conferma, idempotenza, annullamento) e reale e
testato; connettori veri (Google Calendar, Gmail) richiederebbero account
reali e consensi espliciti, e non sono implementati.

## Uscita fase 5 (dalla specifica) e stato

- [x] Ricostruzione dello stato a una data (`GET /timemachine/state-at`): memorie attive alla data (sostituzioni rispettate per data di registrazione), eventi fino alla data per tipo, previsioni come risultavano alla data
- [x] Cronologia (`GET /timemachine/timeline`): eventi in ordine temporale con fonte
- [x] Confronto tra due periodi (`GET /timemachine/compare`): eventi, memorie e previsioni per periodo
- [x] Obiettivi abbandonati (`GET /timemachine/abandoned-goals`): progetti attivi senza eventi recenti o con data obiettivo passata, con l'ultimo evento da cui deriva la segnalazione
- [x] Attivita ricorrenti (`GET /timemachine/recurring`): tipi di evento ripetuti con cadenza media e regolarita
- [x] Lacune sempre mostrate, mai riempite: ogni risposta su un intervallo include `gaps` (periodi senza alcun dato)
- [x] Dashboard web (pannello React + `GET /dashboard`): progetti, attivita imminenti, eventi recenti, previsioni e scadenze, agenti attivi, automazioni, memorie importanti, richieste di autorizzazione
- [x] Gate: ogni elemento della dashboard espone `derived_from` con gli id dei dati da cui deriva (verificato in `tests/test_dashboard.py`)
- [ ] Involucro desktop Tauri: non implementato (nella specifica viene dopo la dashboard web)

Metodo dichiarato: la ricostruzione usa solo dati registrati (eventi
append-only, memorie con sostituzioni datate). Il record progetto non ha
storia propria: `state-at` lo riporta marcato come stato attuale, non come
stato alla data. Nessuna statistica oltre i dati: le lacune restano lacune.

## Uscita fase 6 (dalla specifica) e stato

- [x] Prestazioni: indici sulle query piu frequenti (migrazione 0006: events,
  memories, predictions, engine_actions, agent_runs, action_logs, tabelle
  mock) e sonda `scripts/perf.py`. Su dataset sintetico realistico (300
  eventi, 60 memorie): tutti gli endpoint chiave sotto 14 ms p95 in-process
  (GET /dashboard 12.1 ms, /timemachine/* 5-13 ms, /memories/search 10.5 ms)
- [x] Test di sicurezza: isolamento utenti anche sui nuovi endpoint
  (timemachine, dashboard), 401 senza token su tutti i nuovi endpoint;
  zero azioni non autorizzate resta garantito dai test di fase 4
- [x] Documentazione API (`docs/API.md`) e architettura
  (`docs/ARCHITECTURE.md`, sezioni per fase)
- [ ] Beta con poche persone fidate: NON eseguibile da un processo di
  sviluppo automatico — richiede persone reali che usano il sistema e
  riportano problemi. Resta aperta; nessun dato reale di terzi e stato
  toccato.

Stato: il consolidamento tecnico della fase 6 e fatto; l'uscita completa
della fase richiede la beta reale. Il badge resta "fase 5 di 6" finche la
beta non e fatta.

## Counterfactual Engine (ricerca parallela, facoltativa)

Non implementato. La specifica (sez. 5J) pone precondizioni esplicite:
almeno sei mesi di eventi affidabili e centinaia di eventi registrati,
modello esplicito (non "chiedi a un LLM di immaginare"). Senza dati reali
longitudinali un prototipo misurerebbe solo se stesso: dichiarato apertamente
invece di simulare la funzione.

## Connettori Google reali (fase 4: calendario read-only, bozze email)

- [x] OAuth gratuito con state firmato HMAC e scadenza; token cifrati con
  Fernet (`ATLAS_CREDENTIALS_KEY`), fail-closed senza chiave; refresh
  automatico con ri-cifratura
- [x] `google_calendar.list_events` (livello 0, scope calendar.readonly) e
  `google_gmail.create_draft` (livello 1, scope gmail.compose, annullabile
  con eliminazione bozza). Nessuna operazione di invio esiste
- [x] 14 test (monkeypatch, zero account reali toccati): OAuth, cifratura,
  mapping, bozza+undo, isolamento, 401/403
- [ ] Validazione con account reale: richiede il consenso di Fred
  (docs/CONNECTORS-GOOGLE.md, passi 1-5); unico passaggio non automatizzabile

## Involucro desktop Tauri (fase 5, "poi involucro desktop Tauri")

- [x] Shell Tauri v2 in `desktop/` attorno alla dashboard web esistente:
  stesso frontend compilato, base API risolta a `http://127.0.0.1:8000`
  dentro il webview, CORS del backend aperto solo per le origini Tauri
  (test `test_cors.py`: preflight Tauri ok, origine sconosciuta senza
  header, same-origin invariato)
- [x] Build CI su ubuntu-latest e macos-latest
  (`.github/workflows/desktop.yml`) con icone generate dal logo v7
  approvato e artifact .deb/.AppImage/.dmg
- [ ] Il backend resta un processo separato avviato dall'utente
  (nessun sidecar embedded): limite dichiarato, vedi `docs/DESKTOP.md`
