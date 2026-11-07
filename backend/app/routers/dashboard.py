"""Router dashboard (fase 5, spec 5I): pannelli aggregati con provenienza."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import get_current_user
from ..services.dashboard import build_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    return build_dashboard(db, user.id)
