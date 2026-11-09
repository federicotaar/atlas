"""Ponte dashboard -> agente (desktop): allowlist, pairing locale,
instradamento dei messaggi dei connettori al loop agente.

Nessuna rete: registry finta a risposte programmate; i trasporti sono
sostituiti dalle chiamate dirette a dispatch().
"""
from __future__ import annotations

import time

from app.agent.actionlog import ActionLog
from app.agent.memory import MemoryStore
from app.agent.safety import SecretFilter
from app.agent.tools import ToolContext
from app.channels.desktop_bridge import DesktopChannelBridge
from app.providers.base import ProviderResponse


class FakeRegistry:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat_with_fallback(self, active, messages, tools=None, stream=False,
                           on_fallback=None):
        self.calls.append({"messages": list(messages)})
        return self._responses.pop(0)


def make_bridge(tmp_path, responses, env=None):
    secrets = SecretFilter(["sk-test-1234567890"])
    store = MemoryStore(tmp_path / "memory.db")
    return DesktopChannelBridge(
        workspace=tmp_path, registry=FakeRegistry(responses),
        get_active=lambda: "fake", set_active=lambda n: None,
        describe_registry=lambda reg, act, out: None,
        ctx=ToolContext(workspace=tmp_path, secrets=secrets),
        store=store, log=ActionLog(tmp_path / "logs" / "actions.log", secrets),
        seal=None, secrets=secrets, max_steps=5, env=env or {})


def collect():
    replies = []
    return replies, replies.append


def test_unknown_sender_silenced_and_recorded(tmp_path):
    bridge = make_bridge(tmp_path, [ProviderResponse(text="ok")])
    replies, reply = collect()
    bridge.dispatch("telegram", "999", "marco", "ciao", reply)
    time.sleep(0.2)
    assert replies == []  # mai risposta a uno sconosciuto
    pending = bridge.pending()
    assert len(pending) == 1
    assert pending[0]["channel"] == "telegram"
    assert pending[0]["sender"] == "999"
    assert pending[0]["display"] == "marco"


def test_on_identity_allows_immediately_and_at_restart(tmp_path):
    bridge = make_bridge(tmp_path, [ProviderResponse(text="risposta agente")])
    bridge.on_identity("telegram", "111", "federico")
    replies, reply = collect()
    bridge.dispatch("telegram", "111", "federico", "ciao", reply)
    for _ in range(50):
        if replies:
            break
        time.sleep(0.05)
    assert replies == ["risposta agente"]
    # Persistenza: un bridge nuovo (riavvio app) ritrova l'allowlist.
    bridge2 = make_bridge(tmp_path, [ProviderResponse(text="ancora")])
    replies2, reply2 = collect()
    bridge2.dispatch("telegram", "111", "federico", "di nuovo", reply2)
    for _ in range(50):
        if replies2:
            break
        time.sleep(0.05)
    assert replies2 == ["ancora"]


def test_on_identity_whatsapp_normalizes_number(tmp_path):
    bridge = make_bridge(tmp_path, [ProviderResponse(text="ok")])
    bridge.on_identity("whatsapp", "+39 333 1234567", "")
    assert "393331234567" in bridge._allowed["whatsapp"]
    replies, reply = collect()
    bridge.dispatch("whatsapp", "393331234567", "", "ciao", reply)
    for _ in range(50):
        if replies:
            break
        time.sleep(0.05)
    assert replies == ["ok"]


def test_approve_pending_activates_sender(tmp_path):
    bridge = make_bridge(tmp_path, [ProviderResponse(text="benvenuto")])
    replies, reply = collect()
    bridge.dispatch("telegram", "555", "luca", "ciao", reply)
    time.sleep(0.2)
    assert replies == []
    entry = bridge.approve("telegram", "555")
    assert entry is not None and entry["sender"] == "555"
    assert bridge.pending() == []
    bridge.dispatch("telegram", "555", "luca", "ciao", reply)
    for _ in range(50):
        if replies:
            break
        time.sleep(0.05)
    assert replies == ["benvenuto"]


def test_approve_unknown_returns_none(tmp_path):
    bridge = make_bridge(tmp_path, [])
    assert bridge.approve("telegram", "404") is None


def test_reject_removes_request(tmp_path):
    bridge = make_bridge(tmp_path, [])
    replies, reply = collect()
    bridge.dispatch("whatsapp", "393330000000", "", "ciao", reply)
    time.sleep(0.2)
    assert len(bridge.pending()) == 1
    assert bridge.reject("whatsapp", "393330000000") is True
    assert bridge.pending() == []
    # Respinto NON e' in allowlist: se riscrive, resta in silenzio.
    bridge2 = make_bridge(tmp_path, [ProviderResponse(text="no")])
    bridge2.dispatch("whatsapp", "393330000000", "", "ancora", reply)
    time.sleep(0.2)
    assert replies == []


def test_env_allowlist_seed(tmp_path):
    bridge = make_bridge(tmp_path, [ProviderResponse(text="ok")],
                         env={"ALLOWED_TELEGRAM_IDS": "42, 43",
                              "ALLOWED_WHATSAPP_NUMBERS": "+39 340 0000000"})
    assert bridge._allowed["telegram"] == {"42", "43"}
    assert "393400000000" in bridge._allowed["whatsapp"]
