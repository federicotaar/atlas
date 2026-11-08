import { useEffect, useState } from "react";
import { api, apiDiscoveryFailed, initApiBase, setToken } from "../api";

type Mode = "login" | "register";
type LoginMethod = null | "password" | "code";
type RegisterStep = "email" | "code" | "password";

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
  const [busy, setBusy] = useState(false);
  // modes = capacita' auth dell'istanza; null = backend non ancora pronto.
  // Il backend (sidecar) puo' impiegare secondi ad avviarsi: si riprova
  // finche' non risponde, altrimenti la prima schermata mostrerebbe scelte
  // sbagliate (es. codice email su installazione locale).
  const [modes, setModes] = useState<string[] | null>(null);
  // Errore terminale di avvio (backend non trovato oltre il limite): messaggio
  // esplicito con Riprova invece di una splash silenziosa infinita.
  const [fatal, setFatal] = useState<string | null>(null);
  const local = modes?.includes("local_password") ?? false;
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      api
        .modes()
        .then((r) => {
          if (alive) setModes(r.modes);
        })
        .catch(() => {
          if (!alive) return;
          // Il gate di scoperta ha fallito in via definitiva: fermare il
          // retry silenzioso e mostrare l'errore con un tasto Riprova.
          if (apiDiscoveryFailed()) {
            setFatal(
              "Il backend locale non e' stato trovato. Chiudi e riapri ATLAS; se il problema continua, riavvia il computer.",
            );
            return;
          }
          setTimeout(load, 1000);
        });
    };
    load();
    return () => {
      alive = false;
    };
  }, []);

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
      const res = await api.requestCode(email, purpose);
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

  if (modes === null) {
    return (
      <div className="auth-wrap">
        <div className="auth-card">
          <img src="/atlas-logo.png" alt="ATLAS" className="auth-logo" />
          <h1 className="auth-title">ATLAS</h1>
          {fatal ? (
            <>
              <p className="auth-sub">{fatal}</p>
              <button
                className="auth-btn"
                onClick={() => {
                  setFatal(null);
                  initApiBase().catch(() => {});
                  api
                    .modes()
                    .then((r) => setModes(r.modes))
                    .catch(() => setFatal(fatal));
                }}
              >
                Riprova
              </button>
            </>
          ) : (
            <p className="auth-sub">Avvio in corso…</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <img src="/atlas-logo.png" alt="ATLAS" className="auth-logo" />
        <h1 className="auth-title">ATLAS</h1>
        <p className="auth-sub">Ricorda, ragiona e prevede.</p>

        {mode === "login" && method === null && !local && (
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

        {mode === "login" && (method === "password" || (local && method === null)) && (
          <form className="auth" onSubmit={submit}>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input type="password" placeholder="password" value={password}
                   onChange={(e) => setPassword(e.target.value)} required />
            {error && <div className="err">{error}</div>}
            <button type="submit" className="primary" disabled={busy}>Entra</button>
            {!(local && method === null) && (
              <button type="button" className="ghost" onClick={() => { setMethod(null); setError(null); }}>
                Indietro
              </button>
            )}
            {local && method === null && (
              <button type="button" className="ghost" onClick={() => reset("register")}>
                Non ho un profilo
              </button>
            )}
          </form>
        )}

        {mode === "login" && method === "code" && (
          <form className="auth" onSubmit={submit}>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <button type="button" className="ghost" disabled={busy || !email}
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

        {mode === "register" && local && (
          <form className="auth" onSubmit={submit}>
            <p className="auth-ask">Crea il tuo profilo locale</p>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            <input type="password" placeholder="scegli una password (min 8 caratteri)" value={password}
                   onChange={(e) => setPassword(e.target.value)} required minLength={8} />
            {error && <div className="err">{error}</div>}
            <button type="submit" className="primary" disabled={busy}>Crea profilo</button>
            <p className="auth-note">Il profilo vive solo su questo dispositivo: niente account cloud.</p>
            <button type="button" className="ghost" onClick={() => reset("login")}>
              Ho gia un profilo
            </button>
          </form>
        )}

        {mode === "register" && !local && (
          <form className="auth" onSubmit={submit}>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)}
                   required disabled={step !== "email"} />
            {step === "email" && (
              <>
                    <button type="button" className="primary" disabled={busy || !email}
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
