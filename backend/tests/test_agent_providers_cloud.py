"""Test provider cloud (stage 2): openai_compat (Groq/Gemini/OpenAI/OpenRouter/
custom) e anthropic nativo. Trasporti finti: NESSUNA di queste verifiche conta
come E2E con provider reale - quella richiede chiavi autorizzate."""
from __future__ import annotations

import json

import httpx
import pytest

from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    AuthenticationError,
    LLMProvider,
    ModelNotFound,
    ProviderError,
    ProviderResponse,
    ProviderUnavailable,
    RateLimitError,
)
from app.providers.openai_compat import OpenAICompatProvider
from app.providers.registry import PROVIDER_TYPES, ProviderRegistry


def _post_mock(monkeypatch, handler):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(
        transport=httpx.MockTransport(handler)).post(url, **kw))


def _stream_mock(monkeypatch, lines, status=200, error_body=None):
    class FakeResp:
        status_code = status

        def iter_lines(self):
            return iter(lines)

        def read(self):
            return None

        @property
        def text(self):
            return error_body or ""

        def json(self):
            return json.loads(error_body) if error_body else {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: FakeResp())


def _sse(*payloads, done=True):
    lines = []
    for p in payloads:
        lines.append("data: " + json.dumps(p))
        lines.append("")
    if done:
        lines.append("data: [DONE]")
    return lines


# --- openai_compat -----------------------------------------------------------

def test_openai_compat_non_stream(monkeypatch):
    def handler(request):
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer chiave-finta"
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-oss-120b"
        assert body["max_tokens"] == 256
        assert body["tools"][0]["function"]["name"] == "shell"
        return httpx.Response(200, json={
            "model": "openai/gpt-oss-120b",
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "shell", "arguments": "{\"cmd\": \"ls\"}"}}]}}]})

    _post_mock(monkeypatch, handler)
    p = OpenAICompatProvider(name="groq", model="openai/gpt-oss-120b",
                             base_url="https://api.groq.com/openai/v1",
                             api_key="chiave-finta", max_tokens=256)
    resp = p.chat([{"role": "user", "content": "lista"}],
                  tools=[{"name": "shell", "description": "d", "parameters": {}}])
    assert resp.tool_calls[0].name == "shell"
    assert resp.tool_calls[0].arguments == {"cmd": "ls"}
    assert resp.finish_reason == "tool_calls"


def test_openai_compat_stream_with_tool_fragments(monkeypatch):
    lines = _sse(
        {"choices": [{"delta": {"role": "assistant", "content": "ecco"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "read_file", "arguments": "{\"pa"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "th\": \"a.txt\"}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )
    _stream_mock(monkeypatch, lines)
    p = OpenAICompatProvider(name="gemini", model="gemini-2.5-flash",
                             base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                             api_key="k")
    stream = p.chat([{"role": "user", "content": "leggi"}], stream=True)
    assert list(stream) == ["ecco"]
    assert stream.response.text == "ecco"
    assert stream.response.finish_reason == "tool_calls"
    assert stream.response.tool_calls[0].name == "read_file"
    assert stream.response.tool_calls[0].arguments == {"path": "a.txt"}


@pytest.mark.parametrize("status,exc", [
    (401, AuthenticationError),
    (403, AuthenticationError),
    (404, ModelNotFound),
    (429, RateLimitError),
    (500, ProviderUnavailable),
    (400, ProviderError),
])
def test_openai_compat_errors(monkeypatch, status, exc):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": f"boom {status}"}})

    _post_mock(monkeypatch, handler)
    p = OpenAICompatProvider(name="openai", model="m", base_url="https://api.openai.com/v1",
                             api_key="segreta-xyz")
    with pytest.raises(exc) as ei:
        p.chat([{"role": "user", "content": "c"}])
    assert "segreta-xyz" not in str(ei.value)


def test_openai_compat_gemini_400_chiave_invalida(monkeypatch):
    """Regressione reale: Gemini OpenAI-compat risponde 400 (non 401) a una
    chiave invalida; deve valere come AuthenticationError per il fallback."""
    def handler(request):
        return httpx.Response(400, json={"error": {
            "code": 400, "message": "Please pass a valid API key",
            "status": "INVALID_ARGUMENT"}})

    _post_mock(monkeypatch, handler)
    p = OpenAICompatProvider(name="gemini", model="gemini-2.5-flash",
                             base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                             api_key="invalida")
    with pytest.raises(AuthenticationError):
        p.chat([{"role": "user", "content": "c"}])


def test_dettaglio_errore_body_array(monkeypatch):
    """Regressione reale: l'endpoint OpenAI-compat di Gemini risponde con un
    ARRAY JSON; il messaggio d'errore deve estrarre solo error.message."""
    def handler(request):
        return httpx.Response(400, json=[{"error": {
            "code": 400, "message": "Please pass a valid API key",
            "status": "INVALID_ARGUMENT"}}])

    _post_mock(monkeypatch, handler)
    p = OpenAICompatProvider(name="gemini", model="m",
                             base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                             api_key="invalida")
    with pytest.raises(AuthenticationError) as ei:
        p.chat([{"role": "user", "content": "c"}])
    assert "Please pass a valid API key" in str(ei.value)
    assert "INVALID_ARGUMENT" not in str(ei.value)


def test_dettaglio_errore_su_una_riga(monkeypatch):
    """I dettagli JSON multi-riga del provider non devono sporcarsi i messaggi."""
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "riga1\nriga2\nriga3"}})

    _post_mock(monkeypatch, handler)
    p = OpenAICompatProvider(name="custom", model="m", base_url="http://x/v1")
    with pytest.raises(ProviderError) as ei:
        p.chat([{"role": "user", "content": "c"}])
    assert "\n" not in str(ei.value)


def test_openai_compat_network_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("giu'")

    _post_mock(monkeypatch, handler)
    p = OpenAICompatProvider(name="openrouter", model="m",
                             base_url="https://openrouter.ai/api/v1", api_key="k")
    with pytest.raises(ProviderUnavailable):
        p.chat([{"role": "user", "content": "c"}])


def test_openai_compat_custom_senza_chiave(monkeypatch):
    """Endpoint custom locale senza auth: nessun header Authorization."""
    def handler(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}]})

    _post_mock(monkeypatch, handler)
    p = OpenAICompatProvider(name="custom", model="m", base_url="http://localhost:1234/v1")
    assert p.chat([{"role": "user", "content": "c"}]).text == "ok"


# --- anthropic ---------------------------------------------------------------

def test_anthropic_non_stream(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "chiave-finta"
        assert request.headers["anthropic-version"]
        body = json.loads(request.content)
        assert body["system"] == "Sei ATLAS."
        assert [m["role"] for m in body["messages"]] == ["user"]  # system estratto
        assert body["max_tokens"] > 0  # obbligatorio per Anthropic
        assert body["tools"][0]["input_schema"] == {"type": "object"}
        return httpx.Response(200, json={
            "model": "claude-sonnet-4-5", "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": "Uso lo strumento."},
                {"type": "tool_use", "id": "t1", "name": "shell",
                 "input": {"cmd": "ls"}}]})

    _post_mock(monkeypatch, handler)
    p = AnthropicProvider(name="anthropic", model="claude-sonnet-4-5",
                          base_url="https://api.anthropic.com", api_key="chiave-finta")
    resp = p.chat([{"role": "system", "content": "Sei ATLAS."},
                   {"role": "user", "content": "lista"}],
                  tools=[{"name": "shell", "description": "d",
                          "parameters": {"type": "object"}}])
    assert resp.text == "Uso lo strumento."
    assert resp.tool_calls[0].name == "shell"
    assert resp.tool_calls[0].arguments == {"cmd": "ls"}
    assert resp.finish_reason == "tool_use"


def test_anthropic_stream(monkeypatch):
    lines = _sse(
        {"type": "message_start"},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "ciao"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": " mondo"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "t1", "name": "shell"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": "{\"cmd\":"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": " \"ls\"}"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    )
    _stream_mock(monkeypatch, lines)
    p = AnthropicProvider(name="anthropic", model="claude-sonnet-4-5",
                          base_url="https://api.anthropic.com", api_key="k")
    stream = p.chat([{"role": "user", "content": "lista"}], stream=True)
    assert list(stream) == ["ciao", " mondo"]
    assert stream.response.text == "ciao mondo"
    assert stream.response.tool_calls[0].arguments == {"cmd": "ls"}
    assert stream.response.finish_reason == "tool_use"


@pytest.mark.parametrize("status,exc", [
    (401, AuthenticationError),
    (429, RateLimitError),
    (529, ProviderUnavailable),
    (500, ProviderUnavailable),
])
def test_anthropic_errors(monkeypatch, status, exc):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "boom"}})

    _post_mock(monkeypatch, handler)
    p = AnthropicProvider(name="anthropic", model="m",
                          base_url="https://api.anthropic.com", api_key="segreta-xyz")
    with pytest.raises(exc) as ei:
        p.chat([{"role": "user", "content": "c"}])
    assert "segreta-xyz" not in str(ei.value)


# --- registro: nuovi tipi + switch/fallback misti ----------------------------

CLOUD_YAML = """
providers:
  - name: ollama_local
    type: ollama
    base_url: http://localhost:11434
    model: llama3.2:3b
    api_key_env: ""
  - name: groq
    type: openai_compat
    base_url: https://api.groq.com/openai/v1
    model: openai/gpt-oss-120b
    api_key_env: GROQ_API_KEY
  - name: gemini
    type: openai_compat
    base_url: https://generativelanguage.googleapis.com/v1beta/openai
    model: gemini-2.5-flash
    api_key_env: GEMINI_API_KEY
  - name: anthropic
    type: anthropic
    base_url: https://api.anthropic.com
    model: claude-sonnet-4-5
    api_key_env: ANTHROPIC_API_KEY
"""


def test_new_types_registered():
    assert PROVIDER_TYPES["openai_compat"] is OpenAICompatProvider
    assert PROVIDER_TYPES["anthropic"] is AnthropicProvider


def test_registry_cloud_providers(tmp_path):
    p = tmp_path / "providers.yaml"
    p.write_text(CLOUD_YAML, encoding="utf-8")
    reg = ProviderRegistry.from_file(p, env={"GROQ_API_KEY": "gk", "GEMINI_API_KEY": "gek"})
    assert reg.usable("groq")[0] and reg.usable("gemini")[0]
    ok, reason = reg.usable("anthropic")
    assert not ok and "ANTHROPIC_API_KEY" in reason
    # fallback: ollama -> groq -> gemini (anthropic senza chiave saltato)
    assert reg.fallback_order("ollama_local") == ["ollama_local", "groq", "gemini"]
    groq = reg.get("groq")
    assert isinstance(groq, OpenAICompatProvider)
    assert groq.base_url == "https://api.groq.com/openai/v1"
    assert isinstance(reg.get("anthropic"), AnthropicProvider)


def test_switch_and_fallback_mixed_types(tmp_path):
    """Switch /model use + fallback tra tipi diversi (unit, NON E2E reale)."""
    from app.providers.base import ChatStream

    p = tmp_path / "providers.yaml"
    p.write_text(CLOUD_YAML, encoding="utf-8")
    reg = ProviderRegistry.from_file(p, env={"GROQ_API_KEY": "gk", "GEMINI_API_KEY": "gek"})

    class FakeCloud(LLMProvider):
        def __init__(self, name, fail):
            super().__init__(name=name, model="m")
            self.fail = fail

        def chat(self, messages, tools=None, stream=False):
            if self.fail:
                raise RateLimitError(self.name, "limite")
            if stream:
                return ChatStream(iter([f"ok-{self.name}"]), self.name, "m")
            return ProviderResponse(text=f"ok-{self.name}", provider=self.name, model="m")

    # ollama_local e groq in rate limit -> la catena arriva a gemini
    reg.get = lambda name: FakeCloud(name, fail=name in ("ollama_local", "groq"))  # type: ignore[method-assign]
    resp = reg.chat_with_fallback("ollama_local", [{"role": "user", "content": "c"}])
    assert resp.text == "ok-gemini"
    stream = reg.chat_with_fallback("ollama_local", [{"role": "user", "content": "c"}], stream=True)
    assert list(stream) == ["ok-gemini"]
