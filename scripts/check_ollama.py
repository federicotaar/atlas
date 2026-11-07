"""Verifica genuina del percorso Ollama (non solo interfaccia).

Richiede un server Ollama locale (http://127.0.0.1:11434 di default) con
nomic-embed-text e un modello chat piccolo (llama3.2:1b). Usa i provider
REALI dell'app (services/embeddings.py, services/generation.py) con
embed_provider=ollama e llm_provider=ollama.

Uso: ATLAS_OLLAMA_LLM_MODEL=llama3.2:1b python scripts/check_ollama.py
Esce 0 solo se embeddings e generazione arrivano davvero dal server.
"""
import os
import sys

os.environ.setdefault("ATLAS_EMBED_PROVIDER", "ollama")
os.environ.setdefault("ATLAS_LLM_PROVIDER", "ollama")
os.environ.setdefault("ATLAS_OLLAMA_LLM_MODEL", "llama3.2:1b")
os.environ.setdefault("ATLAS_MODEL_CALL_TIMEOUT_S", "300")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.config import get_settings  # noqa: E402
from app.services.embeddings import build_embedder  # noqa: E402
from app.services.generation import build_generator  # noqa: E402


def main() -> int:
    settings = get_settings()
    embedder = build_embedder(settings)
    vec = embedder.embed("il progetto Mercurio usa PostgreSQL")
    assert len(vec) == settings.embedding_dim, (len(vec), settings.embedding_dim)
    vec2 = embedder.embed("PostgreSQL e il database del progetto Mercurio")
    from app.services.embeddings import cosine
    sim = cosine(vec, vec2)
    assert 0.0 < sim <= 1.0, sim
    print(f"embeddings Ollama ok: dim={len(vec)}, similarita frasi correlate={sim:.3f}")

    generator = build_generator(settings)
    try:
        out = generator.answer(
            "Cosa usa il progetto Mercurio come database? Rispondi in una frase.",
            [{"content": "Il progetto Mercurio usa PostgreSQL.", "ref": "M1"}])
    except Exception as exc:  # il provider deve esistere davvero
        print(f"generazione Ollama FALLITA: {exc}")
        return 1
    assert out and len(out) > 3, repr(out)
    print(f"generazione Ollama ok: {out[:120]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
