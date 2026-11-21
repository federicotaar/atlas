import uuid

from .conftest import make_memory, make_source, make_user


def test_create_memory(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    m = make_memory(client, u["headers"], sid, "Il progetto ATLAS usa PostgreSQL 16 con pgvector")
    assert m["kind"] == "fact"
    assert m["origin"] == "stated"
    assert m["superseded_by"] is None


def test_memory_has_source_and_dates(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    m = make_memory(client, u["headers"], sid, "Fact con fonte", event_at="2026-11-01T09:00:00Z")
    assert m["source_id"] == sid
    assert m["event_at"].startswith("2026-11-01")
    assert m["recorded_at"]


def test_invalid_kind_rejected(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    r = client.post("/memories", json={"content": "x", "source_id": sid, "kind": "boh"}, headers=u["headers"])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_kind"


def test_invalid_origin_rejected(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    r = client.post("/memories", json={"content": "x", "source_id": sid, "origin": "sognato"}, headers=u["headers"])
    assert r.status_code == 400


def test_confidence_out_of_range_rejected(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    r = client.post("/memories", json={"content": "x", "source_id": sid, "confidence": 1.5}, headers=u["headers"])
    assert r.status_code in (400, 422)


def test_memory_requires_existing_source(client):
    u = make_user(client)
    r = client.post("/memories", json={"content": "x", "source_id": str(uuid.uuid4())}, headers=u["headers"])
    assert r.status_code == 404


def test_get_memory(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    m = make_memory(client, u["headers"], sid, "Dettaglio")
    r = client.get(f"/memories/{m['id']}", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["content"] == "Dettaglio"


def test_get_memory_missing_404(client):
    u = make_user(client)
    assert client.get(f"/memories/{uuid.uuid4()}", headers=u["headers"]).status_code == 404


def test_duplicate_warning_on_identical_content(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    make_memory(client, u["headers"], sid, "La scadenza del progetto e il 30 novembre")
    r = client.post("/memories", json={"content": "La scadenza del progetto e il 30 novembre", "source_id": sid},
                    headers=u["headers"])
    assert r.status_code == 201
    assert len(r.json()["possible_duplicates"]) == 1


def test_no_duplicate_warning_for_different_content(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    make_memory(client, u["headers"], sid, "Il database e PostgreSQL")
    r = client.post("/memories", json={"content": "La scadenza e il 30 novembre", "source_id": sid},
                    headers=u["headers"])
    assert r.json()["possible_duplicates"] == []


def test_supersede_flow(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    old = make_memory(client, u["headers"], sid, "La scadenza e il 30 novembre")
    new = make_memory(client, u["headers"], sid, "La scadenza e il 15 dicembre", supersedes=old["id"])
    r = client.get(f"/memories/{old['id']}", headers=u["headers"])
    assert r.json()["superseded_by"] == new["id"]


def test_supersede_unknown_memory_404(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    r = client.post("/memories", json={"content": "x", "source_id": sid, "supersedes": str(uuid.uuid4())},
                    headers=u["headers"])
    assert r.status_code == 404


def test_cannot_supersede_twice(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    old = make_memory(client, u["headers"], sid, "v1")
    make_memory(client, u["headers"], sid, "v2", supersedes=old["id"])
    r = client.post("/memories", json={"content": "v3", "source_id": sid, "supersedes": old["id"]},
                    headers=u["headers"])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "already_superseded"


def test_superseded_memory_excluded_from_search(client):
    u = make_user(client)
    sid = make_source(client, u["headers"])
    old = make_memory(client, u["headers"], sid, "La password del router e squalo-verde-42")
    make_memory(client, u["headers"], sid, "La password del router e balena-rossa-7", supersedes=old["id"])
    r = client.post("/memories/search", json={"query": "password router"}, headers=u["headers"])
    contents = [h["memory"]["content"] for h in r.json()]
    assert "La password del router e balena-rossa-7" in contents
    assert "La password del router e squalo-verde-42" not in contents


def test_browse_memories_filters_and_isolation(client):
    from .conftest import make_memory, make_source, make_user
    a, b = make_user(client, "browsea"), make_user(client, "browseb")
    sa, sb = make_source(client, a["headers"]), make_source(client, b["headers"])
    old = make_memory(client, a["headers"], sa, "old fact")
    new = make_memory(client, a["headers"], sa, "new fact", supersedes=old["id"])
    task = make_memory(client, a["headers"], sa, "read the book", kind="task", origin="inferred")
    make_memory(client, b["headers"], sb, "not yours")
    def get(**params):
        return client.get("/memories", headers=a["headers"], params=params)
    assert {x["id"] for x in get().json()} == {new["id"],task["id"]}
    assert len(get(include_superseded=True).json()) == 3
    assert get(kind="task", origin="inferred").json()[0]["id"] == task["id"]
    assert get(source_id=sb).json() == []
    first, second = get(limit=1).json(), get(limit=1,offset=1).json()
    assert len(first)==len(second)==1 and first[0]["id"] != second[0]["id"]
    assert get(kind="bad").status_code == 400
    assert get(origin="bad").status_code == 400
    assert get(limit=0).status_code == 422
    assert get(offset=-1).status_code == 422


def test_memory_statistics_active_and_superseded(client):
    from .conftest import make_memory, make_source, make_user
    u=make_user(client,"memorystats")
    h=u["headers"]
    s=make_source(client,h)
    assert client.get("/memories/stats",headers=h).json()["total"] == 0
    old=make_memory(client,h,s,"old")
    make_memory(client,h,s,"new",supersedes=old["id"],kind="decision")
    stats=client.get("/memories/stats",headers=h).json()
    assert stats == {"total":2,"active":1,"superseded":1,
                     "by_kind":{"decision":1},"by_origin":{"stated":1}}
    other=make_user(client,"memorystatsother")
    assert client.get("/memories/stats",headers=other["headers"]).json()["total"] == 0
