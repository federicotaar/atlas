"""Counterfactual Engine (ricerca parallela, spec 5J): scenari what-if
espliciti sulla velocita' osservata. Mai inferenza causale spacciata per
previsione: modello, dati e precondizioni sono dichiarati in ogni risposta."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import get_current_user
from ..services.counterfactual import history_stats, preconditions, what_if
from ..services.memory import log_action

router = APIRouter(prefix="/counterfactual", tags=["counterfactual"])


class ScenarioIn(BaseModel):
    label: str = Field(default="scenario", max_length=120)
    velocity_factor: float = Field(default=1.0, gt=0, le=10)
    extra_completed_tasks: int = Field(default=0, ge=0, le=100000)
    use_observed: Literal["all", "best", "worst"] = "all"


class WhatIfIn(BaseModel):
    scenarios: list[ScenarioIn] = Field(min_length=1, max_length=5)


@router.get("/status")
def status(db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> dict:
    """Precondizioni della specifica (5J) misurate sui dati reali."""
    return {"preconditions": preconditions(db, user.id),
            "history": history_stats(db, user.id)}


@router.post("/what-if")
def run_what_if(body: WhatIfIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    """Confronto fattuale/controfattuale. Ogni scenario dichiara input,
    modello e se le precondizioni 5J sono soddisfatte (esplorativo se no)."""
    result = what_if(db, user.id, [s.model_dump() for s in body.scenarios])
    # audit: mai contenuti, solo numero di scenari ed esito (spec sez. 9)
    log_action(db, user.id, "counterfactual.what_if", {
        "scenarios": len(body.scenarios),
        "preconditions_met": result["preconditions"]["met"],
    })
    return result
