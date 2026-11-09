"""Router /connectors/pairing: richieste di pairing dei canali dell'agente.

Quando un mittente non autorizzato scrive al bot Telegram o al numero
WhatsApp collegato, ATLAS non risponde MAI sul canale: registra solo una
richiesta in locale. Da qui il proprietario la vede e decide: approvare
(il contatto entra in allowlist, subito e ai prossimi avvii) o respingere.

Disponibile solo nell'app desktop (stack agente in processo): in server
mode ogni endpoint risponde 501, perche' li' i canali dell'agente si
governano dalla CLI (/pairing), non da questa API.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..errors import AtlasError
from ..models import User
from ..security import get_current_user
from ..services import telegram as tg

router = APIRouter(prefix="/connectors/pairing", tags=["pairing"])


def _bridge(user: User):
    bridge = tg.agent_bridge()
    if bridge is None:
        raise AtlasError(
            "pairing_unavailable",
            "Le richieste di pairing dei canali si gestiscono dalla app "
            "desktop (o dalla CLI dell'agente).",
            status=501)
    return bridge


class PairingRequestOut(BaseModel):
    channel: str
    sender: str
    display: str = ""
    ts: float


class PairingDecisionIn(BaseModel):
    channel: str
    sender: str


@router.get("", response_model=list[PairingRequestOut])
def pending(user: User = Depends(get_current_user)) -> list[dict]:
    return _bridge(user).pending()


@router.post("/approve", status_code=200)
def approve(body: PairingDecisionIn,
            user: User = Depends(get_current_user)) -> dict:
    entry = _bridge(user).approve(body.channel, body.sender)
    if entry is None:
        raise AtlasError("pairing_not_found",
                         "Richiesta di pairing non trovata (scaduta o gia' "
                         "decisa).", status=404)
    return {"approved": True, "channel": body.channel, "sender": body.sender}


@router.post("/reject", status_code=200)
def reject(body: PairingDecisionIn,
           user: User = Depends(get_current_user)) -> dict:
    if not _bridge(user).reject(body.channel, body.sender):
        raise AtlasError("pairing_not_found",
                         "Richiesta di pairing non trovata (scaduta o gia' "
                         "decisa).", status=404)
    return {"rejected": True, "channel": body.channel, "sender": body.sender}
