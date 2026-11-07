"""Action Engine fase 4 (spec 5F): connettori fittizi, anteprima esatta,
conferma esplicita per il livello 2, esecuzione registrata, annullamento,
chiavi di idempotenza. Gli agenti non eseguono mai direttamente: questa e
l'unica porta."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Connector, EngineAction, User
from ..schemas import ActionOut, ConnectorOut
from ..security import get_current_user
from ..services.actions import (
    authorize_connector,
    cancel_action,
    confirm_action,
    get_or_create_connector,
    request_action,
    revoke_connector,
)
from ..services.memory import log_action

router = APIRouter(tags=["actions"])


class ConnectorIn(BaseModel):
    kind: str


class ActionIn(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)


def _get_connector(db: Session, user: User, connector_id: uuid.UUID) -> Connector:
    conn = db.get(Connector, connector_id)
    if conn is None or conn.user_id != user.id:
        raise AtlasError("connector_not_found", "Connettore non trovato.", status=404)
    return conn


def _get_action(db: Session, user: User, action_id: uuid.UUID) -> EngineAction:
    action = db.get(EngineAction, action_id)
    if action is None or action.user_id != user.id:
        raise AtlasError("action_not_found", "Azione non trovata.", status=404)
    return action


@router.post("/connectors", response_model=ConnectorOut, status_code=201)
def create_connector(body: ConnectorIn, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> ConnectorOut:
    conn = get_or_create_connector(db, user.id, body.kind)
    log_action(db, user.id, "connector.created", {"kind": conn.kind})
    return ConnectorOut.model_validate(conn)


@router.get("/connectors", response_model=list[ConnectorOut])
def list_connectors(db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> list[ConnectorOut]:
    rows = db.query(Connector).filter(Connector.user_id == user.id).all()
    return [ConnectorOut.model_validate(c) for c in rows]


@router.post("/connectors/{connector_id}/authorize", response_model=ConnectorOut)
def authorize(connector_id: uuid.UUID, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> ConnectorOut:
    conn = _get_connector(db, user, connector_id)
    conn = authorize_connector(db, conn)
    log_action(db, user.id, "connector.authorized", {"kind": conn.kind})
    return ConnectorOut.model_validate(conn)


@router.post("/connectors/{connector_id}/revoke", response_model=ConnectorOut)
def revoke(connector_id: uuid.UUID, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> ConnectorOut:
    conn = _get_connector(db, user, connector_id)
    conn = revoke_connector(db, conn)
    log_action(db, user.id, "connector.revoked", {"kind": conn.kind})
    return ConnectorOut.model_validate(conn)


@router.post("/actions", response_model=ActionOut, status_code=201)
def create_action(body: ActionIn, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> ActionOut:
    action, created = request_action(db, user.id, body.kind, body.payload,
                                     body.idempotency_key)
    if created:
        log_action(db, user.id, f"action.{action.status}", {"kind": action.kind})
    return ActionOut.model_validate(action)


@router.get("/actions", response_model=list[ActionOut])
def list_actions(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> list[ActionOut]:
    rows = (db.query(EngineAction).filter(EngineAction.user_id == user.id)
            .order_by(EngineAction.created_at.desc()).limit(100).all())
    return [ActionOut.model_validate(a) for a in rows]


@router.get("/actions/{action_id}", response_model=ActionOut)
def get_action(action_id: uuid.UUID, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> ActionOut:
    return ActionOut.model_validate(_get_action(db, user, action_id))


@router.post("/actions/{action_id}/confirm", response_model=ActionOut)
def confirm(action_id: uuid.UUID, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> ActionOut:
    action = confirm_action(db, _get_action(db, user, action_id))
    log_action(db, user.id, "action.confirmed", {"kind": action.kind})
    return ActionOut.model_validate(action)


@router.post("/actions/{action_id}/cancel", response_model=ActionOut)
def cancel(action_id: uuid.UUID, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> ActionOut:
    action = cancel_action(db, _get_action(db, user, action_id))
    log_action(db, user.id, "action.cancelled", {"kind": action.kind})
    return ActionOut.model_validate(action)
