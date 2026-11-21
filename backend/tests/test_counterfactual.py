"""Test API del Counterfactual Engine (ricerca parallela, spec 5J):
precondizioni misurate e dichiarate, scenari what-if con delta esplicito,
rifiuto onesto con pochi dati, isolamento tra utenti."""
from datetime import datetime, timedelta, timezone

from .conftest import make_source, make_user


def _make_task(client, h, src, content, days_ago, supersede_days_ago=None):
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
            "origin": "stated", "confidence": 0.9, "supersedes": mem["id"],
            "event_at": (datetime.now(timezone.utc) - timedelta(days=supersede_days_ago)).isoformat(),
        }, headers=h)
        assert r2.status_code == 201, r2.text
    return mem


def _seed_history(client, h, src):
    # due settimane di completamenti (2 + 1) e due attivita aperte
    _make_task(client, h, src, "Task A", 10, supersede_days_ago=6)
    _make_task(client, h, src, "Task B", 9, supersede_days_ago=5)
    _make_task(client, h, src, "Task C", 17, supersede_days_ago=13)
    _make_task(client, h, src, "Aperto 1", 2)
    _make_task(client, h, src, "Aperto 2", 1)


def test_status_requires_auth(client):
    assert client.get("/counterfactual/status").status_code == 401
    assert client.post("/counterfactual/what-if", json={"scenarios": [{}]}).status_code == 401


def test_status_reports_preconditions_honestly(client):
    u = make_user(client, "cf-status")
    r = client.get("/counterfactual/status", headers=u["headers"])
    assert r.status_code == 200, r.text
    pre = r.json()["preconditions"]
    # utente nuovo: le precondizioni 5J NON possono risultare soddisfatte
    assert pre["met"] is False
    assert pre["events_total"] == 0 and pre["months_covered"] == 0.0
    assert pre["missing"], "le mancanze vanno elencate, non nascoste"


def test_what_if_delta_and_exploratory_flag(client):
    u = make_user(client, "cf-whatif")
    h = u["headers"]
    src = make_source(client, h)
    _seed_history(client, h, src)
    r = client.post("/counterfactual/what-if", json={"scenarios": [
        {"label": "doppia velocita", "velocity_factor": 2.0},
        {"label": "meta' velocita", "velocity_factor": 0.5},
        {"label": "una gia' fatta", "extra_completed_tasks": 1},
    ]}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["factual"]["possible"], data["factual"].get("reason")
    factual_central = data["factual"]["central"]
    by_label = {s["label"]: s for s in data["scenarios"]}
    assert by_label["doppia velocita"]["central"] < factual_central
    assert by_label["meta' velocita"]["central"] > factual_central
    assert by_label["doppia velocita"]["delta_weeks_vs_factual"] < 0
    # poca storia: ogni scenario e' dichiarato esplorativo, mai conclusione
    assert data["exploratory_note"]
    assert all(s["exploratory"] for s in data["scenarios"])
    assert data["preconditions"]["met"] is False
    assert data["model"], "il modello usato va sempre dichiarato"


def test_what_if_refused_with_too_little_data(client):
    u = make_user(client, "cf-refused")
    h = u["headers"]
    src = make_source(client, h)
    _make_task(client, h, src, "Unico task aperto", 1)
    r = client.post("/counterfactual/what-if",
                    json={"scenarios": [{"label": "x", "velocity_factor": 2.0}]},
                    headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["factual"]["possible"] is False
    assert "Dati troppo pochi" in data["factual"]["reason"]
    assert data["scenarios"][0]["possible"] is False


def test_what_if_validates_input(client):
    u = make_user(client, "cf-validate")
    h = u["headers"]
    r = client.post("/counterfactual/what-if",
                    json={"scenarios": [{"velocity_factor": 0}]}, headers=h)
    assert r.status_code == 422
    r = client.post("/counterfactual/what-if", json={"scenarios": []}, headers=h)
    assert r.status_code == 422


def test_what_if_uses_only_own_data(client):
    """Isolamento: lo scenario di A non vede le attivita di B."""
    a = make_user(client, "cf-user-a")
    b = make_user(client, "cf-user-b")
    src_b = make_source(client, b["headers"])
    _seed_history(client, b["headers"], src_b)
    r = client.post("/counterfactual/what-if",
                    json={"scenarios": [{"label": "x"}]}, headers=a["headers"])
    assert r.status_code == 200, r.text
    # A non ha completamenti: rifiuto onesto, non i dati di B
    assert r.json()["factual"]["possible"] is False
