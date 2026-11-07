from .conftest import make_memory, make_source, make_user


def test_export_contains_all_user_data(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    client.post("/projects", json={"name": "ExportProject"}, headers=u["headers"])
    client.post("/events", json={"type": "export-test", "payload": {"k": 1}, "event_at": "2026-11-07T10:00:00Z",
                                 "source_id": sid}, headers=u["headers"])
    make_memory(client, u["headers"], sid, "Memoria da esportare")
    r = client.get("/me/export", headers=u["headers"])
    data = r.json()
    assert data["user"]["email"] == u["email"]
    assert len(data["projects"]) == 1
    assert len(data["events"]) == 1
    assert len(data["memories"]) == 1
    assert len(data["sources"]) == 1


def test_delete_removes_everything(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    mid = make_memory(client, u["headers"], sid, "Da dimenticare")["id"]
    client.delete("/me", headers=u["headers"])
    # anche con token valido, l'utente non esiste piu
    assert client.get(f"/memories/{mid}", headers=u["headers"]).status_code == 401
