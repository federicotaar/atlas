import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initApiBase } from "./api";

// In Tauri: scopre la porta reale del backend prima del primo render
// (il launcher puo' averne scelta una di riserva rispetto a 8000). In caso di
// errore di discovery si renderizza comunque con il default: mai schermo
// bianco per un backend che non risponde ancora.
initApiBase()
  .catch(() => undefined)
  .finally(() => {
    ReactDOM.createRoot(document.getElementById("root")!).render(
      <React.StrictMode>
        <App />
      </React.StrictMode>,
    );
  });
