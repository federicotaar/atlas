/** Connettore WhatsApp: linked-device tramite bridge locale (rotta non
 *  ufficiale). Il pairing e' sempre uno scan esplicito dell'utente dal suo
 *  WhatsApp; ATLAS legge/risponde solo nella chat con se stessi. L'avviso
 *  rischio restrizioni/ban resta visibile nella card, sempre. */
import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { api, type WhatsAppStatus } from "../api";

export default function WhatsAppCard() {
  const [st, setSt] = useState<WhatsAppStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = () => {
    api.getWhatsAppStatus().then((s) => { setSt(s); setError(null); }).catch(() => setError("Stato WhatsApp non raggiungibile."));
  };

  useEffect(() => {
    refresh();
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  // Finché non e' collegato, ricontrolla: il QR ruota (~20s) e
  // l'accoppiamento avviene dal telefono dell'utente.
  useEffect(() => {
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
    if (st?.connected && !st.linked) {
      timer.current = setInterval(refresh, 5000);
    }
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [st?.connected, st?.linked]);

  const connect = async () => {
    setBusy(true); setError(null);
    try {
      setSt(await api.connectWhatsApp());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Collegamento fallito.");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true); setError(null);
    try {
      await api.disconnectWhatsApp();
      await refresh();
    } catch {
      setError("Scollegamento fallito.");
    } finally {
      setBusy(false);
    }
  };

  // Il QR codifica il segreto di pairing del bridge: generato localmente,
  // mai inviato a servizi esterni, ruota da solo.
  useEffect(() => {
    if (!st?.qr || st.qr_state !== "qr") { setQr(null); return; }
    QRCode.toDataURL(st.qr, { width: 220, margin: 1 })
      .then(setQr)
      .catch(() => setQr(null));
  }, [st?.qr, st?.qr_state]);

  // Mai sparire in silenzio: se lo stato non arriva, la card resta visibile
  // con l'errore e un modo per riprovare (una card che svanisce si legge
  // come "il setup non esiste").
  if (!st) {
    return (
      <section className="panel">
        <h3>WhatsApp</h3>
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
      <h3>WhatsApp</h3>
      {error && <div className="err">{error}</div>}
      <p className="muted">
        Collegamento non ufficiale (dispositivo collegato): puo' comportare
        restrizioni o il ban dell&apos;account WhatsApp. ATLAS risponde solo
        nella chat con te stesso.
      </p>

      {!st.connected && (
        <div className="row">
          <button className="primary" onClick={connect} disabled={busy}>
            {busy ? "Avvio…" : "Collega WhatsApp"}
          </button>
        </div>
      )}

      {st.connected && !st.linked && (
        <div className="item">
          {st.qr_state === "qr" && qr ? (
            <>
              <p className="muted">
                Inquadra il QR da WhatsApp &gt; Dispositivi collegati &gt; Collega
                dispositivo. Il codice ruota da solo per sicurezza.
              </p>
              <p>
                <img src={qr} width={220} height={220} alt="QR di collegamento WhatsApp" />
              </p>
            </>
          ) : (
            <p className="muted">
              {st.qr_state === "expired"
                ? "QR scaduto: ricontrollo tra pochi secondi…"
                : "Preparazione del QR…"}
            </p>
          )}
          <div className="row">
            <button className="ghost" onClick={refresh}>Ricontrolla</button>
            <button className="ghost" onClick={disconnect} disabled={busy}>Annulla</button>
          </div>
        </div>
      )}

      {st.connected && st.linked && (
        <>
          <p>
            WhatsApp collegato{st.pushname ? <> come <strong>{st.pushname}</strong></> : null}.
            Scrivi ad ATLAS nella chat con te stesso: ricorda e risponde come nella app.
          </p>
          <div className="row">
            <button className="ghost" onClick={disconnect} disabled={busy}>Scollega</button>
          </div>
        </>
      )}
    </section>
  );
}
