"""Setup test: database Postgres+pgvector reale (in CI: service container).

ATLAS_DATABASE_URL deve puntare a un database di test vuoto; le tabelle vengono
create e distrutte a fine sessione. I provider modelli sono quelli locali
(deterministici, gratis) cosi i test non dipendono da Ollama.
"""
import os
import uuid

os.environ.setdefault("ATLAS_DATABASE_URL", "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test")
os.environ.setdefault("ATLAS_EMBED_PROVIDER", "local")
os.environ.setdefault("ATLAS_LLM_PROVIDER", "extractive")
os.environ.setdefault("ATLAS_JWT_SECRET", "test-secret-not-for-production")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


def _test_engine():
    # Stesso costruttore dell'app: gestisce anche SQLite (PRAGMA, sqlite-vec,
    # schema completo) oltre a Postgres.
    from app.db import get_engine

    return get_engine()


@pytest.fixture(scope="session")
def _setup_db():
    url = os.environ["ATLAS_DATABASE_URL"]
    engine = _test_engine()
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("database di test non raggiungibile", allow_module_level=True)
    with engine.begin() as conn:
        if not url.startswith("sqlite"):
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        else:
            conn.execute(text("DROP TABLE IF EXISTS memory_vec"))
            conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
            from app.db import FTS_DDL, VEC_DDL

            conn.execute(text(VEC_DDL))
            conn.execute(text(FTS_DDL))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(_setup_db):
    return TestClient(app)


@pytest.fixture()
def db_session(_setup_db):
    from sqlalchemy.orm import Session
    with Session(_test_engine()) as session:
        yield session


@pytest.fixture()
def db_user(db_session):
    from app.models import User
    user = User(email=f"db-{uuid.uuid4().hex[:10]}@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


TEST_CODE = "123456"


@pytest.fixture(autouse=True)
def fake_codes_worker(monkeypatch):
    """Worker codici finto: nessuna rete, firma Ed25519 reale.

    Ogni test riceve una coppia di chiavi fresca; la pubblica va in settings
    (come in produzione) e _post e' sostituito con l'implementazione in
    memoria: richiesta codice (codice fisso 123456), verifica con consumo
    atomico e attestazione firmata.
    """
    import base64
    import json
    import time
    import uuid as _uuid

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from app.config import get_settings
    from app.services import codes as codes_svc

    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    def b64u(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    state: dict = {}

    def sign(email: str, purpose: str, ttl: int = 300) -> str:
        now = int(time.time())
        payload = json.dumps({
            "iss": "atlas-codes", "email": email, "purpose": purpose,
            "iat": now, "exp": now + ttl, "jti": _uuid.uuid4().hex,
        }).encode()
        return b64u(payload) + "." + b64u(priv.sign(payload))

    def fake_post(path: str, payload: dict):
        email = str(payload.get("email", "")).lower()
        purpose = payload.get("purpose")
        if purpose not in ("register", "login"):
            return 400, {"error": "bad_purpose"}
        if path.endswith("/request"):
            state[(email, purpose)] = {"code": TEST_CODE, "consumed": False}
            return 200, {"ok": True, "ttl_seconds": 600}
        row = state.get((email, purpose))
        if not row:
            return 400, {"ok": False, "error": "no_code"}
        if row["consumed"]:
            return 400, {"ok": False, "error": "already_used"}
        if payload.get("code") != row["code"]:
            return 400, {"ok": False, "error": "wrong_code"}
        row["consumed"] = True
        return 200, {"ok": True, "attestation": sign(email, purpose)}

    # Via env + cache_clear: i valori sopravvivono ai get_settings.cache_clear()
    # che alcuni test (es. connettori Google) fanno per iniettare la propria config.
    monkeypatch.setenv("ATLAS_CODES_API_URL", "https://codes.test")
    monkeypatch.setenv("ATLAS_CODES_PUBLIC_KEY", b64u(pub_raw))
    get_settings.cache_clear()
    monkeypatch.setattr(codes_svc, "_post", fake_post)
    state["sign"] = sign
    state["priv"] = priv
    return state


def make_user(client: TestClient, prefix: str = "user") -> dict:
    """Registra un utente e ritorna header auth + email."""
    email = f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"
    client.post("/auth/codes/request", json={"email": email, "purpose": "register"})
    r = client.post("/auth/codes/check", json={"email": email, "code": TEST_CODE})
    assert r.status_code == 200, r.text
    signup_token = r.json()["signup_token"]
    r = client.post("/auth/register", json={"email": email, "password": "password-segreta-1", "signup_token": signup_token})
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    return {"headers": {"Authorization": f"Bearer {token}"}, "email": email, "token": token}


def make_source(client: TestClient, headers: dict, kind: str = "chat", label: str = "test") -> str:
    r = client.post("/sources", json={"kind": kind, "label": label}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def make_memory(client: TestClient, headers: dict, source_id: str, content: str, **kw) -> dict:
    payload = {"content": content, "source_id": source_id, "kind": "fact", "origin": "stated", "confidence": 0.9}
    payload.update(kw)
    r = client.post("/memories", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["memory"]
