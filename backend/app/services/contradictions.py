"""Rilevamento contraddizioni (spec 5B scrittura: 'controlla duplicati e
contraddizioni'; 5C: 'quali informazioni sono in contraddizione').

Euristica dichiarata e conservativa: due memorie attive sono POSSIBILI
contraddizioni quando condividono il soggetto (almeno 2 parole di contenuto)
ma assegnano valori diversi (numeri, giorni, mesi o nomi propri diversi e
disgiunti). Niente fusione o risoluzione automatica: sono proposte per
l'utente, che risolve con una sostituzione (supersedes)."""
import re
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Memory

_WORD = re.compile(r"[a-zàèéìòù0-9]+", re.IGNORECASE)
_NUM = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_NUM_GROUPED = re.compile(r"\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b")
_NAME = re.compile(r"\b[A-ZÀÈÉÌÒÙ][a-zàèéìòù]{2,}\b")

_STOPWORDS = {
    "alla", "alle", "allo", "come", "cosa", "della", "delle", "dello", "dove",
    "ogni", "perché", "perche", "quando", "quanto", "questo", "questa",
    "sono", "sulla", "sulle", "un", "una", "uno", "con", "per", "che",
    "del", "dei", "dal", "dai", "nel", "nei", "non", "più", "piu", "mario",
    "luigi", "giorni", "progetto",
}
_DAY_ALIASES = {
    "lunedì": "monday", "lunedi": "monday", "monday": "monday",
    "martedì": "tuesday", "martedi": "tuesday", "tuesday": "tuesday",
    "mercoledì": "wednesday", "mercoledi": "wednesday", "wednesday": "wednesday",
    "giovedì": "thursday", "giovedi": "thursday", "thursday": "thursday",
    "venerdì": "friday", "venerdi": "friday", "friday": "friday",
    "sabato": "saturday", "saturday": "saturday", "domenica": "sunday", "sunday": "sunday",
}
_MONTH_ALIASES = dict(zip(
    ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
     "settembre", "ottobre", "novembre", "dicembre"),
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"),
))
_MONTH_ALIASES.update({value: value for value in tuple(_MONTH_ALIASES.values())})


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) >= 4 and w.lower() not in _STOPWORDS}


def _numbers(text: str) -> set:
    """Numeri in formato italiano: '1.000' vale mille (punto migliaia) e
    '2,5' vale due e mezzo (virgola decimale). Senza questa distinzione
    '1.000 euro' e '1000 euro' risultavano valori diversi (1 vs 1000)."""
    out = {Decimal(g.replace(".", "").replace(",", ".")) for g in _NUM_GROUPED.findall(text)}
    rest = _NUM_GROUPED.sub(" ", text)
    out |= {Decimal(n.replace(",", ".")) for n in _NUM.findall(rest)}
    return out


def _value_sets(text: str) -> list[set]:
    """Valori per categoria: numeri, giorni, mesi. I nomi propri non contano:
    il soggetto condiviso e gia misurato dalle parole di contenuto."""
    words = set(_WORD.findall(text.lower()))
    return [
        _numbers(text),
        {_DAY_ALIASES[w] for w in words if w in _DAY_ALIASES},
        {_MONTH_ALIASES[w] for w in words if w in _MONTH_ALIASES},
    ]


def is_possible_contradiction(a: str, b: str) -> tuple[bool, list[str]]:
    """Ritorna (conflitto?, termini_condivisi). Conflitto quando le due
    memorie condividono il soggetto ma assegnano valori diversi nella stessa
    categoria (numeri, giorni della settimana o mesi)."""
    shared = _content_words(a) & _content_words(b)
    if len(shared) < 2:
        return False, []
    for va, vb in zip(_value_sets(a), _value_sets(b)):
        if va and vb and va.isdisjoint(vb):
            return True, sorted(shared)
    return False, []


def find_contradictions_for(db: Session, settings: Settings, user_id: uuid.UUID,
                            content: str, exclude_id: uuid.UUID | None = None,
                            limit: int = 5, cap: int = 100) -> list[tuple[Memory, list[str]]]:
    """Memorie attive che potrebbero contraddire il nuovo contenuto.

    Candidati: le `cap` memorie attive piu recenti (l'euristica sulle parole
    condivise fa da filtro; la FTS con AND di tutti i termini non va bene qui
    perche il valore cambiato non compare nella vecchia memoria)."""
    candidates = (
        db.query(Memory)
        .filter(Memory.user_id == user_id, Memory.superseded_by.is_(None))
        .order_by(Memory.recorded_at.desc())
        .limit(cap)
        .all()
    )
    out: list[tuple[Memory, list[str]]] = []
    for m in candidates:
        if exclude_id is not None and m.id == exclude_id:
            continue
        conflict, shared = is_possible_contradiction(content, m.content)
        if conflict:
            out.append((m, shared))
        if len(out) >= limit:
            break
    return out


def find_open_contradictions(db: Session, settings: Settings, user_id: uuid.UUID,
                             cap: int = 50) -> list[tuple[Memory, Memory, list[str]]]:
    """Coppie di memorie attive in possibile contraddizione (vista on-demand)."""
    memories = (
        db.query(Memory)
        .filter(Memory.user_id == user_id, Memory.superseded_by.is_(None))
        .order_by(Memory.recorded_at.desc())
        .limit(cap)
        .all()
    )
    pairs: list[tuple[Memory, Memory, list[str]]] = []
    for i, a in enumerate(memories):
        for b in memories[i + 1:]:
            conflict, shared = is_possible_contradiction(a.content, b.content)
            if conflict:
                pairs.append((a, b, shared))
    return pairs
