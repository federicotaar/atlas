"""Sonda prestazioni (fase 6): misura la latenza degli endpoint chiave
in-process (TestClient) su un dataset sintetico e stampa p50/p95.

Uso: ATLAS_DATABASE_URL=... python scripts/perf.py
Non e un benchmark di carico: verifica che le operazioni comuni restino
nell'ordine dei millisecondi su dati realistici e segnala regressioni
evidenti (soglia morbida stampata, nessun fallimento automatico).
"""
import os
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ATLAS_EMBED_PROVIDER", "local")
os.environ.setdefault("ATLAS_LLM_PROVIDER", "extractive")
os.environ.setdefault("ATLAS_JWT_SECRET", "perf-secret")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.db import get_engine  # noqa: E402

NOW = datetime.now(timezone.utc)
REPS = 30


def main() -> None:
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client = TestClient(app)

    email = f"perf-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "password-segreta-1"})
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    src = client.post("/sources", json={"kind": "chat", "label": "perf"}, headers=h).json()["id"]
    project = client.post("/projects", json={"name": "Perf"}, headers=h).json()["id"]

    # dataset sintetico realistico: 300 eventi su 100 giorni, 60 memorie
    for i in range(300):
        client.post("/events", json={
            "type": f"tipo-{i % 7}", "payload": {"i": i}, "project_id": project,
            "source_id": src,
            "event_at": (NOW - timedelta(days=i % 100, hours=i % 24)).isoformat(),
        }, headers=h)
    for i in range(60):
        client.post("/memories", json={
            "content": f"Memoria sintetica numero {i} sul progetto perf",
            "source_id": src, "kind": "fact", "origin": "stated", "confidence": 0.9,
        }, headers=h)

    def probe(name: str, fn) -> None:
        samples = []
        for _ in range(REPS):
            t0 = time.perf_counter()
            res = fn()
            samples.append((time.perf_counter() - t0) * 1000)
            assert res.status_code == 200, (name, res.status_code, res.text[:200])
        p50 = statistics.median(samples)
        p95 = sorted(samples)[int(REPS * 0.95) - 1]
        print(f"{name:35s} p50 {p50:7.1f} ms   p95 {p95:7.1f} ms")

    start = (NOW - timedelta(days=100)).isoformat()
    end = NOW.isoformat()
    probe("GET /events", lambda: client.get("/events", headers=h))
    probe("GET /timemachine/timeline", lambda: client.get(
        "/timemachine/timeline", params={"from": start, "to": end}, headers=h))
    probe("GET /timemachine/state-at", lambda: client.get(
        "/timemachine/state-at", params={"at": end}, headers=h))
    probe("GET /timemachine/compare", lambda: client.get(
        "/timemachine/compare",
        params={"a_start": start, "a_end": (NOW - timedelta(days=50)).isoformat(),
                "b_start": (NOW - timedelta(days=50)).isoformat(), "b_end": end},
        headers=h))
    probe("GET /timemachine/abandoned-goals", lambda: client.get(
        "/timemachine/abandoned-goals", headers=h))
    probe("GET /timemachine/recurring", lambda: client.get(
        "/timemachine/recurring", headers=h))
    probe("GET /dashboard", lambda: client.get("/dashboard", headers=h))
    probe("POST /memories/search", lambda: client.post(
        "/memories/search", json={"query": "progetto perf"}, headers=h))
    print("Dataset: 300 eventi, 60 memorie, 1 progetto. Soglia morbida: "
          "operazioni comuni sotto ~500 ms p95 in-process.")


if __name__ == "__main__":
    main()
