"""Implementazioni rule-based e locali: niente LLM a pagamento (vincolo costo
zero). Il Critic controlla che ogni attivita del piano poggi sulle memorie
recuperate: un'attivita senza appoggio nei fatti viene segnalata, non nascosta."""
import re
import time
import uuid

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AgentRun
from .schemas import (
    CriticInput,
    CriticIssue,
    CriticOutput,
    PlanInput,
    PlanOutput,
    PlanTask,
    ResearchInput,
    ResearchOutput,
)

MAX_STEPS = 3  # researcher -> planner -> critic
TIMEOUT_S = 20

_WORD = re.compile(r"[a-zàèéìòù0-9]{4,}", re.IGNORECASE)
_SPLIT = re.compile(r"\b(?:e poi|poi|dopo|quindi|infine|;|\.\s)\s*", re.IGNORECASE)


class BudgetExceeded(Exception):
    pass


def _log_run(db: Session, user_id: uuid.UUID, agent: str, inp, out, steps: int,
             start: float, status: str, error: str | None = None) -> AgentRun:
    run = AgentRun(
        user_id=user_id, agent=agent,
        input=inp.model_dump(), output=out.model_dump() if out is not None else {},
        steps=steps, duration_ms=int((time.monotonic() - start) * 1000),
        cost_eur=0.0, status=status, error=error,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def run_researcher(db: Session, settings: Settings, user_id: uuid.UUID,
                   inp: ResearchInput) -> ResearchOutput:
    from ..services.retrieval import search_memories
    from ..services.router import ModelRouter

    start = time.monotonic()
    router = ModelRouter(settings)
    qvec, _ = router.embed(inp.goal, db=db, user_id=user_id)
    hits = search_memories(db, settings, user_id, inp.goal, qvec, settings.retrieval_top_k)
    relevant = [h for h in hits if h.relevance >= settings.ask_min_relevance]
    out = ResearchOutput(goal=inp.goal, facts=[h.memory.content for h in relevant],
                         found=bool(relevant))
    _log_run(db, user_id, "researcher", inp, out, 1, start, "ok")
    return out


def run_planner(db: Session, user_id: uuid.UUID, inp: PlanInput) -> PlanOutput:
    start = time.monotonic()
    tasks: list[PlanTask] = []
    if inp.facts:
        parts = [p.strip() for p in _SPLIT.split(inp.goal) if p.strip()]
        if len(parts) <= 1:
            parts = [inp.goal.strip()]
        for i, part in enumerate(parts, start=1):
            tasks.append(PlanTask(title=part, priority=i))
    out = PlanOutput(goal=inp.goal, tasks=tasks, based_on_facts=bool(inp.facts))
    _log_run(db, user_id, "planner", inp, out, 1, start, "ok")
    return out


def _support_words(facts: list[str]) -> set[str]:
    words: set[str] = set()
    for f in facts:
        words |= {w.lower() for w in _WORD.findall(f)}
    return words


def critique(db: Session, user_id: uuid.UUID, inp: CriticInput) -> CriticOutput:
    """Il Critic ha l'ultima parola (spec 5D). Regole:
    - piano vuoto con fatti disponibili -> errore;
    - ogni attivita deve avere appoggio lessicale nei fatti (soglia minima):
      senza appoggio e un 'affermazione senza fonte' segnalata;
    - nessuna attivita inventata passa inosservata: resta nella risposta solo
      se segnalata."""
    start = time.monotonic()
    issues: list[CriticIssue] = []
    if not inp.tasks:
        issues.append(CriticIssue(kind="empty_plan",
                                  detail="Il Planner non ha prodotto attivita."))
    support = _support_words(inp.facts)
    verified: list[PlanTask] = []
    for t in inp.tasks:
        tw = {w.lower() for w in _WORD.findall(t.title)}
        overlap = tw & support
        if support and len(overlap) < max(1, len(tw) // 3):
            issues.append(CriticIssue(
                kind="unsourced_claim",
                detail=f"L'attivita '{t.title}' non trova appoggio nelle memorie recuperate."))
        else:
            verified.append(t)
    out = CriticOutput(approved=not issues, issues=issues, verified_tasks=verified)
    _log_run(db, user_id, "critic", inp, out, 1, start, "ok")
    return out
