"""Endpoint fase 2: pipeline Researcher -> Planner -> Critic con limiti e
registro agent_runs. Il Critic ha l'ultima parola (spec 5D)."""
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents import core
from ..agents.schemas import CriticInput, PlanInput, ResearchInput
from ..config import get_settings
from ..db import get_db
from ..models import AgentRun, User
from ..schemas import AgentRunOut
from ..security import get_current_user

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentPipelineIn(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)


class AgentPipelineOut(BaseModel):
    goal: str
    facts: list[str]
    tasks: list[dict]
    critic_approved: bool
    critic_issues: list[dict]
    runs: list[str]  # id degli agent_runs registrati
    stopped_reason: str | None = None


@router.post("/run", response_model=AgentPipelineOut)
def run_pipeline(body: AgentPipelineIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> AgentPipelineOut:
    """Pipeline con limiti EFFETTIVI (spec 5D): passi, timeout, budget.

    Ogni agente e un passo. Se un limite scatta, la pipeline si ferma e lo
    dice (stopped_reason): i limiti non sono solo registrati, sono applicati.
    """
    settings = get_settings()
    start = time.monotonic()
    steps = 0

    def stopped(reason: str, detail: str, facts: list[str]) -> AgentPipelineOut:
        return AgentPipelineOut(goal=body.goal, facts=facts, tasks=[],
                                critic_approved=False,
                                critic_issues=[{"kind": reason, "detail": detail}],
                                runs=_run_ids(db, user), stopped_reason=reason)

    def check_limits(facts: list[str]) -> AgentPipelineOut | None:
        if steps >= settings.agent_max_steps:
            return stopped("step_limit", f"Limite di {settings.agent_max_steps} passi raggiunto.", facts)
        if time.monotonic() - start > settings.agent_timeout_s:
            return stopped("timeout", f"Timeout di {settings.agent_timeout_s}s superato.", facts)
        spent = db.query(AgentRun).filter(AgentRun.user_id == user.id).with_entities(
            AgentRun.cost_eur).all()
        if sum(c for (c,) in spent) > settings.agent_budget_eur:
            return stopped("budget", f"Budget di {settings.agent_budget_eur} EUR superato.", facts)
        return None

    research = core.run_researcher(db, settings, user.id, ResearchInput(goal=body.goal))
    steps += 1
    if (hit := check_limits(research.facts)) is not None:
        return hit

    plan = core.run_planner(db, user.id, PlanInput(goal=body.goal, facts=research.facts))
    steps += 1
    if (hit := check_limits(research.facts)) is not None:
        return hit

    verdict = core.critique(db, user.id, CriticInput(
        goal=body.goal, facts=research.facts, tasks=plan.tasks))
    steps += 1

    return AgentPipelineOut(
        goal=body.goal,
        facts=research.facts,
        tasks=[t.model_dump() for t in verdict.verified_tasks],
        critic_approved=verdict.approved,
        critic_issues=[i.model_dump() for i in verdict.issues],
        runs=_run_ids(db, user),
    )


def _run_ids(db: Session, user: User) -> list[str]:
    rows = (db.query(AgentRun).filter(AgentRun.user_id == user.id)
            .order_by(AgentRun.created_at.desc()).limit(3).all())
    return [str(r.id) for r in rows]


@router.get("/runs", response_model=list[AgentRunOut])
def list_runs(db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> list[AgentRunOut]:
    rows = (db.query(AgentRun).filter(AgentRun.user_id == user.id)
            .order_by(AgentRun.created_at.desc()).limit(50).all())
    return [AgentRunOut.model_validate(r) for r in rows]
