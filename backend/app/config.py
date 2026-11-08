from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurazione ATLAS. Ogni campo si sovrascrive con env ATLAS_<NOME>."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    # Postgres precedente da cui migrare (desktop standalone primo avvio).
    # Vuoto = nessuna origine configurata: profilo nuovo senza prompt.
    legacy_database_url: str = ""
    jwt_secret: str = "dev-secret-change-me"
    jwt_ttl_minutes: int = 60 * 24

    # API codici email (worker Cloudflare). La chiave pubblica Ed25519 NON e'
    # un segreto: serve solo a verificare le attestazioni firmate dal worker.
    codes_api_url: str = "https://atlas.federicotaranu.site"
    codes_public_key: str = "fuF-owcEmKTrOHw-nlBa4qDxruzoCLw8JGSGctua0FA"  # base64url della chiave pubblica Ed25519 (x del JWK)
    codes_timeout_s: float = 10.0

    ollama_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_llm_model: str = "llama3.2"
    embed_provider: str = "local"  # local | ollama
    llm_provider: str = "extractive"  # extractive | ollama
    embedding_dim: int = 768
    model_call_timeout_s: float = 30.0

    retrieval_candidates: int = 40
    retrieval_top_k: int = 8
    w_semantic: float = 0.55
    w_text: float = 0.25
    w_recency: float = 0.10
    w_confidence: float = 0.10
    recency_half_life_days: float = 30.0
    ask_min_relevance: float = 0.12
    duplicate_max_distance: float = 0.05
    agent_max_steps: int = 3
    agent_timeout_s: float = 20.0
    agent_budget_eur: float = 0.0

    # Origini extra ammesse via CORS, separate da virgola. Di default solo le
    # origini dell'involucro desktop Tauri; il browser resta same-origin.
    cors_origins: str = "http://tauri.localhost,tauri://localhost"

    # Connettori Google reali (OAuth gratuito; setup in docs/CONNECTORS-GOOGLE.md).
    # Vuoti = non configurato: gli endpoint OAuth rispondono 503 chiaro.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/connectors/google/callback"
    # Chiave Fernet per cifrare i token dei connettori (mai in chiaro, spec sicurezza).
    # Generare con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Senza chiave le credenziali NON vengono salvate (fail-closed).
    credentials_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
