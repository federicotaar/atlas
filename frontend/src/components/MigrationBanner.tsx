import { useEffect, useState } from "react";
import { api } from "../api";

type State =
  | { state: "loading" }
  | { state: "hidden" }
  | { state: "has_data"; counts: Record<string, number> }
  | { state: "unreachable"; error: string }
  | { state: "busy" }
  | { state: "imported"; undecryptable: number }
  | { state: "error"; message: string };

/** Primo avvio desktop: se esiste un Postgres precedente, propone l'import;
 * se e' irraggiungibile, avvisa esplicitamente (mai partire in silenzio).
 * Ogni scelta (importa / continua senza) e' registrata lato backend. */
export default function MigrationBanner({ onRelogin }: { onRelogin: () => void }) {
  const [st, setSt] = useState<State>({ state: "loading" });

  useEffect(() => {
    let alive = true;
    api.getMigrationStatus()
      .then((r) => {
        if (!alive) return;
        if (r.state === "has_data") setSt({ state: "has_data", counts: r.counts });
        else if (r.state === "unreachable") setSt({ state: "unreachable", error: r.error });
        else setSt({ state: "hidden" });
      })
      .catch(() => alive && setSt({ state: "hidden" }));
    return () => {
      alive = false;
    };
  }, []);

  function act(kind: "import" | "dismiss") {
    setSt({ state: "busy" });
    const call = kind === "import" ? api.importMigration() : api.dismissMigration();
    call
      .then((r) =>
        setSt(
          kind === "import"
            ? { state: "imported", undecryptable: r.undecryptable_credentials ?? 0 }
            : { state: "hidden" }
        )
      )
      .catch((e: Error) => setSt({ state: "error", message: e.message }));
  }

  if (st.state === "hidden" || st.state === "loading") return null;

  return (
    <div className="migration-banner" role="alert">
      {st.state === "has_data" && (
        <>
          <span>
            Trovati dati ATLAS esistenti ({st.counts.memories ?? 0} memorie).
            L&apos;import SOSTITUISCE il profilo attuale (provvisorio): al
            termine accederai con le credenziali originali. Vuoi importarli?
          </span>
          <button onClick={() => act("import")}>Importa e sostituisci</button>
          <button className="ghost" onClick={() => act("dismiss")}>
            Continua senza importare
          </button>
        </>
      )}
      {st.state === "unreachable" && (
        <>
          <span>
            Il database ATLAS esistente non e' raggiungibile ({st.error}). Non ho
            creato un profilo vuoto in silenzio: riprova piu' tardi oppure
            continua senza importare.
          </span>
          <button className="ghost" onClick={() => act("dismiss")}>
            Continua senza importare
          </button>
        </>
      )}
      {st.state === "busy" && <span>Importazione in corso…</span>}
      {st.state === "imported" && (
        <>
          <span>
            Importazione completata. Il profilo provvisorio e' stato sostituito:
            accedi con le tue credenziali originali.
            {st.undecryptable > 0 &&
              ` Attenzione: ${st.undecryptable} chiavi API dei provider non sono
                decifrabili su questa installazione: reinseriscile nelle
                impostazioni dopo l'accesso.`}
          </span>
          <button onClick={onRelogin}>Vai all&apos;accesso</button>
        </>
      )}
      {st.state === "error" && (
        <>
          <span>Importazione non riuscita: {st.message}</span>
          <button className="ghost" onClick={() => setSt({ state: "hidden" })}>
            Chiudi
          </button>
        </>
      )}
    </div>
  );
}
