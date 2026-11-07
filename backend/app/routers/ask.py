"""Endpoint /ask: tutta la logica vive nell'orchestratore centrale (fase 1)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..schemas import AskIn, AskOut
from ..security import get_current_user
from ..services.orchestrator import handle_ask

router = APIRouter(tags=["ask"])


@router.post("/ask", response_model=AskOut)
def ask(body: AskIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> AskOut:
    return handle_ask(db, get_settings(), user, body.text, body.conversation_id)
