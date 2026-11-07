"""Conversazioni salvate (fase 1): storico completo degli scambi /ask."""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Conversation, Message, User
from ..schemas import ConversationDetailOut, ConversationOut, MessageOut
from ..security import get_current_user

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> list[ConversationOut]:
    rows = (
        db.query(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.user_id == user.id)
        .group_by(Conversation.id)
        .order_by(Conversation.created_at.desc())
        .all()
    )
    return [ConversationOut(id=c.id, title=c.title, created_at=c.created_at, message_count=n)
            for c, n in rows]


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> ConversationDetailOut:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise AtlasError("conversation_not_found", "Conversazione non trovata.", status=404)
    msgs = (db.query(Message).filter(Message.conversation_id == conv.id)
            .order_by(Message.created_at).all())
    return ConversationDetailOut(
        id=conv.id, title=conv.title, created_at=conv.created_at,
        messages=[MessageOut.model_validate(m) for m in msgs],
    )
