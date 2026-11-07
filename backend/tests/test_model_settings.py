"""BYOK model settings: scelta provider per utente, chiave cifrata, consenso
cloud, isolamento tra utenti, uso reale nel flusso /ask (provider mockati)."""
import base64

import pytest

from app.config import get_settings
from app.services import llm_providers
from tests.conftest import make_user

TEST_FERNET = base64.urlsafe_b64encode(b"k" * 32).decode()


@pytest.fixture()
def creds_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "credentials_key", TEST_FERNET)


def test_default_is_extractive(client, fake_codes_worker):
    u = make_user(client, "ms")
    r = client.get("/settings/model", headers=u["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "extractive"
    assert body["has_key"] is False
    ids = {p["id"] for p in body["providers"]}
    assert ids == {"extractive", "ollama_local", "ollama_cloud", "groq", "gemini", "custom"}


def test_key_provider_requires_key(client, fake_codes_worker, creds_key):
    u = make_user(client, "ms")
    r = client.put("/settings/model", headers=u["headers"],
                   json={"provider": "groq", "consent_cloud": True})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_api_key"


def test_cloud_requires_consent(client, fake_codes_worker, creds_key):
    u = make_user(client, "ms")
    r = client.put("/settings/model", headers=u["headers"],
                   json={"provider": "groq", "api_key": "gsk_test"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "cloud_consent_required"


def test_unknown_provider_rejected(client, fake_codes_worker, creds_key):
    u = make_user(client, "ms")
    r = client.put("/settings/model", headers=u["headers"], json={"provider": "openai"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_provider"


def test_save_and_clear_key_never_exposed(client, fake_codes_worker, creds_key, db_session):
    u = make_user(client, "ms")
    r = client.put("/settings/model", headers=u["headers"],
                   json={"provider": "groq", "model": "llama-3.3-70b-versatile",
                         "api_key": "gsk_segreto", "consent_cloud": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_key"] is True
    assert "gsk_segreto" not in r.text and "api_key" not in body

    # nel DB la chiave e' cifrata (Fernet), mai in chiaro
    from app.models import User, UserModelSetting
    row = (db_session.query(UserModelSetting).join(User, User.id == UserModelSetting.user_id)
           .filter(User.email == u["email"]).one())
    assert row.api_key_enc.startswith("gAAAA")
    assert "gsk_segreto" not in row.api_key_enc

    r = client.put("/settings/model", headers=u["headers"],
                   json={"provider": "groq", "api_key": "", "consent_cloud": True})
    assert r.status_code == 200
    assert r.json()["has_key"] is False


def test_isolation_between_users(client, fake_codes_worker, creds_key):
    a = make_user(client, "msa")
    b = make_user(client, "msb")
    r = client.put("/settings/model", headers=a["headers"],
                   json={"provider": "gemini", "api_key": "AIza_a", "consent_cloud": True})
    assert r.status_code == 200
    rb = client.get("/settings/model", headers=b["headers"])
    assert rb.json()["provider"] == "extractive"
    assert rb.json()["has_key"] is False


def test_custom_provider_needs_base_url(client, fake_codes_worker, creds_key):
    u = make_user(client, "ms")
    r = client.put("/settings/model", headers=u["headers"],
                   json={"provider": "custom", "model": "x", "consent_cloud": True})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_base_url"


def test_ask_uses_user_provider(client, fake_codes_worker, creds_key, monkeypatch):
    u = make_user(client, "ms")
    client.post("/settings/model", headers=u["headers"], json={})  # route sanity via PUT below
    r = client.put("/settings/model", headers=u["headers"],
                   json={"provider": "groq", "api_key": "gsk_live", "consent_cloud": True})
    assert r.status_code == 200

    calls = {}

    def fake_chat(provider, model, base_url, api_key, question, memories, timeout):
        calls.update(provider=provider, api_key=api_key, question=question)
        return "Risposta mockata [M1]"

    monkeypatch.setattr(llm_providers, "chat_completion", fake_chat)
    client.post("/ask", json={"text": "ricorda che il mio colore preferito e' il viola"},
                headers=u["headers"])
    r = client.post("/ask", json={"text": "qual e' il mio colore preferito?"}, headers=u["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "Risposta mockata [M1]"
    assert calls["provider"] == "groq"
    assert calls["api_key"] == "gsk_live"
    assert "colore preferito" in calls["question"]


def test_ask_missing_key_is_clear_error(client, fake_codes_worker, creds_key, db_session):
    u = make_user(client, "ms")
    r = client.put("/settings/model", headers=u["headers"],
                   json={"provider": "groq", "api_key": "gsk_x", "consent_cloud": True})
    assert r.status_code == 200
    # cancella la chiave direttamente nel DB: al prossimo /ask errore chiaro
    from app.models import User, UserModelSetting
    row = (db_session.query(UserModelSetting).join(User, User.id == UserModelSetting.user_id)
           .filter(User.email == u["email"]).one())
    row.api_key_enc = ""
    db_session.commit()
    client.post("/ask", json={"text": "ricorda che ho un gatto"}, headers=u["headers"])
    r = client.post("/ask", json={"text": "ho un gatto?"}, headers=u["headers"])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_api_key"


def test_list_models_failure_returns_empty(client, fake_codes_worker, creds_key, monkeypatch):
    u = make_user(client, "ms")

    def boom(*a, **k):
        raise RuntimeError("giu")

    monkeypatch.setattr(llm_providers.httpx, "get", boom)
    r = client.get("/settings/models", params={"provider": "groq"}, headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["models"] == []
