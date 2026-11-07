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
