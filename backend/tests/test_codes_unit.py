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
