"""Test fase 3: Prediction Engine (registro, stima da velocita, rifiuto con
pochi dati, verifica con errore e Brier, metriche e calibrazione)."""
from datetime import datetime, timedelta, timezone

from .conftest import make_source, make_user


def _make_task(client, h, src, content, days_ago, supersede_days_ago=None):
    """Crea una task; se supersede_days_ago, la chiude sostituendola."""
    r = client.post("/memories", json={
        "content": content, "source_id": src, "kind": "task", "origin": "stated",
        "confidence": 0.9,
        "event_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
    }, headers=h)
    assert r.status_code == 201, r.text
    mem = r.json()["memory"]
    if supersede_days_ago is not None:
        r2 = client.post("/memories", json={
            "content": content + " (fatta)", "source_id": src, "kind": "fact",
            "origin": "stated", "confidence": 0.9,
            "supersedes": mem["id"],
            "event_at": (datetime.now(timezone.utc) - timedelta(days=supersede_days_ago)).isoformat(),
        }, headers=h)
        assert r2.status_code == 201, r2.text
    return mem


def test_refused_when_too_little_data(client):
    """Regola spec: con dati troppo pochi il sistema lo dice invece di stimare."""
    u = make_user(client, "pred-refused")
    h = u["headers"]
    src = make_source(client, h)
    _make_task(client, h, src, "Preparare la relazione", 3)
    r = client.post("/predictions", json={
        "question": "Quando finiranno le attivita aperte?",
        "verify_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    }, headers=h)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "refused"
    assert "Dati troppo pochi" in data["outcome_note"]
    assert data["estimate_central"] is None


def test_estimate_from_velocity_and_verify(client):
    u = make_user(client, "pred-est")
    h = u["headers"]
    src = make_source(client, h)
    # due settimane di completamenti: 2 task la scorsa settimana, 1 quella prima
    _make_task(client, h, src, "Task A settimana scorsa", 10, supersede_days_ago=6)
    _make_task(client, h, src, "Task B settimana scorsa", 9, supersede_days_ago=5)
    _make_task(client, h, src, "Task C due settimane fa", 17, supersede_days_ago=13)
    # attivita aperte
    _make_task(client, h, src, "Task aperto 1", 2)
    _make_task(client, h, src, "Task aperto 2", 1)

    r = client.post("/predictions", json={
        "question": "In quante settimane finiranno le attivita aperte?",
        "verify_at": (datetime.now(timezone.utc) + timedelta(days=21)).isoformat(),
        "probability": 0.7,
        "risk_factors": "Possibili nuove attivita",
    }, headers=h)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "open"
    assert data["estimate_central"] is not None
    assert data["estimate_low"] <= data["estimate_central"] <= data["estimate_high"]
    assert data["unit"] == "settimane"
    assert data["data_used"]["tasks_open"] == 2
    assert data["assumptions"], "le assunzioni sono sempre registrate"

    # verifica: esito reale 1.5 settimane
    r = client.post(f"/predictions/{data['id']}/verify",
                    json={"outcome": 1.5, "outcome_note": "Finite prima del previsto"}, headers=h)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "verified"
    assert v["error"] == round(abs(1.5 - data["estimate_central"]), 3)
    assert v["brier"] == round((0.7 - 1.0) ** 2, 4)  # outcome >= 0.5 -> successo

    # doppia verifica: 409
    r = client.post(f"/predictions/{data['id']}/verify", json={"outcome": 2.0}, headers=h)
    assert r.status_code == 409


def test_verify_refused_prediction_is_409(client):
    u = make_user(client, "pred-ref-verify")
    h = u["headers"]
    r = client.post("/predictions", json={
        "question": "Quando finisce?",
        "verify_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
    }, headers=h)
    assert r.json()["status"] == "refused"
    r = client.post(f"/predictions/{r.json()['id']}/verify", json={"outcome": 1.0}, headers=h)
    assert r.status_code == 409


def test_metrics_and_calibration(client):
    u = make_user(client, "pred-metrics")
    h = u["headers"]
    # due previsioni manuali con probabilita dichiarata, poi verificate
    for prob, outcome in ((0.9, 1.0), (0.2, 0.0)):
        r = client.post("/predictions", json={
            "question": f"Test {prob}",
            "verify_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            "auto_estimate": False, "estimate_central": 3.0, "estimate_low": 2.0,
            "estimate_high": 4.0, "unit": "settimane", "probability": prob,
        }, headers=h)
        assert r.status_code == 201, r.text
        client.post(f"/predictions/{r.json()['id']}/verify", json={"outcome": outcome}, headers=h)

    m = client.get("/predictions/metrics", headers=h).json()
    assert m["verified_count"] == 2
    assert m["mean_absolute_error"] is not None
    assert m["mean_brier"] == round(((0.9 - 1.0) ** 2 + (0.2 - 0.0) ** 2) / 2, 4)
    assert m["calibration"], "la curva di calibrazione a bucket e presente"


def test_predictions_isolation(client):
    u1 = make_user(client, "predA")
    u2 = make_user(client, "predB")
    r = client.post("/predictions", json={
        "question": "Solo mia",
        "verify_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        "auto_estimate": False, "estimate_central": 1.0,
    }, headers=u1["headers"])
    pid = r.json()["id"]
    assert client.get("/predictions", headers=u2["headers"]).json() == []
    assert client.post(f"/predictions/{pid}/verify", json={"outcome": 1.0},
                       headers=u2["headers"]).status_code == 404
