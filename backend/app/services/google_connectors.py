"""Connettori Google reali (fase 4, spec: "Primo connettore di sola lettura
(calendario), poi bozze email").

- google_calendar: sola lettura (scope calendar.readonly), livello 0.
- google_gmail: solo BOZZE (scope gmail.compose), livello 1, annullabili
  (annullare elimina la bozza). Nessun invio esiste nel codice.
Autorizzazione via OAuth (gratuita); token cifrati con Fernet
(connector_credentials), mai in chiaro. Lo stato OAuth e firmato HMAC con il
jwt_secret: il callback non richiede sessione ma non e falsificabile."""
import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import AtlasError
from ..models import Connector, ConnectorCredential
from . import google_api
from .credentials import decrypt_secret, encrypt_secret

SCOPE_SETS = {
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
    "gmail": ["https://www.googleapis.com/auth/gmail.compose"],
}
KIND_FOR_SCOPE_SET = {"calendar": "google_calendar", "gmail": "google_gmail"}

_STATE_TTL_MINUTES = 15


def _sign(secret: str, payload_b64: str) -> str:
    return hmac.new(secret.encode(), payload_b64.encode(),
                    hashlib.sha256).hexdigest()


def build_auth_url(settings: Settings, user_id: uuid.UUID, scope_set: str) -> str:
    if scope_set not in SCOPE_SETS:
        raise AtlasError("unknown_scope_set",
                         f"Scope set sconosciuto: {scope_set}. Validi: calendar, gmail.")
    if not settings.google_client_id:
        raise AtlasError(
            "google_not_configured",
            "OAuth Google non configurato: servono ATLAS_GOOGLE_CLIENT_ID e "
            "ATLAS_GOOGLE_CLIENT_SECRET (setup gratuito in docs/CONNECTORS-GOOGLE.md).",
            status=503)
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES)).timestamp())
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": str(user_id), "s": scope_set, "e": exp}).encode()).decode()
    state = f"{payload}.{_sign(settings.jwt_secret, payload)}"
    query = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPE_SETS[scope_set]),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def _verify_state(settings: Settings, state: str) -> tuple[uuid.UUID, str]:
    try:
        payload, sig = state.rsplit(".", 1)
    except ValueError as exc:
        raise AtlasError("invalid_state", "Parametro state malformato.", status=400) from exc
    if not hmac.compare_digest(sig, _sign(settings.jwt_secret, payload)):
        raise AtlasError("invalid_state", "Parametro state non valido.", status=400)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
        user_id = uuid.UUID(data["u"])
        scope_set = data["s"]
        exp = int(data["e"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise AtlasError("invalid_state", "Parametro state illeggibile.", status=400) from exc
    if scope_set not in SCOPE_SETS:
        raise AtlasError("invalid_state", "Scope set sconosciuto nello state.", status=400)
    if datetime.now(timezone.utc).timestamp() > exp:
        raise AtlasError("state_expired",
                         "Il collegamento OAuth e scaduto: genera un nuovo URL.",
                         status=400)
    return user_id, scope_set


def handle_callback(db: Session, settings: Settings, code: str,
                    state: str) -> Connector:
    """Callback OAuth: verifica lo state, scambia il codice, salva i token
    CIFRATI e marca il connettore come autorizzato."""
    from .actions import get_or_create_connector  # import locale: evita cicli

    user_id, scope_set = _verify_state(settings, state)
    tokens = google_api.exchange_code(code, settings.google_client_id,
                                      settings.google_client_secret,
                                      settings.google_redirect_uri)
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not access or not refresh:
        raise AtlasError("google_token_missing",
                         "Google non ha restituito i token attesi. Riprova il "
                         "collegamento con prompt=consent.", status=502)

    # fail-closed: senza chiave valida questa chiamata esplode PRIMA di toccare il db
    access_enc = encrypt_secret(access, settings.credentials_key)
    refresh_enc = encrypt_secret(refresh, settings.credentials_key)

    conn = get_or_create_connector(db, user_id, KIND_FOR_SCOPE_SET[scope_set])
    cred = (db.query(ConnectorCredential)
            .filter(ConnectorCredential.connector_id == conn.id).one_or_none())
    expires_in = int(tokens.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    if cred is None:
        cred = ConnectorCredential(connector_id=conn.id)
        db.add(cred)
    cred.access_token_enc = access_enc
    cred.refresh_token_enc = refresh_enc
    cred.expires_at = expires_at
    cred.scope = " ".join(SCOPE_SETS[scope_set])
    conn.status = "authorized"
    conn.authorized_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conn)
    return conn


def _valid_access_token(db: Session, settings: Settings, conn: Connector) -> str:
    """Access token valido: rinnova via refresh token se scaduto/in scadenza,
    ri-cifrando il risultato."""
    cred = (db.query(ConnectorCredential)
            .filter(ConnectorCredential.connector_id == conn.id).one_or_none())
    if cred is None:
        raise AtlasError("connector_not_authorized",
                         "Connettore senza credenziali: ricollegalo via OAuth.",
                         status=403)
    now = datetime.now(timezone.utc)
    expires_at = cred.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at > now + timedelta(seconds=60):
        return decrypt_secret(cred.access_token_enc, settings.credentials_key)
    refresh = decrypt_secret(cred.refresh_token_enc, settings.credentials_key)
    tokens = google_api.refresh_access_token(refresh, settings.google_client_id,
                                             settings.google_client_secret)
    access = tokens.get("access_token")
    if not access:
        raise AtlasError("google_token_missing",
                         "Rinnovo token fallito: ricollega il connettore.", status=502)
    cred.access_token_enc = encrypt_secret(access, settings.credentials_key)
    expires_in = int(tokens.get("expires_in", 3600))
    cred.expires_at = now + timedelta(seconds=expires_in)
    db.commit()
    return access


def list_calendar_events(db: Session, settings: Settings, user_id: uuid.UUID,
                         conn: Connector, max_results: int = 20) -> dict:
    """Sola lettura dal calendario reale. Restituisce la stessa forma del
    connettore fittizio, con source='google_calendar'."""
    access = _valid_access_token(db, settings, conn)
    now = datetime.now(timezone.utc).isoformat()
    data = google_api.calendar_list_events(access, now, max_results=max_results)
    events = []
    for item in data.get("items", []):
        start = item.get("start", {})
        end = item.get("end", {})
        events.append({
            "id": item.get("id", ""),
            "title": item.get("summary", "(senza titolo)"),
            "starts_at": start.get("dateTime") or start.get("date", ""),
            "ends_at": end.get("dateTime") or end.get("date", ""),
            "source": "google_calendar",
        })
    return {"events": events}


def _build_mime(to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def create_gmail_draft(db: Session, settings: Settings, user_id: uuid.UUID,
                       conn: Connector, to: str, subject: str, body: str) -> dict:
    """Crea una bozza reale su Gmail. Una bozza non e un invio."""
    access = _valid_access_token(db, settings, conn)
    data = google_api.gmail_create_draft(access, _build_mime(to, subject, body))
    draft_id = data.get("id")
    if not draft_id:
        raise AtlasError("google_draft_failed", "Bozza non creata.", status=502)
    return {"draft_id": draft_id, "note": "Bozza creata su Gmail. Non inviata."}


def delete_gmail_draft(db: Session, settings: Settings, user_id: uuid.UUID,
                       conn: Connector, draft_id: str) -> None:
    """Annullamento: elimina la bozza creata dall'azione."""
    access = _valid_access_token(db, settings, conn)
    google_api.gmail_delete_draft(access, draft_id)
