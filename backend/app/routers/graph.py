"""Knowledge Graph fase 1: proposte di estrazione, conferma/rifiuto, grafo
confermato e collegamenti tra progetti (spec 5C)."""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Entity, Memory, Relationship, User
from ..schemas import EntityOut, ExtractIn, ExtractOut, RelationshipDetailOut, RelationshipOut
from ..security import get_current_user
from ..services import entities as graph
from ..services.memory import log_action

router = APIRouter(tags=["graph"])


def _rel_detail(r: Relationship) -> RelationshipDetailOut:
    return RelationshipDetailOut(
        id=r.id,
        from_entity=EntityOut.model_validate(r.from_entity),
        to_entity=EntityOut.model_validate(r.to_entity),
        relation=r.relation,
        status=r.status,
        created_at=r.created_at,
    )


@router.post("/graph/extract", response_model=ExtractOut, status_code=201)
def extract(body: ExtractIn, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> ExtractOut:
    if body.memory_id is not None:
        mem = db.get(Memory, body.memory_id)
        if mem is None or mem.user_id != user.id:
            raise AtlasError("memory_not_found", "Memoria non trovata.", status=404)
        text = mem.content
        source_id = mem.source_id
    elif body.text:
        text = body.text
        source_id = None
    else:
        raise AtlasError("empty_extract", "Servono text o memory_id.")
    ents, rels = graph.propose_from_text(db, user.id, text, body.memory_id, source_id)
    log_action(db, user.id, "graph.extract", {"entities": len(ents), "relationships": len(rels)})
    return ExtractOut(
        entities=[EntityOut.model_validate(e) for e in ents],
        relationships=[RelationshipOut.model_validate(r) for r in rels],
    )


@router.get("/graph/proposals", response_model=ExtractOut)
def proposals(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ExtractOut:
    ents = db.query(Entity).filter(Entity.user_id == user.id, Entity.status == "pending").all()
    rels = db.query(Relationship).filter(Relationship.user_id == user.id,
                                         Relationship.status == "pending").all()
    return ExtractOut(
        entities=[EntityOut.model_validate(e) for e in ents],
        relationships=[RelationshipOut.model_validate(r) for r in rels],
    )


@router.post("/graph/entities/{entity_id}/confirm", response_model=EntityOut)
def confirm_entity(entity_id: uuid.UUID, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> EntityOut:
    ent = graph.set_status(db, user.id, db.get(Entity, entity_id), "confirmed")
    log_action(db, user.id, "graph.entity_confirmed", {"entity_id": str(ent.id)})
    return EntityOut.model_validate(ent)


@router.post("/graph/entities/{entity_id}/reject", response_model=EntityOut)
def reject_entity(entity_id: uuid.UUID, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> EntityOut:
    ent = graph.set_status(db, user.id, db.get(Entity, entity_id), "rejected")
    log_action(db, user.id, "graph.entity_rejected", {"entity_id": str(ent.id)})
    return EntityOut.model_validate(ent)


@router.post("/graph/relationships/{rel_id}/confirm", response_model=RelationshipOut)
def confirm_relationship(rel_id: uuid.UUID, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)) -> RelationshipOut:
    rel = graph.set_status(db, user.id, db.get(Relationship, rel_id), "confirmed")
    log_action(db, user.id, "graph.relationship_confirmed", {"relationship_id": str(rel.id)})
    return RelationshipOut.model_validate(rel)


@router.post("/graph/relationships/{rel_id}/reject", response_model=RelationshipOut)
def reject_relationship(rel_id: uuid.UUID, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)) -> RelationshipOut:
    rel = graph.set_status(db, user.id, db.get(Relationship, rel_id), "rejected")
    log_action(db, user.id, "graph.relationship_rejected", {"relationship_id": str(rel.id)})
    return RelationshipOut.model_validate(rel)


@router.get("/graph", response_model=ExtractOut)
def confirmed(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ExtractOut:
    ents, rels = graph.confirmed_graph(db, user.id)
    return ExtractOut(
        entities=[EntityOut.model_validate(e) for e in ents],
        relationships=[RelationshipOut.model_validate(r) for r in rels],
    )


@router.get("/graph/project-links", response_model=list[RelationshipDetailOut])
def project_links(db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> list[RelationshipDetailOut]:
    return [_rel_detail(r) for r in graph.project_links(db, user.id)]
