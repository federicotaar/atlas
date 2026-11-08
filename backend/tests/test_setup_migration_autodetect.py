"""Rilevamento automatico della migrazione 2.0.x -> desktop standalone.

Requisiti del proprietario:
- chi aggiorna da un 2.0.x con i default documentati del docker-compose
  distribuito vede il banner di import SENZA configurare nulla a mano;
- installazione nuova (o Postgres spento/vuoto): setup semplice, nessun
  banner, nessun falso allarme;
- la fonte configurata via env ha sempre la precedenza sul rilevamento;
- il rilevamento non parte mai fuori dalla modalita' desktop;
- le credenziali cifrate con il default pubblico del compose vengono
  ri-cifrate con la chiave locale (che non viene mai sostituita).
"""
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.config import get_settings
from app.db import get_engine
from app.main import app
from app.models import Base, Memory, Source, User, UserModelSetting
from app.security import hash_password
from app.services import migrate

from .conftest import make_user

_BASE_URL = os.environ.get(
    "ATLAS_DATABASE_URL", "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test")
_parts = urlsplit(_BASE_URL if _BASE_URL.startswith("postgres") else
                  "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test")
LEGACY_URL = urlunsplit(_parts._replace(path="/atlas_migrate_auto"))
_ADMIN_URL = urlunsplit(_parts._replace(path="/atlas_test"))


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_banner_senza_configurazione_con_dati_20(client, monkeypatch):
    """Upgrade 2.0.x stock: niente env, il banner compare da solo."""
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    calls = []

    def fake_probe(url):
        calls.append(url)
        return migrate.ProbeResult(state="has_data", counts={"users": 1, "memories": 3})

    monkeypatch.setattr(migrate, "probe", fake_probe)
    u = make_user(client, "autod_has")
    r = client.get("/setup/migration-status", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["state"] == "has_data"
    assert calls and calls[0] == migrate.LEGACY_AUTODETECT_CANDIDATES[0]


def test_niente_banner_se_vuoto_o_irraggiungibile(client, monkeypatch):
    """Nuova installazione (o DB fermo): mai banner, setup semplice."""
    for state in ("unreachable", "empty"):
        monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
        monkeypatch.setenv("ATLAS_DESKTOP", "1")
        get_settings.cache_clear()
        monkeypatch.setattr(
            migrate, "probe",
            lambda url, s=state: migrate.ProbeResult(state=s, error="x"))
        u = make_user(client, "autod_" + state[:5])
        r = client.get("/setup/migration-status", headers=u["headers"])
        assert r.json()["state"] == "not_configured"


def test_rilevamento_solo_in_desktop(client, monkeypatch):
    """Fuori da ATLAS_DESKTOP=1 il probe automatico non deve partire."""
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
    monkeypatch.delenv("ATLAS_DESKTOP", raising=False)
    get_settings.cache_clear()

    def boom(url):
        raise AssertionError("il probe automatico non deve partire fuori desktop")

    monkeypatch.setattr(migrate, "probe", boom)
    u = make_user(client, "autod_nodesk")
    r = client.get("/setup/migration-status", headers=u["headers"])
    assert r.json()["state"] == "not_configured"


def test_configurata_ha_precedenza_sul_rilevamento(client, monkeypatch):
    """ATLAS_LEGACY_DATABASE_URL esplicita vince: solo lei viene provata."""
    configured = "postgresql+psycopg://postgres@localhost:59999/nope"
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", configured)
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    seen = []
    monkeypatch.setattr(
        migrate, "probe",
        lambda url: seen.append(url)
        or migrate.ProbeResult(state="unreachable", error="down"))
    u = make_user(client, "autod_cfg")
    r = client.get("/setup/migration-status", headers=u["headers"])
    assert r.json()["state"] == "unreachable"
    assert seen == [configured]


@pytest.fixture()
def legacy_pg_default_key(monkeypatch, tmp_path):
    """Postgres legacy seedato con la chiave PUBBLICA di default del compose
    2.0.x; destinazione SQLite isolata con una chiave locale NUOVA."""
    try:
        admin = create_engine(
            _ADMIN_URL,
            connect_args={"connect_timeout": 3}, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname='atlas_migrate_auto'")).scalar()
            if not exists:
                conn.execute(text(
                    "CREATE DATABASE atlas_migrate_auto ENCODING 'UTF8' TEMPLATE template0"))
    except Exception:
        pytest.skip("Postgres di test non raggiungibile")
    engine = create_engine(LEGACY_URL)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    uid = uuid.uuid4()
    legacy_key = migrate.LEGACY_KNOWN_CREDENTIALS_KEYS[0]
    from app.services.credentials import encrypt_secret
    enc = encrypt_secret("sk-fred-groq", legacy_key)
    with Session(engine) as s:
        s.add(User(id=uid, email="fred-upgrade@example.com",
                   password_hash=hash_password("originale-1")))
        s.flush()
        sid = uuid.uuid4()
        s.add(Source(id=sid, user_id=uid, kind="chat", label="origine"))
        s.add(UserModelSetting(user_id=uid, provider="groq", model="openai/gpt-oss-120b",
                               base_url="", api_key_enc=enc, consent_cloud=True))
        s.flush()
        s.add(Memory(user_id=uid, source_id=sid,
                     content="La bici di Fred e nel garage",
                     kind="fact", origin="stated", confidence=0.9,
                     embedding=[0.05] * 768))
        s.commit()

    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("ATLAS_DATABASE_URL", f"sqlite:///{tmp_path}/target.db")
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", LEGACY_URL)
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", new_key)
    get_settings.cache_clear()
    get_engine.cache_clear()  # get_engine e' lru_cache: rilegge l'env
    yield uid, new_key
    get_settings.cache_clear()
    get_engine.cache_clear()
    Base.metadata.drop_all(engine)


def test_import_ricifra_col_default_compose(legacy_pg_default_key):
    """Chiave vecchia = default pubblico compose, chiave nuova diversa:
    l'import decifra con la legacy nota e ri-cifra con la locale."""
    uid, new_key = legacy_pg_default_key
    client = TestClient(app)

    boot = make_user(client, "bootauto")
    r = client.get("/setup/migration-status", headers=boot["headers"])
    assert r.status_code == 200 and r.json()["state"] == "has_data"

    r = client.post("/setup/migration-import", headers=boot["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "imported"
    assert body["undecryptable_credentials"] == 0

    r = client.post("/auth/login",
                    json={"email": "fred-upgrade@example.com", "password": "originale-1"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.get("/me/export", headers=headers)
    assert r.status_code == 200 and "bici di Fred" in r.text

    from app.services.credentials import decrypt_secret
    with Session(get_engine()) as s:
        st = s.query(UserModelSetting).filter(UserModelSetting.user_id == uid).one()
        # Ri-cifrata con la chiave NUOVA: decifrabile localmente...
        assert decrypt_secret(st.api_key_enc, new_key) == "sk-fred-groq"
        # ...e NON marcata inutilizzabile.
        assert st.key_unusable is False
