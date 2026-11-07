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
    settings = get_settings()
    start = time.monotonic()

    research = core.run_researcher(db, settings, user.id, ResearchInput(goal=body.goal))
    if time.monotonic() - start > core.TIMEOUT_S:
        return AgentPipelineOut(goal=body.goal, facts=research.facts, tasks=[],
                                critic_approved=False,
                                critic_issues=[{"kind": "timeout",
                                                "detail": "Timeout prima del Planner."}],
                                runs=_run_ids(db, user), stopped_reason="timeout")

    plan = core.run_planner(db, user.id, PlanInput(goal=body.goal, facts=research.facts))
    verdict = core.critique(db, user.id, CriticInput(
        goal=body.goal, facts=research.facts, tasks=plan.tasks))

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
