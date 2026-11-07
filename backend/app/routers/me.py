"""Esportazione e cancellazione dati (spec sez. 9: diritto all'oblio).
La cancellazione rimuove davvero i dati dell'utente, eventi compresi."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ActionLog, Event, Memory, Project, Source, User
from ..schemas import EventOut, ExportOut, MemoryOut, ProjectOut, SourceOut, UserOut
from ..security import get_current_user

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/export", response_model=ExportOut)
def export_data(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ExportOut:
    uid = user.id
    return ExportOut(
        user=UserOut.model_validate(user),
        sources=[SourceOut.model_validate(s) for s in db.query(Source).filter(Source.user_id == uid).all()],
        projects=[ProjectOut.model_validate(p) for p in db.query(Project).filter(Project.user_id == uid).all()],
        events=[EventOut.model_validate(e) for e in db.query(Event).filter(Event.user_id == uid).all()],
        memories=[MemoryOut.model_validate(m) for m in db.query(Memory).filter(Memory.user_id == uid).all()],
    )


@router.delete("", status_code=204)
def delete_data(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    uid = user.id
    # scollega le sostituzioni prima di cancellare
    db.query(Memory).filter(Memory.user_id == uid).update({"superseded_by": None})
    db.query(Memory).filter(Memory.user_id == uid).delete()
    db.query(Event).filter(Event.user_id == uid).delete()
    db.query(Project).filter(Project.user_id == uid).delete()
    db.query(Source).filter(Source.user_id == uid).delete()
    db.query(ActionLog).filter(ActionLog.user_id == uid).delete()
    db.query(User).filter(User.id == uid).delete()
    db.commit()
