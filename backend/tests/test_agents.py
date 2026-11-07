"""Test fase 2: pipeline agenti, limiti, registro agent_runs, Critic che
individua errori inseriti di proposito (gate di uscita della specifica)."""
from app.agents import core
from app.agents.schemas import CriticInput, PlanTask

from .conftest import make_memory, make_source, make_user


def test_pipeline_end_to_end(client):
    u = make_user(client, "agents")
    h = u["headers"]
    src = make_source(client, h)
    make_memory(client, h, src, "Il progetto Mercurio usa PostgreSQL 16 con pgvector")
    make_memory(client, h, src, "La scadenza della prima versione di Mercurio e il 20 dicembre 2026")

    r = client.post("/agents/run", json={
        "goal": "Prepara il rilascio di Mercurio entro la scadenza e poi verifica i test"
    }, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["facts"], "il Researcher deve trovare le memorie"
    assert data["tasks"], "il Planner deve produrre attivita"
    assert len(data["runs"]) == 3

    runs = client.get("/agents/runs", headers=h).json()
    agents = {r["agent"] for r in runs}
    assert {"researcher", "planner", "critic"} <= agents
    assert all(r["status"] == "ok" and r["cost_eur"] == 0.0 for r in runs)
    assert all(r["input"] is not None and r["output"] is not None for r in runs)


def test_pipeline_without_facts_produces_empty_plan_flagged(client):
    u = make_user(client, "agents2")
    h = u["headers"]
    r = client.post("/agents/run", json={"goal": "Organizza il lancio del prodotto Xyz"}, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["facts"] == []
    assert any(i["kind"] == "empty_plan" for i in data["critic_issues"])
    assert not data["critic_approved"]


def test_critic_catches_planted_unsourced_task(db_session, db_user):
    """Gate spec: il Critic individua errori inseriti di proposito."""
    user_id = db_user.id
    inp = CriticInput(
        goal="Pianifica il rilascio",
        facts=["Il progetto Mercurio usa PostgreSQL 16 con pgvector"],
        tasks=[
            PlanTask(title="Verificare la migrazione PostgreSQL del progetto", priority=1),
            PlanTask(title="Comprare il dominio example.com dal provider", priority=2),  # piantato
        ],
    )
    out = core.critique(db_session, user_id, inp)
    assert not out.approved
    assert any(i.kind == "unsourced_claim" and "example.com" in i.detail for i in out.issues)
    assert [t.title for t in out.verified_tasks] == ["Verificare la migrazione PostgreSQL del progetto"]


def test_critic_approves_clean_plan(db_session, db_user):
    inp = CriticInput(
        goal="Verifica i test",
        facts=["La libreria di test scelta per Mercurio e pytest"],
        tasks=[PlanTask(title="Eseguire la suite pytest del progetto", priority=1)],
    )
    out = core.critique(db_session, db_user.id, inp)
    assert out.approved


def test_agent_runs_isolation(client):
    u1 = make_user(client, "agentsA")
    u2 = make_user(client, "agentsB")
    client.post("/agents/run", json={"goal": "Qualunque obiettivo"}, headers=u1["headers"])
    assert client.get("/agents/runs", headers=u2["headers"]).json() == []


def test_timeout_limit_enforced(client, monkeypatch):
    """Con timeout 0 la pipeline si FERMA dopo il primo passo: non solo log."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "agent_timeout_s", 0.0)
    u = make_user(client, "limit-timeout")
    h = u["headers"]
    src = make_source(client, h)
    make_memory(client, h, src, "Il progetto Mercurio usa PostgreSQL 16")
    r = client.post("/agents/run", json={"goal": "Pianifica il rilascio di Mercurio"}, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["stopped_reason"] == "timeout"
    assert data["tasks"] == []
    assert not data["critic_approved"]


def test_step_limit_enforced(client, monkeypatch):
    """Con 2 passi massimi il Critic non parte: si ferma davvero."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "agent_max_steps", 2)
    u = make_user(client, "limit-steps")
    h = u["headers"]
    src = make_source(client, h)
    make_memory(client, h, src, "Il progetto Mercurio usa PostgreSQL 16")
    r = client.post("/agents/run", json={"goal": "Pianifica il rilascio di Mercurio"}, headers=h)
    data = r.json()
    assert data["stopped_reason"] == "step_limit"
    runs = client.get("/agents/runs", headers=h).json()
    assert {r["agent"] for r in runs} == {"researcher", "planner"}  # niente critic


def test_budget_limit_enforced(client, monkeypatch):
    """Budget negativo (gia sforato) ferma la pipeline dopo il primo passo."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "agent_budget_eur", -1.0)
    u = make_user(client, "limit-budget")
    h = u["headers"]
    src = make_source(client, h)
    make_memory(client, h, src, "Il progetto Mercurio usa PostgreSQL 16")
    r = client.post("/agents/run", json={"goal": "Pianifica il rilascio di Mercurio"}, headers=h)
    assert r.json()["stopped_reason"] == "budget"


def test_critic_flags_irrelevant_cited_memory(db_session, db_user):
    """Caso avversario: fatti presenti ma irrilevanti per l'attivita."""
    inp = CriticInput(
        goal="Pianifica il marketing",
        facts=["La ricetta del tiramisu della nonna richiede mascarpone e savoiardi"],
        tasks=[PlanTask(title="Preparare la campagna pubblicitaria del lancio", priority=1)],
    )
    out = core.critique(db_session, db_user.id, inp)
    assert not out.approved
    assert any(i.kind == "unsourced_claim" for i in out.issues)
    assert out.verified_tasks == []


def test_critic_flags_contradicted_task(db_session, db_user):
    """Caso avversario: attivita che contraddice un fatto noto."""
    inp = CriticInput(
        goal="Organizza la riunione",
        facts=["La riunione del progetto Mercurio e il martedi alle 10"],
        tasks=[PlanTask(title="Fissare la riunione del progetto Mercurio al giovedi alle 15", priority=1)],
    )
    out = core.critique(db_session, db_user.id, inp)
    assert not out.approved
    assert any(i.kind == "contradicts_fact" for i in out.issues)
    assert out.verified_tasks == []
