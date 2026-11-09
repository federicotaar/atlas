"""Router /connectors/telegram: collegamento bot, stato, disconnessione."""
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import get_current_user
from ..services import telegram as tg

router = APIRouter(prefix="/connectors/telegram", tags=["telegram"])


class TelegramConnectIn(BaseModel):
    bot_token: str = Field(min_length=20, max_length=100)


class TelegramStatusOut(BaseModel):
    connected: bool
    bot_username: str | None = None
    paired: bool = False
    deep_link: str | None = None
    pairing_expired: bool = False
    last_update_at: datetime | None = None
    # True = i messaggi inbound arrivano all'agente (app desktop); False =
    # pipeline classica v2.x (server mode o stack agente non attivo).
    agent: bool = False


def _status(db: Session, user: User) -> TelegramStatusOut:
    link = tg.get_link(db, user)
    if link is None:
        return TelegramStatusOut(connected=False,
                                 agent=tg.agent_bridge_active())
    return TelegramStatusOut(
        agent=tg.agent_bridge_active(),
        connected=True,
        bot_username=link.bot_username,
        paired=link.owner_chat_id is not None,
        deep_link=tg.deep_link(link),
        pairing_expired=tg.pairing_expired(link),
        last_update_at=link.last_update_at,
    )


@router.post("/connect", response_model=TelegramStatusOut, status_code=201)
def connect(body: TelegramConnectIn, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> TelegramStatusOut:
    tg.connect(db, get_settings(), user, body.bot_token)
    return _status(db, user)


@router.get("/status", response_model=TelegramStatusOut)
def status(db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> TelegramStatusOut:
    return _status(db, user)


@router.post("/pairing-code", response_model=TelegramStatusOut)
def rotate_pairing_code(db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)) -> TelegramStatusOut:
    tg.rotate_pairing_code(db, user)
    return _status(db, user)


@router.post("/disconnect", status_code=204)
def disconnect(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> Response:
    tg.disconnect(db, user)
    return Response(status_code=204)
