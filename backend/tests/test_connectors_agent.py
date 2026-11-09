"""Connettori dashboard -> stack agente (desktop): i servizi di trasporto
Telegram/WhatsApp consegnano l'inbound al ponte dell'agente quando e'
registrato, e l'identita' del proprietario alimenta l'allowlist.

Nessuna rete: Bot API e bridge sostituiti al confine; ponte finto che
registra. Il comportamento legacy (senza ponte) e' coperto dai test
test_telegram.py / test_whatsapp.py esistenti.
"""

import pytest

from app.config import get_settings
from app.services import telegram as tg
from app.services import whatsapp as wa
from tests.conftest import make_user


class FakeBridge:
    def __init__(self):
        self.dispatched = []   # (channel, sender, display, text)
        self.identities = []   # (channel, sender, display)

    def dispatch(self, channel, sender, display, text, reply):
        self.dispatched.append((channel, sender, display, text))
        reply("risposta agente")

    def on_identity(self, channel, sender, display=""):
        self.identities.append((channel, sender, display))


@pytest.fixture()
def bridge():
    b = FakeBridge()
    tg.set_agent_bridge(b)
    wa.set_agent_bridge(b)
    yield b
    tg.set_agent_bridge(None)
    wa.set_agent_bridge(None)


# --- Telegram ------------------------------------------------------------

@pytest.fixture()
def fake_tg(monkeypatch):
    monkeypatch.setattr(tg, "ensure_poller", lambda: None)
    from cryptography.fernet import Fernet
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    calls = []

    def fake_api(token, method, **params):
        calls.append({"method": method, "params": params})
        if method == "getMe":
            return {"id": 999, "is_bot": True, "username": "atlas_test_bot"}
        if method == "sendMessage":
            return {"message_id": 1}
        raise AssertionError(f"metodo inatteso: {method}")

    monkeypatch.setattr(tg, "_api", fake_api)
    yield calls
    get_settings.cache_clear()


def _tg_link(db_session, user):
    return tg.connect(db_session, get_settings(), user,
                      "123456789:AAFakeTokenForTestsOnly12345")


def test_tg_owner_message_goes_to_agent(db_session, db_user, fake_tg, bridge,
                                        monkeypatch):
    link = _tg_link(db_session, db_user)
    link.owner_chat_id = 555
    link.pairing_code = None
    db_session.commit()
    monkeypatch.setattr(tg, "handle_ask",
                        lambda *a, **k: pytest.fail("pipeline classica chiamata"))
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 2, "message": {
                         "text": "ciao", "chat": {"id": 555},
                         "from": {"username": "fred"}}})
    assert bridge.dispatched == [("telegram", "555", "fred", "ciao")]
    sent = [c for c in fake_tg if c["method"] == "sendMessage"]
    assert sent[0]["params"]["text"] == "risposta agente"
    assert sent[0]["params"]["chat_id"] == 555


def test_tg_foreign_message_reaches_hub_not_pipeline(db_session, db_user,
                                                     fake_tg, bridge,
                                                     monkeypatch):
    """Col ponte attivo il gate NON e' piu' nel trasporto: l'hub decide
    (sconosciuto -> silenzio + richiesta di pairing)."""
    link = _tg_link(db_session, db_user)
    link.owner_chat_id = 555
    db_session.commit()
    monkeypatch.setattr(tg, "handle_ask",
                        lambda *a, **k: pytest.fail("pipeline classica chiamata"))
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 3, "message": {
                         "text": "ciao", "chat": {"id": 777},
                         "from": {"first_name": "Sconosciuto"}}})
    assert bridge.dispatched == [("telegram", "777", "Sconosciuto", "ciao")]


def test_tg_pairing_code_feeds_allowlist(db_session, db_user, fake_tg, bridge):
    link = _tg_link(db_session, db_user)
    code = link.pairing_code
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 1, "message": {
                         "text": f"/start {code}", "chat": {"id": 555},
                         "from": {"username": "fred"}}})
    assert bridge.identities == [("telegram", "555", "fred")]


def test_tg_status_reports_agent_mode(client, fake_tg, bridge):
    u = make_user(client, "tga")
    r = client.get("/connectors/telegram/status", headers=u["headers"])
    assert r.status_code == 200 and r.json()["agent"] is True


# --- WhatsApp ------------------------------------------------------------

@pytest.fixture()
def fake_wa(monkeypatch):
    monkeypatch.setattr(wa, "ensure_poller", lambda: None)
    monkeypatch.setattr(wa, "ensure_bridge", lambda: None)
    state = {"linked": False, "sent": [], "events": []}

    def fake_bridge(settings, method, path, **kwargs):
        if path == "/healthz":
            return {"ok": True, "linked": state["linked"],
                    "jid": "393340000000@s.whatsapp.net" if state["linked"] else None,
                    "pushname": "Fred" if state["linked"] else None}
        if path == "/pair":
            return {}
        if path == "/qr":
            return {"state": "paired"} if state["linked"] else {"state": "qr", "qr": "x"}
        if path == "/send":
            state["sent"].append(kwargs["json"])
            return {"sent": True, "id": f"r-{len(state['sent'])}"}
        if path.startswith("/events"):
            return {"events": list(state["events"])}
        raise AssertionError(f"path inatteso: {path}")

    monkeypatch.setattr(wa, "_bridge", fake_bridge)
    yield state
    from app.models import WhatsAppLink  # noqa: F401


def test_wa_dm_goes_to_agent_with_reply_to_sender(client, db_session, fake_wa,
                                                  bridge, monkeypatch):
    u = make_user(client, "waa")
    client.post("/connectors/whatsapp/connect", headers=u["headers"])
    fake_wa["linked"] = True
    client.get("/connectors/whatsapp/status", headers=u["headers"])
    # Lo stato collegato registra il proprietario (self-chat) in allowlist.
    assert ("whatsapp", "393340000000@s.whatsapp.net", "Fred") in bridge.identities

    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    link = db_session.query(wa.WhatsAppLink).filter(
        wa.WhatsAppLink.user_id == user.id).one()
    monkeypatch.setattr(wa, "handle_ask",
                        lambda *a, **k: pytest.fail("pipeline classica chiamata"))
    fake_wa["events"] = [{"seq": 3, "id": "m1", "text": "ciao atlas",
                          "ts": 1, "from": "393351112222"}]
    wa._poll_link(db_session, get_settings(), link)
    assert bridge.dispatched == [("whatsapp", "393351112222", "", "ciao atlas")]
    # La risposta torna al mittente del DM, non per forza in self-chat.
    assert fake_wa["sent"] == [{"text": "risposta agente", "to": "393351112222"}]


def test_wa_status_reports_agent_mode(client, fake_wa, bridge):
    u = make_user(client, "was")
    r = client.get("/connectors/whatsapp/status", headers=u["headers"])
    assert r.status_code == 200 and r.json()["agent"] is True


def test_wa_bridge_spawn_allow_dms_only_with_agent(db_session, bridge,
                                                   monkeypatch):
    cmds = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            cmds.append(cmd)

    healthy = {"n": 0}

    def fake_healthz(settings):
        healthy["n"] += 1
        return {"ok": True} if healthy["n"] > 2 else None

    monkeypatch.setattr(wa, "_healthz", fake_healthz)
    monkeypatch.setattr(wa.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(wa.time, "sleep", lambda s: None)
    monkeypatch.setenv("ATLAS_WA_BRIDGE_BIN", "/bin/true")
    get_settings.cache_clear()
    try:
        wa.ensure_bridge()
        assert "-allow-dms" in cmds[0]

        cmds.clear()
        healthy["n"] = 0
        wa._bridge_proc = None
        wa.set_agent_bridge(None)  # server mode: niente DM
        wa.ensure_bridge()
        assert "-allow-dms" not in cmds[0]
    finally:
        wa._bridge_proc = None
        get_settings.cache_clear()


# --- Router pairing --------------------------------------------------------

def test_pairing_router_501_senza_stack(client):
    tg.set_agent_bridge(None)
    u = make_user(client, "pair0")
    r = client.get("/connectors/pairing", headers=u["headers"])
    assert r.status_code == 501


def test_pairing_router_pending_approve_reject(client, bridge):
    class PairBridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self._pending = [{"channel": "telegram", "sender": "42",
                              "display": "marco", "ts": 1.0}]

        def pending(self):
            return list(self._pending)

        def approve(self, channel, sender):
            self._pending = [p for p in self._pending if p["sender"] != sender]
            return {"channel": channel, "sender": sender}

        def reject(self, channel, sender):
            before = len(self._pending)
            self._pending = [p for p in self._pending if p["sender"] != sender]
            return len(self._pending) != before

    tg.set_agent_bridge(PairBridge())
    u = make_user(client, "pair1")
    r = client.get("/connectors/pairing", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()[0]["sender"] == "42"

    r = client.post("/connectors/pairing/approve", headers=u["headers"],
                    json={"channel": "telegram", "sender": "42"})
    assert r.status_code == 200 and r.json()["approved"] is True
    assert client.get("/connectors/pairing", headers=u["headers"]).json() == []

    # 404 su richiesta inesistente
    r = client.post("/connectors/pairing/reject", headers=u["headers"],
                    json={"channel": "telegram", "sender": "42"})
    assert r.status_code == 404


# --- Dashboard: job dell'agente nel pannello Automazioni -------------------

def test_dashboard_shows_agent_jobs(client):
    from app.services import dashboard as dash
    u = make_user(client, "dashj")
    dash.set_agent_jobs_provider(lambda: [{
        "id": 7, "kind": "once", "schedule": "2026-11-10 09:00",
        "task": "ricordami il dentista", "channel": "webui",
        "approved": True, "paused": False,
        "next_fire": "2026-11-10T08:00:00+00:00", "last_run": None}])
    try:
        r = client.get("/dashboard", headers=u["headers"])
        assert r.status_code == 200
        jobs = r.json()["panels"]["automations"]["agent_jobs"]
        assert len(jobs) == 1 and jobs[0]["task"] == "ricordami il dentista"
        assert jobs[0]["derived_from"]["agent_job_id"] == "7"
    finally:
        dash.set_agent_jobs_provider(None)


def test_dashboard_agent_jobs_provider_failure_is_empty(client):
    from app.services import dashboard as dash
    u = make_user(client, "dashf")
    def boom():
        raise RuntimeError("stack giu'")
    dash.set_agent_jobs_provider(boom)
    try:
        r = client.get("/dashboard", headers=u["headers"])
        assert r.status_code == 200
        assert r.json()["panels"]["automations"]["agent_jobs"] == []
    finally:
        dash.set_agent_jobs_provider(None)
