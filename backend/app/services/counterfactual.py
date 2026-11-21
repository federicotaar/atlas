"""Counterfactual Engine (ricerca parallela, spec 5J).

Primo modello esplicito e dichiarato. NON e' inferenza causale: il motore
risponde a scenari "what-if" ricalcolando la stima di completamento del
Prediction Engine con input modificati (velocita scalata, settimane
migliori/peggiori osservate, attivita in piu' gia' chiuse), e dichiara
sempre modello, dati usati e precondizioni.

Modello (dichiarato in ogni risposta):
- la velocita' futura assomiglia a quella osservata, modificata come lo
  scenario richiede; e' lo stesso modello lineare del Prediction Engine,
  applicato due volte (fattuale e controfattuale) per mostrare il DELTA,
  che e' la risposta controfattuale;
- niente effetti causali nascosti, niente LLM che "immagina" il passato.

Precondizioni della specifica (sez. 5J) per trarre CONCLUSIONI: almeno
MIN_MONTHS mesi di storia registrata e almeno MIN_EVENTS eventi. Il motore
le misura sui dati reali e le riporta sempre: se non sono soddisfatte lo
scenario resta calcolabile ma e' marcato exploratory=True (esplorazione,
non conclusione). Se i dati non bastano nemmeno per la stima base (come il
Prediction Engine: almeno 2 settimane con completamenti) lo scenario e'
rifiutato con la ragione esplicita, mai inventato.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Event
from .predictions import _interval, estimate_completion, open_tasks, weekly_completion

# Precondizioni dalla specifica (sez. 5J): "almeno sei mesi di eventi
# affidabili e centinaia di eventi registrati".
MIN_MONTHS = 6
MIN_EVENTS = 200
_DAYS_PER_MONTH = 30.44

MODEL_DECLARATION = (
    "Modello lineare sulla velocita' di completamento osservata (lo stesso del "
    "Prediction Engine), applicato a input modificati dallo scenario: mostra il "
    "delta tra fattuale e controfattuale. Non e' inferenza causale."
)

EXPLORATORY_NOTE = (
    "Precondizioni della specifica (5J) non soddisfatte: servono almeno "
    f"{MIN_MONTHS} mesi di storia e {MIN_EVENTS} eventi registrati. "
    "Questo scenario e' un'esplorazione meccanica, non una conclusione."
)


def history_stats(db: Session, user_id: uuid.UUID,
                  now: datetime | None = None) -> dict:
    """Misure reali della storia registrata: quanti eventi e quanti mesi
    coprono (dal primo evento a oggi)."""
    now = now or datetime.now(timezone.utc)
    events = (db.query(Event).filter(Event.user_id == user_id)
              .order_by(Event.event_at).all())
    events_total = len(events)
    if events_total:
        first = events[0].event_at
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        months_covered = round(max((now - first).days, 0) / _DAYS_PER_MONTH, 2)
        first_event_at = first.isoformat()
    else:
        months_covered = 0.0
        first_event_at = None
    weekly = weekly_completion(db, user_id)
    return {
        "events_total": events_total,
        "first_event_at": first_event_at,
        "months_covered": months_covered,
        "weeks_with_completions": len(weekly),
        "tasks_completed_total": sum(weekly.values()),
        "tasks_open": open_tasks(db, user_id),
    }


def preconditions(db: Session, user_id: uuid.UUID,
                  now: datetime | None = None) -> dict:
    """Precondizioni 5J misurate, mai date per scontate."""
    stats = history_stats(db, user_id, now)
    missing: list[str] = []
    if stats["months_covered"] < MIN_MONTHS:
        missing.append(
            f"mesi di storia: {stats['months_covered']} su {MIN_MONTHS} richiesti")
    if stats["events_total"] < MIN_EVENTS:
        missing.append(
            f"eventi registrati: {stats['events_total']} su {MIN_EVENTS} richiesti")
    return {
        "min_months": MIN_MONTHS,
        "min_events": MIN_EVENTS,
        "months_covered": stats["months_covered"],
        "events_total": stats["events_total"],
        "met": not missing,
        "missing": missing,
    }


def select_rates(weekly: dict[tuple[int, int], int],
                 use_observed: str = "all") -> list[float]:
    """Velocita' settimanali osservate da usare nel modello:
    - all: tutte le settimane con completamenti;
    - best: solo la settimana migliore ("se ogni settimana fosse come la migliore");
    - worst: solo la peggiore ("se ogni settimana fosse come la peggiore")."""
    rates = [float(c) for c in weekly.values()]
    if not rates:
        return []
    if use_observed == "best":
        return [max(rates)]
    if use_observed == "worst":
        return [min(rates)]
    return rates


def scenario_estimate(remaining: int, rates: list[float],
                      velocity_factor: float = 1.0,
                      extra_completed_tasks: int = 0) -> dict:
    """Stima controfattuale pura (niente DB): scala le velocita' osservate del
    fattore richiesto e riduce le attivita' rimanenti di quelle gia' chiuse
    nello scenario. Restituisce settimane al completamento (centrale/bassa/alta)."""
    remaining_eff = max(remaining - extra_completed_tasks, 0)
    if remaining_eff == 0:
        return {"possible": True, "central": 0.0, "low": 0.0, "high": 0.0,
                "remaining_effective": 0}
    scaled = [max(r * velocity_factor, 1e-9) for r in rates]
    if not scaled:
        return {"possible": False,
                "reason": "Nessuna settimana con completamenti osservata: "
                          "non esiste una velocita' da modificare.",
                "remaining_effective": remaining_eff}
    central, low, high = _interval(remaining_eff, scaled)
    return {"possible": True, "central": central, "low": low, "high": high,
            "remaining_effective": remaining_eff}


def what_if(db: Session, user_id: uuid.UUID, scenarios: list[dict]) -> dict:
    """Confronto fattuale/controfattuale per gli scenari richiesti."""
    pre = preconditions(db, user_id)
    factual_est = estimate_completion(db, user_id)
    weekly = weekly_completion(db, user_id)
    remaining = open_tasks(db, user_id)

    factual = {
        "possible": factual_est.possible,
        "reason": factual_est.reason or None,
        "central": factual_est.central,
        "low": factual_est.low,
        "high": factual_est.high,
        "data": factual_est.data,
        "assumptions": factual_est.assumptions or None,
    }

    results: list[dict] = []
    for sc in scenarios:
        label = sc.get("label") or "scenario"
        use_observed = sc.get("use_observed", "all")
        factor = sc.get("velocity_factor", 1.0)
        extra = sc.get("extra_completed_tasks", 0)
        rates = select_rates(weekly, use_observed)
        if not factual_est.possible:
            res = {"label": label, "possible": False, "reason": factual_est.reason}
        else:
            est = scenario_estimate(remaining, rates, factor, extra)
            res = {"label": label, **est}
            if est["possible"] and factual_est.central:
                res["delta_weeks_vs_factual"] = round(est["central"] - factual_est.central, 1)
        res["input"] = {"velocity_factor": factor, "extra_completed_tasks": extra,
                        "use_observed": use_observed}
        res["exploratory"] = not pre["met"]
        results.append(res)

    return {
        "model": MODEL_DECLARATION,
        "preconditions": pre,
        "exploratory_note": None if pre["met"] else EXPLORATORY_NOTE,
        "factual": factual,
        "scenarios": results,
    }
