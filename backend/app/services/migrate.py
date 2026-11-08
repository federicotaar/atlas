"""Migrazione Postgres -> SQLite per il passaggio al desktop standalone.

Regole di sicurezza (decise con il proprietario):

- MAI modificare o cancellare il Postgres di origine: solo SELECT.
- Se il Postgres configurato non e' raggiungibile lo stato e' "unreachable"
  e l'app NON deve partire come profilo nuovo: il chiamante deve mostrare
  l'avviso "dati esistenti non raggiungibili" e lasciare all'utente la scelta
  (riprovare / continuare senza migrare). Mai confondere "vuoto" con
  "irraggiungibile".
- L'import avviene solo in un database SQLite genuinamente vuoto (nuovo
  profilo); mai sopra dati esistenti.
- Le credenziali BYOK restano CIFRATE: api_key_enc viene copiata cosi' com'e'
  (stessa ATLAS_CREDENTIALS_KEY richiesta sulla destinazione). Nessuna chiave
  in chiaro passa mai per l'export.
- Stati transitori di autenticazione (codici usati, prove di signup) NON
  vengono migrati: sono monouso per definizione.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from ..models import (
    ActionLog,
    AgentRun,
    Connector,
    ConnectorCredential,
    Conversation,
    EngineAction,
    Entity,
    Event,
    Memory,
    Message,
    MockCalendarEvent,
    MockEmailDraft,
    Prediction,
    Project,
    Relationship,
    Source,
    User,
    UserModelSetting,
)

# Tabelle migrate, in ordine di dipendenza FK. Escluse per scelta:
# UsedAttestation e SignupProof (stati auth monouso, non esportabili).
TABLES = [
    User,
    UserModelSetting,
    Source,
    Project,
    Event,
    Memory,
    ActionLog,
    Entity,
    Relationship,
    Conversation,
    Message,
    AgentRun,
    Prediction,
    Connector,
    EngineAction,
    MockCalendarEvent,
    MockEmailDraft,
    ConnectorCredential,
]


@dataclass
class ProbeResult:
    """Esito del controllo del database di origine."""

    state: str  # "has_data" | "empty" | "unreachable"
    counts: dict[str, int] = field(default_factory=dict)
    error: str = ""


def probe(pg_url: str) -> ProbeResult:
    """Controlla il Postgres di origine SENZA scrivere nulla.

    Distingue "vuoto" da "irraggiungibile": il chiamante usa lo stato per
    decidere se offrire la migrazione, partire con profilo nuovo oppure
    avvisare che i dati esistenti non sono raggiungibili."""
    try:
        engine = create_engine(pg_url, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
    except Exception as exc:  # rete giu', credenziali, DNS, TLS...
        return ProbeResult(state="unreachable", error=f"{type(exc).__name__}: {exc}")

    counts: dict[str, int] = {}
    try:
        with engine.connect() as conn:
            for model in (User, Memory, Source, Event):
                n = conn.execute(
                    sql_text(f"SELECT count(*) FROM {model.__tablename__}")).scalar_one()
                counts[model.__tablename__] = int(n)
    except Exception as exc:
        return ProbeResult(state="unreachable", error=f"{type(exc).__name__}: {exc}")
    state = "has_data" if counts.get("users", 0) > 0 else "empty"
    return ProbeResult(state=state, counts=counts)


def _row_to_dict(row: Any) -> dict:
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if isinstance(val, uuid.UUID):
            val = str(val)
        elif isinstance(val, datetime):
            val = val.isoformat()
        elif col.name == "embedding" and val is not None:
            val = [float(v) for v in val]
        out[col.name] = val
    return out


def export_payload(pg_url: str) -> dict:
    """Esporta tutto il contenuto in un payload serializzabile (solo lettura).

    Le credenziali cifrate (api_key_enc, ConnectorCredential) vengono copiate
    come ciphertext: la destinazione deve usare la STESSA credentials key."""
    engine = create_engine(pg_url)
    payload: dict[str, list[dict]] = {}
    with Session(engine) as session:
        for model in TABLES:
            rows = session.query(model).all()
            payload[model.__tablename__] = [_row_to_dict(r) for r in rows]
    return {"version": 1, "tables": payload}


def _parse(col_type_name: str, val: Any) -> Any:
    if val is None:
        return None
    if col_type_name == "uuid" and isinstance(val, str):
        return uuid.UUID(val)
    if col_type_name == "datetime" and isinstance(val, str):
        return datetime.fromisoformat(val)
    return val


def _col_kind(column) -> str:
    import sqlalchemy

    if isinstance(column.type, sqlalchemy.Uuid):
        return "uuid"
    if isinstance(column.type, sqlalchemy.DateTime):
        return "datetime"
    return "plain"


def import_payload(db: Session, payload: dict) -> dict[str, int]:
    """Importa il payload nel DB corrente SOLO se genuinamente vuoto.

    "Vuoto" = zero utenti E zero memorie: un profilo appena creato dall'app
    desktop. Se c'e' qualunque dato, rifiuta (mai fondere o sovrascrivere
    in silenzio)."""
    users = db.query(User).count()
    memories = db.query(Memory).count()
    if users > 0 or memories > 0:
        raise ValueError(
            f"database di destinazione non vuoto ({users} utenti, {memories} memorie): "
            "import rifiutato"
        )

    tables: dict[str, list[dict]] = payload.get("tables", {})
    inserted: dict[str, int] = {}
    is_sqlite = db.bind is not None and db.bind.dialect.name == "sqlite"
    embeddings: list[tuple[uuid.UUID, list[float], str]] = []

    for model in TABLES:
        rows = tables.get(model.__tablename__, [])
        kinds = {col.name: _col_kind(col) for col in model.__table__.columns}
        count = 0
        for raw in rows:
            data = {}
            for col in model.__table__.columns:
                val = raw.get(col.name)
                if col.name == "embedding":
                    continue  # gestito sotto (vec0 su sqlite, colonna su PG)
                data[col.name] = _parse(kinds[col.name], val)
            obj = model(**data)
            if hasattr(model, "embedding") and raw.get("embedding") is not None and not is_sqlite:
                obj.embedding = raw["embedding"]
            db.add(obj)
            count += 1
            if hasattr(model, "embedding") and raw.get("embedding") is not None and is_sqlite:
                embeddings.append((uuid.UUID(raw["id"]), raw["embedding"], raw["content"]))
        inserted[model.__tablename__] = count
        db.flush()

    if is_sqlite:
        from . import sqlite_store

        for mid, emb, content in embeddings:
            sqlite_store.vec_insert(db, mid, emb)
        for raw in tables.get("memories", []):
            sqlite_store.fts_insert(db, uuid.UUID(raw["id"]), raw["content"])
    db.commit()
    return inserted
