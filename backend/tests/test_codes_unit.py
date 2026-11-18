"""Utility del servizio codici: base64url e mappatura errori del worker."""
import base64

from app.errors import AtlasError
from app.services.codes import _b64u_decode, _map_error


def test_b64u_decode_restores_padding():
    raw = b"atlas"
    assert _b64u_decode(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw


def test_map_error_known_code_keeps_status():
    err = _map_error(429, "rate_limited")
    assert isinstance(err, AtlasError)
    assert err.code == "rate_limited" and err.status == 429


def test_map_error_unknown_is_generic_502():
    err = _map_error(500, "mystery")
    assert err.code == "codes_error" and err.status == 502

def test_attestation_rejects_signed_invalid_claim_shapes(fake_codes_worker):
    import json
    import time

    import pytest

    from app.services.codes import verify_attestation

    email = "claims@example.com"
    valid = {"iss": "atlas-codes", "email": email, "purpose": "login",
             "exp": int(time.time()) + 300, "jti": "unique"}
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    def sign(claims):
        raw = json.dumps(claims).encode()
        return b64(raw) + "." + b64(fake_codes_worker["priv"].sign(raw))
    assert verify_attestation(sign(valid), email, "login") == valid
    invalid = [[], None, "claims", 1]
    invalid += [{**valid, "exp": value} for value in (None, "99999999999", True, [], 0)]
    invalid += [{**valid, "jti": value} for value in (None, [], 1, " ", "x" * 257)]
    for claims in invalid:
        with pytest.raises(AtlasError) as caught:
            verify_attestation(sign(claims), email, "login")
        assert caught.value.code == "attestation_invalid"


def test_attestation_expiry_boundary(fake_codes_worker, monkeypatch):
    import json

    import pytest

    from app.services import codes

    monkeypatch.setattr(codes.time, "time", lambda: 1000)
    for expiry, accepted in ((999, False), (1000, False), (1001, True)):
        claims = {"iss": "atlas-codes", "email": "a@example.com", "purpose": "login",
                  "exp": expiry, "jti": "boundary"}
        raw = json.dumps(claims).encode()
        def b64(value):
            return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
        attestation = b64(raw) + "." + b64(fake_codes_worker["priv"].sign(raw))
        if accepted:
            assert codes.verify_attestation(attestation, "a@example.com", "login") == claims
        else:
            with pytest.raises(AtlasError):
                codes.verify_attestation(attestation, "a@example.com", "login")
