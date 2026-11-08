"""Modalita' desktop standalone: profilo locale senza codice email.

Installazione pulita: nessun worker codici raggiungibile/configurato, il
backend gira solo su 127.0.0.1. La registrazione deve funzionare comunque
(profilo locale) e /auth/modes deve dire al frontend di non proporre codici.
"""
import uuid

import pytest

from app.config import get_settings


@pytest.fixture()
def desktop_on(monkeypatch):
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_modes_local_only(desktop_on, client):
    r = client.get("/auth/modes")
    assert r.status_code == 200
    assert r.json()["modes"] == ["local_password"]


def test_modes_hosted_unchanged(client):
    r = client.get("/auth/modes")
    assert r.status_code == 200
    assert r.json()["modes"] == ["email_code", "password"]


def test_register_local_without_signup_token(desktop_on, client):
    email = f"loc-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "password-locale-1"})
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    r = client.post("/auth/login", json={"email": email, "password": "password-locale-1"})
    assert r.status_code == 200
    assert r.json()["token"]
    assert token


def test_register_duplicate_still_409(desktop_on, client):
    email = f"loc-{uuid.uuid4().hex[:8]}@example.com"
    client.post("/auth/register", json={"email": email, "password": "password-locale-1"})
    r = client.post("/auth/register", json={"email": email, "password": "altra-password-9"})
    assert r.status_code == 409


def test_register_outside_desktop_requires_proof(client):
    email = f"web-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "password-web-123"})
    assert r.status_code in (400, 401, 403), r.text
