import { useState } from "react";
import { api } from "../api";

type ProbeState =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "none" }
  | { kind: "unreachable"; error: string }
  | { kind: "has_data"; counts: Record<string, number> }
  | { kind: "importing" }
  | { kind: "imported"; undecryptable: number }
  | { kind: "error"; message: string };

/** Voce sempre disponibile: importa memorie e impostazioni da una versione
 * precedente (Docker/Postgres 2.0.x). Il rilevamento e' su richiesta e senza
 * terminale: distingue "nessun dato noto" da "trovato ma non raggiungibile"
 * (vecchio backend spento) senza mai dire che non c'e' nulla. */
export default function MigrationCard({ onRelogin }: { onRelogin: () => void }) {
  const [st, setSt] = useState<ProbeState>({ kind: "idle" });

  function probe() {
    setSt({ kind: "busy" });
    api
      .probeMigration()
      .then((r) => {
        if (r.state === "has_data") setSt({ kind: "has_data", counts: r.counts });
        else if (r.state === "unreachable") setSt({ kind: "unreachable", error: r.error });
        else setSt({ kind: "none" });
      })
      .catch((e: Error) => setSt({ kind: "error", message: e.message }));
  }

  function doImport() {
    setSt({ kind: "importing" });
    api
      .importMigration()
      .then((r) => setSt({ kind: "imported", undecryptable: r.undecryptable_credentials ?? 0 }))
      .catch((e: Error) => setSt({ kind: "error", message: e.message }));
  }

  return (
    <section className="panel">
      <h3>Importa dati da una versione precedente</h3>
      {st.kind === "idle" && (
        <>
          <p className="muted">
            Se usavi ATLAS con Docker (versione 2.0.x), puoi importare memorie,
            fonti e impostazioni. Il vecchio database non viene modificato.
          </p>
          <button className="primary" onClick={probe}>Verifica dati precedenti</button>
        </>
      )}
      {st.kind === "busy" && <p className="muted">Verifica in corso…</p>}
      {st.kind === "none" && (
        <>
          <p className="muted">
            Nessun dato di una versione precedente trovato nelle posizioni note
            di questo computer. Se il vecchio backend Docker e' spento, avvialo
            e riprova: niente viene cancellato.
          </p>
          <button className="ghost" onClick={probe}>Riprova</button>
        </>
      )}
      {st.kind === "unreachable" && (
        <>
          <p className="muted">
            Il database della versione precedente non risponde ({st.error}).
            NON significa che i dati non ci siano: avvia il vecchio backend
            Docker e riprova.
          </p>
          <button className="ghost" onClick={probe}>Riprova</button>
        </>
      )}
      {st.kind === "has_data" && (
        <>
          <p>
            Trovati dati ATLAS esistenti ({st.counts.memories ?? 0} memorie).
            L&apos;import SOSTITUISCE il profilo attuale (provvisorio): al
            termine accederai con le credenziali originali.
          </p>
          <button className="primary" onClick={doImport}>Importa e sostituisci</button>
          <button className="ghost" onClick={() => setSt({ kind: "idle" })}>
            Annulla
          </button>
        </>
      )}
      {st.kind === "importing" && <p className="muted">Importazione in corso…</p>}
      {st.kind === "imported" && (
        <>
          <p>
            Importazione completata: accedi con le tue credenziali originali.
            {st.undecryptable > 0 &&
              ` Attenzione: ${st.undecryptable} chiavi API dei provider non sono
                decifrabili su questa installazione: reinseriscile nelle
                impostazioni dopo l'accesso.`}
          </p>
          <button className="primary" onClick={onRelogin}>Vai all&apos;accesso</button>
        </>
      )}
      {st.kind === "error" && (
        <>
          <p className="muted">Import non riuscito: {st.message}</p>
          <button className="ghost" onClick={probe}>Riprova</button>
        </>
      )}
    </section>
  );
}
