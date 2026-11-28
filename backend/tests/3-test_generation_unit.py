"""Test unitari puri per il generatore estrattivo."""
from app.services.generation import ExtractiveGenerator


def test_extractive_lists_memories_with_citations():
    out = ExtractiveGenerator().answer(
        "Che database uso?",
        [{"content": "Il progetto usa PostgreSQL."},
         {"content": "Il server e su Oracle Cloud."}])
    lines = out.splitlines()
    assert lines[0] == "Dalle tue memorie:"
    assert "1. Il progetto usa PostgreSQL. [M1]" in lines
    assert "2. Il server e su Oracle Cloud. [M2]" in lines


def test_extractive_empty_memories():
    assert ExtractiveGenerator().answer("q", []) == "Dalle tue memorie:"


def test_build_generator_default_is_extractive():
    from types import SimpleNamespace

    from app.services.generation import build_generator
    gen = build_generator(SimpleNamespace(llm_provider="extractive"))
    assert gen.name == "extractive"


def test_build_generator_ollama_uses_settings():
    from types import SimpleNamespace

    from app.services.generation import build_generator
    gen = build_generator(SimpleNamespace(llm_provider="ollama", ollama_base_url="http://h:11434/",
                                          ollama_llm_model="m", model_call_timeout_s=7.0))
    assert gen.name == "ollama" and gen.base_url == "http://h:11434" and gen.model == "m" and gen.timeout == 7.0


def test_ollama_http_error_is_model_unavailable(monkeypatch):
    import httpx
    import pytest

    from app.services import generation
    from app.services.embeddings import ModelUnavailable

    def boom(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(generation.httpx, "post", boom)
    with pytest.raises(ModelUnavailable):
        generation.OllamaGenerator("http://x", "m").answer("q", [{"content": "c"}])


def test_ollama_prompt_and_response_strip(monkeypatch):
    from app.services import generation
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "  Risposta [M1]  \n"}

    def fake_post(url, json, timeout):
        seen.update(url=url, json=json, timeout=timeout)
        return Resp()
    monkeypatch.setattr(generation.httpx, "post", fake_post)
    out = generation.OllamaGenerator("http://x/", "mod", timeout=3).answer("Dove?", [{"content": "A Roma"}])
    assert out == "Risposta [M1]"
    assert seen["url"] == "http://x/api/generate" and seen["json"]["stream"] is False
    assert "[M1] A Roma" in seen["json"]["prompt"] and "Domanda: Dove?" in seen["json"]["prompt"]


def test_ollama_missing_response_key_is_unavailable(monkeypatch):
    import pytest

    from app.services import generation
    from app.services.embeddings import ModelUnavailable

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": 1}
    monkeypatch.setattr(generation.httpx, "post", lambda *a, **k: Resp())
    with pytest.raises(ModelUnavailable):
        generation.OllamaGenerator("http://x", "m").answer("q", [])


def test_cited_indexes_dedupes_in_order():
    from app.services.generation import cited_indexes
    assert cited_indexes("Usa PostgreSQL [M2] su Oracle [M1] [M2].") == [2, 1]
    assert cited_indexes("nessuna fonte") == []


def test_strip_unknown_citations_keeps_valid_ones():
    from app.services.generation import strip_unknown_citations
    out = strip_unknown_citations("Postgres [M1] e Oracle [M7].", 2)
    assert "[M1]" in out and "[M7]" not in out
    assert strip_unknown_citations("Testo [M0] [M3]", 2) == "Testo"


def test_ollama_answer_drops_invented_citations(monkeypatch):
    import httpx

    from app.services.generation import OllamaGenerator

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "Usa PostgreSQL [M1] e Redis [M5]."}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    out = OllamaGenerator("http://x", "m").answer("db?", [{"content": "Usa PostgreSQL."}])
    assert "[M1]" in out and "[M5]" not in out
