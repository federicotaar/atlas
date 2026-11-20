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
from ..schemas import ProjectIn, ProjectOut, ProjectPatch
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


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: uuid.UUID, body: ProjectPatch, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> ProjectOut:
    from ..services.memory import log_action
    project=db.get(Project,project_id)
    if project is None or project.user_id != user.id:
        raise AtlasError("project_not_found", "Progetto non trovato.", status=404)
    changes=body.model_dump(exclude_unset=True)
    if not changes:
        raise AtlasError("empty_update", "Nessun campo da aggiornare.")
    for field in ("name","description","status"):
        if field in changes:
            value=changes[field]
            if value is None or (field != "description" and not value.strip()):
                raise AtlasError("invalid_project", "Nome, descrizione e stato non possono essere nulli.")
            changes[field]=value.strip() if field != "description" else value
    start=changes.get("start_date",project.start_date)
    target=changes.get("target_date",project.target_date)
    if start is not None and target is not None and target < start:
        raise AtlasError("invalid_dates", "La scadenza precede l'inizio del progetto.")
    # Validate the complete merged state before changing any ORM attributes.
    for field,value in changes.items():setattr(project,field,value)
    db.commit();db.refresh(project)
    log_action(db,user.id,"project.update",{"project_id":str(project.id),"fields":sorted(changes)})
    return ProjectOut.model_validate(project)
