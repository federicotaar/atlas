"""Connettore WhatsApp: connect/QR, stato link, routing self-chat, disconnect.

Il bridge Go e' sostituito interamente al confine (_bridge): nessuna rete,
nessun processo, nessun contatto con WhatsApp. ensure_bridge e' neutralizzato:
il ciclo di vita del binario non parte nei test.
"""
import pytest

from app.config import get_settings
from app.models import WhatsAppLink
from app.schemas import AskOut
from app.services import whatsapp as wa
from tests.conftest import make_user


@pytest.fixture(autouse=True)
def _wa_env(monkeypatch):
    monkeypatch.setattr(wa, "ensure_poller", lambda: None)
    monkeypatch.setattr(wa, "ensure_bridge", lambda: None)
    yield


@pytest.fixture(autouse=True)
def _clean_wa_links(db_session):
    """Un solo collegamento attivo e' ammesso: ogni test parta pulito."""
    yield
    db_session.query(WhatsAppLink).delete()
    db_session.commit()


@pytest.fixture()
def fake_wa(monkeypatch):
    """Bridge finto: stati qr -> paired, eventi e send registrati."""
    state = {"linked": False, "sent": [], "events": [], "disconnected": False}

    def fake_bridge(settings, method, path, **kwargs):
        if path == "/healthz":
            return {"ok": True, "linked": state["linked"],
                    "jid": "393340000000@s.whatsapp.net" if state["linked"] else None,
                    "pushname": "Fred" if state["linked"] else None}
        if path == "/pair":
            return {}
        if path == "/qr":
            return {"state": "paired"} if state["linked"] else {
                "state": "qr", "qr": "2@fake-qr-string-per-test"}
        if path == "/send":
            state["sent"].append(kwargs["json"]["text"])
            state["seq_sent"] = state.get("seq_sent", 0) + 1
            return {"sent": True, "id": f"reply-{state['seq_sent']}"}
        if path.startswith("/events"):
            after = 0
            if "after=" in path:
                after = int(path.split("after=")[1].split("&")[0])
            return {"events": [e for e in state["events"] if e["seq"] > after]}
        if path == "/disconnect":
            state["disconnected"] = True
            return {}
        raise AssertionError(f"path inatteso: {path}")

    monkeypatch.setattr(wa, "_bridge", fake_bridge)
    return state


def _link(db_session, user):
    return db_session.query(WhatsAppLink).filter(WhatsAppLink.user_id == user.id).one()


def test_status_not_connected(client, fake_wa):
    u = make_user(client, "wa0")
    r = client.get("/connectors/whatsapp/status", headers=u["headers"])
    assert r.status_code == 200 and r.json()["connected"] is False


def test_connect_and_qr_then_paired(client, db_session, fake_wa):
    u = make_user(client, "wa1")
    r = client.post("/connectors/whatsapp/connect", headers=u["headers"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["connected"] is True and body["linked"] is False
    assert body["qr_state"] == "qr" and body["qr"] == "2@fake-qr-string-per-test"

    # L'utente inquadra il QR: il bridge risulta collegato.
    fake_wa["linked"] = True
    r = client.get("/connectors/whatsapp/status", headers=u["headers"])
    body = r.json()
    assert body["linked"] is True and body["jid"] == "393340000000@s.whatsapp.net"
    assert body["pushname"] == "Fred" and body["qr_state"] == "paired"
    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    assert _link(db_session, user).jid == "393340000000@s.whatsapp.net"


def test_inbound_selfchat_answered(client, db_session, fake_wa, monkeypatch):
    u = make_user(client, "wa2")
    client.post("/connectors/whatsapp/connect", headers=u["headers"])
    fake_wa["linked"] = True
    client.get("/connectors/whatsapp/status", headers=u["headers"])

    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    link = _link(db_session, user)
    fake_wa["events"] = [{"seq": 7, "text": "come si chiama il mio gatto?", "ts": 1}]

    called = {}

    def fake_ask(db, settings, user_, text):
        called["text"] = text
        return AskOut(classification="question", answer="Il tuo gatto si chiama Briciola.",
                      memories_used=[], citations=[])

    monkeypatch.setattr(wa, "handle_ask", fake_ask)
    from app.config import get_settings as gs
    wa._poll_link(db_session, gs(), link)
    assert called["text"] == "come si chiama il mio gatto?"
    assert fake_wa["sent"] == ["Il tuo gatto si chiama Briciola."]
    assert link.last_seq == 7


def test_disconnect(client, db_session, fake_wa):
    u = make_user(client, "wa3")
    client.post("/connectors/whatsapp/connect", headers=u["headers"])
    r = client.post("/connectors/whatsapp/disconnect", headers=u["headers"])
    assert r.status_code == 204
    assert fake_wa["disconnected"] is True
    r = client.get("/connectors/whatsapp/status", headers=u["headers"])
    assert r.json()["connected"] is False


def test_bridge_disabled_refuses(client, monkeypatch):
    """Con il connettore disabilitato il connect risponde 503 chiaro, mai
    un tentativo silenzioso di avvio."""
    import importlib
    wa_mod = importlib.reload(wa)  # sblocca ensure_bridge monkeypatched
    monkeypatch.setenv("ATLAS_WA_BRIDGE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        monkeypatch.setattr(wa_mod, "ensure_poller", lambda: None)
        u = make_user(client, "wa4")
        r = client.post("/connectors/whatsapp/connect", headers=u["headers"])
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "wa_disabled"
    finally:
        get_settings.cache_clear()
        importlib.reload(wa_mod)


def _mklink(db_session, email, client, prefix):
    u = make_user(client, prefix)
    client.post("/connectors/whatsapp/connect", headers=u["headers"])
    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    return u, user, _link(db_session, user)


def test_echo_guard_precise(client, db_session, fake_wa, monkeypatch):
    """Anti-eco MIRATO: in self-chat i messaggi del proprietario dal telefono
    hanno from_me=true e DEVONO essere elaborati (sono l'input del canale);
    solo le risposte inviate da ATLAS stesso (ID noto dal /send) vengono
    saltate, altrimenti il connettore non risponderebbe mai."""
    u, user, link = _mklink(db_session, "wa5@x", client, "wa5")
    seen = []
    monkeypatch.setattr(wa, "handle_ask", lambda db, s, u_, t: (
        seen.append(t), AskOut(classification="question", answer="risposta",
                               memories_used=[], citations=[]))[1])
    from app.config import get_settings as gs

    # Input reale del proprietario dal telefono: from_me=true, ID sconosciuto.
    fake_wa["events"] = [{"seq": 1, "id": "phone-1", "text": "ciao dal telefono",
                          "ts": 1, "from_me": True}]
    wa._poll_link(db_session, gs(), link)
    assert seen == ["ciao dal telefono"]
    assert fake_wa["sent"] == ["risposta"]
    sent_id = "reply-1"  # id assegnato dal bridge al nostro invio

    # La nostra stessa risposta riemersa come evento: saltata, niente loop.
    fake_wa["events"] = [
        {"seq": 2, "id": sent_id, "text": "risposta", "ts": 2, "from_me": True},
        {"seq": 3, "id": "phone-2", "text": "altra domanda", "ts": 3, "from_me": True},
    ]
    wa._poll_link(db_session, gs(), link)
    assert seen == ["ciao dal telefono", "altra domanda"]
    assert fake_wa["sent"] == ["risposta", "risposta"]  # solo la domanda nuova


def test_replay_deduplicated(client, db_session, fake_wa, monkeypatch):
    """Replay dopo reconnect: lo stesso seq non viene elaborato due volte."""
    u, user, link = _mklink(db_session, "wa6@x", client, "wa6")
    fake_wa["events"] = [{"seq": 1, "id": "m1", "text": "prima", "ts": 1}]
    seen = []
    monkeypatch.setattr(wa, "handle_ask", lambda db, s, u_, t: (
        seen.append(t), AskOut(classification="question", answer="ok",
                               memories_used=[], citations=[]))[1])
    from app.config import get_settings as gs
    wa._poll_link(db_session, gs(), link)
    # replay: il bridge restituisce di nuovo il vecchio evento piu' uno nuovo
    fake_wa["events"] = [
        {"seq": 1, "id": "m1", "text": "prima", "ts": 1},
        {"seq": 2, "id": "m2", "text": "seconda", "ts": 2},
    ]
    wa._poll_link(db_session, gs(), link)
    assert seen == ["prima", "seconda"]


def test_boot_restart_resets_offset(client, db_session, fake_wa, monkeypatch):
    """Bridge riavviato: boot diverso -> offset azzerato e i nuovi seq bassi
    vengono elaborati (mai persi per un last_seq alto)."""
    u, user, link = _mklink(db_session, "wa7@x", client, "wa7")
    link.last_seq = 9
    link.boot_id = "boot-vecchio"
    db_session.commit()
    calls = {"n": 0}
    orig_bridge = wa._bridge

    def bridging(settings, method, path, **kwargs):
        out = orig_bridge(settings, method, path, **kwargs)
        if path.startswith("/events"):
            calls["n"] += 1
            out = dict(out)
            out["boot"] = "boot-nuovo"
        return out

    monkeypatch.setattr(wa, "_bridge", bridging)
    fake_wa["events"] = [{"seq": 1, "id": "n1", "text": "dopo riavvio", "ts": 1}]
    seen = []
    monkeypatch.setattr(wa, "handle_ask", lambda db, s, u_, t: (
        seen.append(t), AskOut(classification="question", answer="ok",
                               memories_used=[], citations=[]))[1])
    from app.config import get_settings as gs
    wa._poll_link(db_session, gs(), link)
    assert seen == ["dopo riavvio"]
    assert link.boot_id == "boot-nuovo" and link.last_seq == 1
    assert calls["n"] == 2  # prima lettura con offset vecchio, poi after=0


def test_endpoints_require_auth(client):
    for method, path in [("GET", "/connectors/whatsapp/status"),
                         ("POST", "/connectors/whatsapp/connect"),
                         ("POST", "/connectors/whatsapp/disconnect")]:
        r = client.request(method, path)
        assert r.status_code == 401, (method, path, r.status_code)


def test_other_profile_isolated_and_switch(client, db_session, fake_wa):
    """Un solo profilo alla volta: B non vede il collegamento di A, non puo'
    collegarsi finche' A e' attivo; dopo lo scollegamento di A, B puo'."""
    a = make_user(client, "waA")
    b = make_user(client, "waB")
    client.post("/connectors/whatsapp/connect", headers=a["headers"])

    r = client.get("/connectors/whatsapp/status", headers=b["headers"])
    assert r.json()["connected"] is False  # B non vede nulla di A

    r = client.post("/connectors/whatsapp/connect", headers=b["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "wa_other_profile"

    r = client.post("/connectors/whatsapp/disconnect", headers=a["headers"])
    assert r.status_code == 204
    r = client.post("/connectors/whatsapp/connect", headers=b["headers"])
    assert r.status_code == 201, r.text


def test_same_inbound_id_across_restart_no_second_reply(client, db_session, fake_wa, monkeypatch):
    """Replay dopo riavvio del bridge: stesso ID inbound con boot diverso e
    seq basso -> NESSUNA seconda risposta; un ID nuovo risponde."""
    u, user, link = _mklink(db_session, "wa8@x", client, "wa8")
    seen = []
    monkeypatch.setattr(wa, "handle_ask", lambda db, s, u_, t: (
        seen.append(t), AskOut(classification="question", answer="ok",
                               memories_used=[], citations=[]))[1])
    from app.config import get_settings as gs

    calls = {"boot": "boot-1"}
    orig_bridge = wa._bridge

    def bridging(settings, method, path, **kwargs):
        out = orig_bridge(settings, method, path, **kwargs)
        if path.startswith("/events"):
            out = dict(out)
            out["boot"] = calls["boot"]
        return out

    monkeypatch.setattr(wa, "_bridge", bridging)

    fake_wa["events"] = [{"seq": 5, "id": "msg-1", "text": "ricorda questo", "ts": 1}]
    wa._poll_link(db_session, gs(), link)
    assert seen == ["ricorda questo"]
    assert len(fake_wa["sent"]) == 1

    # Riavvio del bridge: boot diverso, seq ripartiti, replay del vecchio ID
    # insieme a un messaggio genuinamente nuovo.
    calls["boot"] = "boot-2"
    fake_wa["events"] = [
        {"seq": 1, "id": "msg-1", "text": "ricorda questo", "ts": 1},
        {"seq": 2, "id": "msg-2", "text": "domanda nuova", "ts": 2},
    ]
    wa._poll_link(db_session, gs(), link)
    assert seen == ["ricorda questo", "domanda nuova"]
    assert len(fake_wa["sent"]) == 2  # una risposta sola per msg-1, mai due


def test_status_bridge_available(tmp_path, monkeypatch, client):
    """La disponibilita' del binario bridge risolto e' osservabile via API:
    False con path mancante, True con eseguibile presente. Solo filesystem."""
    from app.config import get_settings

    monkeypatch.setenv("ATLAS_WA_BRIDGE_BIN", str(tmp_path / "non-esiste"))
    get_settings.cache_clear()
    u = make_user(client, "wa-bin")
    r = client.get("/connectors/whatsapp/status", headers=u["headers"])
    assert r.status_code == 200 and r.json()["bridge_available"] is False

    exe = tmp_path / "wa-bridge-x86_64-unknown-linux-gnu"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("ATLAS_WA_BRIDGE_BIN", str(exe))
    get_settings.cache_clear()
    r = client.get("/connectors/whatsapp/status", headers=u["headers"])
    assert r.json()["bridge_available"] is True
    get_settings.cache_clear()
