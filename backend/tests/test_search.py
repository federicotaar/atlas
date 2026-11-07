from .conftest import make_memory, make_source, make_user


def _seed(client, headers, sid):
    make_memory(client, headers, sid, "Il progetto ATLAS usa PostgreSQL 16 con pgvector per la ricerca semantica")
    make_memory(client, headers, sid, "La riunione con il team e spostata a giovedi alle 15")
    make_memory(client, headers, sid, "Ho deciso di usare FastAPI per il backend di ATLAS")


def test_text_search_finds_keyword(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    _seed(client, u["headers"], sid)
    r = client.post("/memories/search", json={"query": "PostgreSQL pgvector"}, headers=u["headers"])
    assert r.status_code == 200
    assert "PostgreSQL" in r.json()[0]["memory"]["content"]


def test_search_ranks_relevant_first(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    _seed(client, u["headers"], sid)
    r = client.post("/memories/search", json={"query": "riunione team giovedi"}, headers=u["headers"])
    assert "riunione" in r.json()[0]["memory"]["content"]


def test_search_no_match_returns_empty(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    _seed(client, u["headers"], sid)
    r = client.post("/memories/search", json={"query": "zanzara filobus meridiano quantum"}, headers=u["headers"])
    assert r.json() == []


def test_search_scores_present(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    _seed(client, u["headers"], sid)
    r = client.post("/memories/search", json={"query": "FastAPI backend"}, headers=u["headers"])
    hit = r.json()[0]
    assert hit["score"] > 0
    assert hit["relevance"] > 0
    assert 0 <= hit["semantic"] <= 1
    assert 0 <= hit["text_score"] <= 1


def test_search_limit(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    for i in range(5):
        make_memory(client, u["headers"], sid, f"Nota numero {i} sul progetto comune")
    r = client.post("/memories/search", json={"query": "progetto comune", "limit": 2}, headers=u["headers"])
    assert len(r.json()) == 2
