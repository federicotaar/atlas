"""Test del layer provider dell'agente (spec 1): astrazione, Ollama,
registro da YAML, chiavi da .env, fallback di catena, parsing /model."""
from __future__ import annotations

import json

import httpx
import pytest

from app.agent_cli import parse_model_target
from app.providers.base import (
    AllProvidersFailed,
    AuthenticationError,
    ChatStream,
    LLMProvider,
    ModelNotFound,
    ProviderError,
    ProviderResponse,
    ProviderUnavailable,
    RateLimitError,
    ToolCall,
)
from app.providers.ollama import OllamaProvider
from app.providers.registry import ConfigError, ProviderRegistry, ProviderSpec, load_dotenv


def _ollama(payload: dict, base_url: str = "http://localhost:11434", api_key: str = "") -> OllamaProvider:
    return OllamaProvider(name="test", model=payload.pop("model", "m"),
                          base_url=base_url, api_key=api_key)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


class MockOllama(OllamaProvider):
    """Ollama con trasporto finto: niente rete nei test."""

    def __init__(self, *args, handler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._handler = handler

    def _client_post(self, url, **kw):
        with httpx.Client(transport=_mock_transport(self._handler)) as c:
            return c.post(url, **kw)


# --- Ollama: risposta non in streaming -------------------------------------

def test_chat_non_stream_parse(monkeypatch):
    def handler(request):
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "m" and body["stream"] is False
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": "ciao mondo"},
            "done_reason": "stop"})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(
        transport=_mock_transport(handler)).post(url, **kw))
    p = _ollama({"model": "m"})
    resp = p.chat([{"role": "user", "content": "ciao"}])
    assert isinstance(resp, ProviderResponse)
    assert resp.text == "ciao mondo"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


def test_chat_tool_calls_normalized(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "shell",
                                                     "arguments": {"cmd": "ls"}}}]},
            "done_reason": "stop"})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(
        transport=_mock_transport(handler)).post(url, **kw))
    p = _ollama({"model": "m"})
    tools = [{"name": "shell", "description": "run", "parameters": {"type": "object"}}]
    resp = p.chat([{"role": "user", "content": "lista i file"}], tools=tools)
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "shell"
    assert resp.tool_calls[0].arguments == {"cmd": "ls"}


def test_chat_tool_calls_arguments_string_json(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "read_file",
                                                     "arguments": "{\"path\": \"a.txt\"}"}}]}})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(
        transport=_mock_transport(handler)).post(url, **kw))
    resp = _ollama({"model": "m"}).chat([{"role": "user", "content": "x"}])
    assert resp.tool_calls[0].arguments == {"path": "a.txt"}


# --- Ollama: errori mappati, mai la chiave nel messaggio --------------------

@pytest.mark.parametrize("status,exc", [
    (401, AuthenticationError),
    (403, AuthenticationError),
    (404, ModelNotFound),
    (429, RateLimitError),
    (500, ProviderUnavailable),
    (400, ProviderError),
])
def test_error_mapping(monkeypatch, status, exc):
    def handler(request):
        return httpx.Response(status, json={"error": f"boom {status}"})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(
        transport=_mock_transport(handler)).post(url, **kw))
    p = _ollama({"model": "m"}, api_key="segretissima-123")
    with pytest.raises(exc) as ei:
        p.chat([{"role": "user", "content": "c"}])
    assert "segretissima-123" not in str(ei.value)


def test_network_error_is_unavailable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("giu'")

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(
        transport=_mock_transport(handler)).post(url, **kw))
    with pytest.raises(ProviderUnavailable):
        _ollama({"model": "m"}).chat([{"role": "user", "content": "c"}])


# --- Ollama: streaming -------------------------------------------------------

def _ndjson_lines(chunks):
    return "\n".join(json.dumps(c) for c in chunks) + "\n"


def test_stream_accumulates_and_exposes_response(monkeypatch):
    chunks = [
        {"message": {"role": "assistant", "content": "ciao"}, "done": False},
        {"message": {"role": "assistant", "content": " mondo"}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True,
         "done_reason": "stop"},
    ]

    class FakeResp:
        status_code = 200

        def iter_lines(self):
            return iter(_ndjson_lines(chunks).splitlines())

        def read(self):
            return None

        @property
        def text(self):
            return ""

        def json(self):
            return {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: FakeResp())
    p = _ollama({"model": "m"})
    stream = p.chat([{"role": "user", "content": "c"}], stream=True)
    assert isinstance(stream, ChatStream)
    assert list(stream) == ["ciao", " mondo"]
    assert stream.response is not None
    assert stream.response.text == "ciao mondo"
    assert stream.response.finish_reason == "stop"
    assert stream.chunks_emitted == 2


def test_stream_tool_calls_from_final_chunk(monkeypatch):
    chunks = [
        {"message": {"role": "assistant", "content": ""}, "done": True,
         "done_reason": "stop",
         "message_tool_calls": None,
         },
    ]
    chunks[0]["message"]["tool_calls"] = [{"function": {"name": "shell", "arguments": {"cmd": "ls"}}}]

    class FakeResp:
        status_code = 200

        def iter_lines(self):
            return iter(_ndjson_lines(chunks).splitlines())

        def read(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: FakeResp())
    stream = _ollama({"model": "m"}).chat([{"role": "user", "content": "c"}], stream=True)
    list(stream)
    assert stream.response.tool_calls[0].name == "shell"


# --- ChatStream standalone ---------------------------------------------------

def test_chatstream_response_without_final_event():
    s = ChatStream(iter(["a", "b"]), "p", "m")
    assert list(s) == ["a", "b"]
    assert s.response.text == "ab"
    assert s.response.provider == "p"


# --- Registro: YAML, chiavi, fallback ---------------------------------------

def _write_config(tmp_path, text):
    p = tmp_path / "providers.yaml"
    p.write_text(text, encoding="utf-8")
    return p


GOOD_YAML = """
providers:
  - name: ollama_local
    type: ollama
    base_url: http://localhost:11434
    model: llama3.2:3b
    api_key_env: ""
  - name: ollama_cloud
    type: ollama
    base_url: https://ollama.com
    model: gpt-oss:120b-cloud
    api_key_env: OLLAMA_API_KEY
"""


def test_registry_from_yaml(tmp_path):
    reg = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML), env={})
    assert reg.names() == ["ollama_local", "ollama_cloud"]
    ok, reason = reg.usable("ollama_local")
    assert ok and reason == ""
    ok, reason = reg.usable("ollama_cloud")
    assert not ok and "OLLAMA_API_KEY" in reason
    # la chiave arriva dall'ambiente: con la variabile impostata diventa usabile
    reg2 = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML),
                                      env={"OLLAMA_API_KEY": "k"})
    assert reg2.usable("ollama_cloud")[0]


def test_registry_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        ProviderRegistry.from_file(_write_config(tmp_path, "providers: []"))
    with pytest.raises(ConfigError):
        ProviderRegistry.from_file(_write_config(tmp_path, "providers:\n  - name: x\n"))
    with pytest.raises(ConfigError):
        ProviderRegistry.from_file(
            _write_config(tmp_path, GOOD_YAML + GOOD_YAML.split("providers:")[1]))
    with pytest.raises(ConfigError):
        ProviderRegistry.from_file(tmp_path / "manca.yaml")


def test_registry_unknown_provider_and_type(tmp_path):
    reg = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML), env={})
    with pytest.raises(ConfigError):
        reg.get("inesistente")
    bad = _write_config(tmp_path, GOOD_YAML.replace("type: ollama", "type: marziano", 1))
    reg2 = ProviderRegistry.from_file(bad, env={})
    ok, reason = reg2.usable("ollama_local")
    assert not ok and "marziano" in reason


def test_fallback_order_skips_unusable(tmp_path):
    reg = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML), env={})
    # ollama_cloud senza chiave: saltato
    assert reg.fallback_order("ollama_local") == ["ollama_local"]
    reg2 = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML),
                                      env={"OLLAMA_API_KEY": "k"})
    assert reg2.fallback_order("ollama_local") == ["ollama_local", "ollama_cloud"]
    assert reg2.fallback_order("ollama_cloud") == ["ollama_cloud", "ollama_local"]


class FakeProvider(LLMProvider):
    """Provider finto per la catena: programmabile a fallire."""

    def __init__(self, *, name, behavior, **kw):
        super().__init__(name=name, model="m", **kw)
        self.behavior = behavior
        self.calls = 0

    def chat(self, messages, tools=None, stream=False):
        self.calls += 1
        if self.behavior == "429":
            raise RateLimitError(self.name, "limite")
        if self.behavior == "badkey":
            raise AuthenticationError(self.name, "chiave rifiutata")
        if self.behavior == "down":
            raise ProviderUnavailable(self.name, "giu'")
        if stream:
            return ChatStream(iter(["ok-", self.name]), self.name, "m")
        return ProviderResponse(text=f"ok-{self.name}", provider=self.name, model="m")


def _chain(behaviors, tmp_path):
    specs = [ProviderSpec(name=n, type="ollama") for n in behaviors]
    reg = ProviderRegistry(specs, env={})
    reg._instances = {}
    for n, b in behaviors.items():
        reg._instances[n] = FakeProvider(name=n, behavior=b)

    # get() ritorna il finto senza ricostruirlo
    orig_get = reg.get

    def get(name):
        return reg._instances.get(name) or orig_get(name)

    reg.get = get
    return reg


def test_chain_fallback_on_rate_limit(tmp_path):
    reg = _chain({"a": "429", "b": "ok"}, tmp_path)
    resp = reg.chat_with_fallback("a", [{"role": "user", "content": "c"}])
    assert resp.text == "ok-b"
    assert reg.get("a").calls == 1 and reg.get("b").calls == 1


def test_chain_fallback_on_bad_key(tmp_path):
    # chiave invalida: la catena regge e passa oltre (definition of done)
    reg = _chain({"a": "badkey", "b": "ok"}, tmp_path)
    assert reg.chat_with_fallback("a", [{"role": "user", "content": "c"}]).text == "ok-b"


def test_chain_all_failed_reports_attempts(tmp_path):
    reg = _chain({"a": "429", "b": "down"}, tmp_path)
    with pytest.raises(AllProvidersFailed) as ei:
        reg.chat_with_fallback("a", [{"role": "user", "content": "c"}])
    assert [n for n, _ in ei.value.attempts] == ["a", "b"]


def test_chain_stream_fallback_before_first_delta(tmp_path):
    reg = _chain({"a": "down", "b": "ok"}, tmp_path)
    stream = reg.chat_with_fallback("a", [{"role": "user", "content": "c"}], stream=True)
    assert list(stream) == ["ok-", "b"]


def test_set_model_switch(tmp_path):
    reg = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML), env={})
    assert reg.current_model("ollama_local") == "llama3.2:3b"
    reg.set_model("ollama_local", "qwen2.5:0.5b")
    assert reg.current_model("ollama_local") == "qwen2.5:0.5b"
    assert reg.get("ollama_local").model == "qwen2.5:0.5b"
    with pytest.raises(ConfigError):
        reg.set_model("inesistente", "x")


# --- .env loader -------------------------------------------------------------

def test_load_dotenv(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    envfile.write_text("# commento\nOLLAMA_API_KEY=segreta\nVUOTA=\nQUOTED=\"qq\"\n",
                       encoding="utf-8")
    for k in ("OLLAMA_API_KEY", "VUOTA", "QUOTED"):
        monkeypatch.delenv(k, raising=False)
    import os
    loaded = load_dotenv(envfile)
    assert os.environ["OLLAMA_API_KEY"] == "segreta"
    assert os.environ["QUOTED"] == "qq"
    assert set(loaded) == {"OLLAMA_API_KEY", "VUOTA", "QUOTED"}
    # non sovrascrive l'ambiente esistente
    os.environ["OLLAMA_API_KEY"] = "ambiente"
    load_dotenv(envfile)
    assert os.environ["OLLAMA_API_KEY"] == "ambiente"
    assert load_dotenv(tmp_path / "assente") == {}


def test_has_key_onesto(tmp_path):
    # regressione: /model list non deve dire "presente" per una chiave assente
    reg = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML), env={})
    assert not reg.has_key("ollama_cloud")
    assert not reg.has_key("ollama_local")  # chiave non richiesta
    reg2 = ProviderRegistry.from_file(_write_config(tmp_path, GOOD_YAML),
                                      env={"OLLAMA_API_KEY": "k"})
    assert reg2.has_key("ollama_cloud")


def test_max_tokens_dal_config(tmp_path):
    yaml_con_tetto = GOOD_YAML.replace('api_key_env: ""', 'api_key_env: ""\n    max_tokens: 256', 1)
    reg = ProviderRegistry.from_file(_write_config(tmp_path, yaml_con_tetto), env={})
    assert reg.get("ollama_local").max_tokens == 256
    assert reg.get("ollama_cloud").max_tokens == 0


def test_ollama_payload_num_predict(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(
        transport=_mock_transport(handler)).post(url, **kw))
    p = OllamaProvider(name="t", model="m", max_tokens=128)
    p.chat([{"role": "user", "content": "c"}])
    assert seen["options"] == {"num_predict": 128}
    p2 = OllamaProvider(name="t", model="m")  # max_tokens=0: niente options
    seen.clear()
    p2.chat([{"role": "user", "content": "c"}])
    assert "options" not in seen


# --- CLI: /stop e task in background -----------------------------------------

import threading  # noqa: E402

from app.agent_cli import run_cli  # noqa: E402


def _cli_registry(fake):
    reg = ProviderRegistry([ProviderSpec(name="fake", type="ollama")], env={})
    reg.get = lambda name: fake  # type: ignore[method-assign]
    return reg


def _captured():
    buf: list[str] = []
    return buf, (lambda s: buf.append(str(s))), (lambda s: buf.append(str(s)))


def test_stop_cancels_running_task(tmp_path):
    # /stop ferma il task tra un passo e l'altro del loop strumenti. Il
    # provider chiede strumenti all'infinito (con una pausa per lasciare al
    # REPL il tempo di inviare /stop): senza cancel non finirebbe mai.
    class P(LLMProvider):
        def chat(self, messages, tools=None, stream=False):
            threading.Event().wait(0.5)
            resp = ProviderResponse(
                text="", provider=self.name, model="m",
                tool_calls=[ToolCall(name="list_dir", arguments={})])
            if stream:
                return ChatStream(iter([resp]), self.name, "m")
            return resp

    fake = P(name="fake", model="m")
    lines = iter(["racconta", "/stop", "/quit"])
    buf, out, write = _captured()
    run_cli(_cli_registry(fake), active="fake", workspace=tmp_path,
            input_fn=lambda prompt: next(lines), out=out, write=write)
    text = "".join(buf)
    assert "annullamento richiesto" in text
    assert "annullato con /stop" in text


def test_stop_without_task(tmp_path):
    lines = iter(["/stop", "/quit"])
    buf, out, write = _captured()
    run_cli(_cli_registry(FakeProvider(name="fake", behavior="ok")), active="fake",
            workspace=tmp_path,
            input_fn=lambda prompt: next(lines), out=out, write=write)
    assert "Nessun task in corso." in "".join(buf)


def test_second_message_rejected_while_running(tmp_path):
    gate = threading.Event()

    class P(LLMProvider):
        def chat(self, messages, tools=None, stream=False):
            gate.wait(5)
            return ProviderResponse(text="pronto", provider=self.name, model="m")

    fake = P(name="fake", model="m")
    lines = iter(["uno", "due", "/quit"])
    buf, out, write = _captured()

    def release_soon():
        import time
        time.sleep(0.3)
        gate.set()

    threading.Thread(target=release_soon, daemon=True).start()
    run_cli(_cli_registry(fake), active="fake", workspace=tmp_path,
            input_fn=lambda prompt: next(lines), out=out, write=write)
    assert "gia' in corso" in "".join(buf)


# --- parsing /model use ------------------------------------------------------

def test_parse_model_target():
    assert parse_model_target("ollama_local") == ("ollama_local", None)
    assert parse_model_target("ollama_local:llama3.2:3b") == ("ollama_local", "llama3.2:3b")
    with pytest.raises(ConfigError):
        parse_model_target("")
    with pytest.raises(ConfigError):
        parse_model_target(":modello")
    with pytest.raises(ConfigError):
        parse_model_target("provider:")
