import { useEffect, useRef, useState } from "react";
import { api, type AskResponse, type ModelSettings } from "../api";
import { formatCitation } from "../lib";
import ModelPicker from "./ModelPicker";

interface Turn {
  question: string;
  response: AskResponse;
}

// Bozza del composer tenuta in sessionStorage: se la sessione cade (401 ->
// logout) mentre l'utente sta scrivendo, il testo non va perso.
const DRAFT_KEY = "atlas_chat_draft";

function readDraft(): string {
  try {
    return sessionStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(v: string) {
  try {
    if (v) sessionStorage.setItem(DRAFT_KEY, v);
    else sessionStorage.removeItem(DRAFT_KEY);
  } catch { /* storage non disponibile: la bozza resta solo in memoria */ }
}

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInputState] = useState<string>(readDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [modelSettings, setModelSettings] = useState<ModelSettings | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function setInput(v: string) {
    setInputState(v);
    writeDraft(v);
  }

  useEffect(() => {
    api.getModelSettings().then(setModelSettings).catch(() => setModelSettings(null));
  }, []);

  const cloudLabel = modelSettings && modelSettings.provider_kind === "cloud"
    ? modelSettings.providers.find((p) => p.id === modelSettings.provider)?.label ?? modelSettings.provider
    : null;

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
      writeDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Errore");
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  return (
    <div className="chat">
      <div className="chat-topbar">
        {cloudLabel
          ? <span className="cloud-badge" title="Le risposte passano da un provider cloud esterno">Cloud: {cloudLabel}</span>
          : <span />}
        <button type="button" className="ghost" onClick={() => setPickerOpen(true)}>Modello</button>
      </div>
      {pickerOpen && (
        <ModelPicker onClose={() => setPickerOpen(false)} onSaved={setModelSettings} />
      )}
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
                {t.response.model?.includes("->extractive-fallback") && (
                  <div className="fallback-note">Modello AI non raggiungibile: questa risposta e solo estratta dalle memorie, senza generazione.</div>
                )}
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
        <input ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} autoFocus
               placeholder='Chiedi qualcosa, oppure "ricorda che ..."' />
        <button className="primary" disabled={busy}>{busy ? "..." : "Invia"}</button>
      </form>
    </div>
  );
}
