"""Connettori Google reali (fase 4): OAuth, credenziali cifrate, calendario
in sola lettura, bozze Gmail (mai invii).

Tutte le chiamate HTTP verso Google sono sostituite con monkeypatch: NESSUN
account reale viene toccato. La validazione con un account vero richiede il
consenso di Fred (docs/CONNECTORS-GOOGLE.md)."""
import base64
import uuid

import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.models import ConnectorCredential

from .conftest import make_user

CLIENT_ID = "test-client-id.apps.googleusercontent.com"


@pytest.fixture()
def google_env(monkeypatch):
    monkeypatch.setenv("ATLAS_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ATLAS_GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _exchange_ok(code, client_id, client_secret, redirect_uri):
    assert client_id == CLIENT_ID
    return {"access_token": "ya29.access-TOKEN-PLAINTEXT",
            "refresh_token": "1//refresh-TOKEN-PLAINTEXT",
            "expires_in": 3600, "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/calendar.readonly"}


def _auth_url(client, h, scope_set="calendar"):
    r = client.get(f"/connectors/google/auth-url?scope_set={scope_set}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["auth_url"]


def _do_callback(client, h, monkeypatch, scope_set="calendar"):
    """Simula il giro OAuth: auth-url -> state -> callback con code finto."""
    url = _auth_url(client, h, scope_set)
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(url).query)["state"][0]
    monkeypatch.setattr("app.services.google_api.exchange_code", _exchange_ok)
    r = client.get(f"/connectors/google/callback?code=fake-code&state={state}")
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_url_requires_configuration(client, monkeypatch):
    """Senza client id configurato: 503 chiaro, niente URL inventato."""
    monkeypatch.setenv("ATLAS_GOOGLE_CLIENT_ID", "")
    get_settings.cache_clear()
    try:
        u = make_user(client, "g-no-cfg")
        r = client.get("/connectors/google/auth-url?scope_set=calendar",
                       headers=u["headers"])
        assert r.status_code == 503, r.text
        assert "CONNECTORS-GOOGLE" in r.json()["error"]["message"]
    finally:
        get_settings.cache_clear()


def test_auth_url_shape(client, google_env):
    u = make_user(client, "g-url")
    url = _auth_url(client, u["headers"], "calendar")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert f"client_id={CLIENT_ID}" in url
    assert "calendar.readonly" in url
    assert "access_type=offline" in url
    assert "state=" in url


def test_auth_url_requires_login(client, google_env):
    r = client.get("/connectors/google/auth-url?scope_set=calendar")
    assert r.status_code == 401


def test_auth_url_unknown_scope_set(client, google_env):
    u = make_user(client, "g-badscope")
    r = client.get("/connectors/google/auth-url?scope_set=drive",
                   headers=u["headers"])
    assert r.status_code == 400, r.text


def test_callback_stores_tokens_encrypted(client, db_session, google_env, monkeypatch):
    """Il callback autorizza il connettore e salva i token CIFRATI: il valore
    nel db non contiene mai il token in chiaro e si decifra con la chiave."""
    u = make_user(client, "g-cb")
    conn = _do_callback(client, u["headers"], monkeypatch)
    assert conn["kind"] == "google_calendar"
    assert conn["status"] == "authorized"
    cred = (db_session.query(ConnectorCredential)
            .filter(ConnectorCredential.connector_id == uuid.UUID(conn["id"]))
            .one())
    assert "TOKEN-PLAINTEXT" not in cred.access_token_enc
    assert "TOKEN-PLAINTEXT" not in cred.refresh_token_enc
    from app.services.credentials import decrypt_secret
    key = get_settings().credentials_key
    assert decrypt_secret(cred.access_token_enc, key) == "ya29.access-TOKEN-PLAINTEXT"
    assert decrypt_secret(cred.refresh_token_enc, key) == "1//refresh-TOKEN-PLAINTEXT"


def test_callback_rejects_bad_state(client, google_env, monkeypatch):
    monkeypatch.setattr("app.services.google_api.exchange_code", _exchange_ok)
    r = client.get("/connectors/google/callback?code=fake&state=firmato.da-nessuno")
    assert r.status_code == 400, r.text


def test_callback_rejects_expired_state(client, google_env, monkeypatch):
    make_user(client, "g-exp")
    import json as _json

    from app.services.google_connectors import _sign
    payload = base64.urlsafe_b64encode(_json.dumps(
        {"u": str(uuid.uuid4()), "s": "calendar", "e": 1}).encode()).decode()
    state = f"{payload}.{_sign(get_settings().jwt_secret, payload)}"
    monkeypatch.setattr("app.services.google_api.exchange_code", _exchange_ok)
    r = client.get(f"/connectors/google/callback?code=fake&state={state}")
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "state_expired"


def test_callback_fails_closed_without_key(client, google_env, monkeypatch):
    """Senza ATLAS_CREDENTIALS_KEY: niente salvataggio in chiaro, 503."""
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", "")
    get_settings.cache_clear()
    try:
        u = make_user(client, "g-nokey")
        r = client.get("/connectors/google/auth-url?scope_set=calendar",
                       headers=u["headers"])
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(r.json()["auth_url"]).query)["state"][0]
        monkeypatch.setattr("app.services.google_api.exchange_code", _exchange_ok)
        r = client.get(f"/connectors/google/callback?code=fake&state={state}")
        assert r.status_code == 503, r.text
    finally:
        get_settings.cache_clear()


def test_google_connector_rejects_plain_authorize(client, google_env):
    """Il consenso semplice non basta per i connettori Google: serve OAuth."""
    u = make_user(client, "g-noauthz")
    r = client.post("/connectors", json={"kind": "google_calendar"},
                    headers=u["headers"])
    assert r.status_code == 201, r.text
    conn = r.json()
    r = client.post(f"/connectors/{conn['id']}/authorize", headers=u["headers"])
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "oauth_required"


def test_calendar_read_only_action(client, db_session, google_env, monkeypatch):
    """Livello 0 sul calendario reale: mappa gli eventi Google nella forma
    ATLAS, sorgente marcata google_calendar, con refresh del token scaduto."""
    u = make_user(client, "g-cal")
    h = u["headers"]
    conn = _do_callback(client, h, monkeypatch)

    monkeypatch.setattr("app.services.google_api.refresh_access_token",
                        lambda rt, cid, cs: {"access_token": "ya29.fresco", "expires_in": 3600})

    def _list_ok(access_token, time_min_iso, max_results=20):
        return {"items": [
            {"id": "ev1", "summary": "Dentista",
             "start": {"dateTime": "2026-11-08T09:00:00+01:00"},
             "end": {"dateTime": "2026-11-08T09:30:00+01:00"}},
            {"id": "ev2", "summary": "Consegna progetto",
             "start": {"date": "2026-11-10"}, "end": {"date": "2026-11-11"}},
        ]}
    monkeypatch.setattr("app.services.google_api.calendar_list_events", _list_ok)

    # forza il percorso di refresh: scadenza nel passato
    cred = (db_session.query(ConnectorCredential)
            .filter(ConnectorCredential.connector_id == uuid.UUID(conn["id"])).one())
    from datetime import datetime, timezone
    cred.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    r = client.post("/actions",
                    json={"kind": "google_calendar.list_events",
                          "payload": {}, "idempotency_key": "cal-1"},
                    headers=h)
    assert r.status_code in (200, 201), r.text
    action = r.json()["action"] if "action" in r.json() else r.json()
    events = action["result"]["events"]
    assert [e["title"] for e in events] == ["Dentista", "Consegna progetto"]
    assert events[0]["source"] == "google_calendar"
    assert events[1]["starts_at"] == "2026-11-10"
    # il token rinnovato e stato ri-cifrato
    db_session.refresh(cred)
    assert "ya29.fresco" not in cred.access_token_enc


def test_gmail_draft_create_and_undo(client, google_env, monkeypatch):
    """Livello 1: bozza reale creata (con anteprima esatta) e annullata con
    eliminazione. Nessuna operazione di invio esiste."""
    u = make_user(client, "g-draft")
    h = u["headers"]
    _do_callback(client, h, monkeypatch, scope_set="gmail")

    created = {}

    def _create_ok(access_token, raw_b64):
        created["raw"] = base64.urlsafe_b64decode(raw_b64.encode()).decode()
        return {"id": "draft-123", "message": {"id": "msg-1"}}
    deleted = []
    monkeypatch.setattr("app.services.google_api.gmail_create_draft", _create_ok)
    monkeypatch.setattr("app.services.google_api.gmail_delete_draft",
                        lambda at, did: deleted.append(did))

    r = client.post("/actions",
                    json={"kind": "google_gmail.create_draft",
                          "payload": {"to": "prof@example.com",
                                      "subject": "Consegna",
                                      "body": "Buongiorno, in allegato."},
                          "idempotency_key": "draft-1"},
                    headers=h)
    assert r.status_code in (200, 201), r.text
    action = r.json()["action"] if "action" in r.json() else r.json()
    assert action["status"] == "executed"
    assert action["preview"]["draft"]["to"] == "prof@example.com"
    assert action["result"]["draft_id"] == "draft-123"
    assert "To: prof@example.com" in created["raw"]
    assert "Subject: Consegna" in created["raw"]

    r = client.post(f"/actions/{action['id']}/cancel", headers=h)
    assert r.status_code == 200, r.text
    assert deleted == ["draft-123"]


def test_google_actions_require_auth_and_authorized_connector(client, google_env):
    r = client.post("/actions",
                    json={"kind": "google_calendar.list_events",
                          "payload": {}, "idempotency_key": "x1"})
    assert r.status_code == 401
    u = make_user(client, "g-unauthz")
    r = client.post("/actions",
                    json={"kind": "google_calendar.list_events",
                          "payload": {}, "idempotency_key": "x2"},
                    headers=u["headers"])
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "connector_not_authorized"


def test_user_isolation_on_google_connector(client, google_env, monkeypatch):
    """Le credenziali di un utente non sono usabili da un altro."""
    a = make_user(client, "g-iso-a")
    _do_callback(client, a["headers"], monkeypatch)
    b = make_user(client, "g-iso-b")
    monkeypatch.setattr("app.services.google_api.calendar_list_events",
                        lambda at, tmin, max_results=20: {"items": []})
    r = client.post("/actions",
                    json={"kind": "google_calendar.list_events",
                          "payload": {}, "idempotency_key": "iso-1"},
                    headers=b["headers"])
    assert r.status_code == 403, r.text


def test_encryption_roundtrip_and_wrong_key():
    from app.errors import AtlasError
    from app.services.credentials import decrypt_secret, encrypt_secret
    key = Fernet.generate_key().decode()
    enc = encrypt_secret("segreto", key)
    assert "segreto" not in enc
    assert decrypt_secret(enc, key) == "segreto"
    with pytest.raises(AtlasError):
        decrypt_secret(enc, Fernet.generate_key().decode())
    with pytest.raises(AtlasError):
        encrypt_secret("segreto", "")


def test_gmail_list_messages_readonly(client, google_env, monkeypatch):
    """Livello 0: lettura inbox reale (gmail.readonly). Nessuna scrittura."""
    u = make_user(client, "g-read")
    h = u["headers"]
    _do_callback(client, h, monkeypatch, scope_set="gmail")

    def _list_ok(access_token, query="", max_results=10):
        assert query == "in:inbox"
        return {"messages": [
            {"id": "m1", "thread_id": "t1", "from": "prof@example.com",
             "subject": "Verifica", "date": "Mon, 9 Nov 2026",
             "snippet": "ricorda la consegna"},
        ]}
    monkeypatch.setattr("app.services.google_api.gmail_list_messages", _list_ok)

    r = client.post("/actions",
                    json={"kind": "google_gmail.list_messages",
                          "payload": {"query": "in:inbox", "max_results": 5},
                          "idempotency_key": "glist-1"},
                    headers=h)
    assert r.status_code in (200, 201), r.text
    action = r.json()["action"] if "action" in r.json() else r.json()
    assert action["status"] == "executed"
    msgs = action["result"]["messages"]
    assert msgs[0]["from"] == "prof@example.com"
    assert msgs[0]["source"] == "google_gmail"


def test_gmail_get_message_readonly(client, google_env, monkeypatch):
    """Corpo completo di un messaggio (gmail.readonly), sempre sola lettura."""
    u = make_user(client, "g-readmsg")
    h = u["headers"]
    _do_callback(client, h, monkeypatch, scope_set="gmail")

    def _get_ok(access_token, message_id):
        assert message_id == "m1"
        return {"id": "m1", "thread_id": "t1", "from": "prof@example.com",
                "to": "me@example.com", "subject": "Verifica",
                "date": "Mon, 9 Nov 2026", "body": "Testo del messaggio."}
    monkeypatch.setattr("app.services.google_api.gmail_get_message", _get_ok)

    r = client.post("/actions",
                    json={"kind": "google_gmail.get_message",
                          "payload": {"message_id": "m1"},
                          "idempotency_key": "gget-1"},
                    headers=h)
    assert r.status_code in (200, 201), r.text
    action = r.json()["action"] if "action" in r.json() else r.json()
    assert action["status"] == "executed"
    assert action["result"]["body"] == "Testo del messaggio."
    assert action["result"]["source"] == "google_gmail"

    # livello 0: niente annullamento, e' sola lettura
    r = client.post(f"/actions/{action['id']}/cancel", headers=h)
    assert r.status_code == 409


def test_gmail_scope_set_includes_readonly(client, google_env, monkeypatch):
    """Il consenso gmail copre bozze (compose) e lettura inbox (readonly)."""
    from app.services import google_connectors
    scopes = google_connectors.SCOPE_SETS["gmail"]
    assert "https://www.googleapis.com/auth/gmail.compose" in scopes
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes


def test_gmail_get_message_requires_message_id(client, google_env, monkeypatch):
    u = make_user(client, "g-noid")
    h = u["headers"]
    _do_callback(client, h, monkeypatch, scope_set="gmail")
    r = client.post("/actions",
                    json={"kind": "google_gmail.get_message",
                          "payload": {}, "idempotency_key": "gget-2"},
                    headers=h)
    assert r.status_code == 400
