"""Recupero ibrido (spec 5B): semantico (pgvector) + testuale (FTS Postgres) +
recenza + confidenza. I pesi sono configurabili (ATLAS_W_*).

Nota sul gate di pertinenza: recency e confidenza sono prior di qualita e NON
contano per decidere se una memoria riguarda la domanda; il gate "Non lo so"
usa solo la rilevanza (semantica + testuale).
"""
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Memory


@dataclass
class MemoryHit:
    memory: Memory
    semantic: float
    text_score: float
    recency: float
    relevance: float  # w_semantic*semantic + w_text*text_score
    score: float  # relevance + w_recency*recency + w_confidence*confidence


def recency_score(recorded_at: datetime, half_life_days: float, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - recorded_at).total_seconds() / 86400.0)
    if half_life_days <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days / half_life_days)


def combine_score(
    semantic: float,
    text_score: float,
    recency: float,
    confidence: float,
    w_semantic: float,
    w_text: float,
    w_recency: float,
    w_confidence: float,
) -> tuple[float, float]:
    """Ritorna (relevance, score): relevance guida il gate 'Non lo so'."""
    relevance = w_semantic * semantic + w_text * text_score
    score = relevance + w_recency * recency + w_confidence * confidence
    return relevance, score


def search_memories(db: Session, settings: Settings, user_id: uuid.UUID, query: str, query_embedding: list[float] | None,
                    limit: int | None = None) -> list[MemoryHit]:
    limit = limit or settings.retrieval_top_k
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        return _search_memories_sqlite(db, settings, user_id, query, query_embedding, limit)
    params: dict = {"uid": str(user_id), "q": query, "cand": settings.retrieval_candidates}
    sem_select = "0.0"
    sem_where = "FALSE"
    if query_embedding is not None:
        params["qvec"] = "[" + ",".join(f"{v:.6f}" for v in query_embedding) + "]"
        sem_select = "GREATEST(0.0, 1.0 - (embedding <=> CAST(:qvec AS vector)))"
        sem_where = "embedding <=> CAST(:qvec AS vector) < 0.9"

    sql = f"""
        SELECT id, {sem_select} AS sem,
               ts_rank(to_tsvector('simple', content), plainto_tsquery('simple', :q)) AS trank
        FROM memories
        WHERE user_id = :uid AND superseded_by IS NULL
          AND ({sem_where} OR to_tsvector('simple', content) @@ plainto_tsquery('simple', :q))
        ORDER BY GREATEST({sem_select},
                          ts_rank(to_tsvector('simple', content), plainto_tsquery('simple', :q))) DESC
        LIMIT :cand
    """
    rows = db.execute(sql_text(sql), params).all()
    if not rows:
        return []

    ids = [r[0] for r in rows]
    memories = {m.id: m for m in db.query(Memory).filter(Memory.id.in_(ids)).all()}

    hits: list[MemoryHit] = []
    for row_id, sem, trank in rows:
        mem = memories.get(row_id)
        if mem is None:
            continue
        # ts_rank su testi brevi e piccolo: normalizzazione documentata in ARCHITECTURE.md
        text_score = min(1.0, float(trank) * 4.0)
        rec = recency_score(mem.recorded_at, settings.recency_half_life_days)
        relevance, score = combine_score(
            float(sem), text_score, rec, mem.confidence,
            settings.w_semantic, settings.w_text, settings.w_recency, settings.w_confidence,
        )
        hits.append(MemoryHit(memory=mem, semantic=float(sem), text_score=text_score,
                              recency=rec, relevance=relevance, score=score))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def _search_memories_sqlite(db: Session, settings: Settings, user_id: uuid.UUID, query: str,
                            query_embedding: list[float] | None, limit: int) -> list[MemoryHit]:
    """Variante SQLite (desktop): vec0 KNN + FTS5, stessa combinazione pesi."""
    from . import sqlite_store

    sem_map = sqlite_store.knn(db, query_embedding, user_id, settings.retrieval_candidates) \
        if query_embedding is not None else {}
    fts_map = sqlite_store.fts(db, query, user_id, settings.retrieval_candidates)

    ids = set(sem_map) | set(fts_map)
    if not ids:
        return []
    ordered = sorted(ids, key=lambda i: max(sem_map.get(i, 0.0), fts_map.get(i, 0.0)),
                     reverse=True)[: settings.retrieval_candidates]
    uids = [uuid.UUID(mid) for mid in ordered]
    memories = {m.id: m for m in db.query(Memory).filter(Memory.id.in_(uids)).all()}

    hits: list[MemoryHit] = []
    for mid in ordered:
        mem = memories.get(uuid.UUID(mid))
        if mem is None:
            continue
        sem = sem_map.get(mid, 0.0)
        text_score = fts_map.get(mid, 0.0)
        rec = recency_score(mem.recorded_at, settings.recency_half_life_days)
        relevance, score = combine_score(
            sem, text_score, rec, mem.confidence,
            settings.w_semantic, settings.w_text, settings.w_recency, settings.w_confidence,
        )
        hits.append(MemoryHit(memory=mem, semantic=sem, text_score=text_score,
                              recency=rec, relevance=relevance, score=score))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
