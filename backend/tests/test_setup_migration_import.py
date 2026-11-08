"""Import migrazione legacy via endpoint: flusso completo.

Verifica i requisiti del proprietario:
- l'utente originale migrato PUO' fare login sulla destinazione;
- memorie e configurazione provider cifrata si conservano;
- lo stato "imported" sopravvive al rientro (reopen);
- il token del profilo di bootstrap smette di funzionare;
- l'origine non viene toccata (verificato a livello servizio in test_migrate).

Sorgente: Postgres separato (atlas_migrate_src), come il caso reale.
Destinazione: SQLite isolato per-test (forma desktop), mai il DB condiviso.
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

from .conftest import make_user
from .test_migrate import _hash_all

_BASE_URL = os.environ.get(
    "ATLAS_DATABASE_URL", "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test")
_parts = urlsplit(_BASE_URL if _BASE_URL.startswith("postgres") else
                  "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test")
LEGACY_URL = urlunsplit(_parts._replace(path="/atlas_migrate_ep"))
_ADMIN_URL = urlunsplit(_parts._replace(path="/atlas_test"))


@pytest.fixture()
def legacy_pg(monkeypatch, tmp_path):
    """Postgres legacy seedato + destinazione SQLite isolata e pulita."""
    try:
        admin = create_engine(
            _ADMIN_URL,
            connect_args={"connect_timeout": 3}, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname='atlas_migrate_ep'")).scalar()
            if not exists:
                conn.execute(text(
                    "CREATE DATABASE atlas_migrate_ep ENCODING 'UTF8' TEMPLATE template0"))
    except Exception:
        pytest.skip("Postgres di test non raggiungibile")
    engine = create_engine(LEGACY_URL)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    uid = uuid.uuid4()
    key = Fernet.generate_key().decode()
    from app.services.credentials import encrypt_secret
    enc = encrypt_secret("sk-legacy-bydk", key)
    with Session(engine) as s:
        s.add(User(id=uid, email="migra-ep@example.com",
                   password_hash=hash_password("originale-1")))
        s.flush()
        sid = uuid.uuid4()
        s.add(Source(id=sid, user_id=uid, kind="chat", label="origine"))
        s.add(UserModelSetting(user_id=uid, provider="groq", model="openai/gpt-oss-120b",
                               base_url="", api_key_enc=enc, consent_cloud=True))
        s.flush()
        s.add(Memory(user_id=uid, source_id=sid,
                     content="Il progetto Mercurio scade il 20 dicembre 2026",
                     kind="fact", origin="stated", confidence=0.9,
                     embedding=[0.05] * 768))
        s.commit()

    monkeypatch.setenv("ATLAS_DATABASE_URL", f"sqlite:///{tmp_path}/target.db")
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", LEGACY_URL)
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", key)
    get_settings.cache_clear()
    get_engine.cache_clear()  # get_engine e' lru_cache: rilegge l'env
    yield uid, key
    get_settings.cache_clear()
    get_engine.cache_clear()
    Base.metadata.drop_all(engine)


def test_endpoint_import_full_flow(legacy_pg):
    uid, key = legacy_pg
    before = _hash_all(LEGACY_URL)
    client = TestClient(app)

    # Profilo di bootstrap fresco (come il primo avvio desktop reale).
    boot = make_user(client, "boot")
    r = client.get("/setup/migration-status", headers=boot["headers"])
    assert r.status_code == 200 and r.json()["state"] == "has_data"

    r = client.post("/setup/migration-import", headers=boot["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "imported"

    # Il token del bootstrap non vale piu' (profilo sostituito).
    r = client.get("/me/export", headers=boot["headers"])
    assert r.status_code == 401

    # L'utente ORIGINALE fa login con le sue credenziali.
    r = client.post("/auth/login",
                    json={"email": "migra-ep@example.com", "password": "originale-1"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}

    # Memorie migrate visibili...
    r = client.get("/me/export", headers=headers)
    assert r.status_code == 200
    assert "Mercurio" in r.text

    # ...e la configurazione provider cifrata si decifra con la stessa chiave.
    from app.db import get_engine
    from app.services.credentials import decrypt_secret
    with Session(get_engine()) as s:
        st = s.query(UserModelSetting).filter(UserModelSetting.user_id == uid).one()
        assert decrypt_secret(st.api_key_enc, key) == "sk-legacy-bydk"

    # Reopen: lo stato registrato resta "imported" per l'utente migrato.
    r = client.get("/setup/migration-status", headers=headers)
    assert r.json()["state"] == "imported"

    # L'origine non e' stata modificata.
    assert _hash_all(LEGACY_URL) == before


def test_import_failure_rolls_back_bootstrap(legacy_pg, monkeypatch):
    """Fallimento iniettato a meta' import (indice vettoriale): il profilo di
    bootstrap resta intatto e funzionante, nessun dato legacy importato,
    origine invariata, e il retry dopo la causa riuscita va a buon fine."""
    uid, key = legacy_pg
    before = _hash_all(LEGACY_URL)
    client = TestClient(app, raise_server_exceptions=False)
    boot = make_user(client, "bootfail")

    from app.services import sqlite_store

    def boom(*args, **kwargs):
        raise RuntimeError("guasto iniettato")

    monkeypatch.setattr(sqlite_store, "vec_insert", boom)
    r = client.post("/setup/migration-import", headers=boot["headers"])
    assert r.status_code == 500

    # Bootstrap intatto: il suo token funziona ancora e non c'e' traccia
    # dell'import parziale.
    r = client.get("/me/export", headers=boot["headers"])
    assert r.status_code == 200, r.text
    assert "Mercurio" not in r.text
    with Session(get_engine()) as s:
        assert s.query(User).count() == 1
        assert s.query(Memory).count() == 0
        assert s.query(UserModelSetting).count() == 0

    # Origine immutata; dopo la rimozione della causa il retry riesce.
    assert _hash_all(LEGACY_URL) == before
    monkeypatch.undo()
    r = client.post("/setup/migration-import", headers=boot["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "imported"
    r = client.post("/auth/login",
                    json={"email": "migra-ep@example.com", "password": "originale-1"})
    assert r.status_code == 200


def test_undecryptable_credentials_surfaced(legacy_pg, monkeypatch):
    """Chiave credenziali diversa sulla destinazione: l'import completa ma il
    numero di credenziali non decifrabili e' esposto nella risposta (mai
    valori in chiaro, mai ciphertext inutilizzabile in silenzio)."""
    uid, _key = legacy_pg
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    client = TestClient(app)
    boot = make_user(client, "bootkey")

    r = client.post("/setup/migration-import", headers=boot["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "imported"
    assert body["undecryptable_credentials"] == 1
    # Nessun valore di chiave o ciphertext nella risposta.
    assert "gAAAAA" not in r.text and "sk-legacy" not in r.text


def test_unusable_key_blocked_until_reentered(legacy_pg, monkeypatch):
    """Chiave migrata non decifrabile: ciphertext conservato ma MAI usato.
    /settings/model la espone come non configurata, /ask rifiuta con errore
    chiaro, e il reinserimento della chiave azzera il flag."""
    uid, _key = legacy_pg
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    client = TestClient(app)
    boot = make_user(client, "bootguard")
    r = client.post("/setup/migration-import", headers=boot["headers"])
    assert r.status_code == 200 and r.json()["undecryptable_credentials"] == 1

    r = client.post("/auth/login",
                    json={"email": "migra-ep@example.com", "password": "originale-1"})
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['token']}"}

    # Esposta come non configurata, ciphertext ancora presente nel DB.
    r = client.get("/settings/model", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["key_unusable"] is True and body["has_key"] is False
    with Session(get_engine()) as s:
        st = s.query(UserModelSetting).filter(UserModelSetting.user_id == uid).one()
        assert st.api_key_enc.startswith("gAAAAA") and st.key_unusable is True

    # Mai tentata come funzionante: /ask rifiuta con errore esplicito.
    r = client.post("/ask", json={"text": "come si chiama il mio gatto?"},
                    headers=headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "key_unusable"

    # Reinserimento: flag azzerato, chiave utilizzabile.
    r = client.put("/settings/model", headers=headers,
                   json={"provider": "groq", "model": "openai/gpt-oss-120b",
                         "base_url": "", "api_key": "sk-nuova-chiave",
                         "consent_cloud": True})
    assert r.status_code == 200, r.text
    assert r.json()["key_unusable"] is False and r.json()["has_key"] is True
