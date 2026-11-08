"""Stage 7 (spec sez. 6, build order 7): canale WhatsApp unofficial via
sidecar wa-bridge. Nessuna rete e nessun bridge: http iniettabile, client
finti, registry finta a risposte programmate.
"""
from __future__ import annotations

import time

from app.agent.actionlog import ActionLog
from app.agent.memory import MemoryStore
from app.agent.safety import SecretFilter
from app.agent.tools import ToolContext
from app.channels.base import ChannelHub, ChannelLog, ChannelMessage
from app.channels.whatsapp import WhatsAppChannel, _BridgeClient, normalize_number
from app.providers.base import ProviderResponse


def test_normalize_number_strips_plus_and_spaces():
    assert normalize_number("+39 333-123 4567") == "393331234567"
    assert normalize_number("393331234567") == "393331234567"
    assert normalize_number("") == ""


class FakeWAClient:
    """Stessa interfaccia di _BridgeClient, senza rete."""

    def __init__(self, addr, token, interval):
        self.sent = []
        self.on_update = None

    def start_polling(self, on_update):
        self.on_update = on_update

    def send_message(self, to, text):
        self.sent.append((to, text))


def test_channel_maps_events_and_sends():
    channel = WhatsAppChannel("127.0.0.1:1", "tok", client_factory=FakeWAClient)
    messages = []
    channel.on_message(messages.append)
    channel.start()
    channel._client.on_update("+39 333 1234567", "ciao")
    assert len(messages) == 1
    assert messages[0].channel == "whatsapp"
    assert messages[0].sender == "393331234567"
    assert messages[0].text == "ciao"
    channel.send("393331234567", "risposta")
    assert channel._client.sent == [("393331234567", "risposta")]


def test_channel_requires_token():
    try:
        WhatsAppChannel("127.0.0.1:1", "")
    except ValueError:
        return
    raise AssertionError("token vuoto accettato")


# --- _BridgeClient: parsing del protocollo HTTP del sidecar ------------------

def test_bridge_client_delivers_and_skips_from_me():
    def fake_get(path):
        assert path.startswith("/events?after=")
        return {"boot": "b1", "events": [
            {"seq": 1, "from": "393331234567", "text": "ciao", "from_me": False},
            {"seq": 2, "from": "393331234567", "text": "eco", "from_me": True},
            {"seq": 3, "from": "393331234567", "text": "  ", "from_me": False},
            {"seq": 4, "from": "", "text": "senza mittente", "from_me": False},
        ]}

    client = _BridgeClient("x", "tok", get=fake_get,
                           sleep=lambda _: client.stop())
    got = []
    client._poll_loop(lambda sender, text: got.append((sender, text)))
    assert got == [("393331234567", "ciao")]


def test_bridge_client_resets_offset_on_new_boot():
    pages = iter([
        {"boot": "b1", "events": [{"seq": 5, "from": "39", "text": "uno"}]},
        {"boot": "b2", "events": [{"seq": 1, "from": "39", "text": "due"}]},
    ])
    state = {"n": 0}

    def fake_sleep(_):
        state["n"] += 1
        if state["n"] >= 2:
            client.stop()

    client = _BridgeClient("x", "tok", get=lambda path: next(pages),
                           sleep=fake_sleep)
    got = []
    client._poll_loop(lambda sender, text: got.append(text))
    assert got == ["uno", "due"]


def test_bridge_client_survives_bridge_down():
    def fake_get(path):
        raise ConnectionError("sidecar giu'")

    client = _BridgeClient("x", "tok", get=fake_get,
                           sleep=lambda _: client.stop())
    got = []
    client._poll_loop(lambda sender, text: got.append(text))  # nessuna eccezione
    assert got == []


def test_bridge_client_send_posts_to_bridge():
    posts = []
    client = _BridgeClient(
        "x", "tok", post=lambda path, payload: posts.append((path, payload)) or {})
    client.send_message("+39 333 1234567", "ciao")
    assert posts == [("/send", {"text": "ciao", "to": "393331234567"})]


def test_bridge_client_pair_endpoints():
    posts = []
    gets = []

    def fake_post(path, payload):
        posts.append((path, payload))
        return {"code": "ABCD-EFGH"} if path == "/pair-code" else {"state": "qr"}

    client = _BridgeClient("x", "tok", post=fake_post,
                           get=lambda path: gets.append(path) or {"state": "qr"})
    assert client.pair() == {"state": "qr"}
    assert client.pair_code("+39 333 1234567") == {"code": "ABCD-EFGH"}
    assert client.qr_state() == {"state": "qr"}
    assert posts == [("/pair", {}), ("/pair-code", {"phone": "393331234567"})]
    assert gets == ["/qr"]


# --- hub: stessa sicurezza CENTRALE degli altri canali -----------------------

class FakeRegistry:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat_with_fallback(self, active, messages, tools=None, stream=False,
                           on_fallback=None):
        self.calls.append({"messages": list(messages)})
        return self._responses.pop(0)


def text_response(text):
    return ProviderResponse(text=text)


def make_wa_hub(tmp_path, responses, allowed=None):
    store = MemoryStore(tmp_path / "memory.db")
    secrets = SecretFilter(["sk-test-1234567890"])
    hub = ChannelHub(
        registry=FakeRegistry(responses), active_getter=lambda: "fake",
        ctx=ToolContext(workspace=tmp_path, secrets=secrets),
        store=store, log=ActionLog(tmp_path / "logs" / "actions.log", secrets),
        seal=None, secrets=secrets,
        channels_log=ChannelLog(tmp_path / "logs" / "channels.log", secrets),
        allowed=allowed or {"whatsapp": {"393331234567"}},
        max_steps=5, confirm_timeout=5.0)
    return hub, store


def test_hub_replies_on_whatsapp_to_allowed_number(tmp_path):
    hub, store = make_wa_hub(tmp_path, [text_response("risposta wa")])
    replies = []
    hub.handle(ChannelMessage("393331234567", "whatsapp", "come stai?"),
               replies.append)
    deadline = time.time() + 5
    while not replies and time.time() < deadline:
        time.sleep(0.02)
    assert replies == ["risposta wa"]
    hist = store.history("393331234567", "whatsapp")
    assert [m["content"] for m in hist] == ["come stai?", "risposta wa"]


def test_hub_silences_unknown_whatsapp_number(tmp_path):
    hub, _ = make_wa_hub(tmp_path, [text_response("mai")])
    replies = []
    hub.handle(ChannelMessage("390000000000", "whatsapp", "ciao"),
               replies.append)
    time.sleep(0.2)
    assert replies == [] and hub._registry.calls == []
    log = (tmp_path / "logs" / "channels.log").read_text()
    assert '"direction": "in"' in log and '"direction": "out"' not in log
    assert "ciao" not in log  # testo dello sconosciuto mai registrato


def test_hub_confirmation_flow_works_on_whatsapp(tmp_path):
    """Le conferme in-chat (spec sez. 6) valgono anche sul canale whatsapp:
    una domanda in sospeso cattura il prossimo messaggio del mittente."""
    from app.providers.base import ToolCall

    def tool_response(name, args):
        return ProviderResponse(
            text="", tool_calls=[ToolCall(name=name, arguments=args)])

    hub, _ = make_wa_hub(tmp_path, [
        tool_response("write_file", {"path": "x.txt", "content": "hi"}),
        text_response("fatto"),
    ])
    replies = []
    hub.handle(ChannelMessage("393331234567", "whatsapp", "scrivi un file"),
               replies.append)
    deadline = time.time() + 5
    while not replies and time.time() < deadline:
        time.sleep(0.02)
    assert replies and "yes" in replies[0].lower()  # domanda di conferma
    hub.handle(ChannelMessage("393331234567", "whatsapp", "yes"),
               replies.append)
    deadline = time.time() + 5
    while len(replies) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert replies[-1] == "fatto"
