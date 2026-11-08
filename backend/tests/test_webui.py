"""Stage 9 (spec sez. 7.2, build order 9): web UI locale. Nessuna rete e
nessun server: app via TestClient, registry finta a risposte programmate.
"""
from __future__ import annotations

import threading
import time

from app.agent.actionlog import ActionLog
from app.agent.memory import MemoryStore
from app.agent.permissions import Mode
from app.agent.safety import SecretFilter
from app.agent.tools import ToolContext
from app.providers.base import ProviderResponse, ToolCall
from app.webui import WebUISession, create_app, run_webui


class FakeRegistry:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat_with_fallback(self, active, messages, tools=None, stream=False,
                           on_fallback=None):
        self.calls.append({"messages": list(messages)})
        return self._responses.pop(0)

    # usato da /model list
    def names(self, enabled_only=True):
        return ["fake"]

    def spec(self, name):
        class Spec:
            type = "fake"
            requires_key = False
            api_key_env = ""
        return Spec()

    def usable(self, name):
        return True, ""

    def has_key(self, name):
        return True

    def current_model(self, name):
        return "fake-1"

    def get(self, name):
        class P:
            def list_models(self):
                return ["fake-1"]
        return P()


def text_response(text):
    return ProviderResponse(text=text)


def tool_response(name, args):
    return ProviderResponse(text="", tool_calls=[ToolCall(name=name, arguments=args)])


def make_session(tmp_path, responses, **kwargs):
    store = MemoryStore(tmp_path / "memory.db")
    secrets = SecretFilter(["sk-test-1234567890"])
    session = WebUISession(
        registry=FakeRegistry(responses), get_active=lambda: "fake",
        set_active=lambda n: None,
        describe_registry=lambda reg, act, out: out("fake (fake)"),
        ctx=ToolContext(workspace=tmp_path, secrets=secrets),
        store=store, log=ActionLog(tmp_path / "logs" / "actions.log", secrets),
        seal=None, secrets=secrets, mode=Mode.ASK, max_steps=5,
        confirm_timeout=5.0, **kwargs)
    return session, store


def make_client(tmp_path, responses, **kwargs):
    from fastapi.testclient import TestClient
    session, store = make_session(tmp_path, responses, **kwargs)
    return TestClient(create_app(session)), session, store


def wait_idle(session, timeout=5.0):
    deadline = time.time() + timeout
    while session.state()["state"] != "idle" and time.time() < deadline:
        time.sleep(0.02)


def test_index_serves_page(tmp_path):
    client, _, _ = make_client(tmp_path, [])
    r = client.get("/")
    assert r.status_code == 200 and "ATLAS" in r.text and "<script>" in r.text


def test_chat_runs_task_and_stores_history(tmp_path):
    client, session, store = make_client(
        tmp_path, [text_response("risposta web")])
    r = client.post("/api/chat", json={"message": "ciao"})
    assert r.status_code == 200 and r.json() == {"status": "running"}
    wait_idle(session)
    state = client.get("/api/state").json()
    assert state["state"] == "idle" and state["reply"] == "risposta web"
    hist = client.get("/api/history").json()["messages"]
    assert [m["content"] for m in hist] == ["ciao", "risposta web"]


def test_commands_answer_synchronously(tmp_path):
    client, _, _ = make_client(tmp_path, [])
    r = client.post("/api/chat", json={"message": "/model"})
    assert r.json()["status"] == "done"
    assert "provider: fake" in r.json()["reply"]


def test_busy_while_running(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class SlowRegistry(FakeRegistry):
        def chat_with_fallback(self, *a, **k):
            started.set()
            release.wait(5)
            return text_response("fine")

    session, _ = make_session(tmp_path, [])
    session._registry = SlowRegistry([])
    from fastapi.testclient import TestClient
    client = TestClient(create_app(session))
    threading.Thread(target=lambda: client.post(
        "/api/chat", json={"message": "lento"}), daemon=True).start()
    assert started.wait(5)
    r = client.post("/api/chat", json={"message": "secondo"})
    assert r.json()["status"] == "busy"
    r = client.post("/api/stop")
    assert r.json()["status"] == "stopping"
    release.set()


def test_confirmation_flow(tmp_path):
    client, session, _ = make_client(tmp_path, [
        tool_response("write_file", {"path": "x.txt", "content": "hi"}),
        text_response("file scritto"),
    ])
    r = client.post("/api/chat", json={"message": "scrivi un file"})
    assert r.json()["status"] == "running"
    deadline = time.time() + 5
    while session.state()["state"] != "question" and time.time() < deadline:
        time.sleep(0.02)
    assert session.state()["state"] == "question"
    assert session.state()["question"]
    r = client.post("/api/chat", json={"message": "yes"})
    assert r.json() == {"status": "answered"}
    wait_idle(session)
    assert session.state()["reply"] == "file scritto"
    assert (tmp_path / "x.txt").read_text() == "hi"


def test_confirmation_refused_by_other_text(tmp_path):
    client, session, _ = make_client(tmp_path, [
        tool_response("write_file", {"path": "y.txt", "content": "hi"}),
        text_response("ok, non scrivo"),
    ])
    client.post("/api/chat", json={"message": "scrivi"})
    deadline = time.time() + 5
    while session.state()["state"] != "question" and time.time() < deadline:
        time.sleep(0.02)
    client.post("/api/chat", json={"message": "no"})
    wait_idle(session)
    assert not (tmp_path / "y.txt").exists()


def test_reply_is_secret_filtered(tmp_path):
    client, session, _ = make_client(
        tmp_path, [text_response("la chiave e' sk-test-1234567890")])
    client.post("/api/chat", json={"message": "dimmi"})
    wait_idle(session)
    assert "sk-test-1234567890" not in session.state()["reply"]


def test_webui_refuses_non_localhost(tmp_path):
    out = []
    code = run_webui(None, active="fake", workspace=tmp_path,
                     mode=Mode.ASK, max_steps=1,
                     secrets=SecretFilter([]), host="0.0.0.0", out=out.append)
    assert code == 2 and any("localhost" in line for line in out)
