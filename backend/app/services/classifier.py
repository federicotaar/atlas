"""Classificatore di richieste fase 0 (spec 5A passo 1), basato su regole.

Tipi: question | new_info | action_request | analysis | project_status
Le action_request non vengono eseguite: le azioni sono fase 4 della roadmap e
richiedono l'Action Engine con permessi. Fase 0 risponde spiegandolo.
"""
import re

QUESTION_HINTS = (
    "che ", "cosa", "cos'", "quando", "dove", "chi ", "perché", "perche",
    "quanto", "quanti", "quante", "come ", "quale", "quali", "a che punto",
    "what", "when", "where", "who", "why", "how", "which",
)
ACTION_HINTS = (
    "invia", "manda", "cancella", "elimina", "paga", "prenota", "compra",
    "send", "delete", "pay", "book", "buy", "email",
)
SAVE_HINTS = (
    "ricorda", "ricordati", "salva", "annota", "segna", "tieni presente",
    "remember", "save", "note that", "don't forget",
)
ANALYSIS_HINTS = (
    "analizza", "riassumi", "riepilogo", "sintetizza", "analize", "summarize", "riepiloga",
)
PROJECT_STATUS_HINTS = (
    "a che punto", "stato del progetto", "come va il progetto", "come procede",
    "aggiornamento sul progetto", "aggiornamenti sul progetto",
)


def classify(text: str) -> str:
    t = text.strip().lower()
    if any(k in t for k in ACTION_HINTS):
        return "action_request"
    if any(t.startswith(k) or f" {k}" in t for k in SAVE_HINTS):
        return "new_info"
    if any(k in t for k in PROJECT_STATUS_HINTS):
        return "project_status"
    if any(k in t for k in ANALYSIS_HINTS):
        return "analysis"
    if "?" in t or any(t.startswith(k) for k in QUESTION_HINTS):
        return "question"
    # frase dichiarativa senza punto interrogativo: la trattiamo come nuova informazione
    if any(k in t for k in QUESTION_HINTS):
        return "question"
    return "new_info"


_STATEMENT_STRIP = re.compile(
    r"^(ricorda(ti)?( che)?|salva|annota|segna|tieni presente( che)?|remember( that)?|save|note that|don't forget( that)?)[,:;]?\s+",
    re.IGNORECASE,
)


def extract_statement(text: str) -> str:
    """Toglie l'imperativo iniziale ('ricorda che ...') lasciando il fatto."""
    stripped = _STATEMENT_STRIP.sub("", text.strip())
    return stripped if stripped else text.strip()
