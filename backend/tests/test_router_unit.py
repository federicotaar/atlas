"""Test unitari per il ModelRouter: fallback locale quando il provider
configurato non risponde (stub, niente rete)."""
import pytest

from app.config import get_settings
from app.services.embeddings import ModelUnavailable
from app.services.router import ModelRouter


class _BrokenEmbedder:
    name = "broken-embed"

    def embed(self, text):
        raise ModelUnavailable("giu")


class _BrokenGenerator:
    name = "broken-gen"

    def answer(self, question, memories):
        raise ModelUnavailable("giu")


@pytest.fixture()
def router():
    return ModelRouter(get_settings())


def test_normal_path_uses_configured_providers(router):
    vec, used = router.embed("ciao mondo")
    assert used == "local-hash"
    assert len(vec) == router.settings.embedding_dim
    out, used = router.answer("q", [{"content": "fatto"}])
    assert used == "extractive"
    assert out.startswith("Dalle tue memorie:")


def test_embed_falls_back_to_local(router):
    router._embedder = _BrokenEmbedder()
    vec, used = router.embed("ciao mondo")
    assert used == "broken-embed->local-fallback"
    assert len(vec) == router.settings.embedding_dim


def test_answer_falls_back_to_extractive(router):
    router._generator = _BrokenGenerator()
    out, used = router.answer("q", [{"content": "fatto"}])
    assert used == "broken-gen->extractive-fallback"
    assert out.startswith("Dalle tue memorie:")
