"""Accesso dati specifico SQLite (desktop standalone): tabella ombra vec0 per
la ricerca semantica KNN e tabella FTS5 per quella testuale. Le due tabelle
sono indici derivati: la fonte di verita' resta la tabella memories."""
import uuid

import sqlite_vec
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

_WORD = __import__("re").compile(r"[a-z0-9àèéìòù]+", __import__("re").IGNORECASE)


def fts_insert(db: Session, memory_id: uuid.UUID, content: str) -> None:
    db.execute(
        sql_text("INSERT INTO memories_fts (memory_id, content) VALUES (:mid, :c)"),
        {"mid": memory_id.hex, "c": content},
    )


def vec_insert(db: Session, memory_id: uuid.UUID, embedding: list[float]) -> None:
    db.execute(
        sql_text("INSERT INTO memory_vec (memory_id, embedding) VALUES (:mid, :vec)"),
        {"mid": memory_id.hex, "vec": sqlite_vec.serialize_float32(embedding)},
    )


def _active_ids(db: Session, user_id: uuid.UUID) -> set[str]:
    rows = db.execute(
        sql_text("SELECT id FROM memories WHERE user_id = :uid AND superseded_by IS NULL"),
        {"uid": user_id.hex},
    ).all()
    return {str(r[0]) for r in rows}


def knn(db: Session, embedding: list[float], user_id: uuid.UUID, limit: int) -> dict[str, float]:
    """memory_id -> similarita' coseno [0,1], solo memorie attive dell'utente."""
    rows = db.execute(
        sql_text(
            "SELECT memory_id, distance FROM memory_vec "
            "WHERE embedding MATCH :vec AND k = :k ORDER BY distance"
        ),
        {"vec": sqlite_vec.serialize_float32(embedding), "k": max(25, limit)},
    ).all()
    active = _active_ids(db, user_id)
    out: dict[str, float] = {}
    for mid, dist in rows:
        # Stesso filtro del ramo Postgres: distanza coseno < 0.9 (soglia di
        # pertinenza minima, vedi retrieval.py sem_where).
        if mid in active and float(dist) < 0.9:
            out[mid] = max(0.0, 1.0 - float(dist))
            if len(out) >= limit:
                break
    return out


def fts(db: Session, query: str, user_id: uuid.UUID, limit: int) -> dict[str, float]:
    """memory_id -> punteggio testuale normalizzato [0,1].

    FTS5 bm25() restituisce valori negativi (piu' negativo = piu' rilevante);
    la mappa rank/5 con tetto 1 e' una scelta documentata per avere una scala
    comparabile a ts_rank*4 usata su Postgres (vedi ARCHITECTURE.md)."""
    tokens = [t.lower() for t in _WORD.findall(query)]
    if not tokens:
        return {}
    match = " ".join(f'"{t}"' for t in tokens)
    rows = db.execute(
        sql_text(
            "SELECT memory_id, bm25(memories_fts) AS rank FROM memories_fts "
            "WHERE memories_fts MATCH :m ORDER BY rank LIMIT :lim"
        ),
        {"m": match, "lim": limit},
    ).all()
    active = _active_ids(db, user_id)
    out: dict[str, float] = {}
    for mid, rank in rows:
        if mid in active:
            out[mid] = min(1.0, max(0.0, -float(rank)) / 5.0)
    return out


def duplicates(db: Session, embedding: list[float], user_id: uuid.UUID,
               exclude_id: uuid.UUID, max_distance: float) -> list[str]:
    rows = db.execute(
        sql_text(
            "SELECT memory_id, distance FROM memory_vec "
            "WHERE embedding MATCH :vec AND k = 25 ORDER BY distance"
        ),
        {"vec": sqlite_vec.serialize_float32(embedding)},
    ).all()
    active = _active_ids(db, user_id)
    ex = exclude_id.hex
    return [mid for mid, dist in rows
            if mid in active and mid != ex and float(dist) < max_distance][:5]
