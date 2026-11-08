"""Primo avvio desktop: stato migrazione dal Postgres precedente.

Regole (proprietario): mai partire in silenzio come profilo nuovo quando una
origine configurata e' irraggiungibile; mai importare sopra dati esistenti;
scelta esplicita dell'utente per importare O continuare senza migrare."""
import os

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
    # Solo su import: quante credenziali BYOK migrate NON si decifrano con la
    # chiave di questa installazione (da re-inserire nell'app; mai in chiaro).
    undecryptable_credentials: int | None = None


class MigrationProbeOut(BaseModel):
    """Esito del rilevamento SU RICHIESTA (voce in-app): ignora lo stato
    registrato e riprova la fonte. 'none' = nessuna fonte nota trovata;
    'unreachable' = fonte nota ma non raggiungibile (mai spacciato per
    'nessun dato')."""
    source: str  # configured | detected | none
    state: str   # has_data | empty | unreachable | none
    counts: dict = {}
    error: str = ""
    recorded: str | None = None  # imported | dismissed | None


def _legacy_source() -> str:
    """Fonte legacy: configurata (env, priorita' assoluta) oppure rilevata.

    Il rilevamento gira SOLO in modalita' desktop e prova esclusivamente i
    default documentati del docker-compose 2.0.x distribuito
    (migrate.LEGACY_AUTODETECT_CANDIDATES): niente scansione di file, niente
    credenziali indovinate. Una fonte rilevata conta solo se raggiungibile e
    con dati: un'installazione nuova non vede mai il banner e un Postgres
    spento non genera falsi allarmi."""
    configured = get_settings().legacy_database_url
    if configured:
        return configured
    if os.environ.get("ATLAS_DESKTOP") == "1":
        return migrate.autodetect_legacy_url() or ""
    return ""


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
    legacy = _legacy_source()
    if not legacy:
        return MigrationStatusOut(state="not_configured")
    probe = migrate.probe(legacy)
    return MigrationStatusOut(state=probe.state, counts=probe.counts, error=probe.error)


@router.post("/migration-probe", response_model=MigrationProbeOut)
def migration_probe(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Rilevamento on-demand dalla voce "Importa dati da una versione
    precedente": riprova la fonte anche dopo un dismiss, senza scrivere
    nulla. Distingue sempre 'nessuna fonte nota' da 'trovata ma spenta'."""
    recorded = _recorded_state(db, user)
    configured = get_settings().legacy_database_url
    if configured:
        result = migrate.probe(configured)
        return MigrationProbeOut(source="configured", state=result.state,
                                 counts=result.counts, error=result.error,
                                 recorded=recorded)
    if os.environ.get("ATLAS_DESKTOP") == "1":
        url, result = migrate.detect_legacy()
        if url is not None and result is not None:
            return MigrationProbeOut(source="detected", state=result.state,
                                     counts=result.counts, error=result.error,
                                     recorded=recorded)
    return MigrationProbeOut(source="none", state="none", recorded=recorded)


@router.post("/migration-import", response_model=MigrationStatusOut)
def migration_import(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Importa dal Postgres legacy SOLO se il profilo corrente e' vuoto
    (l'utente corrente e' l'unico e non ha memorie) e l'origine e' raggiungibile."""
    legacy = _legacy_source()
    if not legacy:
        raise AtlasError("not_configured", "Nessuna origine configurata.", status=400)
    probe = migrate.probe(legacy)
    if probe.state == "unreachable":
        raise AtlasError("legacy_unreachable",
                         f"Dati esistenti non raggiungibili: {probe.error}", status=503)
    payload = migrate.export_payload(legacy)
    try:
        inserted, _uids, undecryptable = migrate.import_payload(
            db, payload, get_settings().credentials_key,
            legacy_keys=migrate.LEGACY_KNOWN_CREDENTIALS_KEYS)
    except ValueError as exc:
        raise AtlasError("target_not_empty", str(exc), status=409) from exc
    # Transazione unica in import_payload: sostituzione bootstrap + dati +
    # indici + audit migration.imported (sugli utenti importati). Qualunque
    # errore lascia bootstrap e origine intatti e l'import e' ritentabile.
    return MigrationStatusOut(state="imported", counts=inserted,
                              undecryptable_credentials=undecryptable)


@router.post("/migration-dismiss", response_model=MigrationStatusOut)
def migration_dismiss(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Scelta esplicita: continua SENZA migrare (registrato, non silenzioso)."""
    log_action(db, user.id, "migration.dismissed", {})
    db.commit()
    return MigrationStatusOut(state="dismissed")
