"""Test fase 5: Digital Time Machine (spec 5H).

Cronologia, stato a una data (memorie attive alla data, mai dopo), confronto
tra periodi, obiettivi abbandonati, attivita ricorrenti. Le lacune vengono
sempre mostrate e mai riempite. Isolamento: un utente non vede i dati di un
altro."""
from datetime import datetime, timedelta, timezone

from .conftest import make_memory, make_source, make_user

NOW = datetime.now(timezone.utc)


def _event(client, h, type_, days_ago, project_id=None, src=None):
    body = {"type": type_, "payload": {"note": type_},
            "event_at": (NOW - timedelta(days=days_ago)).isoformat()}
    if project_id:
        body["project_id"] = project_id
    if src:
        body["source_id"] = src
    r = client.post("/events", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _project(client, h, name="Progetto TM", target_date=None):
    body = {"name": name}
    if target_date:
        body["target_date"] = target_date
    r = client.post("/projects", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_timeline_order_and_gaps(client):
    u = make_user(client, "tm-timeline")
    h = u["headers"]
    _event(client, h, "lavoro", 10)
    _event(client, h, "lavoro", 9)
    _event(client, h, "lavoro", 2)  # lacuna giorni 8..3 circa

    start = (NOW - timedelta(days=11)).isoformat()
    end = NOW.isoformat()
    r = client.get("/timemachine/timeline", params={"from": start, "to": end}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 3
    times = [e["event_at"] for e in data["events"]]
    assert times == sorted(times)
    # la lacuna tra il giorno 9 e il giorno 2 e riportata, non riempita
    assert len(data["gaps"]) >= 1
    assert max(g["days"] for g in data["gaps"]) >= 5


def test_timeline_bad_range_rejected(client):
    u = make_user(client, "tm-range")
    h = u["headers"]
    r = client.get("/timemachine/timeline",
                   params={"from": NOW.isoformat(), "to": NOW.isoformat()},
                   headers=h)
    assert r.status_code == 422


def test_state_at_respects_recorded_dates_and_supersession(client):
    u = make_user(client, "tm-state")
    h = u["headers"]
    src = make_source(client, h)
    a = make_memory(client, h, src, "Versione A del fatto")
    r = client.post("/memories", json={"content": "Versione B del fatto",
                                       "source_id": src, "kind": "fact",
                                       "origin": "stated", "confidence": 0.9,
                                       "supersedes": a["id"]}, headers=h)
    assert r.status_code == 201, r.text

    future = (NOW + timedelta(days=1)).isoformat()
    r = client.get("/timemachine/state-at", params={"at": future}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    contents = [m["content"] for m in data["memories"]["items"]]
    assert "Versione B del fatto" in contents
    # la versione sostituita non risulta attiva alla data
    assert "Versione A del fatto" not in contents


def test_state_at_intermediate_date_keeps_old_version(client, db_session):
    """A una data tra la registrazione di A e quella di B, A risulta ancora
    attiva e B non esiste ancora."""
    import uuid

    from app.models import Memory, Source, User
    user = User(email=f"tm-mid-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    src = Source(user_id=user.id, kind="chat", label="t")
    db_session.add(src)
    db_session.commit()
    t0 = NOW - timedelta(days=10)
    t1 = NOW - timedelta(days=5)
    a = Memory(user_id=user.id, content="Fatto vecchio", kind="fact",
               origin="stated", confidence=0.9, source_id=src.id,
               recorded_at=t0)
    db_session.add(a)
    db_session.commit()
    b = Memory(user_id=user.id, content="Fatto nuovo", kind="fact",
               origin="stated", confidence=0.9, source_id=src.id,
               recorded_at=t1)
    db_session.add(b)
    db_session.commit()
    a.superseded_by = b.id
    db_session.commit()

    from app.services import timemachine
    mid = NOW - timedelta(days=7)
    active = timemachine.memories_as_of(db_session, user.id, mid)
    contents = [m.content for m in active]
    assert contents == ["Fatto vecchio"]
    active_now = timemachine.memories_as_of(db_session, user.id, NOW)
    assert [m.content for m in active_now] == ["Fatto nuovo"]


def test_state_at_excludes_future_events(client):
    u = make_user(client, "tm-future")
    h = u["headers"]
    _event(client, h, "passato", 5)
    # evento "nel futuro" rispetto alla data richiesta
    _event(client, h, "futuro", -5)
    at = NOW.isoformat()
    r = client.get("/timemachine/state-at", params={"at": at}, headers=h)
    data = r.json()
    assert data["events"]["by_type"].get("passato") == 1
    assert "futuro" not in data["events"]["by_type"]


def test_compare_periods(client):
    u = make_user(client, "tm-compare")
    h = u["headers"]
    _event(client, h, "allenamento", 20)
    _event(client, h, "allenamento", 19)
    _event(client, h, "allenamento", 5)
    a_start = (NOW - timedelta(days=25)).isoformat()
    a_end = (NOW - timedelta(days=15)).isoformat()
    b_start = (NOW - timedelta(days=10)).isoformat()
    b_end = NOW.isoformat()
    r = client.get("/timemachine/compare",
                   params={"a_start": a_start, "a_end": a_end,
                           "b_start": b_start, "b_end": b_end}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["period_a"]["events"]["count"] == 2
    assert data["period_b"]["events"]["count"] == 1
    # provenienza: id eventi campione presenti
    assert data["period_a"]["events"]["sample_ids"]


def test_abandoned_goals(client):
    u = make_user(client, "tm-abandoned")
    h = u["headers"]
    p_idle = _project(client, h, "Progetto fermo")
    _event(client, h, "nota", 30, project_id=p_idle["id"])
    p_active = _project(client, h, "Progetto vivo")
    _event(client, h, "nota", 1, project_id=p_active["id"])
    yesterday = (NOW.date() - timedelta(days=1)).isoformat()
    p_expired = _project(client, h, "Progetto scaduto", target_date=yesterday)
    _event(client, h, "nota", 1, project_id=p_expired["id"])

    r = client.get("/timemachine/abandoned-goals", params={"idle_days": 14}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    ids = [i["project_id"] for i in data["items"]]
    assert p_idle["id"] in ids
    assert p_expired["id"] in ids
    assert p_active["id"] not in ids
    # ogni voce mostra da quali dati deriva
    item = next(i for i in data["items"] if i["project_id"] == p_idle["id"])
    assert item["derived_from"]["last_event_id"] is not None


def test_recurring_activities(client):
    u = make_user(client, "tm-recurring")
    h = u["headers"]
    for d in (28, 21, 14, 7, 0):
        _event(client, h, "spesa", d)
    _event(client, h, "una-tantum", 3)

    r = client.get("/timemachine/recurring", params={"min_occurrences": 3}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    types = [i["type"] for i in data["items"]]
    assert "spesa" in types
    assert "una-tantum" not in types
    spesa = next(i for i in data["items"] if i["type"] == "spesa")
    assert spesa["occurrences"] == 5
    assert 5.0 <= spesa["mean_interval_days"] <= 9.0
    assert len(spesa["derived_from"]["sample_event_ids"]) >= 3


def test_timemachine_isolation(client):
    ua = make_user(client, "tm-iso-a")
    ub = make_user(client, "tm-iso-b")
    _event(client, ua["headers"], "segreto-a", 2)
    start = (NOW - timedelta(days=5)).isoformat()
    end = NOW.isoformat()
    rb = client.get("/timemachine/timeline", params={"from": start, "to": end},
                    headers=ub["headers"])
    assert rb.status_code == 200
    assert rb.json()["count"] == 0
    rb2 = client.get("/timemachine/state-at", params={"at": end}, headers=ub["headers"])
    assert rb2.json()["events"]["count"] == 0
    rb3 = client.get("/timemachine/abandoned-goals", headers=ub["headers"])
    assert rb3.json()["count"] == 0


def test_timemachine_requires_auth(client):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for path, params in [
        ("/timemachine/timeline", {"from": now, "to": now}),
        ("/timemachine/state-at", {"at": now}),
        ("/timemachine/compare", {"a_start": now, "a_end": now,
                                  "b_start": now, "b_end": now}),
        ("/timemachine/abandoned-goals", {}),
        ("/timemachine/recurring", {}),
    ]:
        r = client.get(path, params=params)
        assert r.status_code == 401, (path, r.status_code)
