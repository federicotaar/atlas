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

from urllib.parse import urlsplit

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


# Rilevamento automatico dell'upgrade da 2.0.x: SOLO i default documentati
# del nostro docker-compose.yml distribuito (nessuna scansione di file,
# nessuna credenziale indovinata). Un setup personalizzato si dichiara con
# ATLAS_LEGACY_DATABASE_URL, che ha sempre la precedenza sul rilevamento.
LEGACY_AUTODETECT_CANDIDATES = (
    "postgresql+psycopg://atlas:atlas@localhost:5432/atlas",
)

# Chiavi Fernet legacy NOTE perche' pubbliche: il default di sviluppo
# distribuito nel docker-compose.yml 2.0.x. Usate SOLO per decifrare le
# credenziali migrate e ri-cifrarle con la chiave locale corrente: mai
# salvate, mai adottate come chiave dell'installazione.
LEGACY_KNOWN_CREDENTIALS_KEYS = (
    "dFlmLYWhooNIxCgkSH_aPNs5xJEhnX8kIJCQyUjePtk=",
)

# Campi cifrati migrati: (tabella, colonna). I token dei connettori non hanno
# un flag key_unusable: per loro vale il conteggio complessivo.
ENCRYPTED_FIELDS = (
    ("user_model_settings", "api_key_enc"),
    ("connector_credentials", "access_token_enc"),
    ("connector_credentials", "refresh_token_enc"),
)


def detect_legacy() -> tuple[str | None, "ProbeResult | None"]:
    """Prova i candidati documentati e ritorna (url, esito) del primo con
    dati; altrimenti (url, esito) del primo irraggiungibile; altrimenti
    (None, None). Serve al rilevamento su richiesta: chi preme "riprova" deve
    vedere la differenza tra "nessun dato noto" e "trovato ma non
    raggiungibile" - mai un 'niente da importare' quando il DB e' spento."""
    first_unreachable: tuple[str, "ProbeResult"] | None = None
    for url in LEGACY_AUTODETECT_CANDIDATES:
        try:
            result = probe(url)
        except Exception as exc:
            result = ProbeResult(state="unreachable", error=_safe_error(exc, url))
        if result.state == "has_data":
            return url, result
        if result.state == "unreachable" and first_unreachable is None:
            first_unreachable = (url, result)
    if first_unreachable is not None:
        return first_unreachable
    return None, None


def autodetect_legacy_url() -> str | None:
    """Rileva un Postgres 2.0.x raggiungibile E CON DATI tra i candidati
    documentati. Ritorna l'URL, altrimenti None: vuoto o irraggiungibile
    contano come assente, cosi' un'installazione nuova resta semplice e non
    vede mai un banner."""
    url, result = detect_legacy()
    if url is not None and result is not None and result.state == "has_data":
        return url
    return None


def _safe_error(exc: Exception, pg_url: str) -> str:
    """Errore mostrabile in UI/log: MAI la connection string con le
    credenziali dentro, qualunque cosa il driver includa nel messaggio."""
    msg = f"{type(exc).__name__}: {exc}"
    parts = urlsplit(pg_url)
    if parts.password:
        msg = msg.replace(f"{parts.username}:{parts.password}@", "***@")
        msg = msg.replace(parts.password, "***")
    return msg


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
        return ProbeResult(state="unreachable", error=_safe_error(exc, pg_url))

    counts: dict[str, int] = {}
    try:
        with engine.connect() as conn:
            for model in (User, Memory, Source, Event):
                n = conn.execute(
                    sql_text(f"SELECT count(*) FROM {model.__tablename__}")).scalar_one()
                counts[model.__tablename__] = int(n)
    except Exception as exc:
        return ProbeResult(state="unreachable", error=_safe_error(exc, pg_url))
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


def _user_owned_models() -> list:
    """Tutti i modelli con FK verso users: la classificazione 'vuoto' deve
    coprire OGNI tabella posseduta, non solo quelle migrate oggi, per non
    cancellare mai un profilo perche' il conteggio ha saltato una tabella."""
    from ..models import Base

    owned = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        col = cls.__table__.c.get("user_id")
        if col is None:
            continue
        if any(fk.column.table.name == "users" for fk in col.foreign_keys):
            owned.append(cls)
    return owned


def import_payload(
    db: Session, payload: dict, credentials_key: str | None = None,
    legacy_keys: tuple[str, ...] | list[str] | None = None,
) -> tuple[dict[str, int], list[uuid.UUID], int]:
    """Importa il payload nel DB corrente SOLO se genuinamente vuoto.

    "Vuoto" = zero righe in tutte le tabelle possedute, oppure il solo
    profilo di bootstrap (esattamente 1 utente, zero contenuto proprio in
    OGNI tabella posseduta; ammessi solo i suoi audit-log): in quel caso
    l'utente di bootstrap viene SOSTITUITO dai dati importati e al rientro
    si accede con le credenziali originali. Con qualunque altro dato
    presente, rifiuta (mai fondere o sovrascrivere in silenzio).

    Transazione unica: sostituzione bootstrap + righe + indici vec/fts +
    audit migration.imported; qualsiasi errore (decifratura, indice,
    vincolo) fa rollback di TUTTO, bootstrap compreso, e l'import si puo'
    ritentare. Le credenziali restano cifrate; se credentials_key e'
    fornita, ogni api_key_enc viene provata in pre-flight e il numero di
    credenziali non decifrabili torna al chiamante (mai valori in chiaro,
    mai import silenzioso di ciphertext inutilizzabile).

    Ritorna (conteggi per tabella, id utenti importati, credenziali non
    decifrabili)."""
    users = db.query(User).count()
    bootstrap_ok = False
    if users == 1:
        bootstrap_ok = all(
            db.query(model).count() == 0
            for model in _user_owned_models()
            if model is not ActionLog
        )
    if users > 0 and not bootstrap_ok:
        memories = db.query(Memory).count()
        raise ValueError(
            f"database di destinazione non vuoto ({users} utenti, {memories} memorie): "
            "import rifiutato"
        )

    tables: dict[str, list[dict]] = payload.get("tables", {})

    # Pre-flight: nessuna mutazione prima di questo punto oltre ai conteggi.
    # Le credenziali non decifrabili restano in tabella (ciphertext conservato
    # per un eventuale ripristino della chiave) ma marcate key_unusable: mai
    # tentate come configurazione funzionante finche' non reinserite.
    undecryptable = 0
    bad_cred_users: set[str] = set()
    if credentials_key:
        from .credentials import decrypt_secret, encrypt_secret

        for table, column in ENCRYPTED_FIELDS:
            for raw in tables.get(table, []):
                enc = raw.get(column)
                if not enc:
                    continue
                try:
                    decrypt_secret(enc, credentials_key)
                    continue
                except Exception:
                    pass
                # Chiave locale diversa: riprova con le chiavi legacy NOTE
                # (default pubblici del compose 2.0.x). Se una decifra, il
                # segreto viene SUBITO ri-cifrato con la chiave locale: la
                # chiave vecchia non viene mai salvata ne' adottata.
                reencrypted = False
                for legacy_key in legacy_keys or ():
                    try:
                        plain = decrypt_secret(enc, legacy_key)
                    except Exception:
                        continue
                    raw[column] = encrypt_secret(plain, credentials_key)
                    reencrypted = True
                    break
                if not reencrypted:
                    undecryptable += 1
                    if table == "user_model_settings" and column == "api_key_enc":
                        bad_cred_users.add(str(raw.get("user_id")))

    if bootstrap_ok:
        # sostituzione pulita: le uniche righe possibili sono gli audit-log
        # del bootstrap (tutto il resto e' stato verificato vuoto), poi utente
        bootstrap = db.query(User).one()
        for model in _user_owned_models():
            db.query(model).filter(
                model.__table__.c.user_id == bootstrap.id
            ).delete()
        db.delete(bootstrap)
        db.flush()

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
                if col.name == "embedding":
                    continue  # gestito sotto (vec0 su sqlite, colonna su PG)
                if col.name not in raw:
                    continue  # colonna nuova assente nella sorgente: default
                data[col.name] = _parse(kinds[col.name], raw[col.name])
            if model is UserModelSetting and str(raw.get("user_id")) in bad_cred_users:
                data["key_unusable"] = True
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
    imported_user_ids = [uuid.UUID(r["id"]) for r in tables.get("users", [])]
    # Audit registrato sugli utenti IMPORTATI (il bootstrap non esiste piu'):
    # al rientro con le credenziali originali lo stato risulta "imported".
    # Stesso commit dell'import: una sola transazione, tutto o niente.
    for uid in imported_user_ids:
        db.add(ActionLog(user_id=uid, action="migration.imported",
                         detail={"tables": inserted}))
    db.commit()
    return inserted, imported_user_ids, undecryptable
