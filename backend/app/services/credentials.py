"""Cifratura delle credenziali dei connettori (spec sicurezza: credenziali
cifrate, mai nei log ne nel database in chiaro). Fernet simmetrico con la
chiave in ATLAS_CREDENTIALS_KEY; senza chiave si fallisce chiusi: niente
salvataggio in chiaro, mai."""
from cryptography.fernet import Fernet, InvalidToken

from ..errors import AtlasError

_KEY_HINT = ('Genera una chiave con: python -c "from cryptography.fernet '
             'import Fernet; print(Fernet.generate_key().decode())" e mettila '
             'in ATLAS_CREDENTIALS_KEY.')


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except Exception as exc:
        raise AtlasError("credentials_key_invalid",
                         f"ATLAS_CREDENTIALS_KEY non valida. {_KEY_HINT}",
                         status=500) from exc


def encrypt_secret(plaintext: str, key: str) -> str:
    if not key:
        raise AtlasError("credentials_key_missing",
                         f"ATLAS_CREDENTIALS_KEY non configurata: le credenziali "
                         f"non vengono salvate in chiaro. {_KEY_HINT}", status=503)
    return _fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token_enc: str, key: str) -> str:
    if not key:
        raise AtlasError("credentials_key_missing",
                         f"ATLAS_CREDENTIALS_KEY non configurata. {_KEY_HINT}",
                         status=503)
    try:
        return _fernet(key).decrypt(token_enc.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise AtlasError("credentials_decrypt_failed",
                         "Credenziali illeggibili: chiave diversa da quella usata "
                         "per salvarle. Ricollegare il connettore.", status=500) from exc
