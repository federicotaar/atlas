"""Benchmark di recupero ripetibile (specifica: "si misura").

Semina un persona SINTETICO ("Mario Rossi", dati inventati) con memorie note,
poi pone domande con risposta attesa e misura se la memoria giusta compare
tra i risultati e tra le citazioni di /ask.

Uso: ATLAS_DATABASE_URL=... python scripts/benchmark.py
Richiede il database (in CI gira dopo pytest, stesso service Postgres).
"""
import os
import sys

os.environ.setdefault("ATLAS_DATABASE_URL", "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_bench")
os.environ.setdefault("ATLAS_EMBED_PROVIDER", "local")
os.environ.setdefault("ATLAS_LLM_PROVIDER", "extractive")
os.environ.setdefault("ATLAS_JWT_SECRET", "benchmark-secret")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Worker codici finto (stesso approccio di tests/conftest.py): il benchmark
# gira fuori da pytest e si porta il suo fake in-process, con firma Ed25519
# reale cosi il flusso register a 3 passi resta quello vero.
import base64  # noqa: E402
import json as _json  # noqa: E402
import time  # noqa: E402
import uuid as _uuid  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.services import codes as codes_svc  # noqa: E402

_priv = Ed25519PrivateKey.generate()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


get_settings().codes_public_key = _b64u(_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
get_settings().codes_api_url = "https://codes.test"


def _fake_post(path: str, payload: dict):
    if path.endswith("/request"):
        return 200, {"ok": True, "ttl_seconds": 600}
    now = int(time.time())
    pl = _json.dumps({
        "iss": "atlas-codes", "email": str(payload.get("email", "")).lower(),
        "purpose": payload.get("purpose"), "iat": now, "exp": now + 300, "jti": _uuid.uuid4().hex,
    }).encode()
    return 200, {"ok": True, "attestation": _b64u(pl) + "." + _b64u(_priv.sign(pl))}


codes_svc._post = _fake_post

# Persona sintetica: nessun dato reale.
SEED = [
    "Il progetto Mercurio usa PostgreSQL 16 con pgvector per la ricerca",
    "La riunione settimanale del progetto Mercurio e il martedi alle 10",
    "Mario Rossi ha deciso di usare FastAPI per il backend",
    "La scadenza della prima versione di Mercurio e il 20 dicembre 2026",
    "Il server di staging di Mercurio e ospitato su una macchina locale",
    "Il budget mensile per il progetto Mercurio e zero euro, solo servizi gratuiti",
    "Il designer del progetto Mercurio e Giulia Bianchi",
    "La libreria di test scelta per Mercurio e pytest",
]

CASES = [
    ("Quale database usa il progetto Mercurio?", "PostgreSQL"),
    ("Quando e la riunione settimanale?", "martedi alle 10"),
    ("Chi e il designer del progetto?", "Giulia Bianchi"),
    ("Quale libreria si usa per i test?", "pytest"),
    ("Qual e la scadenza della prima versione?", "20 dicembre 2026"),
    ("Quanto costa l'infrastruttura al mese?", "zero euro"),
    ("Dove e ospitato il server di staging?", "macchina locale"),
    ("Quale framework usa il backend?", "FastAPI"),
]


def main() -> int:
    engine = create_engine(os.environ["ATLAS_DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    client = TestClient(app)
    email = "benchmark@example.com"
    client.post("/auth/codes/request", json={"email": email, "purpose": "register"})
    r = client.post("/auth/codes/check", json={"email": email, "code": "000000"})
    assert r.status_code == 200, r.text
    r = client.post("/auth/register", json={
        "email": email, "password": "benchmark-123", "signup_token": r.json()["signup_token"],
    })
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    sid = client.post("/sources", json={"kind": "benchmark", "label": "benchmark"},
                      headers=headers).json()["id"]
    for content in SEED:
        r = client.post("/memories", json={"content": content, "source_id": sid}, headers=headers)
        assert r.status_code == 201, r.text

    hits = 0
    rows = []
    for question, expected in CASES:
        data = client.post("/ask", json={"text": question}, headers=headers).json()
        cited = " | ".join(c["content"] for c in data["citations"])
        ok = expected.lower() in cited.lower()
        hits += ok
        rows.append((question, expected, ok))

    total = len(CASES)
    accuracy = hits / total
    print(f"\nBenchmark recupero: {hits}/{total} = {accuracy:.0%}")
    for q, e, ok in rows:
        print(f"  [{'OK' if ok else 'NO'}] {q}  (atteso: {e})")

    # il benchmark fallisce se sotto l'80% (soglia di esempio dalla specifica fase 1)
    return 0 if accuracy >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
