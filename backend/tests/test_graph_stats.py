"""Test dell'endpoint /graph/stats con DB reale."""
from .conftest import make_user


def test_graph_stats_counts(client):
    u = make_user(client, "graphstats")
    h = u["headers"]
    r = client.post("/graph/extract", json={"text": "Maria Rossi lavora da Acme"}, headers=h)
    assert r.status_code == 201, r.text
    r = client.get("/graph/stats", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["entities"].get("person/pending", 0) >= 1
    assert data["relationships"].get("lavora_per/pending", 0) >= 1


def test_graph_stats_empty_for_new_user(client):
    u = make_user(client, "graphstatsempty")
    r = client.get("/graph/stats", headers=u["headers"])
    assert r.status_code == 200, r.text
    assert r.json() == {"entities": {}, "relationships": {}}
