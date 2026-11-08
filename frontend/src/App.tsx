import { useEffect, useState } from "react";
import { hasToken, setOnUnauthorized, setToken } from "./api";
import Auth from "./components/Auth";
import Chat from "./components/Chat";
import Dashboard from "./components/Dashboard";
import MigrationBanner from "./components/MigrationBanner";

export default function App() {
  const [logged, setLogged] = useState(hasToken());
  const [view, setView] = useState<"chat" | "dashboard">("chat");
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);

  useEffect(() => {
    // Il backend ha rifiutato il token (401 su una richiesta autenticata):
    // si torna ad Auth con una spiegazione invece di lasciare l'app aperta
    // con ogni funzione rotta (sessione zombie).
    setOnUnauthorized(() => {
      setLogged(false);
      setSessionNotice(
        "La sessione non e' piu' valida (puo' succedere dopo un aggiornamento): accedi di nuovo. Nessun dato e' stato toccato.",
      );
    });
    return () => setOnUnauthorized(null);
  }, []);

  function logout() {
    setToken(null);
    setLogged(false);
  }

  if (!logged) {
    return (
      <Auth
        onLogin={() => {
          setSessionNotice(null);
          setLogged(true);
        }}
        sessionNotice={sessionNotice}
      />
    );
  }

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <img src="/atlas-logo.png" alt="ATLAS" className="logo" />
          <span className="wordmark">ATLAS</span>
        </div>
        <nav className="seg">
          <button
            className={view === "chat" ? "seg-item active" : "seg-item"}
            aria-pressed={view === "chat"}
            onClick={() => setView("chat")}
          >
            Chat
          </button>
          <button
            className={view === "dashboard" ? "seg-item active" : "seg-item"}
            aria-pressed={view === "dashboard"}
            onClick={() => setView("dashboard")}
          >
            Dashboard
          </button>
        </nav>
        <button className="ghost" onClick={logout}>Esci</button>
      </header>
      <MigrationBanner onRelogin={logout} />
      <main className={view === "chat" ? "view view-chat" : "view"}>
        {view === "chat" ? <Chat /> : <Dashboard onRelogin={logout} />}
      </main>
    </>
  );
}
