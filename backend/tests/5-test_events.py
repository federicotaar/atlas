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


def test_event_pagination_stable_for_equal_timestamps(client):
    from .conftest import make_user
    h=make_user(client,"eventpages")["headers"]
    for i in range(4):
        assert client.post("/events",headers=h,json={"type":"note","payload":{"n":i},"event_at":"2026-11-20T10:00:00Z"}).status_code==201
    def page(offset):return client.get("/events",headers=h,params={"limit":2,"offset":offset}).json()
    a,b=page(0),page(2)
    assert len(a)==len(b)==2
    assert not ({r["id"] for r in a} & {r["id"] for r in b})
    assert a==page(0) and b==page(2)
    assert client.get("/events?offset=-1",headers=h).status_code==422


def test_event_time_range_validation(client):
    from .conftest import make_user
    h=make_user(client,"eventrange")["headers"]
    r=client.get("/events",headers=h,params={"from":"2026-11-21T00:00:00Z","to":"2026-11-20T00:00:00Z"})
    assert r.status_code==400 and r.json()["error"]["code"]=="invalid_range"
    # Equal instants in different zones are not a reversed range.
    assert client.get("/events",headers=h,params={"from":"2026-11-20T10:00:00+02:00","to":"2026-11-20T08:00:00Z"}).status_code==200


def test_event_statistics_owner_and_project_filter(client):
    from .conftest import make_user
    a,b=make_user(client,"eventstatsa"),make_user(client,"eventstatsb")
    h=a["headers"]
    pid=client.post("/projects",headers=h,json={"name":"counts"}).json()["id"]
    for project in (pid,None):
        assert client.post("/events",headers=h,json={"type":"note","project_id":project,"payload":{},"event_at":"2026-11-20T10:00:00Z"}).status_code==201
    assert client.get("/events/stats",headers=h).json()=={"total":2,"by_type":{"note":2}}
    assert client.get("/events/stats",headers=h,params={"project_id":pid}).json()["total"]==1
    assert client.get("/events/stats",headers=b["headers"]).json()["total"]==0


def test_event_detail_owner_and_static_stats_route(client):
    from .conftest import make_user
    a,b=make_user(client,"eventdetaila"),make_user(client,"eventdetailb")
    h=a["headers"]
    event=client.post("/events",headers=h,json={"type":"note","payload":{"text":"my event"},"event_at":"2026-11-20T10:00:00Z"}).json()
    url=f"/events/{event['id']}"
    assert client.get(url,headers=h).json()["payload"]=={"text":"my event"}
    assert client.get(url,headers=b["headers"]).status_code==404
    assert client.get("/events/00000000-0000-0000-0000-000000000000",headers=h).status_code==404
    assert client.get("/events/stats",headers=h).status_code==200
