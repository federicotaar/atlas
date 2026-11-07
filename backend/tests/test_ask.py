from .conftest import make_memory, make_source, make_user


def test_ask_idk_when_no_memories(client):
    u = make_user(client)
    r = client.post("/ask", json={"text": "Quale database usa il progetto?"}, headers=u["headers"])
    assert r.status_code == 200
    data = r.json()
    assert "Non lo so" in data["answer"]
    assert data["citations"] == []


def test_ask_idk_when_nothing_relevant(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    make_memory(client, u["headers"], sid, "La riunione e spostata a giovedi")
    r = client.post("/ask", json={"text": "Quanto costa il biglietto del treno per Bologna?"}, headers=u["headers"])
    assert "Non lo so" in r.json()["answer"]


def test_ask_answers_with_citations(client):
    u = make_user(client)
    sid = make_source(client, u["headers"], label="chat")
    m = make_memory(client, u["headers"], sid, "Il progetto ATLAS usa PostgreSQL 16 con pgvector")
    r = client.post("/ask", json={"text": "Quale database usa il progetto ATLAS?"}, headers=u["headers"])
    data = r.json()
    assert data["classification"] == "question"
    assert "PostgreSQL" in data["answer"]
    assert len(data["citations"]) >= 1
    cit = data["citations"][0]
    assert cit["memory_id"] == m["id"]
    assert cit["source"]["label"] == "chat"
    assert cit["confidence"] == 0.9


def test_ask_saves_new_information(client):
    u = make_user(client)
    r = client.post("/ask", json={"text": "Ricorda che la scadenza del progetto e il 30 novembre"},
                    headers=u["headers"])
    data = r.json()
    assert data["classification"] == "new_info"
    assert data["saved_memory"]["content"] == "la scadenza del progetto e il 30 novembre"
    assert data["saved_memory"]["origin"] == "stated"


def test_saved_info_is_then_answerable(client):
    u = make_user(client)
    client.post("/ask", json={"text": "Ricorda che il server di produzione usa PostgreSQL 16 su pgvector"},
                headers=u["headers"])
    r = client.post("/ask", json={"text": "Quale database usa il server di produzione?"}, headers=u["headers"])
    data = r.json()
    assert "PostgreSQL" in data["answer"]
    assert data["citations"]


def test_ask_refuses_actions(client):
    u = make_user(client)
    r = client.post("/ask", json={"text": "Invia una email a Mario domani"}, headers=u["headers"])
    data = r.json()
    assert data["classification"] == "action_request"
    assert "fase 4" in data["answer"]
    assert data["citations"] == []


def test_ask_classification_persists_statement_without_imperative(client):
    u = make_user(client)
    r = client.post("/ask", json={"text": "Il prossimo sprint inizia lunedi 10 novembre"}, headers=u["headers"])
    assert r.json()["classification"] == "new_info"
