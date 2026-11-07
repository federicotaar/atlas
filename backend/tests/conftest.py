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


@pytest.fixture(scope="session")
def _setup_db():
    engine = create_engine(os.environ["ATLAS_DATABASE_URL"])
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("database di test non raggiungibile (serve Postgres+pgvector)", allow_module_level=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
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
    engine = create_engine(os.environ["ATLAS_DATABASE_URL"])
    with Session(engine) as session:
        yield session


@pytest.fixture()
def db_user(db_session):
    from app.models import User
    user = User(email=f"db-{uuid.uuid4().hex[:10]}@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def make_user(client: TestClient, prefix: str = "user") -> dict:
    """Registra un utente e ritorna header auth + email."""
    email = f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "password-segreta-1"})
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
