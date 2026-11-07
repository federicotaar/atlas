import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Event, Project, User
from ..schemas import EventIn, EventOut
from ..security import get_current_user
from .sources import require_source

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=EventOut, status_code=201)
def create_event(body: EventIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> EventOut:
    if body.project_id is not None:
        project = db.get(Project, body.project_id)
        if project is None or project.user_id != user.id:
            raise AtlasError("project_not_found", "Progetto non trovato.", status=404)
    if body.source_id is not None:
        require_source(db, user, body.source_id)
    event = Event(user_id=user.id, project_id=body.project_id, type=body.type,
                  payload=body.payload, source_id=body.source_id, event_at=body.event_at)
    db.add(event)
    db.commit()
    db.refresh(event)
    return EventOut.model_validate(event)


@router.get("", response_model=list[EventOut])
def list_events(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    type: str | None = None,
    project_id: uuid.UUID | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[EventOut]:
    q = db.query(Event).filter(Event.user_id == user.id)
    if type is not None:
        q = q.filter(Event.type == type)
    if project_id is not None:
        q = q.filter(Event.project_id == project_id)
    if from_ts is not None:
        q = q.filter(Event.event_at >= from_ts)
    if to_ts is not None:
        q = q.filter(Event.event_at <= to_ts)
    events = q.order_by(Event.event_at.desc()).limit(limit).all()
    return [EventOut.model_validate(e) for e in events]
