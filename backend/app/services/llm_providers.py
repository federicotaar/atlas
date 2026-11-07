"""Provider LLM esterni e locali selezionabili per utente (BYOK).

Ogni utente sceglie provider e modello; la chiave API e' dell'utente, vive
SOLO nel suo backend locale, cifrata (Fernet via credentials.py), mai nei log,
mai restituita dalle API. I provider cloud trasmettono la domanda e le
memorie recuperate al provider scelto: l'uso e' addebitato sul conto provider
DELL'UTENTE, mai su quello del proprietario dell'installazione.

Provider supportati (verificati contro la documentazione ufficiale):
- extractive: nessun modello, risposta estrattiva locale (default, offline).
- ollama_local: Ollama sulla macchina (nessuna chiave).
- ollama_cloud: https://ollama.com/api/chat con Bearer key (docs.ollama.com/cloud).
- groq: https://api.groq.com/openai/v1/chat/completions, OpenAI-compatibile.
- gemini: generativelanguage.googleapis.com v1beta generateContent (x-goog-api-key).
- custom: endpoint OpenAI-compatibile arbitrario (base_url + modello + chiave
  opzionale): copre OpenAI, OpenRouter, Mistral, Together e simili senza
  pubblicizzare compatibilita' non verificate.
"""
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import AtlasError
from ..models import UserModelSetting
from .credentials import decrypt_secret, encrypt_secret
from .embeddings import ModelUnavailable

OLLAMA_CLOUD_BASE = "https://ollama.com"
GROQ_BASE = "https://api.groq.com/openai/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


@dataclass(frozen=True)
class ProviderMeta:
    id: str
    label: str
    kind: str  # local | cloud
    needs_key: bool
    default_model: str
    needs_base_url: bool = False


PROVIDERS: dict[str, ProviderMeta] = {
    "extractive": ProviderMeta("extractive", "Estrattivo (offline)", "local", False, ""),
    "ollama_local": ProviderMeta("ollama_local", "Ollama (locale)", "local", False, ""),
    "ollama_cloud": ProviderMeta("ollama_cloud", "Ollama (cloud)", "cloud", True, "gemma4:31b"),
    "groq": ProviderMeta("groq", "Groq", "cloud", True, "llama-3.3-70b-versatile"),
    "gemini": ProviderMeta("gemini", "Gemini", "cloud", True, "gemini-3.8-flash"),
    "custom": ProviderMeta("custom", "Personalizzato (OpenAI-compatibile)", "cloud", False, "", True),
}

PROMPT_TEMPLATE = (
    "Rispondi alla domanda SOLO usando le memorie elencate. "
    "Cita le memorie usate con il loro identificativo [M1], [M2], ecc. "
    "Se le memorie non bastano, rispondi che non lo sai.\n\n"
    "Memorie:\n{context}\n\nDomanda: {question}\nRisposta:"
)


def _prompt(question: str, memories: list[dict]) -> str:
    context = "\n".join(f"[M{i}] {m['content']}" for i, m in enumerate(memories, start=1))
    return PROMPT_TEMPLATE.format(context=context, question=question)


def chat_completion(provider: str, model: str, base_url: str, api_key: str,
                    question: str, memories: list[dict], timeout: float) -> str:
    """Una sola chiamata chat verso il provider scelto. Rilancia ModelUnavailable
    su errore di rete/provider; la chiave non finisce mai nel messaggio d'errore."""
    prompt = _prompt(question, memories)
    try:
        if provider in ("ollama_local", "ollama_cloud"):
            base = OLLAMA_CLOUD_BASE if provider == "ollama_cloud" else base_url.rstrip("/")
            headers = {"Authorization": f"Bearer {api_key}"} if provider == "ollama_cloud" else {}
            resp = httpx.post(
                f"{base}/api/chat",
                headers=headers,
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "stream": False},
                timeout=timeout,
            )
            resp.raise_for_status()
            return str(resp.json()["message"]["content"]).strip()
        if provider in ("groq", "custom"):
            base = GROQ_BASE if provider == "groq" else base_url.rstrip("/")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = httpx.post(
                f"{base}/chat/completions",
                headers=headers,
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"]).strip()
        if provider == "gemini":
            resp = httpx.post(
                f"{GEMINI_BASE}/models/{model}:generateContent",
                headers={"x-goog-api-key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data["candidates"][0]["content"]["parts"][0]["text"]).strip()
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable(f"{provider} non disponibile: {type(exc).__name__} {exc}") from exc
    raise ModelUnavailable(f"provider sconosciuto: {provider}")


def list_models(provider: str, base_url: str, api_key: str, settings: Settings,
                timeout: float = 10.0) -> list[str]:
    """Elenca i modelli disponibili per il box di ricerca. Best-effort: errori
    -> lista vuota (il campo modello resta inseribile a mano)."""
    try:
        if provider == "ollama_local":
            resp = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=timeout)
            resp.raise_for_status()
            return [m.get("name", "") for m in resp.json().get("models", []) if m.get("name")]
        if provider == "ollama_cloud":
            resp = httpx.get(f"{OLLAMA_CLOUD_BASE}/api/tags",
                             headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
            resp.raise_for_status()
            return [m.get("name", "") for m in resp.json().get("models", []) if m.get("name")]
        if provider == "groq":
            resp = httpx.get(f"{GROQ_BASE}/models",
                             headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
            resp.raise_for_status()
            return [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
        if provider == "gemini":
            resp = httpx.get(f"{GEMINI_BASE}/models",
                             headers={"x-goog-api-key": api_key}, timeout=timeout)
            resp.raise_for_status()
            out = []
            for m in resp.json().get("models", []):
                name = str(m.get("name", ""))
                if "generateContent" in (m.get("supportedGenerationMethods") or []):
                    out.append(name.removeprefix("models/"))
            return out
        if provider == "custom" and base_url:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
            resp.raise_for_status()
            return [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
    except Exception:
        return []
    return []


class UserGenerator:
    """Adatta chat_completion al protocollo Generator usato dal ModelRouter."""

    def __init__(self, provider: str, model: str, base_url: str, api_key: str, timeout: float):
        self.name = provider
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def answer(self, question: str, memories: list[dict]) -> str:
        return chat_completion(self.provider, self.model, self.base_url, self.api_key,
                               question, memories, self.timeout)


def get_pref(db: Session, user_id) -> UserModelSetting | None:
    return db.get(UserModelSetting, user_id)


def save_pref(db: Session, settings: Settings, user_id, provider: str, model: str,
              base_url: str, api_key: str | None, consent_cloud: bool) -> UserModelSetting:
    """Salva la scelta modello dell'utente. Fail-closed: provider cloud senza
    consenso esplicito, provider a chiave senza chiave, chiave senza
    ATLAS_CREDENTIALS_KEY -> errore, niente salvataggio parziale."""
    meta = PROVIDERS.get(provider)
    if meta is None:
        raise AtlasError("unknown_provider", f"Provider sconosciuto: {provider}", status=400)
    if meta.kind == "cloud" and not consent_cloud:
        pref = get_pref(db, user_id)
        if not (pref and pref.provider == provider and pref.consent_cloud):
            raise AtlasError(
                "cloud_consent_required",
                "Serve il consenso esplicito: con un provider cloud la domanda e le "
                "memorie recuperate escono dal tuo PC verso il provider scelto e "
                "l'uso e' addebitato sul TUO account presso quel provider.", status=400)
    pref = get_pref(db, user_id) or UserModelSetting(user_id=user_id, provider=provider)
    if meta.needs_base_url and not base_url and not pref.base_url:
        raise AtlasError("missing_base_url", "Base URL mancante per il provider personalizzato.",
                         status=400)
    if api_key is not None:
        if api_key == "":
            pref.api_key_enc = ""
        else:
            pref.api_key_enc = encrypt_secret(api_key, settings.credentials_key)
    if meta.needs_key and not pref.api_key_enc:
        raise AtlasError("missing_api_key",
                         f"Il provider {meta.label} richiede una chiave API: inseriscila "
                         "e riprova. La chiave resta cifrata nel tuo backend locale.",
                         status=400)
    pref.provider = provider
    pref.model = model or meta.default_model
    pref.base_url = base_url or pref.base_url or ""
    if meta.kind == "cloud":
        pref.consent_cloud = True
    db.add(pref)
    db.commit()
    db.refresh(pref)
    return pref


def resolve_user_generator(db: Session, settings: Settings, user_id) -> UserGenerator | None:
    """Restituisce il generatore scelto dall'utente, o None se usa il default
    del server. Chiave mancante su provider a chiave = errore chiaro, MAI
    downgrade silenzioso a un altro provider."""
    pref = get_pref(db, user_id)
    if pref is None or pref.provider == "extractive":
        return None
    meta = PROVIDERS.get(pref.provider)
    if meta is None:
        return None
    api_key = decrypt_secret(pref.api_key_enc, settings.credentials_key) if pref.api_key_enc else ""
    if meta.needs_key and not api_key:
        raise AtlasError("missing_api_key",
                         f"La chiave API per {meta.label} non e' configurata: aprila dalle "
                         "impostazioni modello e inseriscila (resta cifrata in locale).",
                         status=400)
    if meta.needs_base_url and not pref.base_url:
        raise AtlasError("missing_base_url", "Base URL mancante per il provider personalizzato.",
                         status=400)
    base = settings.ollama_base_url if pref.provider == "ollama_local" else pref.base_url
    model = pref.model or meta.default_model
    if pref.provider == "ollama_local" and not model:
        model = settings.ollama_llm_model
    return UserGenerator(pref.provider, model, base, api_key, settings.model_call_timeout_s)
