"""Consolidamento backend: atomicita' delle scritture (niente righe parziali
dopo un errore) e audit privacy (i log non contengono mai contenuti
personali, solo metadati)."""
import uuid

from .conftest import make_source, make_user


def test_failed_supersede_leaves_no_partial_memory(client):
    u = make_user(client, "atomic-super")
    h = u["headers"]
    src = make_source(client, h)
    before = client.get("/memories", headers=h).json()
    r = client.post("/memories", json={
        "content": "bozza che non deve restare", "source_id": src,
        "kind": "fact", "origin": "stated", "confidence": 0.9,
        "supersedes": str(uuid.uuid4()),
    }, headers=h)
    assert r.status_code == 404
    after = client.get("/memories", headers=h).json()
    assert len(after) == len(before), "una scrittura fallita non deve lasciare righe parziali"


def test_invalid_payload_leaves_no_partial_memory(client):
    u = make_user(client, "atomic-422")
    h = u["headers"]
    src = make_source(client, h)
    before = client.get("/memories", headers=h).json()
    r = client.post("/memories", json={"source_id": src}, headers=h)
    assert r.status_code == 422
    after = client.get("/memories", headers=h).json()
    assert len(after) == len(before)


def test_invalid_event_project_leaves_no_partial_event(client):
    u = make_user(client, "atomic-event")
    h = u["headers"]
    src = make_source(client, h)
    before = client.get("/events", headers=h).json()
    r = client.post("/events", json={
        "type": "nota", "payload": {"text": "non deve restare"}, "source_id": src,
        "project_id": str(uuid.uuid4()),
        "event_at": "2026-11-01T10:00:00+00:00",
    }, headers=h)
    assert r.status_code == 404
    after = client.get("/events", headers=h).json()
    assert len(after) == len(before)


def test_action_logs_never_store_personal_content(client, db_session):
    """Audit privacy (spec sez. 9): i log registrano tipo e metadati, mai il
    contenuto delle memorie o delle domande."""
    from app.models import ActionLog, User

    secret_memory = "ricordo-segreto-x7q9zz"
    secret_question = "domanda-segreta-k4w8qq"
    u = make_user(client, "privacy-audit")
    h = u["headers"]
    src = make_source(client, h)
    client.post("/memories", json={
        "content": secret_memory, "source_id": src, "kind": "fact",
        "origin": "stated", "confidence": 0.9,
    }, headers=h)
    r = client.post("/ask", json={"text": secret_question}, headers=h)
    assert r.status_code == 200, r.text

    user = db_session.query(User).filter(User.email == u["email"]).one()
    logs = db_session.query(ActionLog).filter(ActionLog.user_id == user.id).all()
    assert logs, "le operazioni vanno registrate (audit), il test serve sui contenuti"
    for row in logs:
        assert secret_memory not in row.action
        assert secret_question not in str(row.detail)
        assert secret_memory not in str(row.detail)
