"""Stage 5 (spec sez. 6): astrazione canali, hub di sicurezza, Telegram.

Nessuna rete: registry finta a risposte programmate, client Telegram finto.
"""
from __future__ import annotations

import time

from app.agent.actionlog import ActionLog
from app.agent.commands import CommandContext, handle_command  # noqa: F401
from app.agent.memory import MemoryStore
from app.agent.safety import SecretFilter
from app.agent.tools import ToolContext
from app.channels.base import ChannelHub, ChannelLog, ChannelMessage, RateLimiter
from app.channels.config import channel_enabled, load_channels_config
from app.channels.telegram import TelegramChannel
from app.providers.base import ProviderResponse, ToolCall


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


def tool_response(name, args):
    return ProviderResponse(text="", tool_calls=[ToolCall(name=name, arguments=args)])


def make_hub(tmp_path, responses, allowed=None, rate=20):
    store = MemoryStore(tmp_path / "memory.db")
    secrets = SecretFilter(["sk-test-1234567890"])
    hub = ChannelHub(
        registry=FakeRegistry(responses), active_getter=lambda: "fake",
        ctx=ToolContext(workspace=tmp_path, secrets=secrets),
        store=store, log=ActionLog(tmp_path / "logs" / "actions.log", secrets),
        seal=None, secrets=secrets,
        channels_log=ChannelLog(tmp_path / "logs" / "channels.log", secrets),
        allowed=allowed or {"telegram": {"111"}},
        max_steps=5, rate_per_minute=rate, confirm_timeout=5.0)
    return hub, store


def collect():
    replies = []
    return replies, replies.append


def test_allowlist_ignores_unknown_silently(tmp_path):
    hub, _ = make_hub(tmp_path, [text_response("ciao")])
    replies, reply = collect()
    hub.handle(ChannelMessage("999", "telegram", "ciao"), reply)
    time.sleep(0.2)
    assert replies == [] and hub._registry.calls == []
    log = (tmp_path / "logs" / "channels.log").read_text()
    assert '"direction": "in"' in log and '"direction": "out"' not in log


def test_rate_limit_drops_silently(tmp_path):
    hub, _ = make_hub(tmp_path, [text_response("ok")] * 5, rate=3)
    replies, reply = collect()
    for _ in range(3):
        hub.handle(ChannelMessage("111", "telegram", "/memories"), reply)
    assert len(replies) == 3
    hub.handle(ChannelMessage("111", "telegram", "/memories"), reply)
    time.sleep(0.1)
    assert len(replies) == 3  # il quarto e' oltre il rate limit


def test_text_task_replies_and_stores_memory(tmp_path):
    hub, store = make_hub(tmp_path, [text_response("risposta del modello")])
    replies, reply = collect()
    hub.handle(ChannelMessage("111", "telegram", "come stai?"), reply)
    deadline = time.time() + 5
    while not replies and time.time() < deadline:
        time.sleep(0.02)
    assert replies == ["risposta del modello"]
    hist = store.history("111", "telegram")
    assert [m["content"] for m in hist] == ["come stai?", "risposta del modello"]


def test_memory_isolated_per_sender_and_channel(tmp_path):
    hub, store = make_hub(tmp_path, [text_response("a"), text_response("b")],
                          allowed={"telegram": {"111", "222"}})
    replies, reply = collect()
    hub.handle(ChannelMessage("111", "telegram", "messaggio di 111"), reply)
    hub.handle(ChannelMessage("222", "telegram", "messaggio di 222"), reply)
    deadline = time.time() + 5
    while len(replies) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert [m["content"] for m in store.history("111", "telegram")] == \
        ["messaggio di 111", "a"]
    assert [m["content"] for m in store.history("222", "telegram")] == \
        ["messaggio di 222", "b"]
    assert store.history("111", "cli") == []


def test_forget_deletes_notes_not_history(tmp_path):
    hub, store = make_hub(tmp_path, [text_response("ok")])
    replies, reply = collect()
    hub.handle(ChannelMessage("111", "telegram", "/remember il gatto e Briciola"), reply)
    hub.handle(ChannelMessage("111", "telegram", "domanda qualsiasi"), reply)
    deadline = time.time() + 5
    while len(replies) < 2 and time.time() < deadline:
        time.sleep(0.02)
    hub.handle(ChannelMessage("111", "telegram", "/forget Briciola"), reply)
    assert store.all_memories() == []
    assert any("cronologia resta" in r for r in replies)
    # la cronologia NON e' stata toccata
    assert any("domanda qualsiasi" in m["content"]
               for m in store.history("111", "telegram"))


def test_shell_requires_yes_in_chat_even_in_yolo(tmp_path):
    (tmp_path / "prova.txt").write_text("contenuto", encoding="utf-8")
    responses = [tool_response("shell", {"command": "cat prova.txt"}),
                 text_response("fatto"),
                 tool_response("shell", {"command": "cat prova.txt"}),
                 text_response("fatto2")]
    hub, _ = make_hub(tmp_path, responses)
    replies, reply = collect()
    hub.handle(ChannelMessage("111", "telegram", "leggi prova.txt"), reply)
    deadline = time.time() + 5
    while not any("yes" in r for r in replies) and time.time() < deadline:
        time.sleep(0.02)
    assert any('Rispondi "yes"' in r for r in replies)
    # risposta diversa da yes => rifiuto
    hub.handle(ChannelMessage("111", "telegram", "si"), reply)
    deadline = time.time() + 5
    while len(replies) < 2 and time.time() < deadline:
        time.sleep(0.02)
    # "fatto" e' la risposta testuale dopo il rifiuto; il file NON e' uscito
    assert replies[-1] == "fatto"
    assert all("contenuto" not in r for r in replies)

    # ora con yes
    hub.handle(ChannelMessage("111", "telegram", "leggi prova.txt"), reply)
    deadline = time.time() + 5
    while not any('Rispondi "yes"' in r for r in replies[2:]) and time.time() < deadline:
        time.sleep(0.02)
    hub.handle(ChannelMessage("111", "telegram", "yes"), reply)
    deadline = time.time() + 5
    while len(replies) < 4 and time.time() < deadline:
        time.sleep(0.02)
    assert replies[-1] == "fatto2"


def test_outbound_is_redacted(tmp_path):
    hub, _ = make_hub(tmp_path, [text_response("la chiave e' sk-test-1234567890, ok?")])
    replies, reply = collect()
    hub.handle(ChannelMessage("111", "telegram", "dimmi"), reply)
    deadline = time.time() + 5
    while not replies and time.time() < deadline:
        time.sleep(0.02)
    assert "sk-test-1234567890" not in replies[0]


def test_commands_via_channel(tmp_path):
    hub, _ = make_hub(tmp_path, [])
    replies, reply = collect()
    hub.handle(ChannelMessage("111", "telegram", "/remember nota canale"), reply)
    hub.handle(ChannelMessage("111", "telegram", "/memories"), reply)
    assert any("Ricordato (#1)" in r for r in replies)
    assert any("nota canale" in r for r in replies)


def test_channel_log_records_in_and_out(tmp_path):
    hub, _ = make_hub(tmp_path, [text_response("ciao")])
    replies, reply = collect()
    hub.handle(ChannelMessage("111", "telegram", "ehi"), reply)
    deadline = time.time() + 5
    while not replies and time.time() < deadline:
        time.sleep(0.02)
    log = (tmp_path / "logs" / "channels.log").read_text()
    assert '"direction": "in"' in log and '"direction": "out"' in log


# --- canale Telegram con client finto ---------------------------------------

class FakeTGClient:
    def __init__(self, token):
        self.token = token
        self.sent = []
        self._cb = None

    def start_polling(self, cb):
        self._cb = cb

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def inject(self, chat_id, text):
        self._cb(chat_id, text)


def test_telegram_channel_maps_updates_and_sends(tmp_path):
    made = []

    def factory(token):
        client = FakeTGClient(token)
        made.append(client)
        return client

    chan = TelegramChannel("token-finto", client_factory=factory)
    received = []
    chan.on_message(received.append)
    chan.start()
    made[0].inject("42", "ciao bot")
    assert received == [ChannelMessage("42", "telegram", "ciao bot")]
    chan.send("42", "risposta")
    assert made[0].sent == [("42", "risposta")]


def test_channels_config_loader(tmp_path):
    assert channel_enabled({}, "telegram") is False
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "channels.yaml").write_text(
        "channels:\n  telegram:\n    enabled: true\n", encoding="utf-8")
    cfg = load_channels_config(tmp_path / "config" / ".." / "x")
    assert channel_enabled(cfg, "telegram") is True
    assert channel_enabled(cfg, "whatsapp_official") is False


def test_rate_limiter_window():
    rl = RateLimiter(2)
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a")
    assert rl.allow("b")  # per-mittente


def make_hub_with_pairing(tmp_path, responses, allowed=None):
    from app.channels.pairing import PairingStore
    store = MemoryStore(tmp_path / "memory.db")
    secrets = SecretFilter(["sk-test-1234567890"])
    pairing = PairingStore(tmp_path / "data" / "pairing.json")
    hub = ChannelHub(
        registry=FakeRegistry(responses), active_getter=lambda: "fake",
        ctx=ToolContext(workspace=tmp_path, secrets=secrets),
        store=store, log=ActionLog(tmp_path / "logs" / "actions.log", secrets),
        seal=None, secrets=secrets,
        channels_log=ChannelLog(tmp_path / "logs" / "channels.log", secrets),
        allowed=allowed or {"telegram": {"111"}}, pairing=pairing,
        max_steps=5, confirm_timeout=5.0)
    return hub, pairing


def test_unknown_sender_recorded_for_pairing_no_reply(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [text_response("ciao")])
    replies, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram", text="ciao",
                              display="Mario Rossi (@mario)"), reply)
    assert replies == []  # mai una risposta a sconosciuti
    pending = pairing.pending()
    assert len(pending) == 1
    assert pending[0]["sender"] == "999"
    assert pending[0]["display"] == "Mario Rossi (@mario)"


def test_pairing_dedupes_and_allowlisted_not_recorded(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [text_response("ok")])
    _, reply = collect()
    for _ in range(3):
        hub.handle(ChannelMessage(sender="999", channel="telegram", text="x"), reply)
    hub.handle(ChannelMessage(sender="111", channel="telegram", text="x"), reply)
    assert [p["sender"] for p in pairing.pending()] == ["999"]


def test_pairing_approve_activates_allowlist_live(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [text_response("ciao")])
    replies, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram", text="ciao"), reply)
    assert replies == []
    entry = hub.approve_pairing("telegram", "999")
    assert entry is not None
    hub.handle(ChannelMessage(sender="999", channel="telegram", text="ciao"), reply)
    deadline = time.time() + 5
    while not replies and time.time() < deadline:
        time.sleep(0.02)
    assert replies != []  # ora autorizzato: il task gira e risponde
    assert "999" in pairing.paired_ids("telegram")
    assert pairing.pending() == []


def test_pairing_reject_drops_request(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [])
    _, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram", text="x"), reply)
    assert pairing.reject("telegram", "999") is True
    assert pairing.pending() == []
    assert "999" not in pairing.paired_ids("telegram")


def test_pairing_persists_across_reload(tmp_path):
    from app.channels.pairing import PairingStore
    hub, pairing = make_hub_with_pairing(tmp_path, [])
    _, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram", text="x"), reply)
    pairing.approve("telegram", "999")
    reloaded = PairingStore(tmp_path / "data" / "pairing.json")
    assert "999" in reloaded.paired_ids("telegram")


def test_pairing_pending_expires(tmp_path):
    from app.channels.pairing import PairingStore
    pairing = PairingStore(tmp_path / "data" / "pairing.json", ttl=0.01)
    pairing.record("telegram", "999")
    time.sleep(0.02)
    assert pairing.pending() == []


def test_challenge_pairs_sender_and_replies_once(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [text_response("ciao")])
    code = pairing.create_challenge()
    replies, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram", text=code), reply)
    assert replies == ["Pairing confermato: questo account ora e' autorizzato."]
    assert "999" in pairing.paired_ids("telegram")
    # dopo il pairing i messaggi girano normali
    hub.handle(ChannelMessage(sender="999", channel="telegram", text="ciao"), reply)
    deadline = time.time() + 5
    while len(replies) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert replies[-1] == "ciao"


def test_challenge_is_single_use(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [])
    code = pairing.create_challenge()
    replies, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram", text=code), reply)
    assert len(replies) == 1
    # lo stesso codice da un altro mittente non autorizza piu'
    hub.handle(ChannelMessage(sender="555", channel="telegram", text=code), reply)
    assert len(replies) == 1
    assert "555" not in pairing.paired_ids("telegram")


def test_wrong_challenge_stays_silent(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [])
    pairing.create_challenge()
    replies, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram",
                              text="ATLAS-SBAGLIATO"), reply)
    assert replies == []
    assert "999" not in pairing.paired_ids("telegram")
    assert [p["sender"] for p in pairing.pending()] == ["999"]


def test_challenge_expires(tmp_path):
    from app.channels.pairing import PairingStore
    pairing = PairingStore(tmp_path / "data" / "pairing.json", ttl=60)
    code = pairing.create_challenge(ttl=0.01)
    time.sleep(0.02)
    assert pairing.consume_challenge(code) is False


def test_unknown_sender_text_not_logged(tmp_path):
    hub, pairing = make_hub_with_pairing(tmp_path, [])
    _, reply = collect()
    hub.handle(ChannelMessage(sender="999", channel="telegram",
                              text="contenuto privato di uno sconosciuto"), reply)
    import json as _json
    lines = (tmp_path / "logs" / "channels.log").read_text().splitlines()
    assert len(lines) == 1
    entry = _json.loads(lines[0])
    assert "contenuto privato" not in entry["text"]
    assert "non autorizzato" in entry["text"]
    # nemmeno il pairing store trattiene il testo
    raw = (tmp_path / "data" / "pairing.json").read_text()
    assert "contenuto privato" not in raw
