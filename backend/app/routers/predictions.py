"""Prediction Engine fase 3: registro, verifica e accuratezza storica."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import Prediction, User
from ..schemas import PredictionOut
from ..security import get_current_user
from ..services.memory import log_action
from ..services.predictions import brier_score, estimate_completion, metrics

router = APIRouter(prefix="/predictions", tags=["predictions"])


class PredictionCreateIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    verify_at: datetime
    probability: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_factors: str = ""
    auto_estimate: bool = True  # stima da velocita reale; se False servono i valori
    estimate_central: float | None = None
    estimate_low: float | None = None
    estimate_high: float | None = None
    unit: str = ""
    assumptions: str = ""


class PredictionVerifyIn(BaseModel):
    outcome: float
    outcome_note: str | None = None


class MetricsOut(BaseModel):
    verified_count: int
    mean_absolute_error: float | None
    mean_brier: float | None
    calibration: dict


def _out(p: Prediction) -> PredictionOut:
    return PredictionOut.model_validate(p)


@router.post("", response_model=PredictionOut, status_code=201)
def create(body: PredictionCreateIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> PredictionOut:
    if body.auto_estimate:
        est = estimate_completion(db, user.id)
        if not est.possible:
            pred = Prediction(
                user_id=user.id, question=body.question, verify_at=body.verify_at,
                status="refused", outcome_note=est.reason, data_used=est.data,
                risk_factors=body.risk_factors,
            )
            db.add(pred)
            db.commit()
            db.refresh(pred)
            log_action(db, user.id, "prediction.refused", {})
            return _out(pred)
        central, low, high = est.central, est.low, est.high
        data_used = est.data
        assumptions = est.assumptions
        unit = body.unit or "settimane"
    else:
        if body.estimate_central is None:
            raise AtlasError("missing_estimate",
                             "Senza auto_estimate servono estimate_central (e idealmente intervallo).")
        central, low, high = body.estimate_central, body.estimate_low, body.estimate_high
        data_used = {"source": "dichiarata dall'utente"}
        assumptions = body.assumptions
        unit = body.unit

    pred = Prediction(
        user_id=user.id, question=body.question,
        estimate_central=central, estimate_low=low, estimate_high=high, unit=unit,
        probability=body.probability, data_used=data_used,
        assumptions=assumptions, risk_factors=body.risk_factors,
        verify_at=body.verify_at,
    )
    db.add(pred)
    db.commit()
    db.refresh(pred)
    log_action(db, user.id, "prediction.created", {"prediction_id": str(pred.id)})
    return _out(pred)


@router.get("", response_model=list[PredictionOut])
def list_predictions(db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> list[PredictionOut]:
    rows = (db.query(Prediction).filter(Prediction.user_id == user.id)
            .order_by(Prediction.created_at.desc()).limit(100).all())
    return [_out(p) for p in rows]


@router.get("/metrics", response_model=MetricsOut)
def get_metrics(db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> MetricsOut:
    return MetricsOut(**metrics(db, user.id))


@router.post("/{prediction_id}/verify", response_model=PredictionOut)
def verify(prediction_id: uuid.UUID, body: PredictionVerifyIn,
           db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> PredictionOut:
    pred = db.get(Prediction, prediction_id)
    if pred is None or pred.user_id != user.id:
        raise AtlasError("prediction_not_found", "Previsione non trovata.", status=404)
    if pred.status == "verified":
        raise AtlasError("already_verified", "Previsione gia verificata.", status=409)
    if pred.status == "refused":
        raise AtlasError("refused_prediction", "Questa previsione era stata rifiutata: niente da verificare.", status=409)
    pred.outcome = body.outcome
    pred.outcome_note = body.outcome_note
    pred.status = "verified"
    pred.verified_at = datetime.now(timezone.utc)
    if pred.estimate_central is not None:
        pred.error = round(abs(body.outcome - pred.estimate_central), 3)
    if pred.probability is not None:
        pred.brier = brier_score(pred.probability, body.outcome >= 0.5)
    db.commit()
    db.refresh(pred)
    log_action(db, user.id, "prediction.verified", {"prediction_id": str(pred.id)})
    return _out(pred)
