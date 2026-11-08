"""Migrazione Postgres -> SQLite: probe distingue vuoto/irraggiungibile,
export read-only, import solo su destinazione vuota, credenziali copiate
cifrate, indici sqlite ricostruiti."""
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import FTS_DDL, VEC_DDL
from app.models import Base, Memory, Source, User, UserModelSetting
from app.security import hash_password
from app.services import migrate

_BASE_URL = os.environ.get(
    "ATLAS_DATABASE_URL", "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test")
_parts = urlsplit(_BASE_URL)
PG_URL = urlunsplit(_parts._replace(path="/atlas_migrate_src"))
_MAINT_URL = _BASE_URL  # DB UTF8 esistente usato come connessione di manutenzione


def _ensure_pg_db():
    """Crea il DB sorgente se manca (UTF8 da template0: il cluster di test
    puo' avere locale POSIX/SQL_ASCII che rompe psycopg)."""
    parts = urlsplit(PG_URL)
    dbname = parts.path.lstrip("/")
    admin = create_engine(_MAINT_URL, connect_args={"connect_timeout": 3},
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": dbname}).scalar()
        if not exists:
            conn.execute(text(
                f"CREATE DATABASE {dbname} TEMPLATE template0 ENCODING 'UTF8'"))


@pytest.fixture()
def pg_source():
    try:
        _ensure_pg_db()
        engine = create_engine(PG_URL, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres di test non raggiungibile")
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    uid = uuid.uuid4()
    with Session(engine) as s:
        s.add(User(id=uid, email="migra@example.com", password_hash=hash_password("x-1")))
        s.flush()
        sid = uuid.uuid4()
        s.add(Source(id=sid, user_id=uid, kind="chat", label="origine"))
        s.add(UserModelSetting(user_id=uid, provider="groq", model="openai/gpt-oss-120b",
                               base_url="", api_key_enc="gAAAAA_ciphertext_fittizio",
                               consent_cloud=True))
        s.flush()
        m = Memory(user_id=uid, source_id=sid,
                   content="Il progetto Mercurio scade il 20 dicembre 2026",
                   kind="fact", origin="stated", confidence=0.9,
                   embedding=[0.05] * 768)
        s.add(m)
        s.commit()
    yield PG_URL, uid
    Base.metadata.drop_all(engine)


@pytest.fixture()
def sqlite_target(tmp_path):
    import sqlite_vec

    url = f"sqlite:///{tmp_path}/dest.db"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event_on(engine)
    def _load(dbapi_conn, _):
        sqlite_vec.load(dbapi_conn)

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(VEC_DDL))
        conn.execute(text(FTS_DDL))
    return engine


def event_on(engine):
    from sqlalchemy import event

    def deco(fn):
        event.listen(engine, "connect", fn)
        return fn
    return deco


def test_probe_unreachable():
    r = migrate.probe("postgresql+psycopg://postgres@localhost:59999/nope")
    assert r.state == "unreachable"
    assert r.error


def test_probe_states(pg_source):
    url, _uid = pg_source
    r = migrate.probe(url)
    assert r.state == "has_data"
    assert r.counts["users"] == 1 and r.counts["memories"] == 1


def test_round_trip(pg_source, sqlite_target):
    url, uid = pg_source
    payload = migrate.export_payload(url)
    with Session(sqlite_target) as s:
        inserted, _uids, _und = migrate.import_payload(s, payload)
        assert inserted["users"] == 1 and inserted["memories"] == 1
        u = s.query(User).one()
        assert u.id == uid and u.email == "migra@example.com"
        # credenziale copiata CIFRATA, identica
        st = s.query(UserModelSetting).one()
        assert st.api_key_enc == "gAAAAA_ciphertext_fittizio"
        # indici sqlite ricostruiti: FTS e vec0 popolati
        fts_n = s.execute(text("SELECT count(*) FROM memories_fts")).scalar_one()
        vec_n = s.execute(text("SELECT count(*) FROM memory_vec")).scalar_one()
        assert fts_n == 1 and vec_n == 1
        # ricerca funziona sulla destinazione
        from app.services import sqlite_store

        hits = sqlite_store.fts(s, "Mercurio scade dicembre", uid, 10)
        assert hits


def test_import_refuses_non_empty(pg_source, sqlite_target):
    """Un target con contenuto proprio (non solo il profilo di bootstrap)
    rifiuta l'import."""
    url, _uid = pg_source
    payload = migrate.export_payload(url)
    with Session(sqlite_target) as s:
        u = User(email="esiste@example.com", password_hash="h")
        s.add(u)
        s.flush()
        sid = uuid.uuid4()
        s.add(Source(id=sid, user_id=u.id, kind="chat", label="propria"))
        s.add(Memory(user_id=u.id, source_id=sid, kind="fact",
                     origin="stated", content="contenuto proprio", confidence=0.5))
        s.commit()
        with pytest.raises(ValueError, match="non vuoto"):
            migrate.import_payload(s, payload)


def test_payload_has_no_auth_state(pg_source):
    url, _uid = pg_source
    payload = migrate.export_payload(url)
    assert "used_attestations" not in payload["tables"]
    assert "signup_proofs" not in payload["tables"]


def _hash_all(url):
    """Istantanea ordinata di TUTTE le righe sorgente (prova che l'import
    non modifica mai l'origine)."""
    import hashlib
    engine = create_engine(url)
    dig = hashlib.sha256()
    with Session(engine) as s:
        for model in migrate.TABLES:
            for row in s.query(model).all():
                dig.update(repr(sorted(migrate._row_to_dict(row).items())).encode())
    return dig.hexdigest()


def test_import_replaces_bootstrap_profile(pg_source, sqlite_target):
    """Destinazione con il solo profilo di bootstrap (1 utente, zero altro):
    l'import lo sostituisce e restituisce gli utenti importati."""
    url, uid = pg_source
    payload = migrate.export_payload(url)
    with Session(sqlite_target) as s:
        s.add(User(email="bootstrap@example.com", password_hash=hash_password("tmp-1")))
        s.commit()
        inserted, imported_ids, _und = migrate.import_payload(s, payload)
        assert inserted["users"] == 1
        assert imported_ids == [uid]
        emails = [u.email for u in s.query(User).all()]
        assert emails == ["migra@example.com"]  # bootstrap sparito


def test_import_refuses_bootstrap_with_data(pg_source, sqlite_target):
    """Bootstrap con anche UNA memoria propria: rifiuto, niente fusione."""
    url, _uid = pg_source
    payload = migrate.export_payload(url)
    with Session(sqlite_target) as s:
        u = User(email="bootstrap@example.com", password_hash=hash_password("tmp-1"))
        s.add(u)
        s.flush()
        src = Source(user_id=u.id, kind="chat", label="mio")
        s.add(src)
        s.flush()
        s.add(Memory(user_id=u.id, source_id=src.id, content="mia memoria",
                     kind="fact", origin="stated", confidence=1.0))
        s.commit()
        with pytest.raises(ValueError, match="non vuoto"):
            migrate.import_payload(s, payload)


def test_credentials_decrypt_after_import(pg_source, sqlite_target, monkeypatch):
    """Credenziale BYOK cifrata con la vera chiave: dopo l'import si decifra
    sulla destinazione (stessa ATLAS_CREDENTIALS_KEY), mai in chiaro."""
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ATLAS_CREDENTIALS_KEY", key)
    url, uid = pg_source
    engine = create_engine(url)
    from app.services.credentials import decrypt_secret, encrypt_secret
    real_enc = encrypt_secret("sk-test-chiave-reale", key)
    with Session(engine) as s:
        st = s.query(UserModelSetting).filter(UserModelSetting.user_id == uid).one()
        st.api_key_enc = real_enc
        s.commit()
    payload = migrate.export_payload(url)
    assert "sk-test-chiave-reale" not in str(payload)  # mai in chiaro nell'export
    with Session(sqlite_target) as s:
        migrate.import_payload(s, payload)
        st = s.query(UserModelSetting).one()
        assert st.api_key_enc == real_enc
        assert decrypt_secret(st.api_key_enc, key) == "sk-test-chiave-reale"


def test_source_untouched_by_export_import(pg_source, sqlite_target):
    """L'origine Postgres resta byte-identica (contenuto) dopo export+import."""
    url, _uid = pg_source
    before = _hash_all(url)
    payload = migrate.export_payload(url)
    with Session(sqlite_target) as s:
        migrate.import_payload(s, payload)
    after = _hash_all(url)
    assert before == after
