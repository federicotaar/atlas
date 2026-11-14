"""Router `/memories`: la memoria verificabile.

Creazione con fonte obbligatoria (ogni dato dichiara da dove arriva),
ricerca ibrida per significato/testo/recenza/confidenza, lettura singola
e rilevamento delle contraddizioni aperte. Le risposte includono gli id
delle fonti cosi ogni affermazione resta controllabile.
"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..errors import AtlasError
from ..models import Memory, User
from ..schemas import ContradictionPairOut, MemoryCreatedOut, MemoryIn, MemoryOut, SearchHitOut, SearchIn
from ..security import get_current_user
from ..services.contradictions import find_contradictions_for, find_open_contradictions
from ..services.memory import create_memory, log_action
from ..services.retrieval import search_memories
from ..services.router import ModelRouter

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("", response_model=MemoryCreatedOut, status_code=201)
def create(body: MemoryIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> MemoryCreatedOut:
    settings = get_settings()
    router_ = ModelRouter(settings)
    embedding, model_used = router_.embed(body.content, db=db, user_id=user.id)
    memory, duplicates = create_memory(
        db, settings, user.id, body.content, body.kind, body.origin, body.source_id,
        body.event_at, body.confidence, body.supersedes, embedding,
    )
    contradictions = find_contradictions_for(db, settings, user.id, body.content,
                                             exclude_id=memory.id)
    log_action(db, user.id, "memory.create", {"memory_id": str(memory.id), "embedder": model_used})
    return MemoryCreatedOut(
        memory=MemoryOut.model_validate(memory),
        possible_duplicates=[MemoryOut.model_validate(d) for d in duplicates],
        possible_contradictions=[MemoryOut.model_validate(m) for m, _ in contradictions],
    )


@router.get("/contradictions", response_model=list[ContradictionPairOut])
def open_contradictions(db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)) -> list[ContradictionPairOut]:
    """Vista on-demand: coppie di memorie attive in possibile contraddizione."""
    settings = get_settings()
    pairs = find_open_contradictions(db, settings, user.id)
    return [
        ContradictionPairOut(
            memory=MemoryOut.model_validate(a),
            conflicts_with=MemoryOut.model_validate(b),
            shared_terms=shared,
        )
        for a, b, shared in pairs
    ]


@router.get("/{memory_id}", response_model=MemoryOut)
def get_one(memory_id: uuid.UUID, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> MemoryOut:
    memory = db.get(Memory, memory_id)
    if memory is None or memory.user_id != user.id:
        raise AtlasError("memory_not_found", "Memoria non trovata.", status=404)
    return MemoryOut.model_validate(memory)


@router.post("/search", response_model=list[SearchHitOut])
def search(body: SearchIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> list[SearchHitOut]:
    settings = get_settings()
    router_ = ModelRouter(settings)
    qvec, _ = router_.embed(body.query, db=db, user_id=user.id)
    hits = search_memories(db, settings, user.id, body.query, qvec, body.limit)
    log_action(db, user.id, "memory.search", {"results": len(hits)})
    return [
        SearchHitOut(
            memory=MemoryOut.model_validate(h.memory),
            score=round(h.score, 4), relevance=round(h.relevance, 4),
            semantic=round(h.semantic, 4), text_score=round(h.text_score, 4),
        )
        for h in hits
    ]
