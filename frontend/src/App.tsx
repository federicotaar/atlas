import { useState } from "react";
import { hasToken, setToken } from "./api";
import Auth from "./components/Auth";
import Chat from "./components/Chat";
import Dashboard from "./components/Dashboard";

export default function App() {
  const [logged, setLogged] = useState(hasToken());
  const [view, setView] = useState<"chat" | "dashboard">("chat");
  return (
    <>
      <h1>ATLAS</h1>
      {logged ? (
        <>
          <nav style={{ marginBottom: "1rem" }}>
            <button disabled={view === "chat"} onClick={() => setView("chat")}>Chat</button>{" "}
            <button disabled={view === "dashboard"} onClick={() => setView("dashboard")}>Dashboard</button>
          </nav>
          {view === "chat" ? (
            <Chat onLogout={() => { setToken(null); setLogged(false); }} />
          ) : (
            <Dashboard />
          )}
        </>
      ) : (
        <Auth onLogin={() => setLogged(true)} />
      )}
    </>
  );
}
