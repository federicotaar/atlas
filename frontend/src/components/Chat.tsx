import { useState } from "react";
import { api, type AskResponse } from "../api";
import { formatCitation } from "../lib";

interface Turn {
  question: string;
  response: AskResponse;
}

export default function Chat({ onLogout }: { onLogout: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.ask(text);
      setTurns((t) => [...t, { question: text, response }]);
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Errore");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {turns.map((t, i) => (
        <div className="msg" key={i}>
          <div><strong>Tu:</strong> {t.question}</div>
          <div style={{ marginTop: "0.5rem", whiteSpace: "pre-wrap" }}><strong>ATLAS:</strong> {t.response.answer}</div>
          {t.response.citations.length > 0 && (
            <ul className="cits">
              {t.response.citations.map((c) => <li key={c.memory_id}>{formatCitation(c)}</li>)}
            </ul>
          )}
        </div>
      ))}
      {error && <div className="err">{error}</div>}
      <form onSubmit={send}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
               placeholder='Chiedi qualcosa, oppure "ricorda che ..."' />
        <button disabled={busy}>{busy ? "..." : "Invia"}</button>
      </form>
      <button style={{ marginTop: "1rem", background: "#374151" }} onClick={onLogout}>Esci</button>
    </div>
  );
}
