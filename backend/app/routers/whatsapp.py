"""Router /connectors/whatsapp: collegamento linked-device, QR, stato,
disconnessione. Rotta non ufficiale: l'utente e' avvisato del rischio
restrizioni/ban e il pairing e' sempre un suo scan esplicito."""
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import get_current_user
from ..services import whatsapp as wa

router = APIRouter(prefix="/connectors/whatsapp", tags=["whatsapp"])


class WhatsAppStatusOut(BaseModel):
    connected: bool
    linked: bool = False
    jid: str | None = None
    pushname: str | None = None
    qr_state: str = "idle"  # idle | qr | paired | expired
    qr: str | None = None
    last_event_at: datetime | None = None
    bridge_available: bool = False


def _status(db: Session, user: User) -> WhatsAppStatusOut:
    settings = get_settings()
    link = wa.status(db, settings, user)
    if link is None:
        return WhatsAppStatusOut(connected=False,
                                 bridge_available=wa.bridge_available(settings))
    qr = wa.qr_state(db, settings, user)
    return WhatsAppStatusOut(
        connected=True,
        linked=link.jid is not None,
        jid=link.jid,
        pushname=link.pushname,
        qr_state=qr.get("state", "idle"),
        qr=qr.get("qr"),
        last_event_at=link.last_event_at,
        bridge_available=wa.bridge_available(settings),
    )


@router.post("/connect", response_model=WhatsAppStatusOut, status_code=201)
def connect(db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> WhatsAppStatusOut:
    wa.connect(db, get_settings(), user)
    return _status(db, user)


@router.get("/status", response_model=WhatsAppStatusOut)
def status(db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> WhatsAppStatusOut:
    return _status(db, user)


@router.post("/disconnect", status_code=204)
def disconnect(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> Response:
    wa.disconnect(db, get_settings(), user)
    return Response(status_code=204)
