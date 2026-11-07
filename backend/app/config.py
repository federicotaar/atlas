from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurazione ATLAS. Ogni campo si sovrascrive con env ATLAS_<NOME>."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/atlas"
    jwt_secret: str = "dev-secret-change-me"
    jwt_ttl_minutes: int = 60 * 24

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
