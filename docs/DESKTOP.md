# App desktop (Tauri) con backend incluso

`desktop/` e una shell Tauri v2 attorno al frontend web, con il backend
Python **incluso nell'installazione** come sidecar: all'avvio dell'app il
backend parte da solo, senza Docker, senza servizi esterni, senza setup
manuale.

## Come funziona

- All'avvio la shell Rust (`desktop/src/lib.rs`) controlla `127.0.0.1:8000`:
  - se risponde gia' un'istanza ATLAS compatibile (`/health` con
    `app="atlas"`, `api="v1"`), la **riusa** e non la chiude mai;
  - se la porta e' occupata da un servizio che non e' ATLAS, l'app lo
    segnala (`backend-port-busy`) e **non uccide nulla**;
  - altrimenti avvia il sidecar `atlas-backend` (externalBin) e alla
    chiusura della finestra ferma **solo** il processo che ha avviato lei.
- Il sidecar e' il backend FastAPI impacchettato con PyInstaller
  (`backend/scripts/build_sidecar.sh`); gira con database **SQLite** nella
  app-data dir (`ATLAS_DESKTOP_DATA_DIR`), provider locali di default
  (embedding hash + LLM estrattivo) e segreti generati e persistiti in
  locale: nessuna configurazione richiesta.
- Il frontend rileva il webview Tauri e chiama `http://127.0.0.1:8000`
  direttamente (`resolveBase` in `frontend/src/api.ts`); il backend espone
  CORS solo per le origini Tauri.

## Dati esistenti (migrazione da Postgres)

Se prima usavi ATLAS 2.0.x con il docker-compose distribuito (i default
documentati, Postgres `atlas` su localhost:5432), l'app lo rileva da sola al
primo avvio e mostra direttamente il banner: niente da configurare. Con un
setup personalizzato imposta invece `ATLAS_LEGACY_DATABASE_URL` sull'URL del
vecchio database prima del primo avvio (ha sempre la precedenza). Al login
l'app mostra un banner:

- **origine raggiungibile con dati** → puoi importare tutto (memorie,
  fonti, eventi, impostazioni e credenziali **cifrate**) oppure continuare
  senza importare. Le credenziali cifrate con il default pubblico del
  compose 2.0.x vengono decifrate e ri-cifrate al volo con la chiave locale
  nuova (che non viene mai sostituita); quelle cifrate con una tua chiave
  personalizzata vanno re-inserite dopo l'accesso (vengono segnalate);
- **origine irraggiungibile** → l'app lo dice esplicitamente e NON parte
  come profilo nuovo in silenzio: riprova o continua senza importare
  (scelta registrata);
- l'import avviene solo su un profilo nuovo e vuoto, mai sopra dati
  esistenti, e non modifica ne' cancella mai il Postgres di origine.

## Sidecar wa-bridge (WhatsApp)

Il connettore WhatsApp usa un secondo binario, `wa-bridge` (Go, whatsmeow,
statico), distribuito come externalBin accanto al backend
(`desktop/bin/wa-bridge-<triple>`, build CI con `actions/setup-go`).
All'avvio `desktop_main` lo risolve accanto all'eseguibile del backend e
imposta sessione e dedup nella app-data dir; il bridge ascolta solo su
127.0.0.1 con token effimero di processo e viene avviato solo quando
l'utente collega WhatsApp dalle impostazioni. Rotta non ufficiale: vedi
l'avviso rischio in README.

## Sviluppo

```
npm run build --prefix frontend
bash backend/scripts/build_sidecar.sh   # produce desktop/bin/atlas-backend-<triple>
cd wa-bridge && CGO_ENABLED=0 go build -o ../desktop/bin/wa-bridge-<triple> .
cd desktop && npx @tauri-apps/cli@2 dev
```

La CI (job `desktop`) compila frontend + sidecar + shell Rust, lancia
l'app pacchettata in headless (xvfb) e verifica end-to-end: bind solo su
127.0.0.1, modalita' auth `local_password`, registrazione di un profilo
locale guidata via UI (xdotool), salvataggio e richiamo con citazione via
UI, riavvio dell'app, nuovo login del profilo esistente via UI e
persistenza delle memorie (via UI e via `/me/export`).

## Modalita' Docker (sviluppo/backend-only)

Resta supportata per sviluppo: `docker compose up --build` avvia API +
PostgreSQL con pgvector su 127.0.0.1:8000 (stesso identico codice; il
backend sceglie il database da `ATLAS_DATABASE_URL`).
