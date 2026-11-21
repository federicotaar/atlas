"""Validazione ripetibile del Prediction Engine su storie sintetiche dichiarate.

Risponde alla nota onesta della roadmap ("Prediction Engine: implementato,
non validato"): qui l'estimatore viene misurato, non solo eseguito.

Metodo (dichiarato): per ogni regime sintetico seminiamo una storia nota di
completamenti settimanali in un database SQLite temporaneo, chiediamo la
stima al vero `estimate_completion`, poi facciamo avanzare il processo noto
e confrontiamo il tempo reale di completamento con la forcella stimata.

Limiti (dichiarati): i regimi sono sintetici e generati dallo stesso modello
lineare che l'estimatore assume (velocita costante). La validazione misura
che il meccanismo calcoli bene cio' che dichiara di calcolare; NON misura
l'accuratezza su dati reali, che richiede mesi di utilizzo vero.

Uso: python scripts/validate_predictions.py
Esce 1 se le regole base della specifica vengono violate (stima senza dati,
forcella disordinata, rifiuto mancato).
"""
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.NamedTemporaryFile(prefix="atlas_predval_", suffix=".db", delete=False)
_tmp.close()
os.environ["ATLAS_DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.setdefault("ATLAS_EMBED_PROVIDER", "local")
os.environ.setdefault("ATLAS_LLM_PROVIDER", "extractive")
os.environ.setdefault("ATLAS_JWT_SECRET", "validation-only-not-for-production")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy.orm import Session  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.models import Base, Memory, Source, User  # noqa: E402
from app.services.predictions import estimate_completion  # noqa: E402

NOW = datetime.now(timezone.utc)


def seed_history(db: Session, user_id: uuid.UUID, weekly_rates: list[int],
                 open_tasks: int) -> None:
    """Settimane di completamenti noti (il piu' recente per ultimo) e un
    numero dichiarato di attivita' aperte."""
    source = Source(id=uuid.uuid4(), user_id=user_id, kind="chat",
                    label="validazione sintetica")
    db.add(source)
    db.flush()
    for weeks_ago, count in enumerate(reversed(weekly_rates), start=1):
        for i in range(count):
            newer = Memory(id=uuid.uuid4(), user_id=user_id, source_id=source.id,
                           kind="fact",
                           content=f"chiusura w-{weeks_ago} #{i}", origin="stated",
                           confidence=0.9,
                           recorded_at=NOW - timedelta(weeks=weeks_ago))
            db.add(newer)
            db.flush()  # la chiusura deve esistere prima del riferimento
            old = Memory(id=uuid.uuid4(), user_id=user_id, source_id=source.id,
                         kind="task",
                         content=f"task chiuso w-{weeks_ago} #{i}", origin="stated",
                         confidence=0.9,
                         recorded_at=NOW - timedelta(weeks=weeks_ago, days=3),
                         superseded_by=newer.id)
            db.add(old)
    for i in range(open_tasks):
        db.add(Memory(id=uuid.uuid4(), user_id=user_id, source_id=source.id,
                      kind="task",
                      content=f"task aperto #{i}", origin="stated", confidence=0.9,
                      recorded_at=NOW - timedelta(days=1)))
    db.commit()


def main() -> int:
    engine = get_engine()
    Base.metadata.create_all(engine)
    failures: list[str] = []
    rows: list[dict] = []

    regimes = [
        # nome, velocita' settimanali osservate (vecchie -> recenti), aperte,
        # velocita' futura reale del processo (dichiarata: e' una scelta del
        # regime, non un dato misurato)
        ("costante 3/settimana", [3] * 12, 9, 3.0),
        ("lenta 1/settimana", [1] * 8, 4, 1.0),
        ("a singhiozzo (1/5)", [1, 5] * 6, 9, 3.0),
        ("in crescita (futuro piu' veloce)", [1, 2, 3, 4, 5, 6], 10, 7.0),
        ("in calo (futuro piu' lento)", [6, 5, 4, 3, 2, 1], 10, 1.0),
        ("troppo poca storia", [4], 5, 4.0),
    ]

    with Session(engine) as db:
        for name, rates, remaining, future_rate in regimes:
            user = User(email=f"val-{uuid.uuid4().hex[:8]}@example.com",
                        password_hash="x")
            db.add(user)
            db.commit()
            seed_history(db, user.id, rates, remaining)
            est = estimate_completion(db, user.id)
            row = {"regime": name, "settimane_osservate": len(rates),
                   "aperte": remaining}
            if len(rates) < 2:
                row["esito"] = "rifiutata (dati troppo pochi)"
                if est.possible:
                    failures.append(f"{name}: stimata invece di rifiutata")
            elif not est.possible:
                row["esito"] = f"ERRORE: rifiutata ({est.reason})"
                failures.append(f"{name}: rifiutata con dati sufficienti")
            else:
                actual = remaining / future_rate
                covered = est.low <= actual <= est.high
                row["stima"] = f"{est.low}-{est.central}-{est.high} settimane"
                row["reale"] = f"{actual:.1f} settimane (a {future_rate}/sett.)"
                row["errore"] = f"{abs(est.central - actual):.1f} settimane"
                row["forcella_copre"] = "si" if covered else "no"
                if not (est.low <= est.central <= est.high):
                    failures.append(f"{name}: forcella disordinata {est.low}/{est.central}/{est.high}")
            rows.append(row)

    print("Validazione Prediction Engine su regimi sintetici dichiarati")
    print(f"data: {NOW.date().isoformat()}  (metodo e limiti nel docstring)")
    print()
    for r in rows:
        print(f"- {r['regime']}: {r['esito'] if 'esito' in r else ''}")
        if "stima" in r:
            print(f"    stima {r['stima']} | reale {r['reale']} | "
                  f"errore {r['errore']} | forcella copre: {r['forcella_copre']}")
    covered_n = sum(1 for r in rows if r.get("forcella_copre") == "si")
    estimable = [r for r in rows if "stima" in r]
    errors = [float(r["errore"].split()[0]) for r in estimable]
    if estimable:
        print()
        print(f"Regimi stimabili: {len(estimable)}; forcella che copre il reale: "
              f"{covered_n}/{len(estimable)}; errore medio della stima centrale: "
              f"{sum(errors) / len(errors):.2f} settimane")
    if failures:
        print()
        for f in failures:
            print(f"VIOLAZIONE: {f}")
        return 1
    print()
    print("Regole base rispettate: rifiuto con pochi dati, forcelle ordinate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
