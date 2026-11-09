"""Ponte dashboard -> stack agente (app desktop).

Le card Telegram/WhatsApp della dashboard collegano i connettori; da quel
momento i messaggi inbound devono arrivare all'AGENTE (loop con strumenti,
memoria, permessi e conferme), non alla pipeline classica v2.x. Questo
ponte e' il punto unico di smistamento: i servizi di trasporto
(services/telegram.py, services/whatsapp.py) restano invariati per il
server mode e, quando il ponte e' registrato (desktop), consegnano qui.

- Allowlist: stesso modello della CLI (channels/pairing.py). Il
  proprietario entra quando il connettore ne PROVA l'identita' (codice
  deep-link Telegram mostrato solo nell'app; self-chat WhatsApp = account
  collegato): on_identity() lo abbina subito e al prossimo avvio.
- Sconosciuti: silenzio sul canale + richiesta di pairing in locale,
  approvabile SOLO dalla superficie locale autenticata (dashboard ->
  router /connectors/pairing). Mai dalla chat: uno sconosciuto non puo'
  approvare se stesso.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from .base import ChannelHub, ChannelLog, ChannelMessage
from .media import MediaProcessor
from .pairing import PairingStore
from .whatsapp import normalize_number


class DesktopChannelBridge:
    """Uno per stack agente: hub + pairing store + allowlist condivisa."""

    def __init__(self, *, workspace: Path, registry, get_active, set_active,
                 describe_registry, ctx, store, log, seal, secrets,
                 scheduler=None, proactive=None, max_steps: int = 10,
                 env: dict | None = None):
        env = os.environ if env is None else env
        self._pairing = PairingStore(workspace / "data" / "pairing.json")
        tg_ids = {s.strip() for s in env.get("ALLOWED_TELEGRAM_IDS", "").split(",")
                  if s.strip()}
        wa_nums = {normalize_number(n)
                   for n in env.get("ALLOWED_WHATSAPP_NUMBERS", "").split(",")
                   if n.strip()}
        self._allowed: dict[str, set[str]] = {
            "telegram": tg_ids | self._pairing.paired_ids("telegram"),
            "whatsapp": wa_nums | self._pairing.paired_ids("whatsapp"),
        }
        # Media (stage 6): chiavi lette dall'ambiente all'avvio, come nella
        # CLI. Una chiave aggiunta dopo dal pannello vale dal prossimo avvio.
        media = MediaProcessor(
            workspace,
            groq_key=env.get("GROQ_API_KEY", ""),
            gemini_key=env.get("GEMINI_API_KEY", ""))
        self._hub = ChannelHub(
            registry=registry, active_getter=get_active, set_active=set_active,
            describe_registry=describe_registry, ctx=ctx, store=store, log=log,
            seal=seal, secrets=secrets,
            channels_log=ChannelLog(workspace / "logs" / "channels.log", secrets),
            allowed=self._allowed, pairing=self._pairing, media=media,
            scheduler=scheduler, proactive=proactive, max_steps=max_steps)
        self._lock = threading.Lock()

    # --- ingresso dai trasporti ----------------------------------------------

    def dispatch(self, channel: str, sender: str, display: str, text: str,
                 reply: Callable[[str], None]) -> None:
        """Un messaggio inbound: l'hub applica allowlist, pairing, rate
        limit, comandi e loop agente. Mai chiamato per messaggi vuoti."""
        if channel == "whatsapp":
            sender = normalize_number(sender)
        self._hub.handle(
            ChannelMessage(sender=sender, channel=channel, text=text,
                           display=display),
            reply)

    def on_identity(self, channel: str, sender: str, display: str = "") -> None:
        """Identita' del proprietario provata dal connettore (codice
        deep-link Telegram, self-chat WhatsApp): allowlist subito e,
        via pairing store, a ogni avvio successivo."""
        if channel == "whatsapp":
            sender = normalize_number(sender)
        if not sender:
            return
        with self._lock:
            self._pairing.approve_direct(channel, sender, display)
            self._allowed.setdefault(channel, set()).add(sender)

    # --- superficie locale di approvazione (dashboard) -----------------------

    def pending(self) -> list[dict]:
        return self._pairing.pending()

    def approve(self, channel: str, sender: str) -> dict | None:
        """Approva una richiesta in sospeso: attiva subito e persiste."""
        with self._lock:
            return self._hub.approve_pairing(channel, sender)

    def reject(self, channel: str, sender: str) -> bool:
        with self._lock:
            return self._pairing.reject(channel, sender)
