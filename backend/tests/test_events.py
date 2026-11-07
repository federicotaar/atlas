from .conftest import make_source, make_user


def _mk_event(client, headers, **kw):
    payload = {"type": "update", "payload": {"note": "x"}, "event_at": "2026-11-07T10:00:00Z"}
    payload.update(kw)
    r = client.post("/events", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_event(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    e = _mk_event(client, u["headers"], source_id=sid)
    assert e["type"] == "update"
    assert e["source_id"] == sid


def test_event_with_project(client):
    u = make_user(client)
    pid = client.post("/projects", json={"name": "P"}, headers=u["headers"]).json()["id"]
    e = _mk_event(client, u["headers"], project_id=pid)
    assert e["project_id"] == pid


def test_event_unknown_project_404(client):
    import uuid
    u = make_user(client)
    r = client.post("/events", json={"type": "t", "payload": {}, "event_at": "2026-11-07T10:00:00Z",
                                     "project_id": str(uuid.uuid4())}, headers=u["headers"])
    assert r.status_code == 404


def test_event_unknown_source_404(client):
    import uuid
    u = make_user(client)
    r = client.post("/events", json={"type": "t", "payload": {}, "event_at": "2026-11-07T10:00:00Z",
                                     "source_id": str(uuid.uuid4())}, headers=u["headers"])
    assert r.status_code == 404


def test_filter_by_type(client):
    u = make_user(client)
    _mk_event(client, u["headers"], type="alpha")
    _mk_event(client, u["headers"], type="beta")
    r = client.get("/events", params={"type": "alpha"}, headers=u["headers"])
    assert [e["type"] for e in r.json()] == ["alpha"]


def test_filter_by_date_range(client):
    u = make_user(client)
    _mk_event(client, u["headers"], type="old", event_at="2026-01-01T10:00:00Z")
    _mk_event(client, u["headers"], type="new", event_at="2026-11-01T10:00:00Z")
    r = client.get("/events", params={"from": "2026-06-01T00:00:00Z"}, headers=u["headers"])
    assert [e["type"] for e in r.json()] == ["new"]
    r = client.get("/events", params={"to": "2026-06-01T00:00:00Z"}, headers=u["headers"])
    assert [e["type"] for e in r.json()] == ["old"]


def test_filter_by_project(client):
    u = make_user(client)
    pid = client.post("/projects", json={"name": "P"}, headers=u["headers"]).json()["id"]
    _mk_event(client, u["headers"], type="withp", project_id=pid)
    _mk_event(client, u["headers"], type="withoutp")
    r = client.get("/events", params={"project_id": pid}, headers=u["headers"])
    assert [e["type"] for e in r.json()] == ["withp"]


def test_events_append_only_no_update_delete(client):
    u = make_user(client)
    e = _mk_event(client, u["headers"])
    assert client.put(f"/events/{e['id']}", json={}, headers=u["headers"]).status_code in (404, 405)
    assert client.delete(f"/events/{e['id']}", headers=u["headers"]).status_code in (404, 405)
