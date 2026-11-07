# API ATLAS

Riferimento degli endpoint HTTP. Tutte le rotte (tranne `/health`,
`/auth/register`, `/auth/login`) richiedono `Authorization: Bearer <token>`.
Ogni risorsa è isolata per utente: gli id di altri utenti risultano 404.
Documentazione interattiva generata dal codice: `/docs` (OpenAPI) su
un'istanza avviata.

## Salute e autenticazione

| Metodo | Percorso | Descrizione |
|---|---|---|
| GET | `/health` | Stato del servizio e del database |
| POST | `/auth/register` | Crea account, ritorna `token` (201) |
| POST | `/auth/login` | Login, ritorna `token` |

## Fonti, progetti, eventi, memorie

| Metodo | Percorso | Descrizione |
|---|---|---|
| POST/GET | `/sources` | Fonti dei dati (chat, file, connettore, cli...) |
| POST/GET | `/projects` | Progetti (nome, stato, date) |
| GET | `/projects/{id}` | Dettaglio progetto |
| POST/GET | `/events` | Eventi append-only: tipo, payload, `event_at`, progetto, fonte. Filtri: `type`, `project_id`, `from`, `to` |
| POST/GET | `/memories` | Memorie con fonte, tipo, origine, confidenza; `supersedes` per sostituire (mai sovrascrivere) |
| GET | `/memories/{id}` | Dettaglio memoria |
| POST | `/memories/search` | Recupero ibrido (semantico + testuale + recenza + confidenza) |
| GET | `/memories/contradictions` | Coppie in contraddizione rilevate (avviso, mai fusione automatica) |

## Domande e conversazioni

| Metodo | Percorso | Descrizione |
|---|---|---|
| POST | `/ask` | Risponde citando le memorie; "non lo so" quando manca l'informazione; salva la conversazione |
| GET/POST | `/conversations` · `/conversations/{id}` | Conversazioni salvate (elenco e dettaglio) |

## Grafo

| Metodo | Percorso | Descrizione |
|---|---|---|
| POST | `/graph/extract` | Estrazione assistita: crea proposte `pending` (mai reali di default) |
| GET | `/graph/proposals` · `/graph` | Proposte in attesa / grafo confermato |
| POST | `/graph/entities/{id}/confirm` · `/reject` | Conferma o rifiuta un'entità proposta |
| POST | `/graph/relationships/{id}/confirm` · `/reject` | Conferma o rifiuta una relazione proposta |
| GET | `/graph/project-links` | Relazioni tra progetti |

## Agenti (fase 2)

| Metodo | Percorso | Descrizione |
|---|---|---|
| POST | `/agents/run` | Esegue Researcher / Planner / Critic con limiti di passi, timeout e budget |
| GET | `/agents/runs` | Registro delle esecuzioni (`agent_runs`) |

## Previsioni (fase 3)

| Metodo | Percorso | Descrizione |
|---|---|---|
| POST | `/predictions` | Registra una previsione (manuale con stima a intervallo, oppure `auto_estimate`) |
| GET | `/predictions` | Elenco |
| GET | `/predictions/due` | Aperte e scadute |
| POST | `/predictions/evaluate-due` | Verifica automatica le stime automatiche scadute dallo stato reale |
| GET | `/predictions/metrics` | MAE, Brier medio, calibrazione |
| POST | `/predictions/{id}/verify` | Verifica manuale con esito reale |

## Connettori e azioni (fase 4, solo connettori fittizi)

| Metodo | Percorso | Descrizione |
|---|---|---|
| POST/GET | `/connectors` | Connettori (`calendar_mock`, `email_mock`), permesso una volta |
| POST | `/connectors/{id}/authorize` · `/revoke` | Autorizza / revoca |
| POST/GET | `/actions` | Azioni con livello 0/1/2, anteprima, idempotenza |
| GET | `/actions/{id}` | Dettaglio |
| POST | `/actions/{id}/confirm` · `/cancel` | Conferma (livello 2) / annulla |

## Time Machine (fase 5)

| Metodo | Percorso | Descrizione |
|---|---|---|
| GET | `/timemachine/timeline?from&to[&project_id]` | Cronologia con fonte; include `gaps` (lacune mai riempite) |
| GET | `/timemachine/state-at?at[&project_id]` | Stato ricostruito alla data: memorie attive (sostituzioni rispettate), eventi per tipo, previsioni come erano |
| GET | `/timemachine/compare?a_start&a_end&b_start&b_end` | Confronto tra due periodi con lacune di ciascuno |
| GET | `/timemachine/abandoned-goals?idle_days` | Progetti attivi fermi o con obiettivo passato, con l'evento da cui deriva |
| GET | `/timemachine/recurring?min_occurrences` | Attività ricorrenti con cadenza media e regolarità |

## Dashboard (fase 5)

| Metodo | Percorso | Descrizione |
|---|---|---|
| GET | `/dashboard` | Pannelli: progetti, attività imminenti, eventi recenti, previsioni e scadenze, agenti, automazioni, memorie importanti, richieste di autorizzazione. Ogni elemento espone `derived_from` con gli id sorgente |

## Dati dell'utente

| Metodo | Percorso | Descrizione |
|---|---|---|
| GET | `/me/export` | Esporta tutti i dati dell'utente |
| DELETE | `/me` | Cancella davvero tutti i dati (diritto all'oblio) |
