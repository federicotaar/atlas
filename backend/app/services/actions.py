"""Action Engine (fase 4, spec 5F).

Ogni azione passa per: identificazione utente (dependency), verifica permessi
(connettore autorizzato per il kind), anteprima esatta, conferma esplicita
per il livello 2, esecuzione, registrazione dell'esito, annullamento quando
tecnicamente possibile. Chiave di idempotenza per evitare doppie esecuzioni.
Una bozza non e un invio: non esiste alcuna operazione di invio email.
In questa fase i connettori sono fittizi (dati sintetici): nessun account reale.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..errors import AtlasError
from ..models import Connector, EngineAction, Memory, MockCalendarEvent, MockEmailDraft, Source

CONNECTOR_KINDS = {"calendar_mock", "email_mock"}

# kind -> (livello, connettore richiesto o None)
ACTION_KINDS: dict[str, tuple[int, str | None]] = {
    "calendar.list_events": (0, "calendar_mock"),
    "calendar.create_event": (1, "calendar_mock"),
    "tasks.add": (1, None),
    "email.create_draft": (2, "email_mock"),
}

FIXTURE_EVENTS = [
    ("Allenamento calcio", 1, 18, 2),
    ("Studio - matematica", 2, 15, 2),
    ("Chiamata con il gruppo di progetto", 4, 20, 1),
]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def get_or_create_connector(db: Session, user_id: uuid.UUID, kind: str) -> Connector:
    if kind not in CONNECTOR_KINDS:
        raise AtlasError("unknown_connector", f"Connettore sconosciuto: {kind}.")
    conn = (db.query(Connector)
            .filter(Connector.user_id == user_id, Connector.kind == kind)
            .one_or_none())
    if conn is None:
        conn = Connector(user_id=user_id, kind=kind)
        db.add(conn)
        db.commit()
        db.refresh(conn)
    return conn


def authorize_connector(db: Session, conn: Connector) -> Connector:
    """Consenso una tantum per connettore. Sul connettore calendario fittizio
    semina eventi sintetici; nessun dato reale viene toccato."""
    conn.status = "authorized"
    conn.authorized_at = datetime.now(timezone.utc)
    if conn.kind == "calendar_mock":
        existing = (db.query(MockCalendarEvent)
                    .filter(MockCalendarEvent.user_id == conn.user_id,
                            MockCalendarEvent.source == "fixture")
                    .count())
        if existing == 0:
            now = datetime.now(timezone.utc)
            for title, days, hour, dur in FIXTURE_EVENTS:
                start = (now + timedelta(days=days)).replace(hour=hour, minute=0,
                                                             second=0, microsecond=0)
                db.add(MockCalendarEvent(user_id=conn.user_id, title=title,
                                         starts_at=start, ends_at=start + timedelta(hours=dur),
                                         source="fixture"))
    db.commit()
    db.refresh(conn)
    return conn


def revoke_connector(db: Session, conn: Connector) -> Connector:
    conn.status = "revoked"
    db.commit()
    db.refresh(conn)
    return conn


def _authorized_connector(db: Session, user_id: uuid.UUID, kind: str) -> Connector:
    conn = (db.query(Connector)
            .filter(Connector.user_id == user_id, Connector.kind == kind)
            .one_or_none())
    if conn is None or conn.status != "authorized":
        raise AtlasError(
            "connector_not_authorized",
            f"Il connettore {kind} non e autorizzato: il permesso va dato una volta "
            "per connettore prima di usarlo.", status=403)
    return conn


def _preview(kind: str, payload: dict) -> dict:
    """Anteprima esatta di cio che l'azione fara."""
    if kind == "calendar.list_events":
        return {"description": "Legge gli eventi del calendario (connettore fittizio)."}
    if kind == "calendar.create_event":
        return {"description": "Crea un evento personale nel calendario (fittizio).",
                "event": {"title": payload.get("title"),
                          "starts_at": payload.get("starts_at"),
                          "ends_at": payload.get("ends_at")}}
    if kind == "tasks.add":
        return {"description": "Aggiunge un'attivita alla memoria.",
                "task": {"content": payload.get("content")}}
    if kind == "email.create_draft":
        return {"description": "Crea una BOZZA email (fittizia). Una bozza non e un invio.",
                "draft": {"to": payload.get("to"),
                          "subject": payload.get("subject"),
                          "body": payload.get("body")}}
    raise AtlasError("unknown_action", f"Azione sconosciuta: {kind}.")


def _execute(db: Session, action: EngineAction) -> dict:
    """Esegue e restituisce l'esito registrato."""
    user_id = action.user_id
    payload = action.payload
    if action.kind == "calendar.list_events":
        events = (db.query(MockCalendarEvent)
                  .filter(MockCalendarEvent.user_id == user_id,
                          MockCalendarEvent.cancelled.is_(False))
                  .order_by(MockCalendarEvent.starts_at).all())
        return {"events": [{"id": str(e.id), "title": e.title,
                            "starts_at": _aware(e.starts_at).isoformat(),
                            "ends_at": _aware(e.ends_at).isoformat(),
                            "source": e.source} for e in events]}
    if action.kind == "calendar.create_event":
        ev = MockCalendarEvent(
            user_id=user_id, title=payload["title"],
            starts_at=_aware(datetime.fromisoformat(payload["starts_at"])),
            ends_at=_aware(datetime.fromisoformat(payload["ends_at"])),
            source="created", created_action_id=action.id)
        db.add(ev)
        db.flush()
        return {"event_id": str(ev.id)}
    if action.kind == "tasks.add":
        src = (db.query(Source)
               .filter(Source.user_id == user_id, Source.kind == "connector",
                       Source.label == "action-engine")
               .one_or_none())
        if src is None:
            src = Source(user_id=user_id, kind="connector", label="action-engine")
            db.add(src)
            db.flush()
        mem = Memory(user_id=user_id, source_id=src.id, kind="task",
                     content=payload["content"], origin="stated", confidence=1.0)
        db.add(mem)
        db.flush()
        return {"memory_id": str(mem.id)}
    if action.kind == "email.create_draft":
        draft = MockEmailDraft(user_id=user_id, to_addr=payload["to"],
                               subject=payload["subject"], body=payload["body"],
                               created_action_id=action.id)
        db.add(draft)
        db.flush()
        return {"draft_id": str(draft.id), "note": "Bozza creata. Non inviata."}
    raise AtlasError("unknown_action", f"Azione sconosciuta: {action.kind}.")


def _validate_payload(kind: str, payload: dict) -> None:
    required = {
        "calendar.create_event": ("title", "starts_at", "ends_at"),
        "tasks.add": ("content",),
        "email.create_draft": ("to", "subject", "body"),
    }.get(kind, ())
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise AtlasError("invalid_payload",
                         f"Campi mancanti per {kind}: {', '.join(missing)}.")


def request_action(db: Session, user_id: uuid.UUID, kind: str,
                   payload: dict, idempotency_key: str) -> tuple[EngineAction, bool]:
    """Registra una richiesta d'azione. Restituisce (azione, creata_ora):
    con la stessa chiave di idempotenza restituisce l'azione esistente senza
    rieseguire nulla."""
    if kind not in ACTION_KINDS:
        raise AtlasError("unknown_action",
                         f"Azione sconosciuta: {kind}. Nessuna azione di invio email esiste.")
    existing = (db.query(EngineAction)
                .filter(EngineAction.user_id == user_id,
                        EngineAction.idempotency_key == idempotency_key)
                .one_or_none())
    if existing is not None:
        return existing, False

    level, connector_kind = ACTION_KINDS[kind]
    _validate_payload(kind, payload)
    connector = _authorized_connector(db, user_id, connector_kind) if connector_kind else None

    action = EngineAction(user_id=user_id, connector_id=connector.id if connector else None,
                          level=level, kind=kind, payload=payload,
                          idempotency_key=idempotency_key,
                          preview=_preview(kind, payload))
    db.add(action)
    db.flush()

    now = datetime.now(timezone.utc)
    if level == 2:
        # anteprima e conferma esplicita ogni volta: mai eseguire qui
        action.status = "pending_confirm"
        db.commit()
        db.refresh(action)
        return action, True

    # livelli 0 e 1: esegue e riporta
    try:
        action.result = _execute(db, action)
        action.status = "executed"
        action.executed_at = now
    except AtlasError:
        action.status = "failed"
        db.commit()
        raise
    db.commit()
    db.refresh(action)
    return action, True


def confirm_action(db: Session, action: EngineAction) -> EngineAction:
    """Conferma esplicita: solo il livello 2 in attesa puo essere confermato.
    L'esito e 'riuscito' solo dopo l'esecuzione verificata."""
    if action.level != 2 or action.status != "pending_confirm":
        raise AtlasError("not_confirmable",
                         "Solo un'azione di livello 2 in attesa di conferma puo essere confermata.",
                         status=409)
    now = datetime.now(timezone.utc)
    action.confirmed_at = now
    action.result = _execute(db, action)
    action.status = "executed"
    action.executed_at = now
    db.commit()
    db.refresh(action)
    return action


def cancel_action(db: Session, action: EngineAction) -> EngineAction:
    """Annullamento quando tecnicamente possibile:
    - livello 2 in attesa: cancella la richiesta, nulla e stato eseguito;
    - livello 2 eseguita (bozza): la bozza viene eliminata;
    - livello 1 eseguita: la modifica reversibile viene annullata."""
    now = datetime.now(timezone.utc)
    if action.status == "pending_confirm":
        action.status = "cancelled"
        action.cancelled_at = now
        db.commit()
        db.refresh(action)
        return action
    if action.status != "executed":
        raise AtlasError("not_cancellable",
                         "Solo un'azione in attesa o eseguita puo essere annullata.", status=409)

    if action.kind == "calendar.create_event":
        ev = db.get(MockCalendarEvent, uuid.UUID(action.result["event_id"]))
        if ev is not None:
            ev.cancelled = True
    elif action.kind == "tasks.add":
        mem = db.get(Memory, uuid.UUID(action.result["memory_id"]))
        if mem is not None:
            db.delete(mem)
    elif action.kind == "email.create_draft":
        draft = db.get(MockEmailDraft, uuid.UUID(action.result["draft_id"]))
        if draft is not None:
            draft.status = "discarded"
    else:
        raise AtlasError("not_cancellable",
                         "Questa azione non e annullabile (livello 0: sola lettura).", status=409)
    action.status = "cancelled"
    action.cancelled_at = now
    db.commit()
    db.refresh(action)
    return action
