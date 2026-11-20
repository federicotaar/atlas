"""Ogni query filtra per user_id: un utente non vede MAI i dati di un altro."""

from .conftest import make_memory, make_source, make_user


def test_projects_isolated(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    client.post("/projects", json={"name": "Segreto di A"}, headers=a["headers"])
    r = client.get("/projects", headers=b["headers"])
    assert r.json() == []


def test_project_detail_isolated(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    pid = client.post("/projects", json={"name": "Di A"}, headers=a["headers"]).json()["id"]
    assert client.get(f"/projects/{pid}", headers=b["headers"]).status_code == 404


def test_events_isolated(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    client.post("/events", json={"type": "privato", "payload": {}, "event_at": "2026-11-07T10:00:00Z"},
                headers=a["headers"])
    assert client.get("/events", headers=b["headers"]).json() == []


def test_memory_detail_isolated(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    sid = make_source(client, a["headers"])
    m = make_memory(client, a["headers"], sid, "Fatto privato di A")
    assert client.get(f"/memories/{m['id']}", headers=b["headers"]).status_code == 404


def test_memory_search_isolated(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    sid = make_source(client, a["headers"])
    make_memory(client, a["headers"], sid, "Il codice segreto di A e xq-99-zanzara")
    sid_b = make_source(client, b["headers"])
    make_memory(client, b["headers"], sid_b, "Appunto innocuo di B")
    r = client.post("/memories/search", json={"query": "codice segreto zanzara"}, headers=b["headers"])
    assert all("zanzara" not in h["memory"]["content"] for h in r.json())


def test_ask_isolated(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    sid = make_source(client, a["headers"])
    make_memory(client, a["headers"], sid, "Il progetto segreto di A si chiama Mercury")
    r = client.post("/ask", json={"text": "Come si chiama il progetto segreto?"}, headers=b["headers"])
    assert "Non lo so" in r.json()["answer"]


def test_cannot_use_other_users_source(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    sid_a = make_source(client, a["headers"])
    r = client.post("/memories", json={"content": "furto", "source_id": sid_a}, headers=b["headers"])
    assert r.status_code == 404


def test_cannot_supersede_other_users_memory(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    sid_a = make_source(client, a["headers"])
    m_a = make_memory(client, a["headers"], sid_a, "Fatto di A")
    sid_b = make_source(client, b["headers"])
    r = client.post("/memories", json={"content": "sovscrittura", "source_id": sid_b, "supersedes": m_a["id"]},
                    headers=b["headers"])
    assert r.status_code == 404


def test_export_only_own_data(client):
    a, b = make_user(client, "aa"), make_user(client, "bb")
    sid = make_source(client, a["headers"])
    make_memory(client, a["headers"], sid, "Fatto di A per export")
    r = client.get("/me/export", headers=b["headers"])
    assert r.json()["memories"] == []


def test_deleted_user_token_rejected(client):
    a = make_user(client, "aa")
    sid = make_source(client, a["headers"])
    make_memory(client, a["headers"], sid, "Da cancellare")
    assert client.delete("/me", headers=a["headers"]).status_code == 204
    assert client.get("/projects", headers=a["headers"]).status_code == 401


def test_source_detail_is_owned(client):
    from .conftest import make_user,make_source
    a,b=make_user(client,"sourcea"),make_user(client,"sourceb")
    sid=make_source(client,a["headers"],label="my document")
    assert client.get(f"/sources/{sid}",headers=a["headers"]).json()["label"] == "my document"
    assert client.get(f"/sources/{sid}",headers=b["headers"]).status_code == 404
    assert client.get("/sources/00000000-0000-0000-0000-000000000000",headers=a["headers"]).status_code == 404


def test_source_usage_counts_and_ownership(client):
    from .conftest import make_user,make_source,make_memory
    a,b=make_user(client,"usagea"),make_user(client,"usageb");h=a["headers"]
    sid=make_source(client,h)
    old=make_memory(client,h,sid,"old source fact")
    make_memory(client,h,sid,"replacement source fact",supersedes=old["id"])
    r=client.post("/events",headers=h,json={"type":"note","payload":{},"source_id":sid,"event_at":"2026-11-20T10:00:00Z"})
    assert r.status_code == 201
    assert client.get(f"/sources/{sid}/usage",headers=h).json()=={"memories":2,"active_memories":1,"events":1}
    assert client.get(f"/sources/{sid}/usage",headers=b["headers"]).status_code == 404
