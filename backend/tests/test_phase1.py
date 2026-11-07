"""Test fase 1 con DB reale: contraddizioni, grafo con conferma, conversazioni,
domande di stato progetto."""
from .conftest import make_memory, make_source, make_user


def test_memory_create_flags_contradiction(client):
    u = make_user(client, "contra")
    h = u["headers"]
    src = make_source(client, h)
    make_memory(client, h, src, "La riunione del progetto Mercurio e il martedi alle 10")
    r = client.post("/memories", json={
        "content": "La riunione del progetto Mercurio e il giovedi alle 15",
        "source_id": src, "kind": "fact", "origin": "stated", "confidence": 0.9,
    }, headers=h)
    assert r.status_code == 201, r.text
    data = r.json()
    assert len(data["possible_contradictions"]) == 1
    assert "martedi alle 10" in data["possible_contradictions"][0]["content"]


def test_open_contradictions_view(client):
    u = make_user(client, "contraview")
    h = u["headers"]
    src = make_source(client, h)
    make_memory(client, h, src, "Il budget mensile del server staging e 200 euro")
    make_memory(client, h, src, "Il budget mensile del server staging e 350 euro")
    r = client.get("/memories/contradictions", headers=h)
    assert r.status_code == 200
    pairs = r.json()
    assert len(pairs) == 1
    assert "budget" in pairs[0]["shared_terms"]


def test_superseded_memory_not_in_contradictions(client):
    u = make_user(client, "contraresolved")
    h = u["headers"]
    src = make_source(client, h)
    old = make_memory(client, h, src, "Il budget mensile del server staging e 200 euro")
    client.post("/memories", json={
        "content": "Il budget mensile del server staging e 350 euro",
        "source_id": src, "kind": "fact", "origin": "stated", "confidence": 0.9,
        "supersedes": old["id"],
    }, headers=h)
    r = client.get("/memories/contradictions", headers=h)
    assert r.status_code == 200
    assert r.json() == []


def test_graph_extract_propose_confirm_flow(client):
    u = make_user(client, "graph")
    h = u["headers"]
    client.post("/projects", json={"name": "Mercurio"}, headers=h)
    src = make_source(client, h)
    mem = make_memory(client, h, src, "Giulia Bianchi lavora a Mercurio")

    r = client.post("/graph/extract", json={"memory_id": mem["id"]}, headers=h)
    assert r.status_code == 201, r.text
    data = r.json()
    names = {e["name"] for e in data["entities"]}
    assert "Giulia Bianchi" in names
    assert "Mercurio" in names
    assert all(e["status"] == "pending" for e in data["entities"])
    assert any(r_["relation"] == "lavora_per" for r_ in data["relationships"])

    # il grafo confermato e vuoto finche l'utente non conferma
    r = client.get("/graph", headers=h)
    assert r.json() == {"entities": [], "relationships": []}

    props = client.get("/graph/proposals", headers=h).json()
    for e in props["entities"]:
        r = client.post(f"/graph/entities/{e['id']}/confirm", headers=h)
        assert r.status_code == 200
    for rel in props["relationships"]:
        r = client.post(f"/graph/relationships/{rel['id']}/confirm", headers=h)
        assert r.status_code == 200

    g = client.get("/graph", headers=h).json()
    kinds = {e["name"]: e["kind"] for e in g["entities"]}
    assert kinds["Mercurio"] == "project"
    assert kinds["Giulia Bianchi"] == "person"
    assert g["relationships"][0]["status"] == "confirmed"

    links = client.get("/graph/project-links", headers=h).json()
    assert len(links) == 1
    assert links[0]["from_entity"]["name"] == "Giulia Bianchi"
    assert links[0]["to_entity"]["name"] == "Mercurio"


def test_graph_proposal_cannot_be_confirmed_twice(client):
    u = make_user(client, "graph2")
    h = u["headers"]
    r = client.post("/graph/extract", json={"text": "Luigi Verdi usa FastAPI"}, headers=h)
    ent = r.json()["entities"][0]
    assert client.post(f"/graph/entities/{ent['id']}/confirm", headers=h).status_code == 200
    assert client.post(f"/graph/entities/{ent['id']}/confirm", headers=h).status_code == 409


def test_graph_isolation_between_users(client):
    u1 = make_user(client, "graphA")
    u2 = make_user(client, "graphB")
    r = client.post("/graph/extract", json={"text": "Carlo Neri lavora a Atlas"}, headers=u1["headers"])
    ent = r.json()["entities"][0]
    assert client.post(f"/graph/entities/{ent['id']}/confirm", headers=u2["headers"]).status_code == 404
    assert client.get("/graph/proposals", headers=u2["headers"]).json() == {"entities": [], "relationships": []}


def test_conversations_persist_ask_turns(client):
    u = make_user(client, "conv")
    h = u["headers"]
    r = client.post("/ask", json={"text": "Ricorda che la scadenza di Mercurio e il 20 dicembre"}, headers=h)
    assert r.status_code == 200
    conv_id = r.json()["conversation_id"]
    assert conv_id

    r = client.post("/ask", json={"text": "Quando e la scadenza di Mercurio?",
                                  "conversation_id": conv_id}, headers=h)
    assert r.status_code == 200
    assert r.json()["conversation_id"] == conv_id

    convs = client.get("/conversations", headers=h).json()
    assert len(convs) == 1
    assert convs[0]["message_count"] == 4

    detail = client.get(f"/conversations/{conv_id}", headers=h).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_conversation_isolation(client):
    u1 = make_user(client, "convA")
    u2 = make_user(client, "convB")
    r = client.post("/ask", json={"text": "Ricorda che piove"}, headers=u1["headers"])
    conv_id = r.json()["conversation_id"]
    assert client.get(f"/conversations/{conv_id}", headers=u2["headers"]).status_code == 404


def test_project_status_question(client):
    u = make_user(client, "status")
    h = u["headers"]
    proj = client.post("/projects", json={"name": "Mercurio", "target_date": "2026-12-20"}, headers=h).json()
    src = make_source(client, h)
    client.post("/events", json={
        "type": "milestone", "payload": {"text": "Completato lo schema del database"},
        "project_id": proj["id"], "source_id": src, "event_at": "2026-11-01T10:00:00Z",
    }, headers=h)

    r = client.post("/ask", json={"text": "A che punto sono con Mercurio?"}, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["classification"] == "project_status"
    assert "Mercurio" in data["answer"]
    assert "Completato lo schema del database" in data["answer"]
    assert "2026-12-20" in data["answer"]


def test_project_status_without_projects(client):
    u = make_user(client, "statusnone")
    r = client.post("/ask", json={"text": "A che punto sono con il progetto?"}, headers=u["headers"])
    assert r.status_code == 200
    assert "Non lo so" in r.json()["answer"]
