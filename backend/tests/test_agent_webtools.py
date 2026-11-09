"""Tool web e python_exec (spec sez. 3) + fallback JSON-in-testo (sez. 2).

Rete sempre sostituita con monkeypatch: nessuna chiamata esce davvero."""
from pathlib import Path

import pytest

from app.agent.actionlog import ActionLog
from app.agent.loop import _parse_text_tool_call, run_agent_loop
from app.agent.permissions import Decision, Mode, decide
from app.agent.safety import SecretFilter
from app.agent.tools import TOOLS_BY_NAME, ToolContext

from .test_agent_stage3 import FakeRegistry, text_response


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, secrets=SecretFilter(["sk-test"]))


@pytest.fixture()
def log(tmp_path: Path) -> ActionLog:
    return ActionLog(tmp_path / "logs" / "actions.log",
                     SecretFilter(["sk-test"]))


# --- web_search -------------------------------------------------------------

def test_web_search_returns_results(ctx, monkeypatch):
    class _Resp:
        text = ('<a class="result__a" href="https://duckduckgo.com/l/?uddg='
                'https%3A%2F%2Fesempio.it%2Farticolo">Titolo <b>uno</b></a>'
                '<a class="result__a" href="https://directo.it/x">Due</a>')

        def raise_for_status(self):
            return None

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    out = TOOLS_BY_NAME["web_search"].run({"query": "test"}, ctx)
    assert "Titolo uno" in out
    assert "https://esempio.it/articolo" in out
    assert "https://directo.it/x" in out


def test_web_search_empty_query(ctx):
    assert "errore" in TOOLS_BY_NAME["web_search"].run({"query": " "}, ctx)


def test_web_search_network_error(ctx, monkeypatch):
    import httpx
    def _boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr("httpx.post", _boom)
    assert "errore ricerca web" in TOOLS_BY_NAME["web_search"].run({"query": "x"}, ctx)


# --- fetch_url ---------------------------------------------------------------

def test_fetch_url_strips_html(ctx, monkeypatch):
    class _Resp:
        headers = {"content-type": "text/html"}
        text = "<html><style>x{}</style><body><p>Ciao mondo</p><script>bad()</script></body></html>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp())
    out = TOOLS_BY_NAME["fetch_url"].run({"url": "https://esempio.it"}, ctx)
    assert "Ciao mondo" in out
    assert "bad()" not in out


def test_fetch_url_blocks_private_hosts(ctx):
    for url in ("http://127.0.0.1:8321/agent", "http://localhost/x",
                "http://192.168.1.1/router", "http://169.254.0.1/meta"):
        out = TOOLS_BY_NAME["fetch_url"].run({"url": url}, ctx)
        assert "errore" in out, url


def test_fetch_url_bad_scheme(ctx):
    assert "errore" in TOOLS_BY_NAME["fetch_url"].run({"url": "file:///etc/passwd"}, ctx)


# --- python_exec -------------------------------------------------------------

def test_python_exec_runs_code(ctx, tmp_path):
    out = TOOLS_BY_NAME["python_exec"].run(
        {"code": "print(sum(range(10)))"}, ctx)
    assert "45" in out


def test_python_exec_no_secrets_in_env(ctx):
    out = TOOLS_BY_NAME["python_exec"].run(
        {"code": "import os; print([k for k in os.environ if 'KEY' in k.upper()])"}, ctx)
    assert "[]" in out


def test_python_exec_timeout(ctx):
    out = TOOLS_BY_NAME["python_exec"].run(
        {"code": "import time; time.sleep(60)"}, ctx)
    assert "timeout" in out


def test_python_exec_asks_permission(ctx):
    """python_exec puo' scrivere: in auto-safe chiede conferma, letture no."""
    d, _ = decide(Mode.AUTO_SAFE, "python_exec", {"code": "print(1)"}, ctx.workspace)
    assert d is Decision.ASK
    d, _ = decide(Mode.AUTO_SAFE, "web_search", {"query": "x"}, ctx.workspace)
    assert d is Decision.RUN
    d, _ = decide(Mode.ASK, "fetch_url", {"url": "https://x.it"}, ctx.workspace)
    assert d is Decision.RUN


# --- fallback JSON-in-testo (spec sez. 2) ------------------------------------

def test_parse_text_tool_call_valid(ctx):
    call = _parse_text_tool_call('{"name": "list_dir", "arguments": {"path": "."}}')
    assert call is not None and call.name == "list_dir"
    call = _parse_text_tool_call('ecco:\n```json\n{"tool": "web_search", "args": {"query": "x"}}\n```')
    assert call is not None and call.name == "web_search"


def test_parse_text_tool_call_rejects(ctx):
    assert _parse_text_tool_call("nessun json qui") is None
    assert _parse_text_tool_call('{"name": "non_esiste", "arguments": {}}') is None
    assert _parse_text_tool_call('{"name": "list_dir", "arguments": "no"}') is None


def test_loop_executes_json_text_fallback(ctx, log, tmp_path):
    """Un modello senza tool-calling nativo emette la chiamata in JSON: il
    loop la esegue come una tool-call nativa e continua."""
    (tmp_path / "nota.txt").write_text("contenuto di prova", encoding="utf-8")
    registry = FakeRegistry([
        text_response('{"name": "read_file", "arguments": {"path": "nota.txt"}}'),
        text_response("Il file dice: contenuto di prova"),
    ])
    result = run_agent_loop(registry, "test", [{"role": "user", "content": "leggi"}],
                            ctx, Mode.ASK, log, ask_fn=lambda q: True)
    assert "contenuto di prova" in result.text
    assert any("fallback JSON" in n for n in result.notes)
