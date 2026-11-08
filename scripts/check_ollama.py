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
    prompt = "Cosa usa il progetto Mercurio come database? Rispondi in una frase."
    mems = [{"content": "Il progetto Mercurio usa PostgreSQL.", "ref": "M1"}]
    out = None
    last_exc: Exception | None = None
    # Un solo retry con richiesta nuova: il check resta genuino (deve
    # arrivare una risposta reale), ma un singolo stallo transitorio del
    # server non abbatte la CI senza diagnosi.
    for attempt in (1, 2):
        try:
            out = generator.answer(prompt, mems)
            break
        except Exception as exc:  # il provider deve esistere davvero
            last_exc = exc
            print(f"generazione Ollama tentativo {attempt} fallito: {exc}")
            _diagnose()
    if out is None:
        print(f"generazione Ollama FALLITA: {last_exc}")
        return 1
    assert out and len(out) > 3, repr(out)
    print(f"generazione Ollama ok: {out[:120]!r}")
    return 0


def _diagnose() -> None:
    """Evidenza sullo stato del server Ollama al momento del fallimento."""
    import subprocess
    for cmd in (["ollama", "--version"], ["ollama", "ps"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            print(f"[diag] {' '.join(cmd)} -> {(r.stdout or r.stderr).strip()[:400]}")
        except Exception as exc:
            print(f"[diag] {' '.join(cmd)} non eseguibile: {exc}")
    try:
        with open("/tmp/ollama.log") as fh:
            lines = fh.read().splitlines()
        print(f"[diag] /tmp/ollama.log (ultime {min(50, len(lines))} righe):")
        for line in lines[-50:]:
            print(f"[diag]   {line}")
    except OSError as exc:
        print(f"[diag] /tmp/ollama.log non leggibile: {exc}")


if __name__ == "__main__":
    sys.exit(main())
