// Mock SOLO per anteprima visiva del picker/chat (nessun backend).
// Stati guidati da location.hash: "", ollama, ollama-cloud, ollama-cloud-consent,
// groq, groq-saved, custom, chat, chat-fallback.

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
  providers: ProviderInfo[];
}

const PROVIDERS: ProviderInfo[] = [
  { id: "extractive", label: "Estrattivo (offline)", kind: "local", needs_key: false, needs_base_url: false, default_model: "" },
  { id: "ollama_local", label: "Ollama (locale)", kind: "local", needs_key: false, needs_base_url: false, default_model: "" },
  { id: "ollama_cloud", label: "Ollama (cloud)", kind: "cloud", needs_key: true, needs_base_url: false, default_model: "gemma4:31b-cloud" },
  { id: "groq", label: "Groq", kind: "cloud", needs_key: true, needs_base_url: false, default_model: "openai/gpt-oss-120b" },
  { id: "gemini", label: "Gemini", kind: "cloud", needs_key: true, needs_base_url: false, default_model: "gemini-3.5-flash" },
  { id: "custom", label: "Personalizzato (OpenAI-compatibile)", kind: "cloud", needs_key: false, needs_base_url: true, default_model: "" },
];

const hash = (typeof location !== "undefined" ? location.hash.slice(1) : "") || "picker";

function settingsFor(): ModelSettings {
  const base = { base_url: "", providers: PROVIDERS };
  switch (hash) {
    case "ollama":
      return { ...base, provider: "ollama_local", model: "llama3.2:1b", has_key: false, consent_cloud: false, provider_kind: "local", needs_key: false };
    case "ollama-cloud":
      return { ...base, provider: "ollama_cloud", model: "gemma4:31b-cloud", has_key: true, consent_cloud: false, provider_kind: "cloud", needs_key: true };
    case "ollama-cloud-consent":
      return { ...base, provider: "ollama_cloud", model: "gemma4:31b-cloud", has_key: true, consent_cloud: true, provider_kind: "cloud", needs_key: true };
    case "groq":
      return { ...base, provider: "groq", model: "", has_key: false, consent_cloud: false, provider_kind: "cloud", needs_key: true };
    case "groq-saved":
      return { ...base, provider: "groq", model: "openai/gpt-oss-120b", has_key: true, consent_cloud: true, provider_kind: "cloud", needs_key: true };
    case "custom":
      return { ...base, provider: "custom", model: "", base_url: "https://api.openai.com/v1", has_key: false, consent_cloud: true, provider_kind: "cloud", needs_key: false };
    default:
      return { ...base, provider: "extractive", model: "", has_key: false, consent_cloud: false, provider_kind: "local", needs_key: false };
  }
}

const MODEL_LISTS: Record<string, string[]> = {
  ollama_local: ["llama3.2:1b", "llama3.2:3b", "qwen2.5:1.5b", "smollm2:135m"],
  ollama_cloud: ["gemma4:31b-cloud", "gemma3:27b", "llama3.3:70b", "qwen3:32b", "mistral-large:123b"],
  groq: ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "moonshotai/kimi-k2-instruct-0905"],
  gemini: ["gemini-3.5-flash", "gemini-3.5-pro", "gemini-2.5-flash"],
  custom: ["gpt-5-mini", "gpt-4o-mini", "mistral-large-latest"],
};

const CIT: Citation = {
  ref: "M1",
  memory_id: "demo-memory-1",
  content: "domani devo portare la bici in garage",
  kind: "fact",
  origin: "stated",
  confidence: 0.9,
  recorded_at: new Date().toISOString(),
  source: { id: "src-1", kind: "chat", label: "chat", created_at: new Date().toISOString() },
};

export const api = {
  getModelSettings: () => Promise.resolve(settingsFor()),
  putModelSettings: (body: { provider: string }) =>
    Promise.resolve({ ...settingsFor(), provider: body.provider }),
  listModels: (provider: string) => Promise.resolve({ models: MODEL_LISTS[provider] ?? [] }),
  ask: (text: string): Promise<AskResponse> =>
    Promise.resolve(
      hash === "chat-fallback"
        ? {
            classification: "question",
            answer: "Dalle tue memorie:\n1. domani devo portare la bici in garage [M1]",
            citations: [CIT],
            saved_memory: null,
            conversation_id: "demo-conv",
            model: "ollama_local->extractive-fallback",
          }
        : {
            classification: "question",
            answer: "Domani devi portare la bici in garage [M1].",
            citations: [CIT],
            saved_memory: null,
            conversation_id: "demo-conv",
            model: "ollama_local",
          },
    ),
  getTelegramStatus: () => {
    if (hash === "telegram-paired")
      return Promise.resolve({ connected: true, bot_username: "fixture_bot",
        paired: true, deep_link: null, pairing_expired: false });
    if (hash === "telegram-expired")
      return Promise.resolve({ connected: true, bot_username: "fixture_bot",
        paired: false, deep_link: null, pairing_expired: true });
    if (hash === "telegram-off")
      return Promise.resolve({ connected: false, bot_username: null,
        paired: false, deep_link: null, pairing_expired: false });
    // telegram-unpaired: deep link sintetico, bot e codice inesistenti
    return Promise.resolve({ connected: true, bot_username: "fixture_bot",
      paired: false, deep_link: "https://t.me/fixture_bot?start=00000000",
      pairing_expired: false });
  },
  connectTelegram: () => Promise.resolve({ connected: true,
    bot_username: "fixture_bot", paired: false,
    deep_link: "https://t.me/fixture_bot?start=00000000", pairing_expired: false }),
  disconnectTelegram: () => Promise.resolve(),
  getWhatsAppStatus: () => {
    if (hash === "whatsapp-linked")
      return Promise.resolve({ connected: true, linked: true,
        jid: "393340000000@s.whatsapp.net", pushname: "Fixture",
        qr_state: "paired", qr: null });
    if (hash === "whatsapp-expired")
      return Promise.resolve({ connected: true, linked: false, jid: null,
        pushname: null, qr_state: "expired", qr: null });
    if (hash === "whatsapp-off")
      return Promise.resolve({ connected: false, linked: false, jid: null,
        pushname: null, qr_state: "idle", qr: null });
    // whatsapp-unpaired: QR sintetico inesistente, mai un segreto reale
    return Promise.resolve({ connected: true, linked: false, jid: null,
      pushname: null, qr_state: "qr",
      qr: "2@fixture-qr-sintetico-00000000000000000000000000000000" });
  },
  connectWhatsApp: () => Promise.resolve({ connected: true, linked: false,
    jid: null, pushname: null, qr_state: "qr",
    qr: "2@fixture-qr-sintetico-00000000000000000000000000000000" }),
  disconnectWhatsApp: () => Promise.resolve(),
  rotateTelegramCode: () => Promise.resolve({ connected: true,
    bot_username: "fixture_bot", paired: false,
    deep_link: "https://t.me/fixture_bot?start=00000000", pairing_expired: false }),
};
