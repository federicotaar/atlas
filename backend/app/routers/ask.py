"""ATLAS Core fase 0 (spec 5A): classifica -> recupera -> risponde citando.
Onesta prima di tutto: se il recupero non trova nulla di pertinente, la
risposta e "Non lo so." (spec sez. 3, principio 3). Le azioni sono fase 4."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..schemas import AskIn, AskOut, CitationOut, MemoryOut, SourceOut
from ..security import get_current_user
from ..services.classifier import classify, extract_statement
from ..services.memory import create_memory, log_action
from ..services.retrieval import MemoryHit, search_memories
from ..services.router import ModelRouter
from .sources import get_or_create_chat_source

router = APIRouter(tags=["ask"])

IDK_ANSWER = "Non lo so: non ho memorie pertinenti su questo."

ACTION_PHASE4_ANSWER = (
    "Questa sembra una richiesta di azione. Le azioni sono la fase 4 della roadmap "
    "(Action Engine con permessi, anteprima e conferma): per ora non eseguo operazioni, "
    "posso solo ricordare informazioni e rispondere alle domande."
)


def _citations(hits: list[MemoryHit]) -> list[CitationOut]:
    out = []
    for i, h in enumerate(hits, start=1):
        m = h.memory
        out.append(CitationOut(
            ref=f"M{i}",
            memory_id=m.id,
            content=m.content,
            kind=m.kind,
            origin=m.origin,
            confidence=m.confidence,
            recorded_at=m.recorded_at,
            source=SourceOut.model_validate(m.source) if m.source else None,
        ))
    return out


@router.post("/ask", response_model=AskOut)
def ask(body: AskIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> AskOut:
    settings = get_settings()
    router_ = ModelRouter(settings)
    classification = classify(body.text)

    if classification == "action_request":
        log_action(db, user.id, "ask.action_refused", {})
        return AskOut(classification=classification, answer=ACTION_PHASE4_ANSWER)

    if classification == "new_info":
        statement = extract_statement(body.text)
        source = get_or_create_chat_source(db, user)
        qvec, _ = router_.embed(statement, db=db, user_id=user.id)
        memory, duplicates = create_memory(
            db, settings, user.id, statement, "fact", "stated", source.id,
            None, 0.9, None, qvec,
        )
        log_action(db, user.id, "ask.memory_saved", {"memory_id": str(memory.id)})
        dup_note = ""
        if duplicates:
            dup_note = f" Attenzione: {len(duplicates)} memorie molto simili esistono gia."
        return AskOut(
            classification=classification,
            answer=f"Salvato.{dup_note}",
            citations=[],
            saved_memory=MemoryOut.model_validate(memory),
        )

    # question | analysis: recupero + risposta con citazioni
    qvec, _ = router_.embed(body.text, db=db, user_id=user.id)
    hits = search_memories(db, settings, user.id, body.text, qvec, settings.retrieval_top_k)
    relevant = [h for h in hits if h.relevance >= settings.ask_min_relevance]

    if not relevant:
        log_action(db, user.id, "ask.idk", {})
        return AskOut(classification=classification, answer=IDK_ANSWER)

    mem_dicts = [{"content": h.memory.content} for h in relevant]
    answer, model_used = router_.answer(body.text, mem_dicts, db=db, user_id=user.id)
    log_action(db, user.id, "ask.answered", {"citations": len(relevant)})
    return AskOut(
        classification=classification,
        answer=answer,
        citations=_citations(relevant),
        model=model_used,
    )
