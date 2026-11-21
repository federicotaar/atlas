"""Implementazioni rule-based e locali: niente LLM a pagamento (vincolo costo
zero). Il Critic controlla che ogni attivita del piano poggi sulle memorie
recuperate: un'attivita senza appoggio nei fatti viene segnalata, non nascosta."""
import re
import time
import uuid

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AgentRun
from .schemas import (
    CriticInput,
    CriticIssue,
    CriticOutput,
    PlanInput,
    PlanOutput,
    PlanTask,
    ResearchInput,
    ResearchOutput,
)

# i valori effettivi arrivano da Settings (ATLAS_AGENT_*); questi restano
# come default documentati per chi legge il codice
DEFAULT_MAX_STEPS = 3
DEFAULT_TIMEOUT_S = 20.0

_WORD = re.compile(r"[a-zàèéìòù0-9]{4,}", re.IGNORECASE)
_SPLIT = re.compile(r"\b(?:e poi|poi|dopo|quindi|infine|;|\.\s)\s*", re.IGNORECASE)
_TASK_LINE = re.compile(r"^\s*(?:\d+\s*[.)]|[-*])\s+(.+?)\s*$")

# Tetto alle attivita' del piano: la pipeline ha gia' un limite di passi;
# qui limitiamo quante attivita' un modello puo' proporre in un colpo solo.
MAX_PLAN_TASKS = 5

_PLAN_PROMPT = (
    "Sei il Planner di ATLAS. Scomponi l'obiettivo in al massimo {max_tasks} "
    "attivita' concrete e verificabili, in italiano, una per riga, numerate "
    "(1. ..., 2. ...). Appoggiati SOLO sui fatti elencati: non inventare fatti "
    "sull'utente. Niente titoli, niente commenti: solo le righe numerate.\n\n"
    "Fatti:\n{facts}\n\nObiettivo: {goal}\nAttivita':"
)


class LimitExceeded(Exception):
    """Un limite (passi, timeout o budget) ha fermato l'esecuzione."""


def _log_run(db: Session, user_id: uuid.UUID, agent: str, inp, out, steps: int,
             start: float, status: str, error: str | None = None) -> AgentRun:
    run = AgentRun(
        user_id=user_id, agent=agent,
        input=inp.model_dump(), output=out.model_dump() if out is not None else {},
        steps=steps, duration_ms=int((time.monotonic() - start) * 1000),
        cost_eur=0.0, status=status, error=error,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def run_researcher(db: Session, settings: Settings, user_id: uuid.UUID,
                   inp: ResearchInput) -> ResearchOutput:
    from ..services.retrieval import search_memories
    from ..services.router import ModelRouter

    start = time.monotonic()
    router = ModelRouter(settings)
    qvec, _ = router.embed(inp.goal, db=db, user_id=user_id)
    hits = search_memories(db, settings, user_id, inp.goal, qvec, settings.retrieval_top_k)
    relevant = [h for h in hits if h.relevance >= settings.ask_min_relevance]
    out = ResearchOutput(goal=inp.goal, facts=[h.memory.content for h in relevant],
                         found=bool(relevant))
    _log_run(db, user_id, "researcher", inp, out, 1, start, "ok")
    return out


def _parse_llm_tasks(text: str, max_tasks: int) -> list[PlanTask]:
    """Estrae attivita' da righe numerate o puntate. Righe non conformi
    vengono ignorate: meglio meno attivita' che testo inventato."""
    tasks: list[PlanTask] = []
    for line in text.splitlines():
        m = _TASK_LINE.match(line)
        if m and m.group(1).strip():
            tasks.append(PlanTask(title=m.group(1).strip(), priority=len(tasks) + 1))
        if len(tasks) >= max_tasks:
            break
    return tasks


def _llm_tasks(db: Session, settings: Settings, user_id: uuid.UUID,
               inp: PlanInput) -> tuple[list[PlanTask], str | None]:
    """Piano dal modello configurato (spec 5G): se un LLM e' attivo e risponde
    con righe numerate valide, il piano viene da lui. Qualsiasi fallimento
    (provider assente o irraggiungibile, risposta non parsabile) torna alla
    baseline rule-based: la pipeline non dipende mai dal modello.
    Restituisce (attivita', provider usato o None)."""
    from ..services.router import ModelRouter

    router = ModelRouter(settings)
    prompt = _PLAN_PROMPT.format(
        max_tasks=MAX_PLAN_TASKS, goal=inp.goal,
        facts="\n".join(f"- {f}" for f in inp.facts))
    try:
        text, used = router.answer(prompt, [], db=db, user_id=user_id)
    except Exception:  # provider giu' o risposta rotta: resta la baseline
        return [], None
    if "extractive" in used:  # nessun LLM configurato, o fallback estrattivo
        return [], None
    return _parse_llm_tasks(text, MAX_PLAN_TASKS), used


def run_planner(db: Session, settings: Settings, user_id: uuid.UUID,
                inp: PlanInput) -> PlanOutput:
    start = time.monotonic()
    tasks: list[PlanTask] = []
    mode = "rule-based"
    if inp.facts:
        tasks, used = _llm_tasks(db, settings, user_id, inp)
        if not tasks:
            parts = [p.strip() for p in _SPLIT.split(inp.goal) if p.strip()]
            if len(parts) <= 1:
                parts = [inp.goal.strip()]
            for i, part in enumerate(parts, start=1):
                tasks.append(PlanTask(title=part, priority=i))
        else:
            mode = f"llm:{used}"
    out = PlanOutput(goal=inp.goal, tasks=tasks, based_on_facts=bool(inp.facts),
                     mode=mode)
    _log_run(db, user_id, "planner", inp, out, 1, start, "ok")
    return out


def _support_words(facts: list[str]) -> set[str]:
    words: set[str] = set()
    for f in facts:
        words |= {w.lower() for w in _WORD.findall(f)}
    return words


def _contradicts_facts(title: str, facts: list[str]) -> str | None:
    """Un'attivita che contraddice un fatto noto (stesso soggetto, valori
    diversi) viene segnalata: riusa l'euristica contraddizioni del Memory
    Engine. Ritorna il fatto contraddetto, se trovato."""
    from ..services.contradictions import is_possible_contradiction

    for f in facts:
        conflict, _ = is_possible_contradiction(title, f)
        if conflict:
            return f
    return None


def critique(db: Session, user_id: uuid.UUID, inp: CriticInput) -> CriticOutput:
    """Il Critic ha l'ultima parola (spec 5D). Controllo SOSTANZIALE, non di
    sola presenza di citazioni:
    - piano vuoto con fatti disponibili -> errore;
    - ogni attivita deve condividere contenuto reale con i fatti recuperati
      (almeno un terzo delle parole significative): memorie citate ma
      irrilevanti non valgono come appoggio;
    - un'attivita che contraddice un fatto noto viene segnalata a parte."""
    start = time.monotonic()
    issues: list[CriticIssue] = []
    if not inp.tasks:
        issues.append(CriticIssue(kind="empty_plan",
                                  detail="Il Planner non ha prodotto attivita."))
    support = _support_words(inp.facts)
    verified: list[PlanTask] = []
    for t in inp.tasks:
        tw = {w.lower() for w in _WORD.findall(t.title)}
        overlap = tw & support
        if support and len(overlap) < max(1, len(tw) // 3):
            issues.append(CriticIssue(
                kind="unsourced_claim",
                detail=f"L'attivita '{t.title}' non trova appoggio nelle memorie recuperate."))
            continue
        contra = _contradicts_facts(t.title, inp.facts)
        if contra is not None:
            issues.append(CriticIssue(
                kind="contradicts_fact",
                detail=f"L'attivita '{t.title}' contraddice il fatto: '{contra}'."))
            continue
        verified.append(t)
    out = CriticOutput(approved=not issues, issues=issues, verified_tasks=verified)
    _log_run(db, user_id, "critic", inp, out, 1, start, "ok")
    return out
