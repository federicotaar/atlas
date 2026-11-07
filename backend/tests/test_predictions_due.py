"""Test fase 3 hardening: previsioni in scadenza (GET /predictions/due) e
verifica automatica alla data di verifica per le stime auto_estimate, con
esito misurato dallo stato reale delle attivita. Le stime manuali restano
da verificare a mano."""
from datetime import datetime, timedelta, timezone

from .conftest import make_source, make_user
from .test_predictions import _make_task


def _velocity_data(client, h, src):
    """Due settimane di completamenti per rendere possibile la stima."""
    _make_task(client, h, src, "Task A settimana scorsa", 10, supersede_days_ago=6)
    _make_task(client, h, src, "Task B settimana scorsa", 9, supersede_days_ago=5)
    _make_task(client, h, src, "Task C due settimane fa", 17, supersede_days_ago=13)


def _auto_prediction(client, h, verify_at):
    r = client.post("/predictions", json={
        "question": "In quante settimane finiranno le attivita aperte?",
        "verify_at": verify_at.isoformat(),
    }, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "open"
    return r.json()


def test_due_lists_only_open_past_due(client):
    u = make_user(client, "due-list")
    h = u["headers"]
    src = make_source(client, h)
    _velocity_data(client, h, src)
    _make_task(client, h, src, "Task aperto", 1)
    now = datetime.now(timezone.utc)
    past = _auto_prediction(client, h, now - timedelta(hours=1))
    future = _auto_prediction(client, h, now + timedelta(days=7))

    due = client.get("/predictions/due", headers=h).json()
    ids = [p["id"] for p in due]
    assert past["id"] in ids
    assert future["id"] not in ids

    # una previsione verificata non torna in scadenza
    client.post(f"/predictions/{past['id']}/verify", json={"outcome": 1.0}, headers=h)
    due = client.get("/predictions/due", headers=h).json()
    assert [p["id"] for p in due] == []


def test_evaluate_due_auto_completed_measured_from_live_state(client):
    u = make_user(client, "due-done")
    h = u["headers"]
    src = make_source(client, h)
    _velocity_data(client, h, src)
    open_mem = _make_task(client, h, src, "Task aperto da chiudere", 2)
    pred = _auto_prediction(client, h, datetime.now(timezone.utc) - timedelta(hours=1))

    # l'attivita aperta viene chiusa (evento datato 3 giorni dopo la creazione)
    close_at = datetime.now(timezone.utc) + timedelta(days=3)
    r = client.post("/memories", json={
        "content": "Task aperto da chiudere (fatta)", "source_id": src, "kind": "fact",
        "origin": "stated", "confidence": 0.9, "supersedes": open_mem["id"],
        "event_at": close_at.isoformat(),
    }, headers=h)
    assert r.status_code == 201, r.text

    r = client.post("/predictions/evaluate-due", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [p["id"] for p in out["evaluated"]] == [pred["id"]]
    assert out["pending_manual"] == []
    ev = out["evaluated"][0]
    assert ev["status"] == "verified"
    assert 0.3 <= ev["outcome"] <= 0.6  # circa 3 giorni = 0.43 settimane
    assert "Valutazione automatica" in ev["outcome_note"]
    assert "esaurite" in ev["outcome_note"]
    assert ev["error"] == round(abs(ev["outcome"] - pred["estimate_central"]), 3)

    # idempotente: una seconda valutazione non trova nulla
    out2 = client.post("/predictions/evaluate-due", headers=h).json()
    assert out2["evaluated"] == []


def test_evaluate_due_auto_still_open_is_lower_bound(client):
    u = make_user(client, "due-open")
    h = u["headers"]
    src = make_source(client, h)
    _velocity_data(client, h, src)
    _make_task(client, h, src, "Task ancora aperto", 1)
    pred = _auto_prediction(client, h, datetime.now(timezone.utc) - timedelta(hours=1))

    out = client.post("/predictions/evaluate-due", headers=h).json()
    assert [p["id"] for p in out["evaluated"]] == [pred["id"]]
    ev = out["evaluated"][0]
    assert ev["status"] == "verified"
    assert "ancora aperte" in ev["outcome_note"]
    assert "limite inferiore" in ev["outcome_note"]


def test_evaluate_due_skips_manual_predictions(client):
    u = make_user(client, "due-manual")
    h = u["headers"]
    r = client.post("/predictions", json={
        "question": "Stima manuale scaduta",
        "verify_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "auto_estimate": False, "estimate_central": 2.0, "unit": "settimane",
    }, headers=h)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    out = client.post("/predictions/evaluate-due", headers=h).json()
    assert out["evaluated"] == []
    assert [p["id"] for p in out["pending_manual"]] == [pid]
    # resta aperta e in scadenza: la verifica manuale e ancora possibile
    assert [p["id"] for p in client.get("/predictions/due", headers=h).json()] == [pid]
    v = client.post(f"/predictions/{pid}/verify", json={"outcome": 1.5}, headers=h)
    assert v.status_code == 200
    assert v.json()["status"] == "verified"


def test_due_isolation(client):
    u1 = make_user(client, "dueA")
    u2 = make_user(client, "dueB")
    src = make_source(client, u1["headers"])
    _velocity_data(client, u1["headers"], src)
    _make_task(client, u1["headers"], src, "Task aperto", 1)
    pred = _auto_prediction(client, u1["headers"],
                            datetime.now(timezone.utc) - timedelta(hours=1))

    assert client.get("/predictions/due", headers=u2["headers"]).json() == []
    out2 = client.post("/predictions/evaluate-due", headers=u2["headers"]).json()
    assert out2 == {"evaluated": [], "pending_manual": []}
    # la previsione di u1 non e stata toccata dalla valutazione di u2
    assert [p["id"] for p in client.get("/predictions/due", headers=u1["headers"]).json()] == [pred["id"]]
