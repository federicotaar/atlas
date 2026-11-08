"""Connettore Telegram: bot ufficiale (Bot API), nessun servizio esterno.

- Il token del bot vive solo cifrato (Fernet, ATLAS_CREDENTIALS_KEY), mai in
  chiaro su disco e mai nei log.
- Pairing del proprietario: l'app mostra un deep-link t.me/<bot>?start=<code>;
  il primo /start con il codice giusto lega owner_chat_id. Solo i messaggi del
  proprietario vengono elaborati, tutti gli altri sono ignorati.
- Inbound: ogni messaggio del proprietario passa dalla stessa pipeline della
  chat in-app (services.orchestrator.handle_ask) e la risposta torna via
  sendMessage. "ricorda che ..." salva una memoria, le domande ricevono
  risposta con citazioni: stessa ATLAS, altro canale.
- Polling getUpdates in un thread di background (long-poll), offset
  persistente: niente webhook, niente URL pubblico, funziona dal desktop.
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..db import get_engine
from ..errors import AtlasError
from ..models import TelegramLink, User
from .credentials import decrypt_secret, encrypt_secret
from .orchestrator import handle_ask

log = logging.getLogger("atlas.telegram")

API_BASE = "https://api.telegram.org"
POLL_TIMEOUT_S = 20


def _api(token: str, method: str, **params):
    """Chiamata Bot API. Unico punto di rete: i test lo sostituiscono intero."""
    r = httpx.post(f"{API_BASE}/bot{token}/{method}", json=params,
                   timeout=POLL_TIMEOUT_S + 15)
    try:
        data = r.json()
    except Exception as exc:
        raise AtlasError("telegram_api", "Risposta non valida da Telegram.",
                         status=502) from exc
    if r.status_code != 200 or not data.get("ok"):
        raise AtlasError("telegram_api",
                         f"Telegram ha rifiutato la richiesta ({method}).",
                         status=400 if r.status_code in (400, 401, 404) else 502)
    return data["result"]


PAIRING_TTL_MIN = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pairing_valid(link: TelegramLink) -> bool:
    """Il codice di pairing e' un segreto bearer monouso a vita breve."""
    if not link.pairing_code:
        return False
    exp = link.pairing_expires_at
    return exp is None or _aware(exp) > _now()


def pairing_expired(link: TelegramLink) -> bool:
    return bool(link.pairing_code) and not pairing_valid(link)


def deep_link(link: TelegramLink) -> str | None:
    if not pairing_valid(link):
        return None
    return f"https://t.me/{link.bot_username}?start={link.pairing_code}"


def connect(db: Session, settings: Settings, user: User, bot_token: str) -> TelegramLink:
    bot_token = bot_token.strip()
    try:
        me = _api(bot_token, "getMe")
    except AtlasError as exc:
        raise AtlasError("telegram_auth_failed",
                         "Token non accettato da Telegram: controlla di averlo "
                         "copiato intero da @BotFather.", status=400) from exc
    username = me.get("username") or ""
    if not username:
        raise AtlasError("telegram_auth_failed", "getMe senza username.", status=502)

    link = db.query(TelegramLink).filter(TelegramLink.user_id == user.id).one_or_none()
    if link is None:
        link = TelegramLink(user_id=user.id)
        db.add(link)
    link.bot_token_enc = encrypt_secret(bot_token, settings.credentials_key)
    link.bot_username = username
    # Nuovo bot (o nuovo token): pairing da rifare, offset azzerato.
    link.pairing_code = secrets.token_hex(4)
    link.pairing_expires_at = _now() + timedelta(minutes=PAIRING_TTL_MIN)
    link.owner_chat_id = None
    link.update_offset = 0
    link.active = True
    db.commit()
    db.refresh(link)
    log.info("telegram: bot @%s collegato per user %s", username, user.id)
    ensure_poller()
    return link


def disconnect(db: Session, user: User) -> None:
    link = db.query(TelegramLink).filter(TelegramLink.user_id == user.id).one_or_none()
    if link is not None:
        db.delete(link)
        db.commit()
        log.info("telegram: collegamento rimosso per user %s", user.id)


def rotate_pairing_code(db: Session, user: User) -> TelegramLink:
    """Nuovo codice/QR di pairing senza reinserire il token (gia' cifrato)."""
    link = get_link(db, user)
    if link is None:
        raise AtlasError("telegram_not_connected",
                         "Nessun bot Telegram collegato.", status=404)
    if link.owner_chat_id is not None:
        raise AtlasError("telegram_already_paired",
                         "Bot gia' accoppiato: scollegalo per riaccoppiarlo.",
                         status=409)
    link.pairing_code = secrets.token_hex(4)
    link.pairing_expires_at = _now() + timedelta(minutes=PAIRING_TTL_MIN)
    db.commit()
    db.refresh(link)
    return link


def get_link(db: Session, user: User) -> TelegramLink | None:
    return db.query(TelegramLink).filter(TelegramLink.user_id == user.id).one_or_none()


def handle_update(db: Session, settings: Settings, link: TelegramLink,
                  update: dict) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = (msg.get("chat") or {}).get("id")
    if not text or chat_id is None:
        return
    token = decrypt_secret(link.bot_token_enc, settings.credentials_key)

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        if (link.owner_chat_id is None and code and pairing_valid(link)
                and secrets.compare_digest(code, link.pairing_code or "")):
            link.owner_chat_id = chat_id
            link.pairing_code = None  # codice monouso: consumato
            link.pairing_expires_at = None
            db.commit()
            _api(token, "sendMessage", chat_id=chat_id,
                 text="ATLAS collegato. Scrivimi qui: ricordero' quello che mi "
                      "chiedi di ricordare e rispondero' alle tue domande, come "
                      "nella app.")
        else:
            _api(token, "sendMessage", chat_id=chat_id,
                 text="Codice non valido o scaduto. Genera un nuovo codice "
                      "dalla app ATLAS.")
        return

    if link.owner_chat_id is None or chat_id != link.owner_chat_id:
        return  # mai elaborare messaggi di altri

    user = db.get(User, link.user_id)
    if user is None:
        return
    out = handle_ask(db, settings, user, text)
    _api(token, "sendMessage", chat_id=chat_id, text=out.answer[:4000])


def _poll_link(db: Session, settings: Settings, link: TelegramLink) -> None:
    token = decrypt_secret(link.bot_token_enc, settings.credentials_key)
    updates = _api(token, "getUpdates", offset=link.update_offset,
                   timeout=POLL_TIMEOUT_S, allowed_updates=["message", "edited_message"])
    for upd in updates:
        link.update_offset = max(link.update_offset, upd["update_id"] + 1)
        try:
            handle_update(db, settings, link, upd)
        except Exception:
            log.exception("telegram: update %s fallito", upd.get("update_id"))
            db.rollback()
        link.last_update_at = datetime.now(timezone.utc)
        db.commit()


class _Poller:
    """Un thread per processo: gira finche' esistono link attivi."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="atlas-telegram",
                                            daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        factory = sessionmaker(bind=get_engine(), autoflush=False,
                               expire_on_commit=False)
        while not self._stop.is_set():
            try:
                settings = get_settings()
                with factory() as db:
                    links = db.query(TelegramLink).filter(TelegramLink.active.is_(True)).all()
                    for link in links:
                        try:
                            _poll_link(db, settings, link)
                        except Exception:
                            log.exception("telegram: poll fallito (user %s)", link.user_id)
                            db.rollback()
                            self._stop.wait(5)
            except Exception:
                log.exception("telegram: giro di polling fallito")
                self._stop.wait(5)
            self._stop.wait(1)


_poller = _Poller()


def ensure_poller() -> None:
    if get_settings().telegram_polling_enabled:
        _poller.start()


def stop_poller() -> None:
    _poller.stop()
