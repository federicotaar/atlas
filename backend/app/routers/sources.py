"""Router `/sources`: registro delle fonti.

Ogni memoria punta a una fonte (una chat, un documento, un connettore).
`require_source` verifica che la fonte appartenga all'utente prima di
legarla a nuovi dati; `get_or_create_chat_source` fornisce la fonte
standard delle conversazioni con l'assistente.
"""
from fastapi import APIRouter, Depends
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
def list_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[SourceOut]:
    sources = db.query(Source).filter(Source.user_id == user.id).order_by(Source.created_at).all()
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
