"""Prediction Engine (fase 3, spec 5E).

Stima di completamento da velocita reale: attivita (memorie kind='task')
completate per settimana (completata = sostituita/chiusa via superseded_by).
L'intervallo viene dalla variabilita delle settimane osservate.

Regole della specifica applicate:
- mai presentata come certezza: la risposta porta sempre intervallo e
  probabilita dichiarata;
- se i dati sono troppo pochi (< 2 settimane di osservazione o nessuna
  attivita completata) il sistema lo dice invece di stimare (status refused);
- alla verifica: errore assoluto per le stime numeriche, Brier per le
  probabilita; metriche aggregate con curva di calibrazione a bucket.
"""
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Memory, Prediction

MIN_WEEKS = 2


@dataclass
class Estimate:
    possible: bool
    reason: str = ""
    central: float | None = None
    low: float | None = None
    high: float | None = None
    data: dict = field(default_factory=dict)
    assumptions: str = ""


def _week_key(dt: datetime) -> tuple[int, int]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.isocalendar()
    return (iso.year, iso.week)


def weekly_completion(db: Session, user_id: uuid.UUID) -> dict[tuple[int, int], int]:
    """Attivita completate per settimana: memorie task sostituite, contate
    sulla settimana di registrazione della memoria che le ha sostituite."""
    superseded = (
        db.query(Memory)
        .filter(Memory.user_id == user_id, Memory.kind == "task",
                Memory.superseded_by.isnot(None))
        .all()
    )
    counts: dict[tuple[int, int], int] = {}
    for m in superseded:
        newer = db.get(Memory, m.superseded_by)
        # il completamento conta alla data dell'evento (se nota), non alla
        # registrazione: chiudere oggi un'attivita finita la scorsa settimana
        # deve contare sulla settimana giusta
        when = (newer.event_at or newer.recorded_at) if newer is not None else m.recorded_at
        key = _week_key(when)
        counts[key] = counts.get(key, 0) + 1
    return counts


def open_tasks(db: Session, user_id: uuid.UUID) -> int:
    return (db.query(Memory)
            .filter(Memory.user_id == user_id, Memory.kind == "task",
                    Memory.superseded_by.is_(None))
            .count())


def estimate_completion(db: Session, user_id: uuid.UUID) -> Estimate:
    """Stima le settimane al completamento delle attivita aperte."""
    weekly = weekly_completion(db, user_id)
    remaining = open_tasks(db, user_id)
    weeks_observed = len(weekly)
    completed = sum(weekly.values())
    data = {
        "weeks_observed": weeks_observed,
        "tasks_completed_total": completed,
        "tasks_open": remaining,
        "weekly_completions": {f"{y}-W{w:02d}": c for (y, w), c in sorted(weekly.items())},
    }
    if weeks_observed < MIN_WEEKS or completed == 0:
        return Estimate(
            possible=False,
            reason=(f"Dati troppo pochi per stimare: servono almeno {MIN_WEEKS} settimane "
                    f"con attivita completate; ho {weeks_observed} settimane e {completed} "
                    "attivita completate."),
            data=data,
        )
    rates = list(weekly.values())
    mean_rate = statistics.fmean(rates)
    spread = statistics.pstdev(rates) if len(rates) > 1 else 0.0
    central = remaining / mean_rate
    slow = max(mean_rate - spread, 1e-9)
    fast = mean_rate + spread
    low = remaining / fast
    high = remaining / slow
    return Estimate(
        possible=True,
        central=round(central, 1),
        low=round(low, 1),
        high=round(high, 1),
        data=data,
        assumptions=("Velocita futura simile alla media delle settimane osservate; "
                     "l'insieme delle attivita aperte non cambia."),
    )


def brier_score(probability: float, happened: bool) -> float:
    return round((probability - (1.0 if happened else 0.0)) ** 2, 4)


def metrics(db: Session, user_id: uuid.UUID) -> dict:
    """Accuratezza storica (spec 5E: errore assoluto, Brier, calibrazione)."""
    verified = (db.query(Prediction)
                .filter(Prediction.user_id == user_id, Prediction.status == "verified")
                .all())
    abs_errors = [p.error for p in verified if p.error is not None]
    briers = [p.brier for p in verified if p.brier is not None]
    buckets: dict[str, dict] = {}
    for p in verified:
        if p.probability is None or p.outcome is None:
            continue
        b = min(int(p.probability * 10), 9)
        key = f"{b / 10:.1f}-{(b + 1) / 10:.1f}"
        buck = buckets.setdefault(key, {"count": 0, "avg_probability": 0.0, "frequency": 0.0})
        buck["count"] += 1
        buck["avg_probability"] += p.probability
        buck["frequency"] += 1.0 if p.outcome == 1.0 else 0.0
    for buck in buckets.values():
        n = buck["count"]
        buck["avg_probability"] = round(buck["avg_probability"] / n, 3)
        buck["frequency"] = round(buck["frequency"] / n, 3)
    return {
        "verified_count": len(verified),
        "mean_absolute_error": round(statistics.fmean(abs_errors), 3) if abs_errors else None,
        "mean_brier": round(statistics.fmean(briers), 4) if briers else None,
        "calibration": buckets,
    }


MANUAL_SOURCE = "dichiarata dall'utente"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def due_predictions(db: Session, user_id: uuid.UUID,
                    now: datetime | None = None) -> list[Prediction]:
    """Previsioni aperte la cui data di verifica e arrivata (spec 5E:
    alla data di verifica il sistema confronta con l'esito reale)."""
    now = now or datetime.now(timezone.utc)
    return (db.query(Prediction)
            .filter(Prediction.user_id == user_id, Prediction.status == "open",
                    Prediction.verify_at <= now)
            .order_by(Prediction.verify_at).all())


def _last_closure_after(db: Session, user_id: uuid.UUID,
                        since: datetime) -> datetime | None:
    """Ultima chiusura di attivita (task sostituito) successiva a `since`."""
    superseded = (db.query(Memory)
                  .filter(Memory.user_id == user_id, Memory.kind == "task",
                          Memory.superseded_by.isnot(None))
                  .all())
    last = None
    for m in superseded:
        newer = db.get(Memory, m.superseded_by)
        when = (newer.event_at or newer.recorded_at) if newer is not None else m.recorded_at
        when = _aware(when)
        if when > since and (last is None or when > last):
            last = when
    return last


def _live_outcome(db: Session, pred: Prediction, now: datetime) -> tuple[float, str]:
    """Esito misurato dallo stato reale delle attivita (non dichiarato):
    se non restano attivita aperte, le settimane effettive dalla creazione
    all'ultima chiusura nota; altrimenti le settimane trascorse, dichiarate
    come limite inferiore perche le attivita non sono ancora finite."""
    created = _aware(pred.created_at)
    remaining = open_tasks(db, pred.user_id)
    if remaining == 0:
        last = _last_closure_after(db, pred.user_id, created)
        end = last or now
        outcome = round(max((end - created).total_seconds() / (7 * 86400), 0.0), 2)
        note = ("Valutazione automatica alla scadenza: attivita aperte esaurite; "
                "esito misurato sullo stato reale delle attivita, non dichiarato.")
    else:
        outcome = round(max((now - created).total_seconds() / (7 * 86400), 0.0), 2)
        note = (f"Valutazione automatica alla scadenza: {remaining} attivita ancora aperte; "
                "l'esito e il tempo trascorso (limite inferiore), la stima resta da confermare.")
    return outcome, note


def evaluate_due(db: Session, user_id: uuid.UUID,
                 now: datetime | None = None) -> tuple[list[Prediction], list[Prediction]]:
    """Verifica automatica delle previsioni auto_estimate arrivate a scadenza:
    l'esito viene calcolato dallo stato reale delle attivita. Le previsioni
    con stima manuale restano aperte e vanno verificate a mano.
    Restituisce (valutate, in_attesa_di_verifica_manuale)."""
    now = now or datetime.now(timezone.utc)
    evaluated: list[Prediction] = []
    pending_manual: list[Prediction] = []
    for pred in due_predictions(db, user_id, now):
        if (pred.data_used or {}).get("source") == MANUAL_SOURCE:
            pending_manual.append(pred)
            continue
        outcome, note = _live_outcome(db, pred, now)
        pred.outcome = outcome
        pred.outcome_note = note
        pred.status = "verified"
        pred.verified_at = now
        if pred.estimate_central is not None:
            pred.error = round(abs(outcome - pred.estimate_central), 3)
        if pred.probability is not None:
            pred.brier = brier_score(pred.probability, outcome >= 0.5)
        evaluated.append(pred)
    db.commit()
    return evaluated, pending_manual
