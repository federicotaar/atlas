"""Tool web, python_exec (sandbox bwrap), google_read + fallback JSON-in-testo.

Rete sempre sostituita con monkeypatch: nessuna chiamata esce davvero.
I test della sandbox richiedono bwrap (come i tool generati, stage 14):
senza bwrap il tool rifiuta l'esecuzione, e quello e' testato a parte."""
import shutil
from pathlib import Path

import pytest

from app.agent.actionlog import ActionLog
from app.agent.loop import _parse_text_tool_call, run_agent_loop
from app.agent.permissions import Decision, Mode, decide
from app.agent.safety import SecretFilter
from app.agent.tools import TOOLS_BY_NAME, ToolContext

from .test_agent_stage3 import FakeRegistry, text_response, tool_response

HAS_BWRAP = shutil.which("bwrap") is not None


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, secrets=SecretFilter(["sk-test"]))


@pytest.fixture()
def log(tmp_path: Path) -> ActionLog:
    return ActionLog(tmp_path / "logs" / "actions.log",
                     SecretFilter(["sk-test"]))


def _resp(status: int, headers: dict, body: bytes | str):
    """Risposta finta per il seam _request_once: (status, headers, bytes)."""
    if isinstance(body, str):
        body = body.encode()
    return status, headers, body


# --- web_search -------------------------------------------------------------

def test_web_search_returns_results(ctx, monkeypatch):
    html = ('<a class="result__a" href="https://duckduckgo.com/l/?uddg='
            'https%3A%2F%2Fesempio.it%2Farticolo">Titolo <b>uno</b></a>'
            '<a class="result__a" href="https://directo.it/x">Due</a>')
    monkeypatch.setattr("app.agent.tools._request_once",
                        lambda url, timeout, max_bytes:
                        _resp(200, {"content-type": "text/html"}, html))
    out = TOOLS_BY_NAME["web_search"].run({"query": "test"}, ctx)
    assert "Titolo uno" in out
    assert "https://esempio.it/articolo" in out
    assert "https://directo.it/x" in out
    # provenance: il modello vede che sono dati esterni non verificati
    assert "fonte esterna" in out and "non istruzioni" in out


def test_web_search_empty_query(ctx):
    assert "errore" in TOOLS_BY_NAME["web_search"].run({"query": " "}, ctx)


def test_web_search_network_error(ctx, monkeypatch):
    import httpx
    def _boom(url, timeout, max_bytes):
        raise httpx.ConnectError("down")
    monkeypatch.setattr("app.agent.tools._request_once", _boom)
    assert "errore ricerca web" in TOOLS_BY_NAME["web_search"].run(
        {"query": "x"}, ctx)


# --- fetch_url ---------------------------------------------------------------

def test_fetch_url_strips_html(ctx, monkeypatch):
    html = ("<html><style>x{}</style><body><p>Ciao mondo</p>"
            "<script>bad()</script></body></html>")
    monkeypatch.setattr("app.agent.tools._request_once",
                        lambda url, timeout, max_bytes:
                        _resp(200, {"content-type": "text/html; charset=utf-8"},
                              html))
    out = TOOLS_BY_NAME["fetch_url"].run({"url": "https://esempio.it"}, ctx)
    assert "Ciao mondo" in out
    assert "bad()" not in out
    assert "fonte esterna" in out and "non istruzioni" in out


def test_fetch_url_blocks_private_hosts(ctx):
    for url in ("http://127.0.0.1:8321/agent", "http://localhost/x",
                "http://192.168.1.1/router", "http://169.254.0.1/meta",
                "http://169.254.169.254/latest/meta-data",
                "http://[::1]/x", "http://[fd00::1]/x", "http://0.0.0.0/x"):
        out = TOOLS_BY_NAME["fetch_url"].run({"url": url}, ctx)
        assert "errore" in out, url


def test_fetch_url_bad_scheme(ctx):
    assert "errore" in TOOLS_BY_NAME["fetch_url"].run({"url": "file:///etc/passwd"}, ctx)


def test_fetch_url_redirect_to_private_blocked(ctx, monkeypatch):
    """Validate-then-follow-cieco e' vietato: OGNI redirect e' rivalidato."""
    calls = []

    def _once(url, timeout, max_bytes):
        calls.append(url)
        if len(calls) == 1:
            return _resp(302, {"location": "http://169.254.169.254/latest"}, b"")
        raise AssertionError("redirect verso IP privato seguito!")

    monkeypatch.setattr("app.agent.tools._request_once", _once)
    out = TOOLS_BY_NAME["fetch_url"].run({"url": "https://esempio.it"}, ctx)
    assert "errore" in out and "non pubblico" in out
    assert calls == ["https://esempio.it"]


def test_fetch_url_redirect_to_public_followed(ctx, monkeypatch):
    calls = []

    def _once(url, timeout, max_bytes):
        calls.append(url)
        if url == "https://esempio.it/a":
            return _resp(301, {"location": "https://esempio.it/b"}, b"")
        return _resp(200, {"content-type": "text/plain"}, "destinazione")

    monkeypatch.setattr("app.agent.tools._request_once", _once)
    out = TOOLS_BY_NAME["fetch_url"].run({"url": "https://esempio.it/a"}, ctx)
    assert "destinazione" in out
    assert calls == ["https://esempio.it/a", "https://esempio.it/b"]


def test_fetch_url_too_many_redirects(ctx, monkeypatch):
    monkeypatch.setattr("app.agent.tools._request_once",
                        lambda url, timeout, max_bytes:
                        _resp(301, {"location": "https://esempio.it/x"}, b""))
    out = TOOLS_BY_NAME["fetch_url"].run({"url": "https://esempio.it"}, ctx)
    assert "troppi redirect" in out


def test_fetch_url_byte_cap(ctx, monkeypatch):
    """Il tetto ai byte e' applicato in streaming dentro _request_once."""
    class _Stream:
        status_code = 200
        headers = {"content-type": "text/plain"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self, n):
            yield b"x" * 100_000
            yield b"x" * 100_000
            yield b"x" * 100_000

    monkeypatch.setattr("httpx.stream", lambda *a, **k: _Stream())
    out = TOOLS_BY_NAME["fetch_url"].run({"url": "https://esempio.it"}, ctx)
    assert "oltre il limite" in out


def test_fetch_url_binary_rejected(ctx, monkeypatch):
    monkeypatch.setattr("app.agent.tools._request_once",
                        lambda url, timeout, max_bytes:
                        _resp(200, {"content-type": "application/octet-stream"},
                              b"\x00\x01"))
    out = TOOLS_BY_NAME["fetch_url"].run({"url": "https://esempio.it/f"}, ctx)
    assert "non testuale" in out


# --- python_exec (sandbox bwrap) --------------------------------------------

@pytest.mark.skipif(not HAS_BWRAP, reason="bwrap non disponibile")
def test_python_exec_runs_code(ctx):
    out = TOOLS_BY_NAME["python_exec"].run(
        {"code": "print(sum(range(10)))"}, ctx)
    assert "45" in out


@pytest.mark.skipif(not HAS_BWRAP, reason="bwrap non disponibile")
def test_python_exec_no_network(ctx):
    """La sandbox non ha rete: connessioni e DNS falliscono."""
    out = TOOLS_BY_NAME["python_exec"].run(
        {"code": ("import socket\n"
                  "try:\n"
                  "    socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
                  "    print('RETE APERTA')\n"
                  "except OSError:\n"
                  "    print('rete bloccata')")}, ctx)
    assert "rete bloccata" in out and "RETE APERTA" not in out


@pytest.mark.skipif(not HAS_BWRAP, reason="bwrap non disponibile")
def test_python_exec_no_host_filesystem(ctx, tmp_path):
    """Il filesystem dell'host non esiste nel namespace: niente /etc, niente
    workspace, niente .env (strato protetto) leggibili dal codice."""
    (tmp_path / ".env").write_text("GROQ_API_KEY=sk-test", encoding="utf-8")
    code = (
        "import os\n"
        "for p in ('/etc/hostname', %r, %r):\n"
        "    try:\n"
        "        open(p).read()\n"
        "        print('LETTO', p)\n"
        "    except OSError:\n"
        "        print('assente', p)\n"
        % (str(tmp_path / ".env"), str(tmp_path)))
    out = TOOLS_BY_NAME["python_exec"].run({"code": code}, ctx)
    assert "LETTO" not in out
    assert "sk-test" not in out


@pytest.mark.skipif(not HAS_BWRAP, reason="bwrap non disponibile")
def test_python_exec_write_confined(ctx, tmp_path):
    """Scritture fuori dalla directory temporanea della sandbox: rifiutate
    (filesystem di sistema read-only). Niente side-effect sull'host."""
    victim = tmp_path / "vittima.txt"
    code = (
        "try:\n"
        "    open('/usr/local/evil.txt', 'w').write('x')\n"
        "    print('SCRITTO usr')\n"
        "except OSError:\n"
        "    print('usr read-only')\n"
        "try:\n"
        "    open(%r, 'w').write('x')\n"
        "    print('SCRITTO workspace')\n"
        "except OSError:\n"
        "    print('workspace non raggiungibile')\n"
        "open('/work/ok.txt', 'w').write('x')\n"
        "print('work scrivibile')" % str(victim))
    out = TOOLS_BY_NAME["python_exec"].run({"code": code}, ctx)
    assert "SCRITTO" not in out
    assert "work scrivibile" in out
    assert not victim.exists()
    assert not Path("/usr/local/evil.txt").exists()


@pytest.mark.skipif(not HAS_BWRAP, reason="bwrap non disponibile")
def test_python_exec_no_secrets_in_env(ctx):
    out = TOOLS_BY_NAME["python_exec"].run(
        {"code": "import os; print(sorted(os.environ))"}, ctx)
    assert "sk-test" not in out
    assert "GROQ" not in out and "TOKEN" not in out


def test_python_exec_refuses_without_bwrap(ctx, monkeypatch):
    """Fail-closed: senza sandbox NIENTE viene eseguito (niente fallback
    sull'host)."""
    monkeypatch.setattr("app.agent.forge.run_python_snippet",
                        lambda code, timeout=30: None)
    out = TOOLS_BY_NAME["python_exec"].run({"code": "print(1)"}, ctx)
    assert "disabilitata" in out and "rifiutata" in out


def test_python_exec_timeout(ctx, monkeypatch):
    import subprocess
    def _slow(code, timeout=30):
        raise subprocess.TimeoutExpired(cmd="bwrap", timeout=timeout)
    monkeypatch.setattr("app.agent.forge.run_python_snippet", _slow)
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


def test_fallback_json_same_permission_path(ctx, log, tmp_path):
    """Una chiamata derivata dal TESTO passa da decide() come una nativa:
    niente scritture senza consenso, niente percorsi protetti, mai
    esecuzione diretta."""
    registry = FakeRegistry([
        text_response('{"name": "write_file", "arguments": '
                      '{"path": "../evil.txt", "content": "x"}}'),
        text_response("fatto"),
    ])
    result = run_agent_loop(registry, "test", [{"role": "user", "content": "x"}],
                            ctx, Mode.ASK, log, ask_fn=lambda q: False)
    assert not (tmp_path.parent / "evil.txt").exists()
    assert any("non approvato" in n for n in result.notes)

    (tmp_path / ".env").write_text("GROQ_API_KEY=sk-test", encoding="utf-8")
    registry = FakeRegistry([
        text_response('{"name": "read_file", "arguments": {"path": ".env"}}'),
        text_response("ok"),
    ])
    result = run_agent_loop(registry, "test", [{"role": "user", "content": "x"}],
                            ctx, Mode.YOLO, log, ask_fn=lambda q: True)
    assert "sk-test" not in result.text
    assert any("negat" in n for n in result.notes)


# --- google_read: ponte agente -> ActionEngine -------------------------------

@pytest.fixture()
def google_env(monkeypatch):
    from cryptography.fernet import Fernet

    from app.config import get_settings

    from .test_google_connectors import CLIENT_ID
    monkeypatch.setenv("ATLAS_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ATLAS_GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _gmail_connector(client, google_env, monkeypatch, name="g-agent"):
    from .test_google_connectors import _do_callback, make_user
    u = make_user(client, name)
    _do_callback(client, u["headers"], monkeypatch, scope_set="gmail")
    return u


def test_google_read_discoverable_and_invoked(client, google_env, monkeypatch,
                                              ctx, log):
    """Prova end-to-end: il tool e' esposto al provider, il loop lo invoca
    e la lettura passa dall'ActionEngine con account fixture (nessuna mail
    reale, nessun invio)."""
    _gmail_connector(client, google_env, monkeypatch)

    def _list_ok(access_token, query="", max_results=10):
        return {"messages": [
            {"id": "m1", "thread_id": "t1", "from": "prof@example.com",
             "subject": "Verifica", "date": "Mon, 9 Nov 2026",
             "snippet": "ricorda la consegna"},
        ]}
    monkeypatch.setattr("app.services.google_api.gmail_list_messages", _list_ok)

    registry = FakeRegistry([
        tool_response("google_read",
                      {"action": "google_gmail.list_messages",
                       "args": {"query": "in:inbox"}}),
        text_response("Hai scritto io"),
    ])
    result = run_agent_loop(registry, "test",
                            [{"role": "user", "content": "leggi la posta"}],
                            ctx, Mode.ASK, log, ask_fn=lambda q: True)
    # discoverability: il tool era tra gli spec passati al provider
    specs = registry.calls[0]["tools"] or []
    assert any(s["name"] == "google_read" for s in specs)
    # invocation: il risultato del tool e' entrato nei messaggi del giro dopo
    tool_msgs = [m for m in registry.calls[1]["messages"]
                 if m.get("role") == "tool"]
    assert any("prof@example.com" in (m.get("content") or "")
               for m in tool_msgs)
    assert any("google_read" in n for n in result.notes)


def test_google_read_unauthorized_connector(client, google_env, monkeypatch,
                                            ctx, db_session):
    """Nessun connettore gmail autorizzato: errore chiaro, niente esecuzione."""
    from app.models import Connector
    for c in db_session.query(Connector).filter(
            Connector.kind == "google_gmail",
            Connector.status == "authorized"):
        c.status = "revoked"
    db_session.commit()
    out = TOOLS_BY_NAME["google_read"].run(
        {"action": "google_gmail.list_messages", "args": {}}, ctx)
    assert "non collegato" in out


def test_google_read_rejects_non_read_actions(ctx):
    out = TOOLS_BY_NAME["google_read"].run(
        {"action": "google_gmail.create_draft", "args": {}}, ctx)
    assert "errore" in out and "Solo letture" in out


def test_connecting_inbox_fetches_nothing(client, google_env, monkeypatch):
    """Collegare la casella NON manda nulla a nessun LLM e non scarica mail:
    il contenuto esce solo quando l'utente chiede una lettura esplicita."""
    calls = []
    monkeypatch.setattr("app.services.google_api.gmail_list_messages",
                        lambda *a, **k: calls.append("list"))
    monkeypatch.setattr("app.services.google_api.gmail_get_message",
                        lambda *a, **k: calls.append("get"))
    _gmail_connector(client, google_env, monkeypatch, name="g-nofetch")
    assert calls == []
