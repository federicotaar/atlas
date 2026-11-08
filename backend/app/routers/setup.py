"""Primo avvio desktop: stato migrazione dal Postgres precedente.

Regole (proprietario): mai partire in silenzio come profilo nuovo quando una
origine configurata e' irraggiungibile; mai importare sopra dati esistenti;
scelta esplicita dell'utente per importare O continuare senza migrare."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..errors import AtlasError
from ..models import User
from ..security import get_current_user
from ..services import migrate
from ..services.memory import log_action

router = APIRouter(prefix="/setup", tags=["setup"])


class MigrationStatusOut(BaseModel):
    state: str  # not_configured | unreachable | empty | has_data | imported | dismissed
    counts: dict = {}
    error: str = ""


def _recorded_state(db: Session, user: User) -> str | None:
    from ..models import ActionLog

    row = (
        db.query(ActionLog)
        .filter(ActionLog.user_id == user.id,
                ActionLog.action.in_(["migration.imported", "migration.dismissed"]))
        .order_by(ActionLog.created_at.desc())
        .first()
    )
    return row.action.split(".", 1)[1] if row else None


@router.get("/migration-status", response_model=MigrationStatusOut)
def migration_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    recorded = _recorded_state(db, user)
    if recorded:
        return MigrationStatusOut(state=recorded)
    legacy = get_settings().legacy_database_url
    if not legacy:
        return MigrationStatusOut(state="not_configured")
    probe = migrate.probe(legacy)
    return MigrationStatusOut(state=probe.state, counts=probe.counts, error=probe.error)


@router.post("/migration-import", response_model=MigrationStatusOut)
def migration_import(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Importa dal Postgres legacy SOLO se il profilo corrente e' vuoto
    (l'utente corrente e' l'unico e non ha memorie) e l'origine e' raggiungibile."""
    legacy = get_settings().legacy_database_url
    if not legacy:
        raise AtlasError("not_configured", "Nessuna origine configurata.", status=400)
    probe = migrate.probe(legacy)
    if probe.state == "unreachable":
        raise AtlasError("legacy_unreachable",
                         f"Dati esistenti non raggiungibili: {probe.error}", status=503)
    payload = migrate.export_payload(legacy)
    try:
        inserted = migrate.import_payload(db, payload)
    except ValueError as exc:
        raise AtlasError("target_not_empty", str(exc), status=409) from exc
    log_action(db, user.id, "migration.imported", {"tables": inserted})
    db.commit()
    return MigrationStatusOut(state="imported", counts=inserted)


@router.post("/migration-dismiss", response_model=MigrationStatusOut)
def migration_dismiss(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Scelta esplicita: continua SENZA migrare (registrato, non silenzioso)."""
    log_action(db, user.id, "migration.dismissed", {})
    db.commit()
    return MigrationStatusOut(state="dismissed")
