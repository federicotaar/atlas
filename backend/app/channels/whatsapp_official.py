"""Canale WhatsApp official (build order 8, spec sez. 6): WhatsApp Business
Cloud API. Il canale riceve i messaggi via webhook Meta e risponde via
Graph API. Niente sessioni linked-device, niente rischio ban della rotta
non ufficiale; richiede un numero Business e un'app Meta configurata.

Sicurezza (spec sez. 6, "critical"):
- ogni webhook POST e' verificato con X-Hub-Signature-256 (HMAC dell'app
  secret): un payload non firmato da Meta viene scartato SEMPRE, senza
  eccezioni: chiunque trovi l'URL del tunnel non puo' iniettare messaggi;
- la verifica GET (hub.challenge) accetta solo il verify token generato
  localmente e tenuto in .env;
- allowlist e pairing restano CENTRALI nell'hub (stesso nome canale
  "whatsapp" dell'unofficial: allowlist, pairing e storico conversazioni
  sopravvivono al cambio di backend);
- i segreti (WHATSAPP_ACCESS_TOKEN, WHATSAPP_APP_SECRET,
  WHATSAPP_VERIFY_TOKEN, WHATSAPP_PHONE_NUMBER_ID) arrivano solo da
  .env/ambiente: mai in config, mai in log.

Il webhook ascolta su loopback; per raggiungerlo Meta serve un tunnel
(cloudflared/ngrok), avviato dal proprietario quando collega il numero.
In locale i test coprono tutta la logica via TestClient e HTTP iniettato:
nessuna rete, nessun account Meta.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from typing import Callable

from fastapi import FastAPI, Request, Response

from .base import Channel, ChannelMessage
from .whatsapp import normalize_number

GRAPH_BASE = "https://graph.facebook.com/v21.0"
WEBHOOK_PATH = "/webhook/whatsapp"


class WhatsAppOfficialChannel(Channel):
    # Stesso nome del canale unofficial: l'hub vede un unico canale
    # "whatsapp" qualunque sia il backend attivo (mai entrambi insieme).
    name = "whatsapp"

    def __init__(self, *, phone_number_id: str, access_token: str,
                 app_secret: str, verify_token: str,
                 listen: str = "127.0.0.1:8765",
                 graph_client_factory: Callable | None = None,
                 server_factory: Callable | None = None):
        if not access_token:
            raise ValueError("WHATSAPP_ACCESS_TOKEN mancante")
        if not app_secret:
            raise ValueError("WHATSAPP_APP_SECRET mancante: la firma dei "
                             "webhook non e' opzionale")
        if not verify_token:
            raise ValueError("WHATSAPP_VERIFY_TOKEN mancante")
        if not phone_number_id:
            raise ValueError("WHATSAPP_PHONE_NUMBER_ID mancante")
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._app_secret = app_secret.encode("utf-8")
        self._verify_token = verify_token
        self._listen = listen
        self._graph_factory = graph_client_factory or _GraphClient
        self._server_factory = server_factory or _UvicornServer
        self._graph = None
        self._server = None
        self._handler: Callable[[ChannelMessage], None] | None = None
        self._seen_ids: set[str] = set()

    def on_message(self, handler: Callable[[ChannelMessage], None]) -> None:
        self._handler = handler

    def start(self) -> None:
        self._graph = self._graph_factory(self._phone_number_id,
                                          self._access_token)
        self._server = self._server_factory(self.webhook_app(), self._listen)
        self._server.start()

    def send(self, to: str, text: str) -> None:
        if self._graph is None:
            raise RuntimeError("canale non avviato")
        self._graph.send_message(normalize_number(to), text)

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()

    # --- webhook ------------------------------------------------------------

    def webhook_app(self):
        """ASGI app del webhook: montabile anche nel backend FastAPI."""
        app = FastAPI(title="atlas-whatsapp-webhook")

        @app.get(WEBHOOK_PATH)
        async def verify(request: Request) -> Response:
            params = request.query_params
            if (params.get("hub.mode") == "subscribe"
                    and params.get("hub.verify_token") == self._verify_token):
                return Response(content=params.get("hub.challenge", ""),
                                media_type="text/plain")
            return Response(status_code=403)

        @app.post(WEBHOOK_PATH)
        async def receive(request: Request) -> Response:
            body = await request.body()
            signature = request.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(
                self._app_secret, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return Response(status_code=403)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return Response(status_code=400)
            self._dispatch(payload)
            return Response(status_code=200)

        return app

    def _dispatch(self, payload: dict) -> None:
        if self._handler is None:
            return
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                names = {
                    normalize_number(str(c.get("wa_id", ""))): c.get("profile", {}).get("name", "")
                    for c in value.get("contacts", [])
                }
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        continue  # vocali/foto/file: non ancora gestiti qui
                    msg_id = str(msg.get("id", ""))
                    if msg_id and msg_id in self._seen_ids:
                        continue  # retry di Meta: ogni messaggio una volta sola
                    if msg_id:
                        self._seen_ids.add(msg_id)
                        if len(self._seen_ids) > 2000:
                            self._seen_ids = set(list(self._seen_ids)[-1000:])
                    text = (msg.get("text", {}).get("body") or "").strip()
                    sender = normalize_number(str(msg.get("from", "")))
                    if text and sender:
                        self._handler(ChannelMessage(
                            sender=sender, channel=self.name, text=text,
                            display=names.get(sender, "")))


class _GraphClient:
    """Invio messaggi via Graph API. `post` iniettabile per i test."""

    def __init__(self, phone_number_id: str, access_token: str,
                 post: Callable | None = None):
        self._url = f"{GRAPH_BASE}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}",
                         "Content-Type": "application/json"}
        self._post = post or self._http_post

    def _http_post(self, url: str, payload: dict) -> dict:
        import urllib.request
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers=self._headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def send_message(self, to: str, text: str) -> None:
        self._post(self._url, {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        })


class _UvicornServer:
    """uvicorn su loopback in thread daemon; il tunnel verso l'esterno
    (cloudflared/ngrok) resta un processo del proprietario."""

    def __init__(self, app, listen: str):
        import uvicorn  # import pigro: i test non avviano server
        host, _, port = listen.rpartition(":")
        config = uvicorn.Config(app, host=host, port=int(port),
                                log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
