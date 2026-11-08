"""Connettore Telegram: connect, pairing owner, routing messaggi, disconnect.

La Bot API e' sostituita interamente al confine (_api): nessuna rete.
"""
import uuid

import pytest

from app.config import get_settings
from app.models import TelegramLink
from app.schemas import AskOut
from app.services import telegram as tg
from tests.conftest import make_user

BOT = {"id": 999, "is_bot": True, "username": "atlas_test_bot", "first_name": "ATLAS"}


@pytest.fixture(autouse=True)
def _tg_env(monkeypatch):
    monkeypatch.setattr(tg, "ensure_poller", lambda: None)
    from cryptography.fernet import Fernet
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def fake_tg(monkeypatch):
    calls: list[dict] = []

    def fake_api(token, method, **params):
        calls.append({"token": token, "method": method, "params": params})
        if method == "getMe":
            return BOT
        if method == "sendMessage":
            return {"message_id": len(calls)}
        if method == "getUpdates":
            return []
        raise AssertionError(f"metodo inatteso: {method}")

    monkeypatch.setattr(tg, "_api", fake_api)
    return calls


@pytest.fixture()
def fake_tg_bad(monkeypatch):
    def fake_api(token, method, **params):
        from app.errors import AtlasError
        raise AtlasError("telegram_api", "rifiutato", status=400)

    monkeypatch.setattr(tg, "_api", fake_api)


def _link(db_session, user):
    return db_session.query(TelegramLink).filter(TelegramLink.user_id == user.id).one()


def test_connect_ok(client, db_session, fake_tg):
    u = make_user(client, "tg")
    r = client.post("/connectors/telegram/connect",
                    json={"bot_token": "123456789:AAFakeTokenForTestsOnly12345"}, headers=u["headers"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["bot_username"] == "atlas_test_bot"
    assert body["paired"] is False
    assert body["deep_link"].startswith("https://t.me/atlas_test_bot?start=")

    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    link = _link(db_session, user)
    assert "123456789:AAFakeTokenForTestsOnly12345" not in link.bot_token_enc
    assert link.pairing_code
    assert link.pairing_code in body["deep_link"]


def test_connect_bad_token_400(client, fake_tg_bad):
    u = make_user(client, "tg")
    r = client.post("/connectors/telegram/connect",
                    json={"bot_token": "123456789:AAWrongTokenForTestsOnly1234"}, headers=u["headers"])
    assert r.status_code == 400


def test_status_not_connected(client, fake_tg):
    u = make_user(client, "tg")
    r = client.get("/connectors/telegram/status", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["connected"] is False


def _connect_direct(db_session, user) -> TelegramLink:
    return tg.connect(db_session, get_settings(), user, "123456789:AAFakeTokenForTestsOnly12345")


def test_pairing_binds_owner_and_consumes_code(db_session, db_user, fake_tg):
    link = _connect_direct(db_session, db_user)
    code = link.pairing_code
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 1, "message": {"text": f"/start {code}",
                                                  "chat": {"id": 555}}})
    assert link.owner_chat_id == 555
    assert link.pairing_code is None
    confirmations = [c for c in fake_tg if c["method"] == "sendMessage"]
    assert confirmations and confirmations[0]["params"]["chat_id"] == 555
    assert "collegato" in confirmations[0]["params"]["text"].lower()


def test_pairing_wrong_code_refused(db_session, db_user, fake_tg):
    link = _connect_direct(db_session, db_user)
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 1, "message": {"text": "/start codice-a-caso",
                                                  "chat": {"id": 555}}})
    assert link.owner_chat_id is None
    assert link.pairing_code is not None
    msg = [c for c in fake_tg if c["method"] == "sendMessage"][0]
    assert "non valido" in msg["params"]["text"].lower()


def test_owner_message_reaches_pipeline(db_session, db_user, fake_tg, monkeypatch):
    link = _connect_direct(db_session, db_user)
    link.owner_chat_id = 555
    link.pairing_code = None
    db_session.commit()

    seen = {}

    def fake_handle_ask(db, settings, user, text, conversation_id=None):
        seen["user_id"] = user.id
        seen["text"] = text
        return AskOut(classification="question", answer="Si chiama Briciola [M1].",
                      conversation_id=uuid.uuid4())

    monkeypatch.setattr(tg, "handle_ask", fake_handle_ask)
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 2, "message": {"text": "come si chiama il mio gatto?",
                                                  "chat": {"id": 555}}})
    assert seen["user_id"] == db_user.id
    assert seen["text"] == "come si chiama il mio gatto?"
    replies = [c for c in fake_tg if c["method"] == "sendMessage"]
    assert replies[0]["params"]["text"] == "Si chiama Briciola [M1]."
    assert replies[0]["params"]["chat_id"] == 555


def test_foreign_message_ignored(db_session, db_user, fake_tg, monkeypatch):
    link = _connect_direct(db_session, db_user)
    link.owner_chat_id = 555
    link.pairing_code = None
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("handle_ask non deve partire per estranei")

    monkeypatch.setattr(tg, "handle_ask", boom)
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 3, "message": {"text": "ciao", "chat": {"id": 999}}})
    assert not [c for c in fake_tg if c["method"] == "sendMessage"]


def test_unpaired_message_ignored(db_session, db_user, fake_tg, monkeypatch):
    link = _connect_direct(db_session, db_user)
    monkeypatch.setattr(tg, "handle_ask",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")))
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 4, "message": {"text": "ciao", "chat": {"id": 555}}})
    assert not [c for c in fake_tg if c["method"] == "sendMessage"]


def test_disconnect(client, db_session, fake_tg):
    u = make_user(client, "tg")
    client.post("/connectors/telegram/connect",
                json={"bot_token": "123456789:AAFakeTokenForTestsOnly12345"}, headers=u["headers"])
    r = client.post("/connectors/telegram/disconnect", headers=u["headers"])
    assert r.status_code == 204
    r = client.get("/connectors/telegram/status", headers=u["headers"])
    assert r.json()["connected"] is False


def test_pairing_expired_refused(db_session, db_user, fake_tg):
    from datetime import datetime, timedelta, timezone
    link = _connect_direct(db_session, db_user)
    link.pairing_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    code = link.pairing_code
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 5, "message": {"text": f"/start {code}",
                                                  "chat": {"id": 555}}})
    assert link.owner_chat_id is None
    assert link.pairing_code == code  # codice scaduto non consumato
    msg = [c for c in fake_tg if c["method"] == "sendMessage"][0]
    assert "scaduto" in msg["params"]["text"].lower()
    # deep link scaduto non viene piu' esposto
    assert tg.deep_link(link) is None
    assert tg.pairing_expired(link) is True


def test_expired_status_hides_deep_link(client, db_session, fake_tg):
    from datetime import datetime, timedelta, timezone
    u = make_user(client, "tg")
    client.post("/connectors/telegram/connect",
                json={"bot_token": "123456789:AAFakeTokenForTestsOnly12345"},
                headers=u["headers"])
    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    link = _link(db_session, user)
    link.pairing_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    r = client.get("/connectors/telegram/status", headers=u["headers"])
    body = r.json()
    assert body["pairing_expired"] is True
    assert body["deep_link"] is None


def test_rotate_pairing_code(client, db_session, fake_tg):
    u = make_user(client, "tg")
    r = client.post("/connectors/telegram/connect",
                    json={"bot_token": "123456789:AAFakeTokenForTestsOnly12345"},
                    headers=u["headers"])
    old_link = r.json()["deep_link"]
    r = client.post("/connectors/telegram/pairing-code", headers=u["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deep_link"] and body["deep_link"] != old_link
    assert body["pairing_expired"] is False
    # il vecchio codice non vale piu'
    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    link = _link(db_session, user)
    old_code = old_link.split("start=")[1]
    tg.handle_update(db_session, get_settings(), link,
                     {"update_id": 6, "message": {"text": f"/start {old_code}",
                                                  "chat": {"id": 555}}})
    assert link.owner_chat_id is None


def test_rotate_when_paired_409(client, db_session, fake_tg):
    u = make_user(client, "tg")
    client.post("/connectors/telegram/connect",
                json={"bot_token": "123456789:AAFakeTokenForTestsOnly12345"},
                headers=u["headers"])
    from app.models import User
    user = db_session.query(User).filter(User.email == u["email"]).one()
    link = _link(db_session, user)
    link.owner_chat_id = 555
    db_session.commit()
    r = client.post("/connectors/telegram/pairing-code", headers=u["headers"])
    assert r.status_code == 409
