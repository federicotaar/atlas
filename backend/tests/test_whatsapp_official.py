"""Stage 8 (spec sez. 6, build order 8): canale WhatsApp official (Cloud
API). Nessuna rete e nessun account Meta: webhook via TestClient, Graph API
con post iniettato, server finto.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from app.channels.whatsapp_official import WEBHOOK_PATH, WhatsAppOfficialChannel, _GraphClient

APP_SECRET = "test-app-secret"
VERIFY = "test-verify-token"


def make_channel(**overrides):
    kwargs = dict(phone_number_id="123456789", access_token="EAAG-test",
                  app_secret=APP_SECRET, verify_token=VERIFY)
    kwargs.update(overrides)
    return WhatsAppOfficialChannel(**kwargs)


def signed_client(channel):
    from fastapi.testclient import TestClient
    return TestClient(channel.webhook_app())


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body,
                                hashlib.sha256).hexdigest()


def text_payload(msg_id="wamid.1", sender="393331234567",
                 text="ciao", name="Fred"):
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": sender, "profile": {"name": name}}],
        "messages": [{"id": msg_id, "from": sender, "type": "text",
                      "text": {"body": text}}]}}]}]}


# --- requisiti di configurazione ---------------------------------------------

def test_secrets_are_required():
    for missing in ("access_token", "app_secret", "verify_token",
                    "phone_number_id"):
        try:
            make_channel(**{missing: ""})
        except ValueError:
            continue
        raise AssertionError(f"{missing} vuoto accettato")


# --- verifica GET del webhook --------------------------------------------------

def test_webhook_verify_accepts_correct_token():
    client = signed_client(make_channel())
    r = client.get(WEBHOOK_PATH, params={"hub.mode": "subscribe",
                                         "hub.verify_token": VERIFY,
                                         "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"


def test_webhook_verify_rejects_wrong_token():
    client = signed_client(make_channel())
    r = client.get(WEBHOOK_PATH, params={"hub.mode": "subscribe",
                                         "hub.verify_token": "sbagliato",
                                         "hub.challenge": "12345"})
    assert r.status_code == 403


# --- firma dei POST ------------------------------------------------------------

def test_unsigned_post_is_rejected():
    channel = make_channel()
    got = []
    channel.on_message(got.append)
    client = signed_client(channel)
    body = json.dumps(text_payload()).encode()
    r = client.post(WEBHOOK_PATH, content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 403 and got == []


def test_forged_signature_is_rejected():
    channel = make_channel()
    got = []
    channel.on_message(got.append)
    client = signed_client(channel)
    body = json.dumps(text_payload()).encode()
    forged = "sha256=" + hmac.new(b"altro-segreto", body,
                                  hashlib.sha256).hexdigest()
    r = client.post(WEBHOOK_PATH, content=body,
                    headers={"X-Hub-Signature-256": forged})
    assert r.status_code == 403 and got == []


# --- parsing dei messaggi -------------------------------------------------------

def test_signed_text_message_reaches_handler():
    channel = make_channel()
    got = []
    channel.on_message(got.append)
    client = signed_client(channel)
    body = json.dumps(text_payload()).encode()
    r = client.post(WEBHOOK_PATH, content=body,
                    headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 200
    assert len(got) == 1
    msg = got[0]
    assert msg.channel == "whatsapp" and msg.sender == "393331234567"
    assert msg.text == "ciao" and msg.display == "Fred"


def test_retry_same_message_id_delivered_once():
    channel = make_channel()
    got = []
    channel.on_message(got.append)
    client = signed_client(channel)
    body = json.dumps(text_payload()).encode()
    headers = {"X-Hub-Signature-256": sign(body)}
    assert client.post(WEBHOOK_PATH, content=body, headers=headers).status_code == 200
    assert client.post(WEBHOOK_PATH, content=body, headers=headers).status_code == 200
    assert len(got) == 1


def test_non_text_and_statuses_ignored():
    channel = make_channel()
    got = []
    channel.on_message(got.append)
    client = signed_client(channel)
    payload = {"entry": [{"changes": [{"value": {
        "messages": [{"id": "w1", "from": "393331234567", "type": "image"},
                     {"id": "w2", "from": "393331234567", "type": "text",
                      "text": {"body": "  "}}],
        "statuses": [{"id": "w1", "status": "delivered"}]}}]}]}
    body = json.dumps(payload).encode()
    r = client.post(WEBHOOK_PATH, content=body,
                    headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 200 and got == []


# --- invio via Graph API ---------------------------------------------------------

def test_graph_client_sends_text_message():
    posts = []
    client = _GraphClient("123456789", "EAAG-test",
                          post=lambda url, payload: posts.append((url, payload)) or {})
    client.send_message("393331234567", "ciao")
    assert posts == [("https://graph.facebook.com/v21.0/123456789/messages", {
        "messaging_product": "whatsapp",
        "to": "393331234567",
        "type": "text",
        "text": {"body": "ciao"},
    })]


# --- avvio con server finto -------------------------------------------------------

class FakeServer:
    def __init__(self, app, listen):
        self.app = app
        self.listen = listen
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


def test_start_wires_graph_and_server():
    channel = make_channel(server_factory=FakeServer)
    channel.on_message(lambda m: None)
    channel.start()
    assert channel._server.started is True
    assert channel._graph is not None
    channel.stop()
    assert channel._server.started is False
