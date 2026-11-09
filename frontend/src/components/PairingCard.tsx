/** Richieste di pairing dei canali dell'agente (Telegram/WhatsApp).
 *  Chi scrive ad ATLAS senza essere in allowlist non riceve MAI risposta:
 *  qui il proprietario vede la richiesta e decide. L'approvazione avviene
 *  solo da questa superficie locale autenticata, mai dalla chat. */
import { useEffect, useRef, useState } from "react";
import { api, type PairingRequest } from "../api";

export default function PairingCard() {
  const [items, setItems] = useState<PairingRequest[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = () => {
    api.getPairingRequests()
      .then((r) => { setItems(r); setUnavailable(false); setError(null); })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Errore";
        // 501: server mode, i pairing si governano dalla CLI dell'agente.
        if (/501|desktop|CLI/.test(msg)) { setUnavailable(true); setItems([]); }
        else setError(msg);
      });
  };

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, 15000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  const decide = async (r: PairingRequest, action: "approve" | "reject") => {
    const key = `${r.channel}:${r.sender}`;
    setBusy(key); setError(null);
    try {
      if (action === "approve") await api.approvePairing(r.channel, r.sender);
      else await api.rejectPairing(r.channel, r.sender);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Operazione fallita.");
    } finally {
      setBusy(null);
    }
  };

  // Server mode o errore di rete: la card resta e lo dice, non svanisce.
  if (unavailable) {
    return (
      <section className="panel">
        <h3>Richieste di pairing</h3>
        <p className="muted">
          In questa modalita' i contatti dei canali si approvano dalla CLI
          dell'agente (comando /pairing).
        </p>
      </section>
    );
  }

  return (
    <section className="panel">
      <h3>Richieste di pairing</h3>
      {error && <div className="err">{error}</div>}
      {items === null && <p className="muted">Caricamento…</p>}
      {items !== null && items.length === 0 && (
        <p className="muted">
          Nessuna richiesta in sospeso. Chi scrive ad ATLAS senza essere
          approvato non riceve risposta e compare qui.
        </p>
      )}
      {items?.map((r) => {
        const key = `${r.channel}:${r.sender}`;
        return (
          <div className="item" key={key}>
            <strong>{r.channel === "telegram" ? "Telegram" : "WhatsApp"}</strong> —{" "}
            {r.display ? <>{r.display} (<code>{r.sender}</code>)</> : <code>{r.sender}</code>}
            <div className="muted">
              {new Date(r.ts * 1000).toLocaleString("it-IT")} — approva solo
              se riconosci il contatto: da quel momento ATLAS gli risponde.
            </div>
            <div className="row">
              <button className="primary" disabled={busy === key}
                      onClick={() => decide(r, "approve")}>
                {busy === key ? "…" : "Approva"}
              </button>
              <button className="ghost" disabled={busy === key}
                      onClick={() => decide(r, "reject")}>
                Respingi
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
