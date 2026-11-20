from .conftest import make_user


def test_create_project(client):
    u = make_user(client)
    r = client.post("/projects", json={"name": "ATLAS", "description": "sistema di intelligenza"},
                    headers=u["headers"])
    assert r.status_code == 201
    assert r.json()["status"] == "active"


def test_list_projects(client):
    u = make_user(client)
    client.post("/projects", json={"name": "Uno"}, headers=u["headers"])
    client.post("/projects", json={"name": "Due"}, headers=u["headers"])
    r = client.get("/projects", headers=u["headers"])
    assert [p["name"] for p in r.json()] == ["Uno", "Due"]


def test_get_project(client):
    u = make_user(client)
    pid = client.post("/projects", json={"name": "X"}, headers=u["headers"]).json()["id"]
    r = client.get(f"/projects/{pid}", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["name"] == "X"


def test_get_project_missing_404(client):
    import uuid
    u = make_user(client)
    r = client.get(f"/projects/{uuid.uuid4()}", headers=u["headers"])
    assert r.status_code == 404


def test_project_with_dates(client):
    u = make_user(client)
    r = client.post("/projects", json={"name": "D", "start_date": "2026-11-01", "target_date": "2026-12-01"},
                    headers=u["headers"])
    assert r.status_code == 201
    assert r.json()["target_date"] == "2026-12-01"


def test_project_status_filter(client):
    from .conftest import make_user
    h=make_user(client,"projectfilter")["headers"]
    for status in ("active","completed"):
        assert client.post("/projects",headers=h,json={"name":status,"status":status}).status_code==201
    rows=client.get("/projects?status=completed",headers=h).json()
    assert len(rows)==1 and rows[0]["name"]=="completed"
    assert client.get("/projects?status=absent",headers=h).json()==[]


def test_project_activity_summary(client):
    from .conftest import make_user
    a,b=make_user(client,"summarya"),make_user(client,"summaryb");h=a["headers"]
    pid=client.post("/projects",headers=h,json={"name":"summary"}).json()["id"]
    url=f"/projects/{pid}/activity-summary"
    assert client.get(url,headers=h).json()["events"]==0
    for kind in ("note","note","milestone"):
        assert client.post("/events",headers=h,json={"type":kind,"project_id":pid,"payload":{},"event_at":"2026-11-20T10:00:00Z"}).status_code==201
    out=client.get(url,headers=h).json()
    assert out["events"]==3 and out["by_type"]=={"note":2,"milestone":1}
    assert out["first_event_at"] and out["last_event_at"]
    assert client.get(url,headers=b["headers"]).status_code==404
