"""Worker codici FITTIZIO standalone per CI desktop (smoke AppImage).

Implementa l'interfaccia HTTP del vero worker (/v1/codes/request e
/v1/codes/verify) con firma Ed25519 reale, cosi il backend locale in CI puo
fare il flusso register a 3 passi senza rete esterna e senza inviare email.

Chiave DETERMINISTICA da seed fisso: e' materiale di test, non un segreto
(il valore e' nel repo e serve solo a CI). In produzione il worker vero usa
la propria chiave privata, tenuta come secret Cloudflare.
"""
import base64
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PORT = 8787
CODE = "123456"
SEED = b"atlas-ci-fake-codes-worker-key!"[:32].ljust(32, b"!")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


PRIV = Ed25519PrivateKey.from_private_bytes(SEED)
PUBLIC_KEY_B64 = b64u(PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def sign(email: str, purpose: str, ttl: int = 300) -> str:
    now = int(time.time())
    payload = json.dumps({
        "iss": "atlas-codes", "email": email, "purpose": purpose,
        "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex,
    }).encode()
    return b64u(payload) + "." + b64u(PRIV.sign(payload))


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # silenzioso in CI
        pass

    def do_POST(self) -> None:
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (ValueError, UnicodeDecodeError):
            return self._reply(400, {"error": "bad_json"})
        email = str(payload.get("email", "")).lower()
        purpose = payload.get("purpose")
        if purpose not in ("register", "login"):
            return self._reply(400, {"error": "bad_purpose"})
        if self.path == "/v1/codes/request":
            return self._reply(200, {"ok": True, "ttl_seconds": 600})
        if self.path == "/v1/codes/verify":
            if payload.get("code") != CODE:
                return self._reply(400, {"ok": False, "error": "wrong_code"})
            return self._reply(200, {"ok": True, "attestation": sign(email, purpose)})
        return self._reply(404, {"error": "not_found"})


if __name__ == "__main__":
    print(f"ATLAS_CODES_PUBLIC_KEY={PUBLIC_KEY_B64}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
