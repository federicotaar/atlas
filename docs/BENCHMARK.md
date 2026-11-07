# Benchmark di recupero

Benchmark ripetibile: `scripts/benchmark.py` semina un persona sintetico
(«Mario Rossi», dati inventati, nessun dato reale) con 8 memorie note e pone
8 domande con risposta attesa; misura se la risposta di `/ask` cita la
memoria corretta. Soglia di successo: 80% (soglia di esempio indicata dalla
specifica per la fase 1, adottata fin da ora come guardrail).

Gira in CI ad ogni push, dopo la suite pytest, con provider locali
(deterministici). Con Ollama (`nomic-embed-text`) la copertura semantica
migliora: il benchmark resta identico, cambia solo il provider via env.

## Risultati misurati (2026-11-07)

| Provider embeddings | Punteggio | Note |
|---|---|---|
| `local` (feature hashing, CI) | 7/8 = 88% | caso mancato: "Quanto costa l'infrastruttura al mese?" (parole diverse da "budget mensile ... zero euro") |
| `ollama` (nomic-embed-text, reale, misurato in sandbox) | 8/8 = 100% | recupero semantico vero: copre parafrasi e sinonimi |

Il caso mancato con `local` è l'esempio onesto del limite del provider
lessicale: la domanda usa "costa/infrastruttura" dove la memoria dice
"budget/zero euro". Con Ollama la stessa domanda recupera la memoria giusta.

Verificato anche con test dedicati: parafrasi ("Mario e impiegato presso
ACME" risponde a "Dove lavora Mario?") e sinonimi ("bici" recupera
"bicicletta") passano con Ollama e non con il provider locale.

Per l'esecuzione in CI vedi il job `backend` (step "Benchmark di recupero").
