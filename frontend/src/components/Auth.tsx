import { useState } from "react";
import { api, setToken } from "../api";

export default function Auth({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const res = mode === "login" ? await api.login(email, password) : await api.register(email, password);
      setToken(res.token);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Errore");
    }
  }

  return (
    <form className="auth" onSubmit={submit}>
      <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      <input type="password" placeholder="password (min 8 caratteri)" value={password}
             onChange={(e) => setPassword(e.target.value)} required minLength={8} />
      {error && <div className="err">{error}</div>}
      <button type="submit">{mode === "login" ? "Entra" : "Crea account"}</button>
      <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
        {mode === "login" ? "Non ho un account" : "Ho gia un account"}
      </button>
    </form>
  );
}
