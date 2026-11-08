"""Memory Engine fase 0 (spec 5B): scrittura con fonte/confidenza, controllo
duplicati come avviso (mai fusione automatica), sostituzione via superseded_by
(la vecchia memoria resta per la Time Machine)."""
import uuid
from datetime import datetime

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import AtlasError
from ..models import Memory, Source

VALID_KINDS = ("fact", "opinion", "decision", "hypothesis", "task")
VALID_ORIGINS = ("stated", "inferred", "imported")


def create_memory(
    db: Session,
    settings: Settings,
    user_id: uuid.UUID,
    content: str,
    kind: str,
    origin: str,
    source_id: uuid.UUID,
    event_at: datetime | None,
    confidence: float,
    supersedes: uuid.UUID | None,
    embedding: list[float] | None,
) -> tuple[Memory, list[Memory]]:
    """Crea una memoria. Ritorna (memoria, possibili_duplicati)."""
    if kind not in VALID_KINDS:
        raise AtlasError("invalid_kind", f"kind deve essere uno di {VALID_KINDS}.")
    if origin not in VALID_ORIGINS:
        raise AtlasError("invalid_origin", f"origin deve essere uno di {VALID_ORIGINS}.")
    if not 0.0 <= confidence <= 1.0:
        raise AtlasError("invalid_confidence", "confidence deve essere tra 0 e 1.")
    if not content.strip():
        raise AtlasError("empty_content", "content non puo essere vuoto.")

    source = db.get(Source, source_id)
    if source is None or source.user_id != user_id:
        raise AtlasError("source_not_found", "Fonte non trovata.", status=404)

    old: Memory | None = None
    if supersedes is not None:
        old = db.get(Memory, supersedes)
        if old is None or old.user_id != user_id:
            raise AtlasError("memory_not_found", "Memoria da sostituire non trovata.", status=404)
        if old.superseded_by is not None:
            raise AtlasError("already_superseded", "La memoria indicata e gia stata sostituita.")

    memory = Memory(
        user_id=user_id,
        content=content.strip(),
        kind=kind,
        origin=origin,
        source_id=source_id,
        event_at=event_at,
        confidence=confidence,
        embedding=embedding,
    )
    db.add(memory)
    db.flush()  # assegna id prima del supersede

    is_sqlite = db.bind is not None and db.bind.dialect.name == "sqlite"
    if is_sqlite:
        from . import sqlite_store

        sqlite_store.fts_insert(db, memory.id, memory.content)
        if embedding is not None:
            sqlite_store.vec_insert(db, memory.id, embedding)

    if old is not None:
        old.superseded_by = memory.id
        db.add(old)

    duplicates: list[Memory] = []
    if embedding is not None:
        if is_sqlite:
            from . import sqlite_store

            dup_ids_str = sqlite_store.duplicates(
                db, embedding, user_id, memory.id, settings.duplicate_max_distance)
            dup_ids = [uuid.UUID(mid) for mid in dup_ids_str]
        else:
            vec = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
            rows = db.execute(
                sql_text(
                    """
                    SELECT id FROM memories
                    WHERE user_id = :uid AND superseded_by IS NULL AND id != :mid
                      AND embedding IS NOT NULL
                      AND embedding <=> CAST(:vec AS vector) < :maxd
                    LIMIT 5
                    """
                ),
                {"uid": str(user_id), "mid": str(memory.id), "vec": vec, "maxd": settings.duplicate_max_distance},
            ).all()
            dup_ids = [r[0] for r in rows]
        if dup_ids:
            duplicates = list(db.query(Memory).filter(Memory.id.in_(dup_ids)).all())

    db.commit()
    db.refresh(memory)
    return memory, duplicates


def log_action(db: Session, user_id: uuid.UUID, action: str, detail: dict | None = None) -> None:
    """Registra operazione/accesso. Mai contenuti personali nel detail (spec sez. 9)."""
    from ..models import ActionLog

    db.add(ActionLog(user_id=user_id, action=action, detail=detail or {}))
    db.commit()
