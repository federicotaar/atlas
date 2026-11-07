# Involucro desktop (Tauri)

`desktop/` e una shell Tauri v2 attorno alla dashboard web esistente
(fase 5, specifica: "Dashboard web, poi involucro desktop Tauri").

## Come funziona

- La finestra carica il frontend gia compilato (`frontend/dist`,
  `frontendDist` in `desktop/tauri.conf.json`); `beforeBuildCommand`
  compila il frontend prima della build Rust.
- Nel webview Tauri non esiste il proxy `/api` del dev server: il
  frontend rileva `window.__TAURI_INTERNALS__` e chiama il backend
  direttamente su `http://127.0.0.1:8000` (`resolveBase` in
  `frontend/src/api.ts`, overridabile con `VITE_API_URL`).
- Il backend espone CORS **solo** per le origini Tauri
  (`http://tauri.localhost`, `tauri://localhost`), configurabili con
  `ATLAS_CORS_ORIGINS`. Il browser resta same-origin senza header CORS.
- Il backend resta un processo separato: ATLAS desktop si aspetta
  `uvicorn` in ascolto su 127.0.0.1:8000 (stessa modalita locale del
  resto del progetto, zero costi, nessun servizio esterno).

## Installazione dalla release (Ubuntu, consigliata)

Dalla [release v0.1.0](https://github.com/federicotaar/atlas/releases/tag/v0.1.0):
`ATLAS_0.1.0_amd64.AppImage` oppure `ATLAS_0.1.0_amd64.deb`
(`sudo apt install ./ATLAS_0.1.0_amd64.deb`, che installa anche le
dipendenze).

Prerequisiti verificati: Ubuntu 22.04 o 24.04 (i pacchetti
`libwebkit2gtk-4.1-0` e `libegl1` esistono in entrambe; con l'AppImage, se
mancanti: `sudo apt install libwebkit2gtk-4.1-0 libegl1`) e Docker con
Docker Compose per il backend.

```
cp .env.example .env   # modifica ATLAS_JWT_SECRET
docker compose up --build        # API + PostgreSQL su 127.0.0.1:8000
chmod +x ATLAS_0.1.0_amd64.AppImage
./ATLAS_0.1.0_amd64.AppImage
```

Il desktop richiede il backend in ascolto su 127.0.0.1:8000 (vedi "Come
funziona"): avvia prima il compose, poi l'app; senza backend si vede solo
la schermata di accesso.

- Risposte AI: di default deterministiche, gratuite e offline (provider
  `local`/`extractive`); con Ollama opzionale diventano semantiche e
  generative (README, "Modelli locali opzionali").
- Account Google: opzionale e solo di chi installa; setup sicuro in
  `docs/CONNECTORS-GOOGLE.md` (client ID e secret nel `.env` locale: mai
  nel repo, in chat o in un sito pubblico).

## Build locale

Richiede Rust stabile, Node 20 e le dipendenze di sistema di Tauri
(su Ubuntu: `libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev
patchelf`).

```
cd desktop
npx --yes @tauri-apps/cli@^2 icon app-icon.png   # genera icons/ (gitignored)
npx --yes @tauri-apps/cli@^2 build
```

## Build in CI

`.github/workflows/desktop.yml` compila su ubuntu-latest e macos-latest
a ogni push su `desktop/` o `frontend/` (e manualmente), genera le
icone da `desktop/app-icon.png` (logo v7 approvato, pixel esatti) e
carica i bundle (.deb/.AppImage, .dmg) come artifact. Al publish di una
release il job `release-assets` allega automaticamente AppImage e .deb alla
release stessa.

L'icona dell'app e il logo v7 approvato, copiato byte per byte da
`assets/atlas-logo.png`: mai ricrearlo.
