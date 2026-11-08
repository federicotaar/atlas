import { useState } from "react";
import { hasToken, setToken } from "./api";
import Auth from "./components/Auth";
import Chat from "./components/Chat";
import Dashboard from "./components/Dashboard";
import MigrationBanner from "./components/MigrationBanner";

export default function App() {
  const [logged, setLogged] = useState(hasToken());
  const [view, setView] = useState<"chat" | "dashboard">("chat");

  function logout() {
    setToken(null);
    setLogged(false);
  }

  if (!logged) {
    return <Auth onLogin={() => setLogged(true)} />;
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
      <MigrationBanner />
      <main className={view === "chat" ? "view view-chat" : "view"}>
        {view === "chat" ? <Chat /> : <Dashboard />}
      </main>
    </>
  );
}
