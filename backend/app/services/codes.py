"""Client per l'API codici email ATLAS (Cloudflare Worker, es. atlas.federicotaranu.site).

Confine di fiducia:
- Il client pubblico (app desktop/web) NON possiede segreti e NON vede mai
  l'attestazione: parla solo con QUESTO backend locale.
- Il backend locale chiama il worker direttamente. L'attestazione restituita
  e' firmata Ed25519 dal worker; qui la verifichiamo con la CHIAVE PUBBLICA
  (valore pubblico in configurazione, non un segreto). La chiave privata resta
  un secret del worker.
- Anti-replay: il jti di ogni attestazione viene registrato in
  used_attestations nella STESSA transazione che crea l'utente o emette la
  sessione. Un jti gia' visto = rifiuto. Il worker consuma anche il codice
  atomicamente (UPDATE ... WHERE consumed = 0), quindi un codice produce una
  sola attestazione.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AtlasError
from ..models import SignupProof, UsedAttestation

PURPOSES = ("register", "login")

_ERRORS = {
    "bad_purpose": ("bad_purpose", "Scopo non valido.", 400),
    "bad_email": ("bad_email", "Email non valida.", 400),
    "rate_limited": ("rate_limited", "Troppe richieste. Riprova tra qualche minuto.", 429),
    "daily_cap": ("daily_cap", "Limite giornaliero di invii raggiunto. Riprova domani.", 429),
    "send_failed": ("send_failed", "Invio email non riuscito. Riprova.", 502),
    "no_code": ("no_code", "Nessun codice attivo. Richiedine uno nuovo.", 400),
    "expired": ("expired", "Codice scaduto. Richiedine uno nuovo.", 400),
    "too_many_attempts": ("too_many_attempts", "Troppi tentativi. Richiedi un nuovo codice.", 429),
    "wrong_code": ("wrong_code", "Codice errato.", 400),
    "already_used": ("already_used", "Codice gia utilizzato. Richiedine uno nuovo.", 400),
}


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _post(path: str, payload: dict) -> tuple[int, dict]:
    settings = get_settings()
    if not settings.codes_api_url:
        raise AtlasError("codes_unavailable", "Verifica email non configurata.", status=503)
    url = settings.codes_api_url.rstrip("/") + path
    try:
        resp = httpx.post(url, json=payload, timeout=settings.codes_timeout_s)
    except httpx.HTTPError:
        raise AtlasError("codes_unreachable", "Servizio codici non raggiungibile. Riprova.", status=502)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def _map_error(status: int, error: str | None) -> AtlasError:
    if error in _ERRORS:
        code, msg, st = _ERRORS[error]
        return AtlasError(code, msg, status=st)
    return AtlasError("codes_error", f"Errore del servizio codici ({status}).", status=502)


def request_code(email: str, purpose: str) -> int:
    """Chiede al worker di inviare un codice. Ritorna il TTL in secondi."""
    if purpose not in PURPOSES:
        raise AtlasError("bad_purpose", "Scopo non valido.", status=400)
    status, body = _post("/v1/codes/request", {"email": email, "purpose": purpose})
    if status == 200 and body.get("ok"):
        return int(body.get("ttl_seconds", 600))
    raise _map_error(status, body.get("error"))


def verify_attestation(attestation: str, email: str, purpose: str) -> dict:
    """Verifica firma Ed25519 e vincoli (iss, email, purpose, scadenza)."""
    settings = get_settings()
    if not settings.codes_public_key:
        raise AtlasError("codes_unavailable", "Verifica email non configurata.", status=503)
    try:
        payload_b64, sig_b64 = attestation.split(".")
        payload_raw = _b64u_decode(payload_b64)
        sig = _b64u_decode(sig_b64)
        key = Ed25519PublicKey.from_public_bytes(_b64u_decode(settings.codes_public_key))
        key.verify(sig, payload_raw)
        claims = json.loads(payload_raw)
    except (ValueError, InvalidSignature, json.JSONDecodeError):
        raise AtlasError("attestation_invalid", "Verifica non valida. Richiedi un nuovo codice.", status=400)
    now = int(time.time())
    if (
        claims.get("iss") != "atlas-codes"
        or claims.get("email") != email.lower()
        or claims.get("purpose") != purpose
        or not claims.get("jti")
        or int(claims.get("exp", 0)) < now
    ):
        raise AtlasError("attestation_invalid", "Verifica non valida. Richiedi un nuovo codice.", status=400)
    return claims


def _verify_at_worker(email: str, code: str, purpose: str) -> dict:
    """Chiama il worker /verify (consumo atomico + limiti tentativi: NIENTE oracle
    illimitato - ogni tentativo conta) e verifica l'attestazione firmata."""
    if purpose not in PURPOSES:
        raise AtlasError("bad_purpose", "Scopo non valido.", status=400)
    status, body = _post(
        "/v1/codes/verify", {"email": email, "purpose": purpose, "code": code}
    )
    if not (status == 200 and body.get("ok")):
        raise _map_error(status, body.get("error"))
    return verify_attestation(body["attestation"], email, purpose)


def check_signup_code(db: Session, email: str, code: str) -> str:
    """Step di VERIFICA del signup: codice corretto -> prova server-held breve.

    Il codice e' consumato atomicamente dal worker (un solo uso, tentativi
    limitati). La prova (jti) resta nel backend e ritorna al chiamante un
    signup_token casuale (256 bit): SOLO il suo hash vive nel DB. La
    registrazione richiedera' lo stesso token, quindi e' consumabile solo
    dalla sessione che ha verificato, non da chiunque conosca l'email.
    Committa subito: la prova deve sopravvivere alla richiesta successiva.
    """
    email = email.lower()
    claims = _verify_at_worker(email, code, "register")
    token = secrets.token_urlsafe(32)
    proof = SignupProof(
        jti=claims["jti"],
        email=email,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=int(claims["exp"]),
    )
    db.add(proof)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AtlasError("code_replay", "Codice gia utilizzato. Richiedine uno nuovo.", status=400)
    return token


def consume_signup_proof(db: Session, email: str, signup_token: str) -> None:
    """Consuma la prova di verifica nella STESSA transazione della creazione
    utente (il chiamante committa). La prova deve combaciare su email E token:
    un altro chiamante che conosca solo l'email non puo' consumarla.

    Il DELETE condizionato e' il gate atomico: due /register concorrenti con
    lo stesso token, uno cancella la riga, l'altro ottiene rowcount 0.
    """
    email = email.lower()
    token_hash = hashlib.sha256(signup_token.encode()).hexdigest()
    now = int(time.time())
    proof = db.query(SignupProof).filter(
        SignupProof.email == email, SignupProof.token_hash == token_hash
    ).one_or_none()
    if proof is None or proof.expires_at < now:
        raise AtlasError(
            "code_required", "Verifica prima la tua email con il codice ricevuto.", status=400
        )
    result = db.execute(
        delete(SignupProof).where(
            SignupProof.jti == proof.jti,
            SignupProof.email == email,
            SignupProof.token_hash == token_hash,
            SignupProof.expires_at >= now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise AtlasError(
            "code_required", "Verifica prima la tua email con il codice ricevuto.", status=400
        )
    db.add(UsedAttestation(jti=proof.jti, email=email, purpose="register"))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AtlasError("code_replay", "Codice gia utilizzato. Richiedine uno nuovo.", status=400)


def verify_code(db: Session, email: str, code: str, purpose: str) -> None:
    """Verifica codice per il LOGIN (passo unico): worker + attestazione + jti.

    NON committa: il jti viaggia nella transazione del chiamante.
    """
    claims = _verify_at_worker(email, code, purpose)
    db.add(UsedAttestation(jti=claims["jti"], email=email.lower(), purpose=purpose))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AtlasError("code_replay", "Codice gia utilizzato. Richiedine uno nuovo.", status=400)
