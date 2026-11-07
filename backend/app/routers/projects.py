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
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ProjectOut]:
    projects = db.query(Project).filter(Project.user_id == user.id).order_by(Project.created_at).all()
    return [ProjectOut.model_validate(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> ProjectOut:
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise AtlasError("project_not_found", "Progetto non trovato.", status=404)
    return ProjectOut.model_validate(project)
