# Benchmark di recupero

Benchmark ripetibile: `scripts/benchmark.py` semina un persona sintetico
(«Mario Rossi», dati inventati, nessun dato reale) con 8 memorie note e pone
8 domande con risposta attesa; misura se la risposta di `/ask` cita la
memoria corretta. Soglia di successo: 80% (soglia di esempio indicata dalla
specifica per la fase 1, adottata fin da ora come guardrail).

Gira in CI ad ogni push, dopo la suite pytest, con provider locali
(deterministici). Con Ollama (`nomic-embed-text`) la copertura semantica
migliora: il benchmark resta identico, cambia solo il provider via env.

## Ultimo risultato

Vedi il job `backend` dell'ultima run CI (step "Benchmark di recupero").
