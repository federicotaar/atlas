"""Router Time Machine (fase 5, spec 5H): cronologia, stato a una data,
confronto tra periodi, obiettivi abbandonati, attivita ricorrenti. Le lacune
sono sempre esplicitate, mai riempite."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Project, User
from ..security import get_current_user
from ..services import timemachine

router = APIRouter(prefix="/timemachine", tags=["timemachine"])


def _parse(dt: datetime | None, name: str) -> datetime:
    if dt is None:
        raise AtlasError("missing_param", f"Parametro '{name}' obbligatorio.", status=422)
    return dt


def _check_project(db: Session, user: User, project_id: uuid.UUID | None) -> None:
    if project_id is None:
        return
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise AtlasError("project_not_found", "Progetto non trovato.", status=404)


@router.get("/timeline")
def get_timeline(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    project_id: uuid.UUID | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    start = _parse(from_ts, "from")
    end = _parse(to_ts, "to")
    if end <= start:
        raise AtlasError("bad_range", "'to' deve essere dopo 'from'.", status=422)
    _check_project(db, user, project_id)
    return timemachine.timeline(db, user.id, start, end, project_id=project_id,
                                limit=limit)


@router.get("/state-at")
def get_state_at(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    at: datetime | None = None,
    project_id: uuid.UUID | None = None,
) -> dict:
    at = _parse(at, "at")
    _check_project(db, user, project_id)
    return timemachine.state_at(db, user.id, at, project_id=project_id)


@router.get("/compare")
def get_compare(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    a_start: datetime | None = None,
    a_end: datetime | None = None,
    b_start: datetime | None = None,
    b_end: datetime | None = None,
) -> dict:
    a_start = _parse(a_start, "a_start")
    a_end = _parse(a_end, "a_end")
    b_start = _parse(b_start, "b_start")
    b_end = _parse(b_end, "b_end")
    if a_end <= a_start or b_end <= b_start:
        raise AtlasError("bad_range", "Ogni periodo deve avere fine dopo l'inizio.",
                         status=422)
    return timemachine.compare_periods(db, user.id, a_start, a_end, b_start, b_end)


@router.get("/abandoned-goals")
def get_abandoned_goals(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    idle_days: int = Query(default=14, ge=1, le=365),
) -> dict:
    return timemachine.abandoned_goals(db, user.id, idle_days=idle_days)


@router.get("/recurring")
def get_recurring(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    min_occurrences: int = Query(default=3, ge=2, le=100),
) -> dict:
    return timemachine.recurring_activities(db, user.id,
                                            min_occurrences=min_occurrences)
