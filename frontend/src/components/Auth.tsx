import { useEffect, useRef, useState } from "react";
import { api, setToken } from "../api";

type Mode = "login" | "register";
type LoginMethod = null | "password" | "code";
type RegisterStep = "email" | "code" | "password";

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      reset: (id?: string) => void;
      remove: (id?: string) => void;
    };
    onloadTurnstileCallback?: () => void;
  }
}

// Sitekey pubblica (non un segreto). La dummy key Cloudflare "sempre ok" e'
// consentita SOLO in vite dev (import.meta.env.DEV): una build di produzione
// senza VITE_TURNSTILE_SITEKEY NON ricade sul dummy (fail-closed: niente
// widget, invio codice disabilitato, errore visibile). La chiave reale
// richiede l'hostname della pagina in Hostname Management: localhost
// (Linux: tauri://localhost), tauri.localhost (Windows), dominio web.
// Validazione server-side SEMPRE nel worker (siteverify+remoteip).
const TURNSTILE_SITEKEY =
  (import.meta.env.VITE_TURNSTILE_SITEKEY as string | undefined) ||
  (import.meta.env.DEV ? "1x00000000000000000000AA" : "");

let turnstileLoader: Promise<void> | null = null;
function loadTurnstile(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  if (!turnstileLoader) {
    turnstileLoader = new Promise((resolve, reject) => {
      window.onloadTurnstileCallback = () => resolve();
      const s = document.createElement("script");
      s.src =
        "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onloadTurnstileCallback&render=explicit";
      s.async = true;
      s.onerror = () => reject(new Error("Verifica anti-bot non caricata. Controlla la connessione."));
      document.head.appendChild(s);
    });
  }
  return turnstileLoader;
}

function TurnstileBox({ onToken }: { onToken: (t: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!TURNSTILE_SITEKEY) return; // produzione senza sitekey: fail-closed
    let alive = true;
    let widgetId: string | null = null;
    loadTurnstile()
      .then(() => {
        if (!alive || !ref.current || !window.turnstile) return;
        widgetId = window.turnstile.render(ref.current, {
          sitekey: TURNSTILE_SITEKEY,
          theme: "dark",
          callback: (t: string) => onToken(t),
          "expired-callback": () => onToken(""),
          "error-callback": () => onToken(""),
        });
      })
      .catch(() => onToken(""));
    return () => {
      alive = false;
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!TURNSTILE_SITEKEY) {
    return <div className="err">Verifica anti-bot non configurata.</div>;
  }
  return <div ref={ref} className="auth-turnstile" />;
}

export default function Auth({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [method, setMethod] = useState<LoginMethod>(null);
  const [step, setStep] = useState<RegisterStep>("email");
  // signup_token: prova di continuazione rilasciata da /auth/codes/check,
  // consumabile una sola volta da /auth/register. Vive solo in memoria qui:
  // il server ne conserva solo l'hash e la rifiuta se non combacia con l'email.
  const [signupToken, setSignupToken] = useState("");
  const [turnstileToken, setTurnstileToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reset(next: Mode) {
    setMode(next);
    setMethod(null);
    setStep("email");
    setCode("");
    setError(null);
    setNotice(null);
  }

  function fail(err: unknown) {
    setError(err instanceof Error ? err.message : "Errore");
    setBusy(false);
  }

  async function sendCode(purpose: "register" | "login", onOk: () => void) {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const res = await api.requestCode(email, purpose, turnstileToken);
      setTurnstileToken(""); // token monouso: il prossimo invio richiede un nuovo challenge
      setNotice(`Codice inviato a ${email}. Vale ${Math.round(res.ttl_seconds / 60)} minuti.`);
      onOk();
    } catch (err) {
      fail(err);
      return;
    }
    setBusy(false);
  }

  async function verifyAndAdvance() {
    setError(null);
    setBusy(true);
    try {
      const res = await api.checkCode(email, code);
      setSignupToken(res.signup_token);
      setNotice("Email verificata. Ora scegli la tua password.");
      setStep("password");
    } catch (err) {
      fail(err);
      return;
    }
    setBusy(false);
  }

  function backFromPassword() {
    // Invalida lo stato locale: il token e' dimenticato e la prova server-side
    // scade da sola in 5 minuti; senza token nessuno puo' consumarla.
    setStep("email");
    setCode("");
    setPassword("");
    setSignupToken("");
    setNotice(null);
    setError(null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res =
        mode === "register"
          ? await api.register(email, password, signupToken)
          : method === "code"
            ? await api.loginCode(email, code)
            : await api.login(email, password);
      setToken(res.token);
      onLogin();
    } catch (err) {
      fail(err);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <img src="/atlas-logo.png" alt="ATLAS" className="auth-logo" />
        <h1 className="auth-title">ATLAS</h1>
        <p className="auth-sub">Ricorda, ragiona e prevede.</p>

        {mode === "login" && method === null && (
          <div className="auth">
            <p className="auth-ask">Come vuoi accedere?</p>
            <button type="button" className="primary" onClick={() => setMethod("code")}>
              Con codice via email
            </button>
            <button type="button" className="ghost" onClick={() => setMethod("password")}>
              Con password
            </button>
            <button type="button" className="ghost" onClick={() => reset("register")}>
              Non ho un account
            </button>
          </div>
        )}

        {mode === "login" && method === "password" && (
          <form className="auth" onSubmit={submit}>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input type="password" placeholder="password" value={password}
                   onChange={(e) => setPassword(e.target.value)} required />
            {error && <div className="err">{error}</div>}
            <button type="submit" className="primary" disabled={busy}>Entra</button>
            <button type="button" className="ghost" onClick={() => { setMethod(null); setError(null); }}>
              Indietro
            </button>
          </form>
        )}

        {mode === "login" && method === "code" && (
          <form className="auth" onSubmit={submit}>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <TurnstileBox onToken={setTurnstileToken} />
            <button type="button" className="ghost" disabled={busy || !email || !turnstileToken}
                    onClick={() => sendCode("login", () => {})}>
              {notice ? "Rinvia codice" : "Invia codice"}
            </button>
            {notice && <div className="ok">{notice}</div>}
            <input type="text" inputMode="numeric" autoComplete="one-time-code" placeholder="codice a 6 cifre"
                   value={code} onChange={(e) => setCode(e.target.value)} required minLength={4} maxLength={8} />
            {error && <div className="err">{error}</div>}
            <button type="submit" className="primary" disabled={busy}>Entra</button>
            <button type="button" className="ghost" onClick={() => { setMethod(null); setError(null); setNotice(null); }}>
              Indietro
            </button>
          </form>
        )}

        {mode === "register" && (
          <form className="auth" onSubmit={submit}>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)}
                   required disabled={step !== "email"} />
            {step === "email" && (
              <>
                <TurnstileBox onToken={setTurnstileToken} />
                <button type="button" className="primary" disabled={busy || !email || !turnstileToken}
                        onClick={() => sendCode("register", () => setStep("code"))}>
                  Invia codice
                </button>
              </>
            )}
            {step !== "email" && (
              <>
                {notice && <div className="ok">{notice}</div>}
                <input type="text" inputMode="numeric" autoComplete="one-time-code" placeholder="codice a 6 cifre"
                       value={code} onChange={(e) => setCode(e.target.value)} required minLength={4} maxLength={8}
                       disabled={step === "password"} />
                {step === "code" && (
                  <button type="button" className="primary" disabled={busy || code.length < 4}
                          onClick={verifyAndAdvance}>
                    Verifica codice
                  </button>
                )}
              </>
            )}
            {step === "password" && (
              <>
                <input type="password" placeholder="scegli una password (min 8 caratteri)" value={password}
                       onChange={(e) => setPassword(e.target.value)} required minLength={8} autoFocus />
                <button type="submit" className="primary" disabled={busy}>Crea account</button>
                <button type="button" className="ghost" onClick={backFromPassword}>
                  Indietro
                </button>
              </>
            )}
            {error && <div className="err">{error}</div>}
            <button type="button" className="ghost" onClick={() => reset("login")}>
              Ho gia un account
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
