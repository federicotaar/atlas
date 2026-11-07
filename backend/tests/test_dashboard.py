"""Test fase 5: dashboard (spec 5I).

Ogni pannello aggrega dati reali dell'utente e ogni elemento espone
derived_from con gli id sorgente: e la regola d'uscita della fase.
Isolamento: la dashboard di un altro utente resta vuota."""
from datetime import datetime, timedelta, timezone

from .conftest import make_memory, make_source, make_user

NOW = datetime.now(timezone.utc)


def _filled_user(client, prefix):
    u = make_user(client, prefix)
    h = u["headers"]
    src = make_source(client, h)
    r = client.post("/projects", json={"name": "Progetto Dash",
                                       "target_date": (NOW.date() + timedelta(days=10)).isoformat()},
                    headers=h)
    assert r.status_code == 201, r.text
    project = r.json()
    r = client.post("/events", json={
        "type": "nota", "payload": {"x": 1}, "project_id": project["id"],
        "source_id": src, "event_at": NOW.isoformat()}, headers=h)
    assert r.status_code == 201, r.text
    event = r.json()
    mem = make_memory(client, h, src, "Fatto importante dashboard", confidence=0.95)
    r = client.post("/predictions", json={
        "question": "Attivita chiuse entro 2 giorni?",
        "auto_estimate": False,
        "estimate_central": 2.0, "estimate_low": 1.0, "estimate_high": 3.0,
        "unit": "attivita", "verify_at": (NOW + timedelta(days=2)).isoformat(),
    }, headers=h)
    assert r.status_code == 201, r.text
    prediction = r.json()
    r = client.post("/connectors", json={"kind": "calendar_mock"}, headers=h)
    assert r.status_code == 201, r.text
    connector = r.json()
    return {"headers": h, "project": project, "event": event, "memory": mem,
            "prediction": prediction, "connector": connector}


def test_dashboard_panels_and_provenance(client):
    fx = _filled_user(client, "dash-main")
    r = client.get("/dashboard", headers=fx["headers"])
    assert r.status_code == 200, r.text
    data = r.json()
    panels = data["panels"]

    # progetti: il progetto compare con la sua derivazione
    proj = next(p for p in panels["projects"] if p["project_id"] == fx["project"]["id"])
    assert proj["derived_from"]["project_id"] == fx["project"]["id"]
    assert proj["derived_from"]["last_event_id"] == fx["event"]["id"]

    # attivita imminenti: la scadenza del progetto e la previsione aperta
    deadlines = panels["upcoming"]["project_deadlines"]
    assert any(d["project_id"] == fx["project"]["id"] for d in deadlines)
    upcoming_preds = panels["upcoming"]["predictions"]
    assert any(p["id"] == fx["prediction"]["id"] for p in upcoming_preds)

    # eventi recenti con id sorgente
    recent_ids = [e["id"] for e in panels["recent_events"]]
    assert fx["event"]["id"] in recent_ids
    ev = next(e for e in panels["recent_events"] if e["id"] == fx["event"]["id"])
    assert ev["derived_from"]["event_id"] == fx["event"]["id"]

    # previsioni: conteggi coerenti
    assert panels["predictions"]["open"] >= 1

    # memorie importanti con fonte
    mems = panels["important_memories"]
    mem = next(m for m in mems if m["id"] == fx["memory"]["id"])
    assert mem["derived_from"]["memory_id"] == fx["memory"]["id"]
    assert mem["derived_from"]["source_id"] is not None

    # richieste di autorizzazione: il connettore appena creato e in attesa
    pending = panels["authorization_requests"]["pending_connectors"]
    assert any(c["id"] == fx["connector"]["id"] for c in pending)

    # automazioni: il connettore compare
    conns = panels["automations"]["connectors"]
    assert any(c["id"] == fx["connector"]["id"] for c in conns)


def test_dashboard_isolation(client):
    _filled_user(client, "dash-iso-a")
    ub = make_user(client, "dash-iso-b")
    r = client.get("/dashboard", headers=ub["headers"])
    assert r.status_code == 200, r.text
    panels = r.json()["panels"]
    assert panels["projects"] == []
    assert panels["recent_events"] == []
    assert panels["important_memories"] == []
    assert panels["upcoming"]["predictions"] == []
    assert panels["upcoming"]["project_deadlines"] == []
    assert panels["authorization_requests"]["pending_connectors"] == []
    assert panels["predictions"]["open"] == 0
