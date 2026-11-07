"""Knowledge Graph fase 1 (spec 5C): estrazione ASSISTITA di entita e
relazioni. Tutto cio che viene estratto nasce come proposta 'pending' e
diventa reale solo con conferma esplicita dell'utente (spec: 'parte come
proposta da confermare, non come scrittura diretta').

Estrazione rule-based e dichiarata: sequenze di parole capitalizzate come
entita candidate; pochi pattern relazionali espliciti ('lavora a/da/presso',
'usa', 'dipende da'). I nomi di progetto registrati sono riconosciuti come
'project'."""
import re
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..errors import AtlasError
from ..models import Entity, Project, Relationship

_NAME_SEQ = re.compile(r"\b([A-ZÀÈÉÌÒÙ][A-Za-zÀ-ÖØ-öø-ÿ0-9]{2,}(?:\s+[A-ZÀÈÉÌÒÙ][A-Za-zÀ-ÖØ-öø-ÿ0-9]{2,})*)\b")
_NON_ENTITY = {
    "Il", "Lo", "La", "Gli", "Un", "Una", "Uno", "Che", "Chi", "Come", "Cosa",
    "Quando", "Dove", "Perché", "Perche", "Quanto", "Quale", "Quali", "Domani",
    "Oggi", "Ieri", "Mentre", "Dopo", "Prima", "Poi", "Attenzione", "Salvato",
    "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica",
    "Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi",
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
}
_REL_PATTERNS = (
    (re.compile(r"^(.+?)\s+lavora\s+(?:a|da|presso|in|per)\s+(.+)$", re.IGNORECASE), "lavora_per"),
    (re.compile(r"^(.+?)\s+usa\s+(.+)$", re.IGNORECASE), "usa"),
    (re.compile(r"^(.+?)\s+dipende\s+da\s+(.+)$", re.IGNORECASE), "dipende_da"),
    (re.compile(r"^(.+?)\s+porta\s+avanti\s+(.+)$", re.IGNORECASE), "porta_avanti"),
)


def _candidates(text: str) -> list[str]:
    found: list[str] = []
    for sent in re.split(r"[.!?;:\n]", text):
        sent = sent.strip()
        if not sent:
            continue
        # togli la prima parola della frase solo se la segue una parola
        # minuscola: "Il progetto ..." perde "Il", ma "Giulia Bianchi ..." resta
        tokens = sent.split()
        if len(tokens) > 1 and tokens[0][0].isupper() and tokens[1][0].islower():
            body = " ".join(tokens[1:])
        else:
            body = sent
        for m in _NAME_SEQ.finditer(body):
            name = m.group(1).strip()
            parts = name.split()
            if any(p in _NON_ENTITY for p in parts):
                continue
            if name not in found:
                found.append(name)
    return found


def _guess_kind(name: str, project_names: set[str]) -> str:
    if name.lower() in project_names:
        return "project"
    if len(name.split()) >= 2:
        return "person"
    return "concept"


def _get_or_propose(db: Session, user_id: uuid.UUID, name: str, kind: str,
                    memory_id: uuid.UUID | None) -> Entity:
    existing = (
        db.query(Entity)
        .filter(Entity.user_id == user_id, func.lower(Entity.name) == name.lower(),
                Entity.status != "rejected")
        .first()
    )
    if existing is not None:
        return existing
    ent = Entity(user_id=user_id, name=name, kind=kind, status="pending", memory_id=memory_id)
    db.add(ent)
    db.flush()
    return ent


def propose_from_text(db: Session, user_id: uuid.UUID, text: str,
                      memory_id: uuid.UUID | None = None,
                      source_id: uuid.UUID | None = None) -> tuple[list[Entity], list[Relationship]]:
    """Crea proposte pending di entita e relazioni da un testo libero."""
    projects = db.query(Project).filter(Project.user_id == user_id).all()
    project_names = {p.name.lower() for p in projects}

    entities: list[Entity] = []
    by_name: dict[str, Entity] = {}
    for name in _candidates(text):
        ent = _get_or_propose(db, user_id, name, _guess_kind(name, project_names), memory_id)
        entities.append(ent)
        by_name[name.lower()] = ent

    relationships: list[Relationship] = []
    for pattern, relation in _REL_PATTERNS:
        m = pattern.match(text.strip())
        if not m:
            continue
        left, right = m.group(1).strip(), m.group(2).strip().rstrip(".")
        left_names = [n for n in _candidates(left)] or ([left] if 2 <= len(left) <= 60 else [])
        right_names = [n for n in _candidates(right)] or ([right] if 2 <= len(right) <= 60 else [])
        for ln in left_names:
            for rn in right_names:
                if ln.lower() == rn.lower():
                    continue
                fe = by_name.get(ln.lower()) or _get_or_propose(
                    db, user_id, ln, _guess_kind(ln, project_names), memory_id)
                te = by_name.get(rn.lower()) or _get_or_propose(
                    db, user_id, rn, _guess_kind(rn, project_names), memory_id)
                dup = (
                    db.query(Relationship)
                    .filter(Relationship.user_id == user_id,
                            Relationship.from_entity_id == fe.id,
                            Relationship.to_entity_id == te.id,
                            Relationship.relation == relation,
                            Relationship.status != "rejected")
                    .first()
                )
                if dup is None:
                    rel = Relationship(user_id=user_id, from_entity_id=fe.id, to_entity_id=te.id,
                                       relation=relation, status="pending", source_id=source_id)
                    db.add(rel)
                    db.flush()
                    relationships.append(rel)
    db.commit()
    return entities, relationships


def set_status(db: Session, user_id: uuid.UUID, row, new_status: str):
    if row is None or row.user_id != user_id:
        raise AtlasError("not_found", "Elemento non trovato.", status=404)
    if row.status != "pending":
        raise AtlasError("already_decided", "Questa proposta e gia stata decisa.", status=409)
    row.status = new_status
    db.commit()
    db.refresh(row)
    return row


def confirmed_graph(db: Session, user_id: uuid.UUID) -> tuple[list[Entity], list[Relationship]]:
    ents = db.query(Entity).filter(Entity.user_id == user_id, Entity.status == "confirmed").all()
    rels = db.query(Relationship).filter(Relationship.user_id == user_id,
                                         Relationship.status == "confirmed").all()
    return ents, rels


def project_links(db: Session, user_id: uuid.UUID) -> list[Relationship]:
    """Quali progetti sono collegati (spec 5C): relazioni confermate che
    coinvolgono almeno un'entita di tipo project."""
    rels = db.query(Relationship).filter(Relationship.user_id == user_id,
                                         Relationship.status == "confirmed").all()
    return [r for r in rels if r.from_entity.kind == "project" or r.to_entity.kind == "project"]
