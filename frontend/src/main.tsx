import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initApiBase } from "./api";

// Render immediato; in Tauri la scoperta della porta del backend gira in
// background e aggiorna BASE appena il sidecar risponde (vedi initApiBase).
initApiBase().catch(() => undefined);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
