"""Test fase 4: Action Engine (spec 5F).

Prova che nessuna azione non autorizzata viene eseguita: permesso per
connettore, anteprima esatta, conferma esplicita per il livello 2 (una bozza
non e un invio), esito registrato, annullamento, chiave di idempotenza."""
from datetime import datetime, timedelta, timezone

from .conftest import make_source, make_user


def _connector(client, h, kind="calendar_mock", authorize=True):
    r = client.post("/connectors", json={"kind": kind}, headers=h)
    assert r.status_code == 201, r.text
    conn = r.json()
    if authorize:
        r = client.post(f"/connectors/{conn['id']}/authorize", headers=h)
        assert r.status_code == 200, r.text
        conn = r.json()
    return conn


def test_level0_requires_authorized_connector(client):
    """Livello 0: permesso una volta per connettore; senza, niente esecuzione."""
    u = make_user(client, "act-l0-noauth")
    h = u["headers"]
    _connector(client, h, authorize=False)  # pending, mai autorizzato
    r = client.post("/actions", json={
        "kind": "calendar.list_events", "payload": {}, "idempotency_key": "k1",
    }, headers=h)
    assert r.status_code == 403
    # nessuna azione registrata/eseguita
    assert client.get("/actions", headers=h).json() == []


def test_level0_reads_fixture_events(client):
    u = make_user(client, "act-l0")
    h = u["headers"]
    _connector(client, h)
    r = client.post("/actions", json={
        "kind": "calendar.list_events", "payload": {}, "idempotency_key": "k1",
    }, headers=h)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["level"] == 0 and a["status"] == "executed"
    assert len(a["result"]["events"]) == 3  # fixture sintetica
    assert all(e["source"] == "fixture" for e in a["result"]["events"])


def test_level1_executes_and_is_cancellable(client):
    """Livello 1: esegue e riporta; annullabile."""
    u = make_user(client, "act-l1")
    h = u["headers"]
    _connector(client, h)
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    r = client.post("/actions", json={
        "kind": "calendar.create_event",
        "payload": {"title": "Ripasso esame",
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=2)).isoformat()},
        "idempotency_key": "ev1",
    }, headers=h)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["level"] == 1 and a["status"] == "executed"
    assert a["preview"]["event"]["title"] == "Ripasso esame"  # anteprima esatta
    event_id = a["result"]["event_id"]

    # l'evento compare nelle letture successive
    r = client.post("/actions", json={
        "kind": "calendar.list_events", "payload": {}, "idempotency_key": "k2",
    }, headers=h)
    assert any(e["id"] == event_id for e in r.json()["result"]["events"])

    # annulla: l'evento sparisce dalle letture
    r = client.post(f"/actions/{a['id']}/cancel", headers=h)
    assert r.json()["status"] == "cancelled"
    r = client.post("/actions", json={
        "kind": "calendar.list_events", "payload": {}, "idempotency_key": "k3",
    }, headers=h)
    assert all(e["id"] != event_id for e in r.json()["result"]["events"])


def test_level2_never_executes_without_confirm(client):
    """Livello 2: anteprima e conferma esplicita ogni volta. Prima della
    conferma non esiste alcuna bozza: un'azione in coda non e completata."""
    u = make_user(client, "act-l2")
    h = u["headers"]
    _connector(client, h, kind="email_mock")
    r = client.post("/actions", json={
        "kind": "email.create_draft",
        "payload": {"to": "prof@example.com", "subject": "Assenza",
                    "body": "Buongiorno, domani non potro essere presente."},
        "idempotency_key": "d1",
    }, headers=h)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["level"] == 2 and a["status"] == "pending_confirm"
    assert a["result"] == {}  # nessun esito prima della conferma
    assert a["preview"]["draft"]["to"] == "prof@example.com"  # anteprima esatta

    # conferma esplicita: solo ora la bozza esiste
    r = client.post(f"/actions/{a['id']}/confirm", headers=h)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "executed"
    assert a["result"]["draft_id"]
    assert "Non inviata" in a["result"]["note"]
    assert a["confirmed_at"] and a["executed_at"]

    # doppia conferma: 409
    assert client.post(f"/actions/{a['id']}/confirm", headers=h).status_code == 409


def test_level2_cancel_before_confirm_creates_nothing(client):
    u = make_user(client, "act-l2-cancel")
    h = u["headers"]
    _connector(client, h, kind="email_mock")
    r = client.post("/actions", json={
        "kind": "email.create_draft",
        "payload": {"to": "x@example.com", "subject": "s", "body": "b"},
        "idempotency_key": "d1",
    }, headers=h)
    a = r.json()
    r = client.post(f"/actions/{a['id']}/cancel", headers=h)
    assert r.json()["status"] == "cancelled"
    # dopo la cancellazione la conferma non puo piu eseguire nulla
    assert client.post(f"/actions/{a['id']}/confirm", headers=h).status_code == 409


def test_no_send_action_exists(client):
    """Non esiste alcuna azione di invio: kind email.send rifiutato."""
    u = make_user(client, "act-nosend")
    h = u["headers"]
    _connector(client, h, kind="email_mock")
    r = client.post("/actions", json={
        "kind": "email.send",
        "payload": {"to": "x@example.com", "subject": "s", "body": "b"},
        "idempotency_key": "s1",
    }, headers=h)
    assert r.status_code == 400
    assert client.get("/actions", headers=h).json() == []


def test_idempotency_key_prevents_double_execution(client):
    u = make_user(client, "act-idem")
    h = u["headers"]
    _connector(client, h, kind="email_mock")
    body = {
        "kind": "email.create_draft",
        "payload": {"to": "x@example.com", "subject": "s", "body": "b"},
        "idempotency_key": "same-key",
    }
    a1 = client.post("/actions", json=body, headers=h).json()
    a2 = client.post("/actions", json=body, headers=h).json()
    assert a1["id"] == a2["id"]  # stessa azione, nessuna doppia esecuzione
    client.post(f"/actions/{a1['id']}/confirm", headers=h)
    a3 = client.post("/actions", json=body, headers=h).json()
    assert a3["id"] == a1["id"] and a3["status"] == "executed"
    # una sola bozza creata in tutto
    assert len(client.get("/actions", headers=h).json()) == 1


def test_revoked_connector_blocks_actions(client):
    u = make_user(client, "act-revoke")
    h = u["headers"]
    conn = _connector(client, h)
    client.post(f"/connectors/{conn['id']}/revoke", headers=h)
    r = client.post("/actions", json={
        "kind": "calendar.list_events", "payload": {}, "idempotency_key": "k1",
    }, headers=h)
    assert r.status_code == 403


def test_actions_isolation(client):
    u1 = make_user(client, "actA")
    u2 = make_user(client, "actB")
    _connector(client, u1["headers"], kind="email_mock")
    r = client.post("/actions", json={
        "kind": "email.create_draft",
        "payload": {"to": "x@example.com", "subject": "s", "body": "b"},
        "idempotency_key": "d1",
    }, headers=u1["headers"])
    aid = r.json()["id"]
    # l'altro utente non vede ne puo confermare/cancellare
    assert client.get("/actions", headers=u2["headers"]).json() == []
    assert client.post(f"/actions/{aid}/confirm", headers=u2["headers"]).status_code == 404
    assert client.post(f"/actions/{aid}/cancel", headers=u2["headers"]).status_code == 404
    # i connettori di u1 non valgono per u2
    r = client.post("/actions", json={
        "kind": "email.create_draft",
        "payload": {"to": "y@example.com", "subject": "s", "body": "b"},
        "idempotency_key": "d1",
    }, headers=u2["headers"])
    assert r.status_code == 403


def test_tasks_add_level1_and_cancel(client):
    """Livello 1 su dati propri senza connettore: aggiungi attivita, annulla."""
    u = make_user(client, "act-task")
    h = u["headers"]
    make_source(client, h)
    r = client.post("/actions", json={
        "kind": "tasks.add", "payload": {"content": "Portare il libro a Marco"},
        "idempotency_key": "t1",
    }, headers=h)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["level"] == 1 and a["status"] == "executed"
    mem_id = a["result"]["memory_id"]
    assert client.get(f"/memories/{mem_id}", headers=h).status_code == 200

    client.post(f"/actions/{a['id']}/cancel", headers=h)
    assert client.get(f"/memories/{mem_id}", headers=h).status_code == 404


def test_level0_not_cancellable(client):
    """Livello 0 (sola lettura): nulla da annullare."""
    u = make_user(client, "act-l0-nocancel")
    h = u["headers"]
    _connector(client, h)
    r = client.post("/actions", json={
        "kind": "calendar.list_events", "payload": {}, "idempotency_key": "k1",
    }, headers=h)
    a = r.json()
    assert client.post(f"/actions/{a['id']}/cancel", headers=h).status_code == 409
