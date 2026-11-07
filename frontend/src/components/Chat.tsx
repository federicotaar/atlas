import { useState } from "react";
import { api, type AskResponse } from "../api";
import { formatCitation } from "../lib";

interface Turn {
  question: string;
  response: AskResponse;
}

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.ask(text, conversationId ?? undefined);
      if (response.conversation_id) setConversationId(response.conversation_id);
      setTurns((t) => [...t, { question: text, response }]);
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Errore");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      {turns.length === 0 && (
        <div className="empty-chat">
          <img src="/atlas-logo.png" alt="" />
          <div className="hello">Come posso aiutarti?</div>
          <div className="hint">Fai una domanda, oppure scrivi "ricorda che ..." per salvare una memoria.</div>
        </div>
      )}
      {turns.length > 0 && (
        <div className="turns">
          {turns.map((t, i) => (
            <div className="turn" key={i}>
              <div className="bubble user">
                <div className="bubble-label">Tu</div>
                {t.question}
              </div>
              <div className="bubble atlas">
                <div className="bubble-label">
                  <img className="avatar" src="/atlas-logo.png" alt="" />
                  ATLAS
                </div>
                <div className="answer">{t.response.answer}</div>
                {t.response.citations.length > 0 && (
                  <ul className="cits">
                    {t.response.citations.map((c) => <li key={c.memory_id}>{formatCitation(c)}</li>)}
                  </ul>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {error && <div className="err">{error}</div>}
      <form className="composer" onSubmit={send}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
               placeholder='Chiedi qualcosa, oppure "ricorda che ..."' />
        <button className="primary" disabled={busy}>{busy ? "..." : "Invia"}</button>
      </form>
    </div>
  );
}
