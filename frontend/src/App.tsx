import { useState } from "react";
import { hasToken, setToken } from "./api";
import Auth from "./components/Auth";
import Chat from "./components/Chat";

export default function App() {
  const [logged, setLogged] = useState(hasToken());
  return (
    <>
      <h1>ATLAS</h1>
      {logged ? (
        <Chat onLogout={() => { setToken(null); setLogged(false); }} />
      ) : (
        <Auth onLogin={() => setLogged(true)} />
      )}
    </>
  );
}
