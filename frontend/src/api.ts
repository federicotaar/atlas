export function resolveBase(envUrl: string | undefined, isTauri: boolean): string {
  if (envUrl) return envUrl;
  // In Tauri non c'e' proxy: il webview (origine tauri.localhost) chiama
  // direttamente il backend locale, che espone CORS solo per le origini Tauri.
  return isTauri ? "http://127.0.0.1:8000" : "/api";
}

const IS_TAURI =
  typeof window !== "undefined" &&
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ !== undefined;

let BASE = resolveBase(import.meta.env.VITE_API_URL as string | undefined, IS_TAURI);

/** Porte candidate del backend desktop: il launcher Tauri usa 8000 se libera,
 * altrimenti una di riserva (mai contesa a server che non sono suoi). */
export const TAURI_PORT_CANDIDATES = [8000, 8100, 8101, 8102, 8103, 8104, 8105, 8106, 8107, 8108];
/** Backend minimo riusabile: da 2.1.1 /health dichiara la versione; un
 * backend piu' vecchio (es. Docker 2.0.x) non ha auth locale, migrazione e
 * connettori e NON va usato dall'app nuova. */
export const MIN_BACKEND_VERSION = "2.1.1";

export function parseVersion(v: string): [number, number, number] | null {
  const m = /^(\d+)\.(\d+)\.(\d+)/.exec(v);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

export function versionAtLeast(v: string, min: string): boolean {
  const a = parseVersion(v);
  const b = parseVersion(min);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return true;
}

/** Sceglie la porta del backend in Tauri: prima istanza ATLAS con versione
 * compatibile tra le candidate; null se nessuna (il launcher mostrera'
 * l'errore). Mai usare un backend troppo vecchio solo perche' risponde. */
export async function probeTauriBase(
  fetchImpl: typeof fetch,
  candidates: number[],
  minVersion: string,
): Promise<string | null> {
  for (const port of candidates) {
    try {
      const res = await fetchImpl(`http://127.0.0.1:${port}/health`);
      if (!res.ok) continue;
      const v = (await res.json()) as Record<string, unknown>;
      if (
        v.app === "atlas" &&
        v.api === "v1" &&
        typeof v.version === "string" &&
        versionAtLeast(v.version, minVersion)
      ) {
        return `http://127.0.0.1:${port}`;
      }
    } catch {
      continue;
    }
  }
  return null;
}

/** In Tauri senza override esplicito, NESSUNA richiesta API parte prima
 * che la scoperta abbia scelto un backend compatibile (>= 2.1.1): altrimenti
 * /auth/modes, register o le memorie potrebbero finire contro un VECCHIO
 * backend sulla porta di default. Il render non aspetta la scoperta: Auth
 * mostra "Avvio in corso..." mentre req() resta in attesa sul gate. La
 * scoperta ha un limite di 120s (il launcher stesso dichiara fallimento a
 * 30s): se nessun backend compatibile risponde, le richieste falliscono con
 * un errore esplicito e Auth mostra il messaggio con un tasto Riprova
 * invece di una scoperta silenziosa infinita. Una volta risolta, BASE non
 * cambia piu': nessun salto tra istanze compatibili dopo l'avvio. */
const DISCOVERY_TIMEOUT_MS = 120_000;

let discoveryGate: Promise<void> | null = null;
let discoveryFailed = false;

/** true se la scoperta del backend e' fallita oltre il limite (errore
 * terminale: mostrare un messaggio, non riprovare in silenzio). */
export function apiDiscoveryFailed(): boolean {
  return discoveryFailed;
}

export async function initApiBase(): Promise<void> {
  if (!IS_TAURI || import.meta.env.VITE_API_URL) return;
  discoveryFailed = false;
  let resolveGate!: () => void;
  let rejectGate!: (e: Error) => void;
  discoveryGate = new Promise<void>((res, rej) => {
    resolveGate = res;
    rejectGate = rej;
  });
  const deadline = Date.now() + DISCOVERY_TIMEOUT_MS;
  let delay = 400;
  while (Date.now() < deadline) {
    const found = await probeTauriBase(fetch.bind(window), TAURI_PORT_CANDIDATES, MIN_BACKEND_VERSION);
    if (found) {
      BASE = found;
      resolveGate();
      return;
    }
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 2, 3_000);
  }
  discoveryFailed = true;
  rejectGate(new Error("backend ATLAS non trovato entro il tempo previsto"));
}

let token: string | null =
  typeof localStorage !== "undefined" ? localStorage.getItem("atlas_token") : null;

export function setToken(t: string | null) {
  token = t;
  if (t) localStorage.setItem("atlas_token", t);
  else localStorage.removeItem("atlas_token");
}

export function hasToken() {
  return token !== null;
}

async function req<T>(path: string, options: RequestInit = {}): Promise<T> {
  // In Tauri attende che la scoperta abbia selezionato un backend
  // compatibile: nessuna richiesta raggiunge mai un vecchio server.
  if (discoveryGate) await discoveryGate;
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });
  if (!res.ok) {
    let message = `Errore ${res.status}`;
    try {
      const data = await res.json();
      message = data?.error?.message ?? message;
    } catch { /* corpo non JSON */ }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface Citation {
  ref: string;
  memory_id: string;
  content: string;
  kind: string;
  origin: string;
  confidence: number;
  recorded_at: string;
  source: { id: string; kind: string; label: string; created_at: string } | null;
}

export interface AskResponse {
  classification: string;
  answer: string;
  citations: Citation[];
  saved_memory: { id: string; content: string } | null;
  conversation_id: string | null;
  model: string | null;
}

export interface DashboardData {
  generated_at: string;
  provenance_rule: string;
  panels: {
    projects: Array<{
      project_id: string; name: string; status: string; target_date: string | null;
      events_count: number; last_event_at: string | null;
      derived_from: Record<string, string | null>;
    }>;
    upcoming: {
      predictions: Array<{ id: string; question: string; verify_at: string; derived_from: Record<string, string | null> }>;
      project_deadlines: Array<{ project_id: string; name: string; target_date: string; derived_from: Record<string, string | null> }>;
    };
    recent_events: Array<{
      id: string; type: string; event_at: string; project_id: string | null;
      derived_from: Record<string, string | null>;
    }>;
    predictions: {
      open: number; verified: number; due_now: number;
      metrics: { count: number; mae: number | null };
      due_items: Array<{ id: string; question: string; verify_at: string; derived_from: Record<string, string | null> }>;
    };
    agents: {
      active_last_24h: number;
      last_runs: Array<{ id: string; agent: string; status: string; created_at: string; derived_from: Record<string, string | null> }>;
    };
    automations: {
      connectors: Array<{ id: string; kind: string; status: string; derived_from: Record<string, string | null> }>;
      engine_actions_by_status: Record<string, number>;
    };
    important_memories: Array<{
      id: string; content: string; kind: string; confidence: number; recorded_at: string;
      derived_from: Record<string, string | null>;
    }>;
    authorization_requests: {
      pending_connectors: Array<{ id: string; kind: string; derived_from: Record<string, string | null> }>;
      pending_confirm_actions: Array<{ id: string; kind: string; level: number; created_at: string; derived_from: Record<string, string | null> }>;
    };
  };
}

export interface ProviderInfo {
  id: string;
  label: string;
  kind: "local" | "cloud";
  needs_key: boolean;
  needs_base_url: boolean;
  default_model: string;
}

export interface ModelSettings {
  provider: string;
  model: string;
  base_url: string;
  has_key: boolean;
  consent_cloud: boolean;
  provider_kind: "local" | "cloud";
  needs_key: boolean;
  key_unusable: boolean;
  providers: ProviderInfo[];
}

export interface TelegramStatus {
  connected: boolean;
  bot_username: string | null;
  paired: boolean;
  deep_link: string | null;
  pairing_expired: boolean;
}

export interface WhatsAppStatus {
  connected: boolean;
  linked: boolean;
  jid: string | null;
  pushname: string | null;
  qr_state: "idle" | "qr" | "paired" | "expired";
  qr: string | null;
}

export interface MigrationProbe {
  source: "configured" | "detected" | "none";
  state: "has_data" | "empty" | "unreachable" | "none";
  counts: Record<string, number>;
  error: string;
  recorded: "imported" | "dismissed" | null;
}

export interface MigrationStatus {
  state: "not_configured" | "unreachable" | "empty" | "has_data" | "imported" | "dismissed";
  counts: Record<string, number>;
  error: string;
  undecryptable_credentials?: number | null;
}

export const api = {
  modes: () => req<{ modes: string[] }>("/auth/modes"),
  requestCode: (email: string, purpose: "register" | "login", turnstileToken = "") =>
    req<{ ok: boolean; ttl_seconds: number }>("/auth/codes/request", {
      method: "POST",
      body: JSON.stringify({ email, purpose, turnstile_token: turnstileToken }),
    }),
  checkCode: (email: string, code: string) =>
    req<{ ok: boolean; signup_token: string }>("/auth/codes/check", {
      method: "POST",
      body: JSON.stringify({ email, code }),
    }),
  register: (email: string, password: string, signupToken: string) =>
    req<{ token: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, signup_token: signupToken }),
    }),
  login: (email: string, password: string) =>
    req<{ token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  loginCode: (email: string, code: string) =>
    req<{ token: string }>("/auth/login-code", { method: "POST", body: JSON.stringify({ email, code }) }),
  ask: (text: string, conversationId?: string) =>
    req<AskResponse>("/ask", {
      method: "POST",
      body: JSON.stringify(conversationId ? { text, conversation_id: conversationId } : { text }),
    }),
  dashboard: () => req<DashboardData>("/dashboard"),
  getModelSettings: () => req<ModelSettings>("/settings/model"),
  putModelSettings: (body: { provider: string; model?: string; base_url?: string;
                             api_key?: string; consent_cloud?: boolean }) =>
    req<ModelSettings>("/settings/model", { method: "PUT", body: JSON.stringify(body) }),
  listModels: (provider: string) =>
    req<{ models: string[] }>(`/settings/models?provider=${encodeURIComponent(provider)}`),
  getMigrationStatus: () => req<MigrationStatus>("/setup/migration-status"),
  probeMigration: () => req<MigrationProbe>("/setup/migration-probe", { method: "POST" }),
  importMigration: () => req<MigrationStatus>("/setup/migration-import", { method: "POST" }),
  dismissMigration: () => req<MigrationStatus>("/setup/migration-dismiss", { method: "POST" }),
  getTelegramStatus: () => req<TelegramStatus>("/connectors/telegram/status"),
  connectTelegram: (botToken: string) =>
    req<TelegramStatus>("/connectors/telegram/connect", {
      method: "POST",
      body: JSON.stringify({ bot_token: botToken }),
    }),
  disconnectTelegram: () =>
    req<void>("/connectors/telegram/disconnect", { method: "POST" }),
  rotateTelegramCode: () =>
    req<TelegramStatus>("/connectors/telegram/pairing-code", { method: "POST" }),
  getWhatsAppStatus: () => req<WhatsAppStatus>("/connectors/whatsapp/status"),
  connectWhatsApp: () =>
    req<WhatsAppStatus>("/connectors/whatsapp/connect", { method: "POST" }),
  disconnectWhatsApp: () =>
    req<void>("/connectors/whatsapp/disconnect", { method: "POST" }),
};
