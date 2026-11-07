from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_db():
    session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    db: Session = session_factory()
    try:
        yield db
    finally:
        db.close()
