"""Crittografia credenziali: chiave mancante o sbagliata deve fallire chiusa."""
import pytest
from cryptography.fernet import Fernet

from app.errors import AtlasError
from app.services.credentials import decrypt_secret, encrypt_secret

KEY = Fernet.generate_key().decode()


def test_roundtrip():
    enc = encrypt_secret("segreto", KEY)
    assert enc != "segreto"
    assert decrypt_secret(enc, KEY) == "segreto"


def test_missing_key_fails_closed():
    with pytest.raises(AtlasError) as exc:
        encrypt_secret("segreto", "")
    assert exc.value.code == "credentials_key_missing"


def test_invalid_key_format():
    with pytest.raises(AtlasError) as exc:
        encrypt_secret("segreto", "not-a-fernet-key")
    assert exc.value.code == "credentials_key_invalid"


def test_wrong_key_cannot_decrypt():
    enc = encrypt_secret("segreto", KEY)
    with pytest.raises(AtlasError) as exc:
        decrypt_secret(enc, Fernet.generate_key().decode())
    assert exc.value.code == "credentials_decrypt_failed"


def test_wrong_key_is_rejected():
    import pytest
    from cryptography.fernet import Fernet

    from app.errors import AtlasError
    from app.services.credentials import decrypt_secret, encrypt_secret
    token = encrypt_secret("segreto", Fernet.generate_key().decode())
    with pytest.raises(AtlasError) as exc:
        decrypt_secret(token, Fernet.generate_key().decode())
    assert exc.value.code == "credentials_decrypt_failed"


def test_invalid_key_is_config_error():
    import pytest

    from app.errors import AtlasError
    from app.services.credentials import encrypt_secret
    with pytest.raises(AtlasError) as exc:
        encrypt_secret("x", "not-a-fernet-key")
    assert exc.value.code == "credentials_key_invalid"


def test_mask_secret_shows_only_tail():
    from app.services.credentials import mask_secret
    assert mask_secret("sk-1234567890abcd") == "****abcd"
    assert mask_secret("") == ""


def test_mask_secret_hides_short_values_entirely():
    from app.services.credentials import mask_secret
    assert mask_secret("abcde") == "********"
    assert "abc" not in mask_secret("abc")
    assert mask_secret("abcdefgh", visible=0) == "********"


def test_is_valid_key():
    from app.services.credentials import is_valid_key
    assert is_valid_key(KEY)
    assert not is_valid_key("")
    assert not is_valid_key("non-una-chiave")
