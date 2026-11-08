/** Connettore Telegram: collega il bot, accoppia con /start <codice>, scollega.
 *  Il token resta solo nel backend (cifrato). L'accoppiamento e' sempre
 *  un'azione esplicita dell'utente dalla sua app Telegram. */
import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { api, type TelegramStatus } from "../api";

export default function TelegramCard() {
  const [st, setSt] = useState<TelegramStatus | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [qr, setQr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = () => {
    api.getTelegramStatus().then((s) => { setSt(s); setError(null); }).catch(() => setError("Stato Telegram non raggiungibile."));
  };

  useEffect(() => {
    refresh();
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  // Finché il bot e' collegato ma non accoppiato, ricontrolla lo stato
  // (l'accoppiamento avviene dalla chat Telegram dell'utente).
  useEffect(() => {
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
    if (st?.connected && !st.paired) {
      timer.current = setInterval(refresh, 5000);
    }
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [st?.connected, st?.paired]);

  const connect = async () => {
    setBusy(true); setError(null);
    try {
      setSt(await api.connectTelegram(token.trim()));
      setToken("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Collegamento fallito.");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true); setError(null);
    try {
      await api.disconnectTelegram();
      await refresh();
    } catch {
      setError("Scollegamento fallito.");
    } finally {
      setBusy(false);
    }
  };

  // Il QR codifica il deep-link di pairing (segreto bearer a vita breve):
  // generato localmente, mai inviato a servizi esterni.
  useEffect(() => {
    if (!st?.deep_link) { setQr(null); return; }
    QRCode.toDataURL(st.deep_link, { width: 220, margin: 1 })
      .then(setQr)
      .catch(() => setQr(null));
  }, [st?.deep_link]);

  const rotate = async () => {
    setBusy(true); setError(null);
    try {
      setSt(await api.rotateTelegramCode());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rotazione codice fallita.");
    } finally {
      setBusy(false);
    }
  };

  const copyLink = async () => {
    if (!st?.deep_link) return;
    try {
      await navigator.clipboard.writeText(st.deep_link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard non disponibile: il link resta visibile */ }
  };

  // Mai sparire in silenzio: stato non arrivato = card visibile con errore
  // e riprova, non una sezione che svanisce dalla Dashboard.
  if (!st) {
    return (
      <section className="panel">
        <h3>Telegram</h3>
        {error ? (
          <>
            <div className="err">{error}</div>
            <div className="row">
              <button className="ghost" onClick={refresh}>Ricontrolla</button>
            </div>
          </>
        ) : (
          <p className="muted">Caricamento…</p>
        )}
      </section>
    );
  }

  return (
    <section className="panel">
      <h3>Telegram</h3>
      {error && <div className="err">{error}</div>}

      {!st.connected && (
        <>
          <p className="muted">
            Crea un bot con @BotFather su Telegram, poi incolla qui il suo token.
            Il token resta salvato solo su questo dispositivo.
          </p>
          <div className="row">
            <input
              type="password"
              placeholder="Token del bot (123456789:AA…)"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              aria-label="Token del bot Telegram"
            />
            <button className="primary" onClick={connect} disabled={busy || token.trim().length < 20}>
              {busy ? "Verifica…" : "Collega"}
            </button>
          </div>
        </>
      )}

      {st.connected && (
        <>
          <p>
            Bot collegato: <strong>@{st.bot_username}</strong>{" "}
            {st.paired ? "— accoppiato al tuo account." : "— in attesa di accoppiamento."}
          </p>
          {!st.paired && st.pairing_expired && (
            <div className="item">
              <p className="muted">Il codice di accoppiamento e' scaduto (vale 15 minuti).</p>
              <div className="row">
                <button className="ghost" onClick={rotate} disabled={busy}>Genera nuovo codice</button>
              </div>
            </div>
          )}
          {!st.paired && st.deep_link && (
            <div className="item">
              <p className="muted">
                Per accoppiare: inquadra il QR con il telefono, oppure apri il link e premi
                «Avvia» nella chat del bot, oppure scrivi al bot il comando{" "}
                <code>/start {st.deep_link.split("start=")[1]}</code>.
                Il codice vale 15 minuti e si usa una sola volta.
              </p>
              {qr && (
                <p>
                  <img src={qr} width={220} height={220}
                       alt="QR di accoppiamento Telegram" />
                </p>
              )}
              <div className="row">
                <a className="ghost" href={st.deep_link} target="_blank" rel="noreferrer">
                  Apri in Telegram
                </a>
                <button className="ghost" onClick={copyLink}>{copied ? "Copiato" : "Copia link"}</button>
                <button className="ghost" onClick={rotate} disabled={busy}>Nuovo codice</button>
                <button className="ghost" onClick={refresh}>Ricontrolla</button>
              </div>
            </div>
          )}
          <div className="row">
            <button className="ghost" onClick={disconnect} disabled={busy}>Scollega</button>
          </div>
        </>
      )}
    </section>
  );
}
