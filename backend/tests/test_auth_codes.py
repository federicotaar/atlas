"""Test flusso codici email: register/login con codice, anti-replay, bypass diretti.

Il worker e' il fake di conftest (firma Ed25519 reale, codice fisso 123456).
I test di attestazione contraffatta/scaduta/sbilanciata costruiscono firme
realmente valide o invalide contro la stessa macchina di verifica del backend.
"""

import base64
import json
import time
import uuid

import pytest

from .conftest import TEST_CODE, make_user


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _email(prefix="codes"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _request(client, email, purpose):
    return client.post("/auth/codes/request", json={"email": email, "purpose": purpose})


def _check(client, email, code=TEST_CODE):
    return client.post("/auth/codes/check", json={"email": email, "code": code})


def _register(client, email, password="password-segreta-1", signup_token=""):
    return client.post(
        "/auth/register",
        json={"email": email, "password": password, "signup_token": signup_token},
    )


def _signup(client, email):
    """Flusso completo: request -> check (verifica vera, ritorna token) -> register."""
    _request(client, email, "register")
    r = _check(client, email)
    assert r.status_code == 200
    return _register(client, email, signup_token=r.json()["signup_token"])


# ---------- richiesta codice ----------

def test_request_register_code_ok(client):
    r = _request(client, _email(), "register")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["ttl_seconds"] == 600


def test_request_register_existing_email_409(client):
    u = make_user(client)
    r = _request(client, u["email"], "register")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


def test_request_login_unknown_email_404(client):
    r = _request(client, _email(), "login")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "no_account"


def test_request_login_known_email_ok(client):
    u = make_user(client)
    r = _request(client, u["email"], "login")
    assert r.status_code == 200


def test_request_bad_purpose_422(client):
    r = _request(client, _email(), "reset")
    assert r.status_code == 422


# ---------- registrazione: verifica vera (check) poi password ----------

def test_register_without_verification_400(client):
    """Bypass diretto: /auth/register senza aver verificato -> rifiutato."""
    r = _register(client, _email())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "code_required"


def test_check_wrong_code_400(client):
    email = _email()
    _request(client, email, "register")
    r = _check(client, email, code="000000")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "wrong_code"


def test_check_without_requesting_code_400(client):
    r = _check(client, _email())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "no_code"


def test_check_existing_email_409(client):
    u = make_user(client)
    r = _check(client, u["email"])
    assert r.status_code == 409


def test_signup_happy_path_201(client):
    email = _email()
    r = _signup(client, email)
    assert r.status_code == 201
    assert r.json()["token"]
    assert r.json()["user"]["email"] == email


def test_register_replayed_code_rejected(client):
    """Il codice e' consumato dal check: una seconda verifica -> already_used."""
    email = _email()
    _request(client, email, "register")
    assert _check(client, email).status_code == 200
    r = _check(client, email)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "already_used"


def test_register_wrong_token_rejected(client):
    """Token contraffatto o di un'altra sessione: registrazione negata, e la
    prova legittima resta valida per il possessore del token giusto."""
    email = _email()
    _request(client, email, "register")
    token = _check(client, email).json()["signup_token"]
    r = _register(client, email, signup_token="forged-" + "x" * 40)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "code_required"
    # il token giusto funziona ancora (il tentativo contraffatto non consuma nulla)
    assert _register(client, email, signup_token=token).status_code == 201


def test_proof_bound_to_email(client):
    """Verifica su email A non autorizza la registrazione di email B, nemmeno
    presentando il token di A: la prova combacia su email E token."""
    a, b = _email("a"), _email("b")
    _request(client, a, "register")
    token_a = _check(client, a).json()["signup_token"]
    r = _register(client, b, signup_token=token_a)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "code_required"


def test_proof_single_use(client):
    """La prova si consuma: lo stesso token non puo' registrare due account."""
    email = _email()
    _request(client, email, "register")
    token = _check(client, email).json()["signup_token"]
    assert _register(client, email, signup_token=token).status_code == 201
    other = _email("other")
    r = _register(client, other, signup_token=token)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "code_required"
    # riregistrare la stessa email -> 409 (utente esistente)
    assert _register(client, email, signup_token=token).status_code == 409


def test_expired_proof_rejected(client, db_session):
    """Prova scaduta (5 min): registrazione negata."""
    import time as _time

    from app.models import SignupProof

    email = _email()
    _request(client, email, "register")
    token = _check(client, email).json()["signup_token"]
    proof = db_session.query(SignupProof).filter(SignupProof.email == email).one()
    proof.expires_at = int(_time.time()) - 1
    db_session.commit()
    r = _register(client, email, signup_token=token)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "code_required"


# ---------- login con codice ----------

def test_login_code_happy_path(client):
    u = make_user(client)
    _request(client, u["email"], "login")
    r = client.post("/auth/login-code", json={"email": u["email"], "code": TEST_CODE})
    assert r.status_code == 200
    assert r.json()["token"]


def test_login_code_wrong_400(client):
    u = make_user(client)
    _request(client, u["email"], "login")
    r = client.post("/auth/login-code", json={"email": u["email"], "code": "000000"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "wrong_code"


def test_login_code_replay_rejected(client):
    u = make_user(client)
    _request(client, u["email"], "login")
    ok = client.post("/auth/login-code", json={"email": u["email"], "code": TEST_CODE})
    assert ok.status_code == 200
    again = client.post("/auth/login-code", json={"email": u["email"], "code": TEST_CODE})
    assert again.status_code == 400


def test_login_code_unknown_email_404(client):
    r = client.post("/auth/login-code", json={"email": _email(), "code": TEST_CODE})
    assert r.status_code == 404


def test_login_password_still_works(client):
    u = make_user(client)
    r = client.post("/auth/login", json={"email": u["email"], "password": "password-segreta-1"})
    assert r.status_code == 200


# ---------- attestazioni: bypass diretti ----------

def _attestation(priv, email, purpose, ttl=300, iss="atlas-codes", jti=None):
    now = int(time.time())
    payload = json.dumps({
        "iss": iss, "email": email, "purpose": purpose,
        "iat": now, "exp": now + ttl, "jti": jti or uuid.uuid4().hex,
    }).encode()
    return b64u(payload) + "." + b64u(priv.sign(payload))


def test_attestation_tampered_rejected(client, db_session, fake_codes_worker):
    from app.errors import AtlasError
    from app.services import codes as codes_svc

    email = _email()
    good = _attestation(fake_codes_worker["priv"], email, "register")
    payload, sig = good.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["email"] = _email("attacker")  # firma non corrisponde piu'
    tampered = b64u(json.dumps(data).encode()) + "." + sig
    with pytest.raises(AtlasError) as exc:
        codes_svc.verify_attestation(tampered, data["email"], "register")
    assert exc.value.code == "attestation_invalid"


def test_attestation_expired_rejected(client, db_session, fake_codes_worker):
    from app.errors import AtlasError
    from app.services import codes as codes_svc

    email = _email()
    stale = _attestation(fake_codes_worker["priv"], email, "register", ttl=-10)
    with pytest.raises(AtlasError) as exc:
        codes_svc.verify_attestation(stale, email, "register")
    assert exc.value.code == "attestation_invalid"


def test_attestation_wrong_purpose_rejected(client, db_session, fake_codes_worker):
    from app.errors import AtlasError
    from app.services import codes as codes_svc

    email = _email()
    att = _attestation(fake_codes_worker["priv"], email, "register")
    with pytest.raises(AtlasError):
        codes_svc.verify_attestation(att, email, "login")


def test_attestation_wrong_email_rejected(client, db_session, fake_codes_worker):
    from app.errors import AtlasError
    from app.services import codes as codes_svc

    att = _attestation(fake_codes_worker["priv"], _email(), "register")
    with pytest.raises(AtlasError):
        codes_svc.verify_attestation(att, _email("other"), "register")


def test_attestation_jti_replay_rejected(client, db_session, fake_codes_worker):
    """Stessa attestazione (stesso jti) presentata due volte: la seconda fallisce."""
    from sqlalchemy.exc import IntegrityError

    from app.models import UsedAttestation
    from app.services import codes as codes_svc

    email = _email()
    jti = uuid.uuid4().hex
    att = _attestation(fake_codes_worker["priv"], email, "register", jti=jti)
    claims = codes_svc.verify_attestation(att, email, "register")
    assert claims["jti"] == jti
    db_session.add(UsedAttestation(jti=jti, email=email, purpose="register"))
    db_session.commit()
    # replay: stesso jti -> vincolo di unicita'
    db_session.add(UsedAttestation(jti=jti, email=email, purpose="register"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
