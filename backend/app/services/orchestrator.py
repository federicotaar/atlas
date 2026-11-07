"""Orchestratore centrale (fase 1, spec 5A/ATLAS CORE): un solo punto che
classifica la richiesta, decide il percorso (memoria, stato progetto, azioni
future) e registra la conversazione. Gli agenti (fase 2) si agganciano qui.

Risposte di progetto oneste (spec 5A esempio): elenca solo cio che risulta
dagli eventi e dalle memorie; non inventa progressi."""
import uuid

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Conversation, Event, Message, Project, User
from ..routers.sources import get_or_create_chat_source
from ..schemas import AskOut, CitationOut, MemoryOut, SourceOut
from ..services.classifier import classify, extract_statement
from ..services.contradictions import find_contradictions_for
from ..services.memory import create_memory, log_action
from ..services.retrieval import MemoryHit, search_memories
from ..services.router import ModelRouter

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


def _get_conversation(db: Session, user: User, conversation_id: uuid.UUID | None, first_text: str) -> Conversation:
    if conversation_id is not None:
        conv = db.get(Conversation, conversation_id)
        if conv is not None and conv.user_id == user.id:
            return conv
    conv = Conversation(user_id=user.id, title=first_text.strip()[:80])
    db.add(conv)
    db.flush()
    return conv


def _save_turn(db: Session, conv: Conversation, user_text: str, answer: str) -> None:
    db.add(Message(conversation_id=conv.id, role="user", content=user_text))
    db.add(Message(conversation_id=conv.id, role="assistant", content=answer))
    db.commit()


def _mentioned_projects(db: Session, user: User, text: str) -> list[Project]:
    low = text.lower()
    projects = db.query(Project).filter(Project.user_id == user.id).all()
    mentioned = [p for p in projects if p.name.lower() in low]
    if mentioned:
        return mentioned
    active = [p for p in projects if p.status == "active"]
    return active[:1] if len(projects) == 1 else []


def _project_status_answer(db: Session, user: User, text: str) -> str:
    projects = _mentioned_projects(db, user, text)
    if not projects:
        return "Non lo so: non hai progetti registrati (o non hai detto quale)."
    blocks = []
    for p in projects:
        events = (
            db.query(Event)
            .filter(Event.user_id == user.id, Event.project_id == p.id)
            .order_by(Event.event_at.desc())
            .limit(5)
            .all()
        )
        lines = [f"Progetto {p.name} (stato: {p.status})"]
        if p.target_date:
            lines.append(f"Data obiettivo: {p.target_date.isoformat()}")
        if events:
            lines.append("Ultimi eventi registrati:")
            for e in events:
                snippet = str(e.payload.get("text", e.payload))[:120]
                lines.append(f"- {e.event_at.date().isoformat()}: {snippet}")
        else:
            lines.append("Non ho eventi registrati per questo progetto.")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def handle_ask(db: Session, settings: Settings, user: User, text: str,
               conversation_id: uuid.UUID | None = None) -> AskOut:
    router_ = ModelRouter(settings)
    # BYOK: se l'utente ha scelto un provider/modello proprio, il suo
    # generatore sostituisce quello di default del server. Errori di
    # configurazione (chiave mancante) arrivano all'utente come errore
    # chiaro; errori di rete del provider ricadono sull'estrattivo locale
    # con il modello segnato "->extractive-fallback" (mai downgrade silenzioso).
    from .llm_providers import resolve_user_generator
    user_gen = resolve_user_generator(db, settings, user.id)
    if user_gen is not None:
        router_._generator = user_gen
    classification = classify(text)
    conv = _get_conversation(db, user, conversation_id, text)

    if classification == "action_request":
        log_action(db, user.id, "ask.action_refused", {})
        out = AskOut(classification=classification, answer=ACTION_PHASE4_ANSWER,
                     conversation_id=conv.id)
        _save_turn(db, conv, text, out.answer)
        return out

    if classification == "project_status":
        log_action(db, user.id, "ask.project_status", {})
        answer = _project_status_answer(db, user, text)
        out = AskOut(classification=classification, answer=answer, conversation_id=conv.id)
        _save_turn(db, conv, text, out.answer)
        return out

    if classification == "new_info":
        statement = extract_statement(text)
        source = get_or_create_chat_source(db, user)
        qvec, _ = router_.embed(statement, db=db, user_id=user.id)
        memory, duplicates = create_memory(
            db, settings, user.id, statement, "fact", "stated", source.id,
            None, 0.9, None, qvec,
        )
        contradictions = find_contradictions_for(db, settings, user.id, statement,
                                                 exclude_id=memory.id)
        log_action(db, user.id, "ask.memory_saved", {"memory_id": str(memory.id)})
        notes = []
        if duplicates:
            notes.append(f"{len(duplicates)} memorie molto simili esistono gia")
        if contradictions:
            notes.append(f"{len(contradictions)} possibili contraddizioni rilevate "
                         "(controlla e conferma la versione giusta)")
        note = f" Attenzione: {'; '.join(notes)}." if notes else ""
        out = AskOut(
            classification=classification,
            answer=f"Salvato.{note}",
            citations=[],
            saved_memory=MemoryOut.model_validate(memory),
            conversation_id=conv.id,
        )
        _save_turn(db, conv, text, out.answer)
        return out

    # question | analysis: recupero ibrido + risposta con citazioni
    qvec, _ = router_.embed(text, db=db, user_id=user.id)
    hits = search_memories(db, settings, user.id, text, qvec, settings.retrieval_top_k)
    relevant = [h for h in hits if h.relevance >= settings.ask_min_relevance]

    if not relevant:
        log_action(db, user.id, "ask.idk", {})
        out = AskOut(classification=classification, answer=IDK_ANSWER, conversation_id=conv.id)
        _save_turn(db, conv, text, out.answer)
        return out

    mem_dicts = [{"content": h.memory.content} for h in relevant]
    answer, model_used = router_.answer(text, mem_dicts, db=db, user_id=user.id)
    log_action(db, user.id, "ask.answered", {"citations": len(relevant)})
    out = AskOut(
        classification=classification,
        answer=answer,
        citations=_citations(relevant),
        model=model_used,
        conversation_id=conv.id,
    )
    _save_turn(db, conv, text, out.answer)
    return out
