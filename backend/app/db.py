"""Engine SQLAlchemy. Postgres (Docker/sviluppo) oppure SQLite file (desktop
standalone): in modalita' SQLite vengono caricati PRAGMA, sqlite-vec e lo
schema completo (create_all + tabelle vettoriali/FTS) alla prima connessione,
senza Alembic."""
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

VEC_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
    "embedding float[768] distance_metric=cosine, +memory_id TEXT)"
)
FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
    "memory_id UNINDEXED, content)"
)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


@lru_cache
def get_engine():
    url = get_settings().database_url
    if _is_sqlite(url):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_setup(dbapi_conn, _record):
            import sqlite_vec

            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            # Alcuni build di sqlite3 richiedono l'autorizzazione esplicita
            # prima di load_extension (senza, OperationalError "not authorized").
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)

        with engine.begin() as conn:
            from .models import Base

            Base.metadata.create_all(conn)
            conn.execute(sql_text(VEC_DDL))
            conn.execute(sql_text(FTS_DDL))
        return engine
    return create_engine(url, pool_pre_ping=True)


def get_db():
    session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    db: Session = session_factory()
    try:
        yield db
    finally:
        db.close()
