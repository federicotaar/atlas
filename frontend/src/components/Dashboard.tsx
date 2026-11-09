/** Dashboard (fase 5, spec 5I): ogni elemento mostra da quali dati deriva. */
import { useEffect, useState } from "react";
import { api, apiBase, type DashboardData } from "../api";
import TelegramCard from "./TelegramCard";
import WhatsAppCard from "./WhatsAppCard";
import PairingCard from "./PairingCard";
import MigrationCard from "./MigrationCard";

function Provenance({ data }: { data: Record<string, unknown> }) {
  return (
    <details className="prov">
      <summary>Da quali dati deriva</summary>
      <ul>
        {Object.entries(data).map(([k, v]) => (
          <li key={k}>
            <code>{k}</code>: {v === null ? <em>nessuno</em> : String(v)}
          </li>
        ))}
      </ul>
    </details>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

export default function Dashboard({ onRelogin }: { onRelogin: () => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dashboard().then(setData).catch((e) => setError(e instanceof Error ? e.message : "Errore"));
  }, []);

  if (error) return <div className="err">{error}</div>;
  if (!data) return <p>Caricamento...</p>;
  const p = data.panels;

  return (
    <div className="dashboard">
      <p className="muted">Generata il {new Date(data.generated_at).toLocaleString("it-IT")}.{" "}
        {data.provenance_rule}</p>

      <div className="dashboard-grid">
      <Section title={`Progetti (${p.projects.length})`}>
        {p.projects.length === 0 && <p className="muted">Nessun progetto.</p>}
        {p.projects.map((pr) => (
          <div className="item" key={pr.project_id}>
            <strong>{pr.name}</strong> — {pr.status}
            {pr.target_date && <> (obiettivo: {pr.target_date})</>}
            <div className="muted">{pr.events_count} eventi
              {pr.last_event_at && <> — ultimo: {new Date(pr.last_event_at).toLocaleString("it-IT")}</>}</div>
            <Provenance data={pr.derived_from} />
          </div>
        ))}
      </Section>

      <Section title="Attività imminenti">
        {p.upcoming.predictions.map((pr) => (
          <div className="item" key={pr.id}>
            Previsione: {pr.question} (verifica: {new Date(pr.verify_at).toLocaleDateString("it-IT")})
            <Provenance data={pr.derived_from} />
          </div>
        ))}
        {p.upcoming.project_deadlines.map((d) => (
          <div className="item" key={d.project_id}>
            Scadenza progetto: {d.name} — {d.target_date}
            <Provenance data={d.derived_from} />
          </div>
        ))}
        {p.upcoming.predictions.length === 0 && p.upcoming.project_deadlines.length === 0 && (
          <p className="muted">Niente in scadenza.</p>
        )}
      </Section>

      <Section title={`Previsioni (aperte: ${p.predictions.open}, da verificare: ${p.predictions.due_now})`}>
        {p.predictions.due_items.map((pr) => (
          <div className="item" key={pr.id}>
            In scadenza: {pr.question}
            <Provenance data={pr.derived_from} />
          </div>
        ))}
        {p.predictions.metrics.count > 0 && (
          <div className="muted">
            Accuratezza storica: {p.predictions.metrics.count} verificate
            {p.predictions.metrics.mae !== null && <> — MAE {p.predictions.metrics.mae.toFixed(2)}</>}
          </div>
        )}
      </Section>

      <Section title="Eventi recenti">
        {p.recent_events.length === 0 && <p className="muted">Nessun evento.</p>}
        {p.recent_events.map((e) => (
          <div className="item" key={e.id}>
            <code>{e.type}</code> — {new Date(e.event_at).toLocaleString("it-IT")}
            <Provenance data={e.derived_from} />
          </div>
        ))}
      </Section>

      <Section title={`Agenti (attivi nelle ultime 24h: ${p.agents.active_last_24h})`}>
        {p.agents.last_runs.map((r) => (
          <div className="item" key={r.id}>
            {r.agent} — {r.status} ({new Date(r.created_at).toLocaleString("it-IT")})
            <Provenance data={r.derived_from} />
          </div>
        ))}
        {p.agents.last_runs.length === 0 && <p className="muted">Nessuna esecuzione.</p>}
      </Section>

      <Section title="Agente ATLAS">
        <div className="item">
          Chat locale con l'agente (provider, memoria, strumenti e conferme):
          stessa superficie della web UI, servita dal backend di questa
          installazione.
        </div>
        {/* Stessa finestra: target=_blank e' un no-op nel webview Tauri
            (provato dal click-through E2E in CI). La pagina agente offre
            "Indietro" (history.back) per tornare alla dashboard. */}
        <a className="ghost" href={`${apiBase()}/agent/`}>
          Apri l'agente
        </a>
      </Section>

      <TelegramCard />
      <WhatsAppCard />
      <PairingCard />
      <MigrationCard onRelogin={onRelogin} />

      <Section title="Automazioni">
        {p.automations.agent_jobs.map((j) => (
          <div className="item" key={`job-${j.id}`}>
            Agente — {j.task}{" "}
            <span className="muted">
              ({j.kind === "cron" ? `ricorrente: ${j.schedule}` : `una volta: ${j.schedule}`}
              {j.paused ? ", in pausa" : ""}
              {j.next_fire && <> — prossima: {new Date(j.next_fire).toLocaleString("it-IT")}</>})
            </span>
            <Provenance data={j.derived_from} />
          </div>
        ))}
        {p.automations.connectors.map((c) => (
          <div className="item" key={c.id}>
            Connettore {c.kind} — {c.status} <span className="muted">(dati della versione precedente)</span>
            <Provenance data={c.derived_from} />
          </div>
        ))}
        {p.automations.agent_jobs.length === 0 && p.automations.connectors.length === 0 && (
          <p className="muted">Nessuna automazione. Chiedi all'agente di ricordarti qualcosa per crearne una.</p>
        )}
      </Section>

      <Section title="Memorie importanti">
        {p.important_memories.map((m) => (
          <div className="item" key={m.id}>
            {m.content} <span className="muted">({m.kind}, confidenza {m.confidence.toFixed(2)})</span>
            <Provenance data={m.derived_from} />
          </div>
        ))}
        {p.important_memories.length === 0 && <p className="muted">Nessuna memoria ad alta confidenza.</p>}
      </Section>

      <Section title="Richieste di autorizzazione">
        {p.authorization_requests.pending_connectors.map((c) => (
          <div className="item" key={c.id}>
            Connettore {c.kind} in attesa di autorizzazione
            <Provenance data={c.derived_from} />
          </div>
        ))}
        {p.authorization_requests.pending_confirm_actions.map((a) => (
          <div className="item" key={a.id}>
            Azione {a.kind} (livello {a.level}) in attesa di conferma
            <Provenance data={a.derived_from} />
          </div>
        ))}
        {p.authorization_requests.pending_connectors.length === 0 &&
          p.authorization_requests.pending_confirm_actions.length === 0 && (
          <p className="muted">Nessuna richiesta in sospeso.</p>
        )}
      </Section>
      </div>
    </div>
  );
}
