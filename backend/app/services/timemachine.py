"""Digital Time Machine (fase 5, spec 5H).

Gli eventi sono solo in aggiunta, quindi lo stato a una data passata si
ricostruisce rileggendo i dati registrati fino a quella data. Funzioni:
cronologia, stato a una data, confronto tra due periodi, obiettivi
abbandonati, attivita ricorrenti. Le lacune (periodi senza dati) vengono
sempre mostrate esplicitamente e mai riempite.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import Event, Memory, Prediction, Project


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def find_gaps(db: Session, user_id: uuid.UUID, start: datetime, end: datetime,
              bucket_days: int | None = None) -> list[dict]:
    """Lacune: intervalli dentro [start, end] senza alcun evento.

    Mai riempite: vengono solo elencate. Bucket giornalieri fino a 62 giorni
    di intervallo, settimanali oltre."""
    start = _aware(start)
    end = _aware(end)
    if end <= start:
        return []
    span_days = (end - start).days
    if bucket_days is None:
        bucket_days = 1 if span_days <= 62 else 7
    rows = (db.query(Event.event_at)
            .filter(Event.user_id == user_id,
                    Event.event_at >= start, Event.event_at <= end)
            .all())
    buckets_with_data: set[int] = set()
    for (event_at,) in rows:
        idx = int((_aware(event_at) - start).total_seconds() // (bucket_days * 86400))
        buckets_with_data.add(idx)
    n_buckets = int((end - start).total_seconds() // (bucket_days * 86400)) + 1
    gaps: list[dict] = []
    run_start: int | None = None
    for i in range(n_buckets):
        if i in buckets_with_data:
            if run_start is not None:
                g0 = start + timedelta(days=run_start * bucket_days)
                g1 = min(start + timedelta(days=i * bucket_days), end)
                gaps.append({"from": g0.isoformat(), "to": g1.isoformat(),
                             "days": round((g1 - g0).total_seconds() / 86400, 2)})
                run_start = None
        elif run_start is None:
            run_start = i
    if run_start is not None:
        g0 = start + timedelta(days=run_start * bucket_days)
        gaps.append({"from": g0.isoformat(), "to": end.isoformat(),
                     "days": round((end - g0).total_seconds() / 86400, 2)})
    return gaps


def timeline(db: Session, user_id: uuid.UUID, start: datetime, end: datetime,
             project_id: uuid.UUID | None = None, limit: int = 500) -> dict:
    """Cronologia: eventi nell'intervallo in ordine temporale, con fonte,
    piu le lacune dell'intervallo."""
    start = _aware(start)
    end = _aware(end)
    q = (db.query(Event)
         .filter(Event.user_id == user_id,
                 Event.event_at >= start, Event.event_at <= end))
    if project_id is not None:
        q = q.filter(Event.project_id == project_id)
    events = q.order_by(Event.event_at.asc()).limit(limit).all()
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(events),
        "events": [
            {"id": str(e.id), "type": e.type, "event_at": e.event_at.isoformat(),
             "project_id": str(e.project_id) if e.project_id else None,
             "source_id": str(e.source_id) if e.source_id else None,
             "payload": e.payload}
            for e in events
        ],
        "gaps": find_gaps(db, user_id, start, end),
    }


def memories_as_of(db: Session, user_id: uuid.UUID, at: datetime) -> list[Memory]:
    """Memorie attive alla data `at`: registrate entro `at` e non ancora
    sostituite a quella data (la sostituzione conta dal recorded_at della
    memoria che sostituisce)."""
    at = _aware(at)
    all_mem = (db.query(Memory)
               .filter(Memory.user_id == user_id, Memory.recorded_at <= at)
               .all())
    rows = (db.query(Memory.id, Memory.recorded_at)
            .filter(Memory.user_id == user_id).all())
    recorded: dict[uuid.UUID, datetime] = {mid: _aware(rec) for mid, rec in rows}
    active = []
    for m in all_mem:
        if m.superseded_by is None:
            active.append(m)
            continue
        repl = recorded.get(m.superseded_by)
        if repl is None or repl > at:
            active.append(m)
    return active


def state_at(db: Session, user_id: uuid.UUID, at: datetime,
             project_id: uuid.UUID | None = None) -> dict:
    """Stato ricostruito a una data: memorie attive, eventi fino alla data
    (conteggi per tipo), previsioni come erano alla data.

    Il record Project non ha storia propria: lo stato riportato e quello
    attuale e viene marcato come tale."""
    at = _aware(at)
    mems = memories_as_of(db, user_id, at)
    eq = (db.query(Event)
          .filter(Event.user_id == user_id, Event.event_at <= at))
    if project_id is not None:
        eq = eq.filter(Event.project_id == project_id)
    events = eq.order_by(Event.event_at.asc()).all()
    by_type: dict[str, int] = {}
    last_by_type: dict[str, dict] = {}
    for e in events:
        by_type[e.type] = by_type.get(e.type, 0) + 1
        last_by_type[e.type] = {"event_id": str(e.id), "event_at": e.event_at.isoformat()}
    preds = (db.query(Prediction)
             .filter(Prediction.user_id == user_id, Prediction.created_at <= at)
             .all())
    pred_list = []
    for p in preds:
        # Ricostruzione dello stato alla data: verificata solo se la verifica
        # era gia avvenuta entro `at`; altrimenti risultava aperta.
        status_then = "verified" if (p.verified_at is not None and _aware(p.verified_at) <= at) else "open"
        pred_list.append({"id": str(p.id), "question": p.question,
                          "status_at_date": status_then,
                          "verify_at": p.verify_at.isoformat()})
    project_info = None
    if project_id is not None:
        project = db.get(Project, project_id)
        if project is not None and project.user_id == user_id:
            project_info = {
                "id": str(project.id), "name": project.name,
                "status": project.status,
                "status_note": ("il record progetto non ha storia: questo e lo "
                                "stato attuale, non necessariamente quello alla data"),
                "target_date": project.target_date.isoformat() if project.target_date else None,
            }
    first_event = events[0].event_at if events else None
    return {
        "at": at.isoformat(),
        "project": project_info,
        "memories": {
            "count": len(mems),
            "items": [
                {"id": str(m.id), "content": m.content, "kind": m.kind,
                 "confidence": m.confidence, "recorded_at": m.recorded_at.isoformat(),
                 "source_id": str(m.source_id)}
                for m in sorted(mems, key=lambda m: m.recorded_at)
            ],
        },
        "events": {
            "count": len(events),
            "by_type": by_type,
            "last_by_type": last_by_type,
            "first_event_at": first_event.isoformat() if first_event else None,
        },
        "predictions": {"count": len(pred_list), "items": pred_list},
        "gaps": (find_gaps(db, user_id, first_event, at)
                 if first_event is not None else []),
    }


def compare_periods(db: Session, user_id: uuid.UUID,
                    a_start: datetime, a_end: datetime,
                    b_start: datetime, b_end: datetime) -> dict:
    """Confronto tra due periodi: eventi per tipo, memorie registrate per
    tipo, previsioni create/verificate, lacune di ciascun periodo."""

    def period_stats(start: datetime, end: datetime) -> dict:
        start = _aware(start)
        end = _aware(end)
        events = (db.query(Event)
                  .filter(Event.user_id == user_id,
                          Event.event_at >= start, Event.event_at <= end)
                  .all())
        by_type: dict[str, int] = {}
        ids: list[str] = []
        for e in events:
            by_type[e.type] = by_type.get(e.type, 0) + 1
            ids.append(str(e.id))
        mems = (db.query(Memory)
                .filter(Memory.user_id == user_id,
                        Memory.recorded_at >= start, Memory.recorded_at <= end)
                .all())
        mem_by_kind: dict[str, int] = {}
        for m in mems:
            mem_by_kind[m.kind] = mem_by_kind.get(m.kind, 0) + 1
        preds_created = (db.query(Prediction)
                         .filter(Prediction.user_id == user_id,
                                 Prediction.created_at >= start,
                                 Prediction.created_at <= end).count())
        preds_verified = (db.query(Prediction)
                          .filter(Prediction.user_id == user_id,
                                  Prediction.verified_at.isnot(None),
                                  Prediction.verified_at >= start,
                                  Prediction.verified_at <= end).count())
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "events": {"count": len(events), "by_type": by_type,
                       "sample_ids": ids[:3] + ids[-1:] if len(ids) > 4 else ids},
            "memories": {"count": len(mems), "by_kind": mem_by_kind},
            "predictions": {"created": preds_created, "verified": preds_verified},
            "gaps": find_gaps(db, user_id, start, end),
        }

    return {"period_a": period_stats(a_start, a_end),
            "period_b": period_stats(b_start, b_end)}


def abandoned_goals(db: Session, user_id: uuid.UUID, idle_days: int = 14,
                    now: datetime | None = None) -> dict:
    """Obiettivi abbandonati: progetti attivi senza eventi da `idle_days`
    giorni, o con data obiettivo gia passata. Mai deduzioni oltre i dati:
    ogni voce mostra l'ultimo evento da cui deriva."""
    now = _aware(now or datetime.now(timezone.utc))
    cutoff = now - timedelta(days=idle_days)
    projects = (db.query(Project)
                .filter(Project.user_id == user_id, Project.status == "active")
                .all())
    items = []
    for p in projects:
        last = (db.query(Event)
                .filter(Event.user_id == user_id, Event.project_id == p.id)
                .order_by(Event.event_at.desc()).first())
        last_at = _aware(last.event_at) if last else None
        reasons = []
        if last_at is None:
            reasons.append("nessun evento registrato")
        elif last_at < cutoff:
            reasons.append(f"nessun evento da {int((now - last_at).days)} giorni")
        if p.target_date is not None and p.target_date < now.date():
            reasons.append("data obiettivo passata")
        if reasons:
            items.append({
                "project_id": str(p.id), "name": p.name,
                "target_date": p.target_date.isoformat() if p.target_date else None,
                "reasons": reasons,
                "derived_from": {
                    "last_event_id": str(last.id) if last else None,
                    "last_event_at": last_at.isoformat() if last_at else None,
                },
            })
    return {"idle_days": idle_days, "now": now.isoformat(), "count": len(items),
            "items": items}


def recurring_activities(db: Session, user_id: uuid.UUID,
                         min_occurrences: int = 3) -> dict:
    """Attivita ricorrenti: tipi di evento con almeno `min_occurrences`
    occorrenze, con cadenza media e regolarita (coefficiente di variazione
    degli intervalli). Ogni voce mostra eventi campione da cui deriva."""
    events = (db.query(Event)
              .filter(Event.user_id == user_id)
              .order_by(Event.event_at.asc()).all())
    by_type: dict[str, list[Event]] = {}
    for e in events:
        by_type.setdefault(e.type, []).append(e)
    items = []
    for etype, evs in by_type.items():
        if len(evs) < min_occurrences:
            continue
        times = [_aware(e.event_at) for e in evs]
        intervals = [(times[i + 1] - times[i]).total_seconds() / 86400
                     for i in range(len(times) - 1)]
        mean = sum(intervals) / len(intervals) if intervals else 0.0
        if intervals and mean > 0:
            var = sum((x - mean) ** 2 for x in intervals) / len(intervals)
            cv = (var ** 0.5) / mean
        else:
            cv = None
        sample = [str(e.id) for e in (evs[:2] + evs[-1:] if len(evs) > 3 else evs)]
        items.append({
            "type": etype,
            "occurrences": len(evs),
            "first_at": times[0].isoformat(),
            "last_at": times[-1].isoformat(),
            "mean_interval_days": round(mean, 2),
            "regularity_cv": round(cv, 3) if cv is not None else None,
            "derived_from": {"sample_event_ids": sample},
        })
    items.sort(key=lambda x: x["occurrences"], reverse=True)
    return {"min_occurrences": min_occurrences, "count": len(items), "items": items}
