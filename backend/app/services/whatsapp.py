"""Connettore WhatsApp: linked-device via bridge locale (whatsmeow, Go).

- Il bridge e' un binario separato (wa-bridge) avviato dal backend, SOLO
  loopback, autenticato con un token condiviso generato a ogni avvio. La
  sessione WhatsApp vive nel suo SQLite locale, mai esportata.
- Rotta non ufficiale: puo' comportare restrizioni o ban dell'account. Il
  proprietario e' stato avvisato e ha scelto questa rotta; l'UI ripete
  l'avviso. Mai promettere rischio zero.
- Il pairing e' sempre e solo un'azione esplicita dell'utente: l'app mostra
  il QR, l'utente lo inquadra da WhatsApp > Dispositivi collegati.
- Solo la chat con se stessi (self-chat): il bridge ignora ogni altro chat;
  i testi inbound passano dalla pipeline della chat in-app
  (services.orchestrator.handle_ask) e la risposta torna nella self-chat.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..db import get_engine
from ..errors import AtlasError
from ..models import User, WhatsAppLink
from .orchestrator import handle_ask

log = logging.getLogger("atlas.whatsapp")

_bridge_proc: subprocess.Popen | None = None
_bridge_lock = threading.Lock()


def _base(settings: Settings) -> str:
    return f"http://127.0.0.1:{settings.wa_bridge_port}"


def _bridge(settings: Settings, method: str, path: str, **kwargs):
    """Chiamata HTTP al bridge. Unico punto di rete: facile da sostituire
    nei test con un fake."""
    token = settings.wa_bridge_token or getattr(_bridge, "_token", "")
    r = httpx.request(
        method, f"{_base(settings)}{path}",
        headers={"X-Bridge-Token": token}, timeout=10, **kwargs)
    if r.status_code >= 400:
        raise AtlasError("wa_bridge", f"Bridge WhatsApp: errore {r.status_code} su {path}.",
                         status=502)
    return r.json() if r.content else {}


def _healthz(settings: Settings) -> dict | None:
    try:
        return _bridge(settings, "GET", "/healthz")
    except Exception:
        return None


def ensure_bridge() -> None:
    """Avvia il processo bridge se non risponde. Mai in test (il fake
    sostituisce _bridge); mai se disabilitato da configurazione."""
    global _bridge_proc
    settings = get_settings()
    if not settings.wa_bridge_enabled:
        raise AtlasError("wa_disabled", "Connettore WhatsApp non abilitato.", status=503)
    if _healthz(settings) is not None:
        return
    with _bridge_lock:
        if _healthz(settings) is not None:
            return
        if not settings.wa_bridge_token:
            # Token effimero di processo: mai su disco, mai nei log.
            _bridge._token = secrets.token_hex(16)  # type: ignore[attr-defined]
        token = settings.wa_bridge_token or _bridge._token  # type: ignore[attr-defined]
        cmd = [settings.wa_bridge_bin,
               "-addr", f"127.0.0.1:{settings.wa_bridge_port}",
               "-token", token,
               "-db", settings.wa_bridge_db or "wa-session.db"]
        _bridge_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _healthz(settings) is not None:
                log.info("whatsapp: bridge avviato sulla porta %s", settings.wa_bridge_port)
                return
            time.sleep(0.1)
        raise AtlasError("wa_bridge_start", "Il bridge WhatsApp non si e' avviato.",
                         status=503)


def bridge_available(settings: Settings) -> bool:
    """True se il binario bridge risolto esiste ed e' eseguibile. Nel bundle
    desktop il path assoluto lo imposta desktop_main (accanto al backend);
    in sviluppo resta False finche' non si configura ATLAS_WA_BRIDGE_BIN.
    Solo osservazione del filesystem: nessun processo, nessuna rete."""
    p = settings.wa_bridge_bin or ""
    return os.path.isfile(p) and os.access(p, os.X_OK)


def get_link(db: Session, user: User) -> WhatsAppLink | None:
    return db.query(WhatsAppLink).filter(WhatsAppLink.user_id == user.id).one_or_none()


def connect(db: Session, settings: Settings, user: User) -> WhatsAppLink:
    """Collega (o ricollega) e avvia il flusso di pairing QR.

    Il bridge ospita UNA sola sessione WhatsApp: un solo profilo locale puo'
    avere un collegamento attivo. Se un altro profilo e' attivo, rifiuta
    con errore chiaro (isolamento dei profili: mai instradare i messaggi
    di un account verso il profilo sbagliato)."""
    ensure_bridge()
    other = (
        db.query(WhatsAppLink)
        .filter(WhatsAppLink.active.is_(True), WhatsAppLink.user_id != user.id)
        .one_or_none()
    )
    if other is not None:
        raise AtlasError(
            "wa_other_profile",
            "WhatsApp e' gia' collegato a un altro profilo di questo dispositivo: "
            "scollegalo da quel profilo prima di collegarlo qui.", status=409)
    link = get_link(db, user)
    if link is None:
        link = WhatsAppLink(user_id=user.id)
        db.add(link)
    link.active = True
    db.commit()
    db.refresh(link)
    _bridge(settings, "POST", "/pair")  # no-op sul bridge se gia' collegato
    log.info("whatsapp: pairing avviato per user %s", user.id)
    ensure_poller()
    return link


def qr_state(db: Session, settings: Settings, user: User) -> dict:
    """Stato QR dal bridge: {state: qr|paired|expired|idle, qr?: str}."""
    link = get_link(db, user)
    if link is None or not link.active:
        return {"state": "idle"}
    try:
        out = _bridge(settings, "GET", "/qr")
    except AtlasError:
        return {"state": "idle"}
    if out.get("state") == "paired":
        _sync_linked(db, settings, link)
    return out


def _sync_linked(db: Session, settings: Settings, link: WhatsAppLink) -> None:
    """Allinea la riga allo stato reale del bridge (jid/pushname)."""
    hz = _healthz(settings) or {}
    if hz.get("linked"):
        link.jid = hz.get("jid") or link.jid
        link.pushname = hz.get("pushname") or link.pushname
        db.commit()


def status(db: Session, settings: Settings, user: User) -> WhatsAppLink | None:
    link = get_link(db, user)
    if link is not None and link.active:
        _sync_linked(db, settings, link)
    return link


def disconnect(db: Session, settings: Settings, user: User) -> None:
    link = get_link(db, user)
    if link is not None:
        try:
            _bridge(settings, "POST", "/disconnect")
        except AtlasError:
            pass  # il bridge potrebbe essere gia' spento: la riga va comunque
        db.delete(link)
        db.commit()
        log.info("whatsapp: collegamento rimosso per user %s", user.id)


def handle_event(db: Session, settings: Settings, link: WhatsAppLink,
                 event: dict) -> None:
    """Un testo inbound dalla self-chat: stessa pipeline della chat in-app,
    risposta via bridge. Il bridge garantisce la provenienza self-chat."""
    # Anti-eco mirato: salta SOLO i messaggi che ATLAS stesso ha inviato
    # (id registrati al /send). Un messaggio del proprietario dal suo
    # telefono in self-chat ha from_me=true ma ID sconosciuto: e' input.
    try:
        replied = set(json.loads(link.replied_ids or "[]"))
    except ValueError:
        replied = set()
    if event.get("id") and event["id"] in replied:
        return
    # Dedup cross-restart: un ID inbound gia' elaborato non risponde due
    # volte, anche se il bridge e' ripartito e il seq e' tornato basso.
    try:
        processed = set(json.loads(link.processed_ids or "[]"))
    except ValueError:
        processed = set()
    if event.get("id") and event["id"] in processed:
        return
    text = (event.get("text") or "").strip()
    if not text:
        return
    user = db.get(User, link.user_id)
    if user is None:
        return
    out = handle_ask(db, settings, user, text)
    resp = _bridge(settings, "POST", "/send", json={"text": out.answer[:4000]})
    if event.get("id"):
        processed.add(event["id"])
        link.processed_ids = json.dumps(sorted(processed)[-200:])
    sent_id = resp.get("id")
    if sent_id:
        replied.add(sent_id)
        link.replied_ids = json.dumps(sorted(replied)[-100:])


def _poll_link(db: Session, settings: Settings, link: WhatsAppLink) -> None:
    out = _bridge(settings, "GET", f"/events?after={link.last_seq}")
    boot = out.get("boot")
    if boot and boot != link.boot_id:
        # Bridge riavviato: i seq ripartono da 1 e la risposta e' stata
        # filtrata con l'offset vecchio: rileggi tutto da zero.
        link.boot_id = boot
        link.last_seq = 0
        out = _bridge(settings, "GET", "/events?after=0")
    for event in out.get("events", []):
        seq = int(event.get("seq", 0))
        if seq <= link.last_seq:
            continue  # replay dopo reconnect del bridge: ogni evento una volta
        link.last_seq = seq
        try:
            handle_event(db, settings, link, event)
        except Exception:
            log.exception("whatsapp: evento %s fallito", event.get("seq"))
            db.rollback()
        link.last_event_at = datetime.now(timezone.utc)
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
            self._thread = threading.Thread(target=self._loop, name="atlas-whatsapp",
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
                    links = db.query(WhatsAppLink).filter(WhatsAppLink.active.is_(True)).all()
                    for link in links:
                        try:
                            _poll_link(db, settings, link)
                        except Exception:
                            log.exception("whatsapp: poll fallito (user %s)", link.user_id)
                            db.rollback()
                            self._stop.wait(5)
            except Exception:
                log.exception("whatsapp: giro di polling fallito")
                self._stop.wait(5)
            self._stop.wait(2)


_poller = _Poller()


def ensure_poller() -> None:
    if get_settings().wa_polling_enabled:
        _poller.start()


def stop_poller() -> None:
    _poller.stop()
