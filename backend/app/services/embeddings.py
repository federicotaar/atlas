"""Provider di embeddings intercambiabili (spec sez. 5G).

- LocalHashEmbedder: deterministico, zero dipendenze esterne, gratis. Pensato per
  sviluppo, test e funzionamento offline senza GPU. Non e un modello linguistico:
  cattura sovrapposizione lessicale, non semantica profonda.
- OllamaEmbedder: modello locale via Ollama (es. nomic-embed-text, 768 dim).
"""
import hashlib
import math
import re
from typing import Protocol

import httpx

from ..config import Settings

_WORD = re.compile(r"[a-z0-9àèéìòù]+", re.IGNORECASE)


class ModelUnavailable(Exception):
    pass


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, text: str) -> list[float]: ...


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def _features(text: str) -> list[str]:
    toks = _tokens(text)
    feats = [f"w:{t}" for t in toks]
    feats += [f"b:{a}_{b}" for a, b in zip(toks, toks[1:])]
    return feats


class LocalHashEmbedder:
    """Embedding lessicale deterministico via feature hashing (signed)."""

    name = "local-hash"

    def __init__(self, dim: int = 768):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat in _features(text):
            digest = hashlib.sha256(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


class OllamaEmbedder:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def embed(self, text: str) -> list[float]:
        try:
            resp = httpx.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data["embedding"]
            return [float(v) for v in vec]
        except Exception as exc:
            raise ModelUnavailable(f"Ollama embeddings non disponibili: {exc}") from exc


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def build_embedder(settings: Settings) -> EmbeddingProvider:
    if settings.embed_provider == "ollama":
        return OllamaEmbedder(settings.ollama_base_url, settings.ollama_embed_model, settings.model_call_timeout_s)
    return LocalHashEmbedder(settings.embedding_dim)
