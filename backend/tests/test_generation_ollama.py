"""Payload del percorso legacy OllamaGenerator: tetto num_predict opzionale."""

from app.services import generation
from app.services.generation import OllamaGenerator


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"response": "PostgreSQL [M1]"}


def _capture(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent["url"] = url
        sent["json"] = json
        sent["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(generation.httpx, "post", fake_post)
    return sent


def test_default_senza_tetto_num_predict(monkeypatch):
    sent = _capture(monkeypatch)
    gen = OllamaGenerator("http://127.0.0.1:11434", "smollm2:135m", timeout=5)
    out = gen.answer("Che database usa Mercurio?", [{"content": "Il progetto Mercurio usa PostgreSQL.", "ref": "M1"}])
    assert out == "PostgreSQL [M1]"
    assert "options" not in sent["json"]
    assert sent["json"]["stream"] is False


def test_tetto_num_predict_quando_configurato(monkeypatch):
    sent = _capture(monkeypatch)
    gen = OllamaGenerator("http://127.0.0.1:11434", "smollm2:135m", timeout=5, max_tokens=512)
    gen.answer("Che database usa Mercurio?", [{"content": "Il progetto Mercurio usa PostgreSQL.", "ref": "M1"}])
    assert sent["json"]["options"] == {"num_predict": 512}
