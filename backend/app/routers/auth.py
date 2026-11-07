"""Codici email: registrazione e accesso con codice (flusso deciso da Fred).

- Signup: email -> codice -> verifica -> imposta password (obbligatoria).
- Sign-in: l'utente sceglie codice OPPURE password; entrambi restano.
- La verifica del codice avviene QUI (backend locale -> worker), mai nel
  frontend: un booleano dal client non e' autorizzazione.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AtlasError
from ..models import User
from ..schemas import (
    CodeCheckIn,
    CodeCheckOut,
    CodeRequestIn,
    CodeRequestOut,
    LoginCodeIn,
    LoginIn,
    RegisterIn,
    TokenOut,
    UserOut,
)
from ..security import create_token, hash_password, verify_password
from ..services import codes as codes_service
from ..services.memory import log_action

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/codes/request", response_model=CodeRequestOut)
def request_email_code(body: CodeRequestIn, db: Session = Depends(get_db)) -> CodeRequestOut:
    email = body.email.lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    if body.purpose == "register" and user is not None:
        raise AtlasError("email_taken", "Email gia registrata.", status=409)
    if body.purpose == "login" and user is None:
        raise AtlasError("no_account", "Nessun account con questa email.", status=404)
    ttl = codes_service.request_code(email, body.purpose)
    return CodeRequestOut(ok=True, ttl_seconds=ttl)


@router.post("/codes/check", response_model=CodeCheckOut)
def check_signup_code(body: CodeCheckIn, db: Session = Depends(get_db)) -> CodeCheckOut:
    """Verifica VERA del codice di signup (bounded: tentativi limitati + consumo
    atomico nel worker). Produce una prova server-held di 5 minuti legata a un
    signup_token impredicibile: /auth/register la consumera' solo con quel
    token, quindi solo la sessione che ha verificato puo' completare il signup."""
    email = body.email.lower()
    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing is not None:
        raise AtlasError("email_taken", "Email gia registrata.", status=409)
    token = codes_service.check_signup_code(db, email, body.code)
    return CodeCheckOut(ok=True, signup_token=token)


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> TokenOut:
    email = body.email.lower()
    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing is not None:
        raise AtlasError("email_taken", "Email gia registrata.", status=409)
    # Richiede la prova di verifica email (server-held, legata al signup_token
    # della sessione che ha verificato); la consuma nella stessa transazione
    # della creazione utente.
    codes_service.consume_signup_proof(db, email, body.signup_token)
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    log_action(db, user.id, "auth.register", {"via": "email_code"})
    return TokenOut(token=create_token(user.id), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = db.query(User).filter(User.email == body.email.lower()).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise AtlasError("invalid_credentials", "Email o password non valide.", status=401)
    log_action(db, user.id, "auth.login", {"via": "password"})
    return TokenOut(token=create_token(user.id), user=UserOut.model_validate(user))


@router.post("/login-code", response_model=TokenOut)
def login_code(body: LoginCodeIn, db: Session = Depends(get_db)) -> TokenOut:
    email = body.email.lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        raise AtlasError("no_account", "Nessun account con questa email.", status=404)
    codes_service.verify_code(db, email, body.code, "login")
    db.commit()
    log_action(db, user.id, "auth.login", {"via": "email_code"})
    return TokenOut(token=create_token(user.id), user=UserOut.model_validate(user))
