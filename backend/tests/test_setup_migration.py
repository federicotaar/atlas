"""Primo avvio desktop: stato migrazione (configurazione, irraggiungibile,
scelta esplicita registrata)."""
import pytest

from app.config import get_settings

from .conftest import make_user


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_status_not_configured(client, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
    get_settings.cache_clear()
    u = make_user(client, "setupnc")
    r = client.get("/setup/migration-status", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["state"] == "not_configured"


def test_status_unreachable_distingue_da_vuoto(client, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL",
                       "postgresql+psycopg://postgres@localhost:59999/nope")
    get_settings.cache_clear()
    u = make_user(client, "setupun")
    r = client.get("/setup/migration-status", headers=u["headers"])
    body = r.json()
    assert body["state"] == "unreachable"
    assert body["error"]
    # import rifiutato con 503 mentre l'origine non risponde
    r = client.post("/setup/migration-import", headers=u["headers"])
    assert r.status_code == 503


def test_dismiss_registrato_e_non_silenzioso(client, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL",
                       "postgresql+psycopg://postgres@localhost:59999/nope")
    get_settings.cache_clear()
    u = make_user(client, "setupdis")
    r = client.post("/setup/migration-dismiss", headers=u["headers"])
    assert r.json()["state"] == "dismissed"
    # dopo la scelta esplicita, lo stato resta dismissed (non ripropone)
    r = client.get("/setup/migration-status", headers=u["headers"])
    assert r.json()["state"] == "dismissed"
