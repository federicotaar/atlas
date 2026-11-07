"""Model Router fase 0 (spec 5G): un'interfaccia embed()/generate(), provider
intercambiabili, fallback locale se Ollama non risponde, log di ogni chiamata
(modello, durata; token stimati; costo 0 per provider locali)."""
import time
import uuid

from sqlalchemy.orm import Session

from ..config import Settings
from .embeddings import EmbeddingProvider, LocalHashEmbedder, ModelUnavailable, build_embedder
from .generation import ExtractiveGenerator, Generator, build_generator


class ModelRouter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._embedder: EmbeddingProvider = build_embedder(settings)
        self._generator: Generator = build_generator(settings)
        self._fallback_embedder = LocalHashEmbedder(settings.embedding_dim)
        self._fallback_generator = ExtractiveGenerator()

    def embed(self, text: str, db: Session | None = None, user_id: uuid.UUID | None = None) -> tuple[list[float], str]:
        start = time.monotonic()
        try:
            vec = self._embedder.embed(text)
            used = self._embedder.name
        except ModelUnavailable:
            vec = self._fallback_embedder.embed(text)
            used = f"{self._embedder.name}->local-fallback"
        self._log(db, user_id, "embed", used, start, text)
        return vec, used

    def answer(self, question: str, memories: list[dict], db: Session | None = None,
               user_id: uuid.UUID | None = None) -> tuple[str, str]:
        start = time.monotonic()
        try:
            out = self._generator.answer(question, memories)
            used = self._generator.name
        except ModelUnavailable:
            out = self._fallback_generator.answer(question, memories)
            used = f"{self._generator.name}->extractive-fallback"
        self._log(db, user_id, "generate", used, start, question)
        return out, used

    def _log(self, db: Session | None, user_id: uuid.UUID | None, op: str, model: str,
             start: float, input_text: str) -> None:
        if db is None or user_id is None:
            return
        from .memory import log_action

        duration_ms = int((time.monotonic() - start) * 1000)
        # mai contenuti personali nei log: solo lunghezza input (spec sez. 9)
        log_action(db, user_id, f"model.{op}", {
            "model": model,
            "duration_ms": duration_ms,
            "input_chars": len(input_text),
            "est_tokens": max(1, len(input_text) // 4),
            "cost_eur": 0.0,
        })
