# wa-bridge

Ponte WhatsApp per ATLAS: gira come linked device e collega la self-chat di
WhatsApp (la chat "tu con te stesso") al backend locale. Go + whatsmeow, un
unico binario (`main.go`).

## Modello di sicurezza

- Solo loopback: ascolta su `127.0.0.1`, richiede un token condiviso in
  header `X-Bridge-Token` generato da chi avvia il processo.
- Il pairing e' sempre un'azione esplicita: il QR viene mostrato nell'app e
  l'utente lo inquadra da WhatsApp. Niente pairing automatico.
- Risponde SOLO nella self-chat: mai risposte automatiche ad altre persone.
- La sessione vive in un SQLite locale (schema whatsmeow). Il file e' un
  segreto bearer: non esce dal dispositivo e non va committato.

## API HTTP (JSON)

| Metodo | Path | Risposta |
| --- | --- | --- |
| GET | `/healthz` | `{"ok":true,"linked":bool,"jid":"...","pushname":"..."}` |
| POST | `/pair` | avvia il flusso QR (no-op se gia' collegato) |
| GET | `/qr` | `{"state":"qr","qr":"2@..."}` / `{"state":"paired"}` / `{"state":"expired"}` |
| POST | `/send` | `{"text":...}` invia nella self-chat |
| GET | `/events?after=N` | `{"events":[{"seq":N,"text":"...","ts":...}]}` |
| POST | `/disconnect` | logout e uscita (il chiamante cancella il db) |

## Build

```bash
go build ./...
```

Dipendenze in `go.mod` (whatsmeow e il driver SQLite modernc, senza cgo).
