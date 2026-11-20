"""Router `/projects`: i progetti dell'utente.

Creazione, elenco e dettaglio. Ogni progetto raccoglie eventi, memorie
collegate e previsioni; lo stato mostrato altrove (dashboard, Time
Machine) deriva sempre da questi dati registrati.
"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Project, User
from ..schemas import ProjectIn, ProjectOut
from ..security import get_current_user

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> ProjectOut:
    project = Project(user_id=user.id, name=body.name, description=body.description,
                      status=body.status, start_date=body.start_date, target_date=body.target_date)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                  status: str | None = None) -> list[ProjectOut]:
    q = db.query(Project).filter(Project.user_id == user.id)
    if status is not None:
        q = q.filter(Project.status == status)
    projects = q.order_by(Project.created_at).all()
    return [ProjectOut.model_validate(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> ProjectOut:
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise AtlasError("project_not_found", "Progetto non trovato.", status=404)
    return ProjectOut.model_validate(project)


@router.get("/{project_id}/activity-summary")
def project_activity_summary(project_id: uuid.UUID, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)) -> dict:
    from sqlalchemy import func
    from ..models import Event
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise AtlasError("project_not_found", "Progetto non trovato.", status=404)
    q = db.query(Event).filter(Event.user_id == user.id, Event.project_id == project_id)
    counts = q.with_entities(Event.type, func.count()).group_by(Event.type).all()
    first, last = q.with_entities(func.min(Event.event_at), func.max(Event.event_at)).one()
    return {"events": sum(n for _, n in counts), "by_type": dict(counts),
            "first_event_at": first, "last_event_at": last}
