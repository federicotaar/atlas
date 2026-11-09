"""Dashboard (fase 5, spec 5I).

Pannelli aggregati: stato dei progetti, attivita imminenti, eventi recenti,
previsioni e scadenze, agenti attivi, automazioni, memorie importanti,
richieste di autorizzazione. Regola d'uscita della fase: ogni elemento
mostra da quali dati deriva (campo derived_from con gli id sorgente).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import AgentRun, Connector, EngineAction, Event, Memory, Prediction, Project
from .predictions import due_predictions
from .predictions import metrics as prediction_metrics

IMPORTANT_CONFIDENCE = 0.85


_agent_jobs_provider = None


def set_agent_jobs_provider(provider) -> None:
    """App desktop: funzione che elenca i job dello scheduler dell'agente
    (automazioni del nuovo stack). None in server mode: il pannello mostra
    solo i connettori classici."""
    global _agent_jobs_provider
    _agent_jobs_provider = provider


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def build_dashboard(db: Session, user_id: uuid.UUID,
                    now: datetime | None = None) -> dict:
    now = _aware(now or datetime.now(timezone.utc))

    # Stato dei progetti
    projects = (db.query(Project).filter(Project.user_id == user_id)
                .order_by(Project.created_at.asc()).all())
    project_items = []
    for p in projects:
        last = (db.query(Event)
                .filter(Event.user_id == user_id, Event.project_id == p.id)
                .order_by(Event.event_at.desc()).first())
        ev_count = (db.query(Event)
                    .filter(Event.user_id == user_id, Event.project_id == p.id)
                    .count())
        project_items.append({
            "project_id": str(p.id), "name": p.name, "status": p.status,
            "target_date": p.target_date.isoformat() if p.target_date else None,
            "events_count": ev_count,
            "last_event_at": last.event_at.isoformat() if last else None,
            "derived_from": {"project_id": str(p.id),
                             "last_event_id": str(last.id) if last else None},
        })

    # Attivita imminenti: previsioni aperte con scadenza entro 14 giorni
    # e obiettivi di progetto entro 30 giorni
    upcoming_preds = (db.query(Prediction)
                      .filter(Prediction.user_id == user_id,
                              Prediction.status == "open",
                              Prediction.verify_at <= now + timedelta(days=14))
                      .order_by(Prediction.verify_at.asc()).all())
    upcoming_projects = [p for p in projects
                         if p.target_date is not None
                         and now.date() <= p.target_date <= (now + timedelta(days=30)).date()]
    upcoming = {
        "predictions": [
            {"id": str(pr.id), "question": pr.question,
             "verify_at": pr.verify_at.isoformat(),
             "derived_from": {"prediction_id": str(pr.id)}}
            for pr in upcoming_preds
        ],
        "project_deadlines": [
            {"project_id": str(p.id), "name": p.name,
             "target_date": p.target_date.isoformat(),
             "derived_from": {"project_id": str(p.id)}}
            for p in upcoming_projects
        ],
    }

    # Eventi recenti
    recent = (db.query(Event).filter(Event.user_id == user_id)
              .order_by(Event.event_at.desc()).limit(10).all())
    recent_events = [
        {"id": str(e.id), "type": e.type, "event_at": e.event_at.isoformat(),
         "project_id": str(e.project_id) if e.project_id else None,
         "derived_from": {"event_id": str(e.id),
                          "source_id": str(e.source_id) if e.source_id else None}}
        for e in recent
    ]

    # Previsioni e scadenze
    due = due_predictions(db, user_id, now=now)
    pred_counts = {
        "open": db.query(Prediction).filter(Prediction.user_id == user_id,
                                            Prediction.status == "open").count(),
        "verified": db.query(Prediction).filter(Prediction.user_id == user_id,
                                                Prediction.status == "verified").count(),
        "due_now": len(due),
    }
    predictions_panel = {
        **pred_counts,
        "metrics": prediction_metrics(db, user_id),
        "due_items": [
            {"id": str(pr.id), "question": pr.question,
             "verify_at": pr.verify_at.isoformat(),
             "derived_from": {"prediction_id": str(pr.id)}}
            for pr in due[:10]
        ],
    }

    # Agenti attivi
    day_ago = now - timedelta(hours=24)
    runs_recent = (db.query(AgentRun)
                   .filter(AgentRun.user_id == user_id,
                           AgentRun.created_at >= day_ago)
                   .order_by(AgentRun.created_at.desc()).all())
    last_runs = (db.query(AgentRun).filter(AgentRun.user_id == user_id)
                 .order_by(AgentRun.created_at.desc()).limit(5).all())
    agents_panel = {
        "active_last_24h": len(runs_recent),
        "last_runs": [
            {"id": str(r.id), "agent": r.agent, "status": r.status,
             "created_at": r.created_at.isoformat(),
             "derived_from": {"agent_run_id": str(r.id)}}
            for r in last_runs
        ],
    }

    # Automazioni: connettori e azioni del motore
    connectors = (db.query(Connector).filter(Connector.user_id == user_id).all())
    action_rows = (db.query(EngineAction.status, EngineAction.id)
                   .filter(EngineAction.user_id == user_id).all())
    action_counts: dict[str, int] = {}
    for status, _ in action_rows:
        action_counts[status] = action_counts.get(status, 0) + 1
    # Job dello scheduler dell'agente (app desktop): le automazioni del
    # nuovo stack (promemoria, ricorrenze) create dalle chat dell'agente.
    agent_jobs = []
    if _agent_jobs_provider is not None:
        try:
            agent_jobs = _agent_jobs_provider() or []
        except Exception:
            agent_jobs = []
    automations_panel = {
        "connectors": [
            {"id": str(c.id), "kind": c.kind, "status": c.status,
             "derived_from": {"connector_id": str(c.id)}}
            for c in connectors
        ],
        "agent_jobs": [
            {**j, "derived_from": {"agent_job_id": str(j.get("id"))}}
            for j in agent_jobs
        ],
        "engine_actions_by_status": action_counts,
    }

    # Memorie importanti: alta confidenza, non sostituite
    important = (db.query(Memory)
                 .filter(Memory.user_id == user_id,
                         Memory.confidence >= IMPORTANT_CONFIDENCE,
                         Memory.superseded_by.is_(None))
                 .order_by(Memory.recorded_at.desc()).limit(10).all())
    memories_panel = [
        {"id": str(m.id), "content": m.content, "kind": m.kind,
         "confidence": m.confidence, "recorded_at": m.recorded_at.isoformat(),
         "derived_from": {"memory_id": str(m.id), "source_id": str(m.source_id)}}
        for m in important
    ]

    # Richieste di autorizzazione: connettori in attesa e azioni da confermare
    pending_connectors = [c for c in connectors if c.status == "pending"]
    pending_actions = (db.query(EngineAction)
                       .filter(EngineAction.user_id == user_id,
                               EngineAction.status == "pending_confirm")
                       .order_by(EngineAction.created_at.asc()).all())
    auth_requests = {
        "pending_connectors": [
            {"id": str(c.id), "kind": c.kind,
             "derived_from": {"connector_id": str(c.id)}}
            for c in pending_connectors
        ],
        "pending_confirm_actions": [
            {"id": str(a.id), "kind": a.kind, "level": a.level,
             "created_at": a.created_at.isoformat(),
             "derived_from": {"engine_action_id": str(a.id)}}
            for a in pending_actions
        ],
    }

    return {
        "generated_at": now.isoformat(),
        "provenance_rule": ("ogni elemento espone derived_from con gli id dei "
                            "dati da cui deriva (spec fase 5I)"),
        "panels": {
            "projects": project_items,
            "upcoming": upcoming,
            "recent_events": recent_events,
            "predictions": predictions_panel,
            "agents": agents_panel,
            "automations": automations_panel,
            "important_memories": memories_panel,
            "authorization_requests": auth_requests,
        },
    }
