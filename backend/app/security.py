"""Autenticazione e password di ATLAS.

Password hashate con Argon2 (mai in chiaro, mai loggate), sessioni come
JWT firmati con `ATLAS_JWT_SECRET` e scadenza configurabile. Il bearer e'
opzionale a livello di schema: e' la dipendenza `get_current_user` a
rifiutare le richieste senza token valido con un `AtlasError` 401.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .errors import AtlasError
from .models import User

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def create_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> uuid.UUID:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise AtlasError("unauthorized", "Token mancante, scaduto o non valido.", status=401)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise AtlasError("unauthorized", "Autenticazione richiesta.", status=401)
    user_id = decode_token(credentials.credentials)
    user = db.get(User, user_id)
    if user is None:
        raise AtlasError("unauthorized", "Utente non trovato.", status=401)
    return user
