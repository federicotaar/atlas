export function resolveBase(envUrl: string | undefined, isTauri: boolean): string {
  if (envUrl) return envUrl;
  // In Tauri non c'e' proxy: il webview (origine tauri.localhost) chiama
  // direttamente il backend locale, che espone CORS solo per le origini Tauri.
  return isTauri ? "http://127.0.0.1:8000" : "/api";
}

const IS_TAURI =
  typeof window !== "undefined" &&
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ !== undefined;

const BASE = resolveBase(import.meta.env.VITE_API_URL as string | undefined, IS_TAURI);

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

export const api = {
  register: (email: string, password: string) =>
    req<{ token: string }>("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    req<{ token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  ask: (text: string, conversationId?: string) =>
    req<AskResponse>("/ask", {
      method: "POST",
      body: JSON.stringify(conversationId ? { text, conversation_id: conversationId } : { text }),
    }),
  dashboard: () => req<DashboardData>("/dashboard"),
};
