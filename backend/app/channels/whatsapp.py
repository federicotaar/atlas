"""Canale WhatsApp unofficial (spec sez. 6, build order 7): il backend Go
wa-bridge (whatsmeow) gira come sidecar SOLO su loopback con token condiviso;
questo canale e' il suo client HTTP. Nessuna libreria WhatsApp in Python.

Modello sicurezza (immutato rispetto agli altri canali):
- l'allowlist dei numeri e' CENTRALE nell'hub (ALLOWED_WHATSAPP_NUMBERS in
  .env + pairing approvati dalla CLI locale): il bridge con -allow-dms
  consegna i DM 1:1 e l'hub decide chi riceve risposta (silenzio per gli
  sconosciuti, nessuna eco, nessun contenuto in log);
- il token del sidecar arriva da .env (WA_BRIDGE_TOKEN) o e' generato a
  ogni avvio quando la CLI spawna lei il bridge: mai in config, mai in log;
- gruppi/canali/broadcast non arrivano mai qui (filtro nel bridge).

Il client reale fa polling di GET /events; i test iniettano un client finto
con la stessa interfaccia (start_polling/on_update + send_message), quindi
la logica del canale e' coperta senza rete e senza bridge.
"""
from __future__ import annotations

import threading
from typing import Callable

from .base import Channel, ChannelMessage


def normalize_number(raw: str) -> str:
    """Numeri come stringa di sole cifre (niente +, spazi o trattini):
    il bridge manda il JID user part, che e' gia' in questa forma."""
    return "".join(ch for ch in raw if ch.isdigit())


class WhatsAppChannel(Channel):
    name = "whatsapp"

    def __init__(self, addr: str, token: str,
                 client_factory: Callable | None = None,
                 poll_interval: float = 2.0):
        if not token:
            raise ValueError("WA_BRIDGE_TOKEN mancante")
        self._addr = addr
        self._token = token
        self._client_factory = client_factory or _BridgeClient
        self._poll_interval = poll_interval
        self._client = None
        self._handler: Callable[[ChannelMessage], None] | None = None

    def on_message(self, handler: Callable[[ChannelMessage], None]) -> None:
        self._handler = handler

    def start(self) -> None:
        self._client = self._client_factory(self._addr, self._token,
                                            self._poll_interval)
        self._client.start_polling(self._on_event)

    def send(self, to: str, text: str) -> None:
        if self._client is None:
            raise RuntimeError("canale non avviato")
        self._client.send_message(to, text)

    def health(self) -> dict:
        """Stato del sidecar (linked/jid/pushname): per /whatsapp status."""
        if self._client is None:
            raise RuntimeError("canale non avviato")
        return self._client.health()

    def pair(self) -> dict:
        """Avvia il pairing QR sul sidecar (SOLO da CLI locale)."""
        if self._client is None:
            raise RuntimeError("canale non avviato")
        return self._client.pair()

    def pair_code(self, phone: str) -> dict:
        """Pairing con codice numerico da digitare sul telefono del numero
        dell'agente (alternativa al QR, SOLO da CLI locale)."""
        if self._client is None:
            raise RuntimeError("canale non avviato")
        return self._client.pair_code(phone)

    def qr_state(self) -> dict:
        if self._client is None:
            raise RuntimeError("canale non avviato")
        return self._client.qr_state()

    def _on_event(self, sender: str, text: str) -> None:
        if self._handler is not None:
            self._handler(ChannelMessage(sender=normalize_number(sender),
                                         channel=self.name, text=text))


class _BridgeClient:
    """Client HTTP del sidecar wa-bridge: polling eventi in thread daemon.

    `get`/`post`/`sleep` iniettabili per i test (nessuna rete richiesta).
    Il boot id del bridge azzera l'offset: dopo un riavvio del sidecar la
    numerazione degli eventi riparte e il buffer in memoria e' vuoto, quindi
    ripartire da 0 non consegna duplicati.
    """

    def __init__(self, addr: str, token: str, poll_interval: float = 2.0,
                 get: Callable | None = None, post: Callable | None = None,
                 sleep: Callable[[float], None] | None = None):
        import time as _time
        self._base = f"http://{addr}"
        self._token = token
        self._interval = poll_interval
        self._get = get or self._http_get
        self._post = post or self._http_post
        self._sleep = sleep or _time.sleep
        self._stop = threading.Event()

    # --- HTTP reale ---------------------------------------------------------

    def _http_get(self, path: str) -> dict:
        import json
        import urllib.request
        req = urllib.request.Request(self._base + path,
                                     headers={"X-Bridge-Token": self._token})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _http_post(self, path: str, payload: dict) -> dict:
        import json
        import urllib.request
        req = urllib.request.Request(
            self._base + path, data=json.dumps(payload).encode("utf-8"),
            headers={"X-Bridge-Token": self._token,
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # --- interfaccia usata dal canale ----------------------------------------

    def health(self) -> dict:
        return self._get("/healthz")

    def pair(self) -> dict:
        return self._post("/pair", {})

    def pair_code(self, phone: str) -> dict:
        return self._post("/pair-code", {"phone": normalize_number(phone)})

    def qr_state(self) -> dict:
        return self._get("/qr")

    def send_message(self, to: str, text: str) -> None:
        self._post("/send", {"text": text, "to": normalize_number(to)})

    def start_polling(self, on_update: Callable[[str, str], None]) -> None:
        threading.Thread(target=self._poll_loop, args=(on_update,),
                         daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # --- loop ----------------------------------------------------------------

    def _poll_loop(self, on_update: Callable[[str, str], None]) -> None:
        after = 0
        boot = None
        while not self._stop.is_set():
            try:
                data = self._get(f"/events?after={after}")
            except Exception:
                # Sidecar giu' o non ancora pronto: riprova al prossimo giro.
                self._sleep(self._interval)
                continue
            if data.get("boot") != boot:
                boot = data.get("boot")
                after = 0  # nuovo processo bridge: offset e buffer azzerati
            for ev in data.get("events", []):
                seq = int(ev.get("seq", 0))
                if seq <= after or ev.get("from_me"):
                    continue
                after = seq
                text = (ev.get("text") or "").strip()
                sender = normalize_number(str(ev.get("from") or ""))
                if text and sender:
                    on_update(sender, text)
            self._sleep(self._interval)
