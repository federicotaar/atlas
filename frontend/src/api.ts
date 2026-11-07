const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "/api";

let token: string | null = localStorage.getItem("atlas_token");

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
};
