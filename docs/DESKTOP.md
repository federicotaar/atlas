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
carica i bundle (.deb/.AppImage, .dmg) come artifact.

L'icona dell'app e il logo v7 approvato, copiato byte per byte da
`assets/atlas-logo.png`: mai ricrearlo.
