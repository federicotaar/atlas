"""Router `/sources`: registro delle fonti.

Ogni memoria punta a una fonte (una chat, un documento, un connettore).
`require_source` verifica che la fonte appartenga all'utente prima di
legarla a nuovi dati; `get_or_create_chat_source` fornisce la fonte
standard delle conversazioni con l'assistente.
"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Source, User
from ..schemas import SourceIn, SourceOut
from ..security import get_current_user

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", response_model=SourceOut, status_code=201)
def create_source(body: SourceIn, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> SourceOut:
    source = Source(user_id=user.id, kind=body.kind, label=body.label)
    db.add(source)
    db.commit()
    db.refresh(source)
    return SourceOut.model_validate(source)


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                 kind: str | None = None,
                 limit: int | None = Query(default=None, ge=1, le=1000),
                 offset: int = Query(default=0, ge=0)) -> list[SourceOut]:
    q = db.query(Source).filter(Source.user_id == user.id)
    if kind is not None:
        q = q.filter(Source.kind == kind)
    q = q.order_by(Source.created_at).offset(offset)
    if limit is not None:
        q = q.limit(limit)
    sources = q.all()
    return [SourceOut.model_validate(s) for s in sources]


def get_or_create_chat_source(db: Session, user: User) -> Source:
    source = (
        db.query(Source)
        .filter(Source.user_id == user.id, Source.kind == "chat", Source.label == "chat")
        .one_or_none()
    )
    if source is None:
        source = Source(user_id=user.id, kind="chat", label="chat")
        db.add(source)
        db.commit()
        db.refresh(source)
    return source


def require_source(db: Session, user: User, source_id) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.user_id != user.id:
        raise AtlasError("source_not_found", "Fonte non trovata.", status=404)
    return source


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> SourceOut:
    return SourceOut.model_validate(require_source(db, user, source_id))


@router.get("/{source_id}/usage")
def source_usage(source_id: uuid.UUID, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict:
    from ..models import Memory, Event
    require_source(db, user, source_id)
    q = db.query(Memory).filter(Memory.user_id == user.id, Memory.source_id == source_id)
    return {"memories": q.count(),
            "active_memories": q.filter(Memory.superseded_by.is_(None)).count(),
            "events": db.query(Event).filter(Event.user_id == user.id, Event.source_id == source_id).count()}
