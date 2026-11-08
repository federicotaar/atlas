"""Desktop integration dello stage 9: mount sotto /agent, lock istanza
singola e provisioning provider/chiavi dalla superficie web."""
from __future__ import annotations

import os
import stat

import pytest

from app.webui import AgentAlreadyRunning, AgentLock, ConfigAPI, create_app

from .test_webui import make_session


class FakeSpec:
    type = "fake"
    requires_key = True
    api_key_env = "ATLAS_TEST_KEY"


class FakeRegistryForConfig:
    def __init__(self):
        self._order = ["fake"]

    def spec(self, name):
        if name != "fake":
            raise KeyError(name)
        return FakeSpec()

    def usable(self, name):
        self.spec(name)
        if os.environ.get("ATLAS_TEST_KEY"):
            return True, ""
        return False, "chiave mancante (ATLAS_TEST_KEY non impostata)"

    def current_model(self, name):
        return "fake-1"


def make_config(tmp_path):
    state = {"active": "fake"}
    return ConfigAPI(FakeRegistryForConfig(), tmp_path,
                     lambda: state["active"],
                     lambda n: state.__setitem__("active", n)), state


def test_page_and_api_work_mounted_under_prefix(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    session, _store = make_session(tmp_path, [])
    config, _state = make_config(tmp_path)
    parent = FastAPI()
    parent.mount("/agent", create_app(session, config))
    client = TestClient(parent)

    page = client.get("/agent/")
    assert page.status_code == 200 and "ATLAS" in page.text
    # URL relativi: il montaggio sotto /agent non rompe le chiamate API
    assert "fetch('api/state')" in page.text
    assert "fetch('/api/state')" not in page.text

    assert client.get("/agent/api/state").status_code == 200
    info = client.get("/agent/api/config").json()
    assert info["active"] == "fake"
    assert info["providers"][0]["key_set"] is False


def test_config_set_provider_writes_key_without_echo(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_TEST_KEY", raising=False)
    session, _store = make_session(tmp_path, [])
    config, state = make_config(tmp_path)
    from fastapi.testclient import TestClient
    client = TestClient(create_app(session, config))

    resp = client.post("/api/config/provider",
                       json={"name": "fake", "api_key": "sk-live-abcdefgh"})
    body = resp.json()
    assert body == {"ok": True, "active": "fake"}
    assert "sk-live-abcdefgh" not in resp.text  # mai in risposta
    assert state["active"] == "fake"
    env_path = tmp_path / ".env"
    assert "ATLAS_TEST_KEY=sk-live-abcdefgh" in env_path.read_text()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert os.environ["ATLAS_TEST_KEY"] == "sk-live-abcdefgh"
    # info riporta solo key_set, mai il valore
    info = client.get("/api/config").json()
    assert info["providers"][0]["key_set"] is True
    assert "sk-live" not in client.get("/api/config").text
    # aggiornamento: una sola riga, valore nuovo
    client.post("/api/config/provider",
                json={"name": "fake", "api_key": "sk-live-nuova123"})
    lines = [line for line in env_path.read_text().splitlines()
             if line.startswith("ATLAS_TEST_KEY=")]
    assert lines == ["ATLAS_TEST_KEY=sk-live-nuova123"]


def test_config_rejects_unknown_and_unusable(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_TEST_KEY", raising=False)
    config, _state = make_config(tmp_path)
    assert config.set_provider("inesistente")["ok"] is False
    result = config.set_provider("fake")  # senza chiave: non usabile
    assert result["ok"] is False and "chiave mancante" in result["error"]


def test_agent_lock_contention_and_release(tmp_path):
    first = AgentLock(tmp_path)
    first.acquire()
    assert (tmp_path / "agent.lock").read_text() == str(os.getpid())
    with pytest.raises(AgentAlreadyRunning):
        AgentLock(tmp_path).acquire()
    first.release()
    assert not (tmp_path / "agent.lock").exists()
    second = AgentLock(tmp_path)  # dopo il rilascio si riprende
    second.acquire()
    second.release()


def test_agent_lock_recycles_stale_pid(tmp_path):
    dead_pid = 2 ** 22
    while True:  # trova un PID sicuramente morto
        try:
            os.kill(dead_pid, 0)
            dead_pid += 1
        except (ProcessLookupError, PermissionError):
            break
    (tmp_path / "agent.lock").write_text(str(dead_pid))
    lock = AgentLock(tmp_path)
    lock.acquire()  # lock stale riciclato senza errore
    assert (tmp_path / "agent.lock").read_text() == str(os.getpid())
    lock.release()


def test_provider_choice_persisted(tmp_path, monkeypatch):
    """Il provider scelto finisce in .env (ATLAS_PROVIDER) e sopravvive
    a un nuovo ConfigAPI (simula riavvio), senza mai esporre la chiave."""
    monkeypatch.delenv("ATLAS_TEST_KEY", raising=False)
    monkeypatch.delenv("ATLAS_PROVIDER", raising=False)
    config, state = make_config(tmp_path)
    config.set_provider("fake", "sk-live-abcdefgh")
    text = (tmp_path / ".env").read_text()
    assert "ATLAS_PROVIDER=fake" in text
    assert os.environ["ATLAS_PROVIDER"] == "fake"
    # riavvio: il .env e' la fonte
    monkeypatch.delenv("ATLAS_PROVIDER", raising=False)
    from app.providers.registry import load_dotenv
    load_dotenv(tmp_path / ".env")
    assert os.environ.get("ATLAS_PROVIDER") == "fake"
    assert os.environ.get("ATLAS_TEST_KEY") == "sk-live-abcdefgh"
