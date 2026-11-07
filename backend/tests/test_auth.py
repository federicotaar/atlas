from .conftest import make_user


def test_register_returns_token(client):
    u = make_user(client)
    assert u["token"]


def test_register_duplicate_email_409(client):
    u = make_user(client)
    r = client.post("/auth/register", json={"email": u["email"], "password": "altra-password-9"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


def test_register_short_password_422(client):
    r = client.post("/auth/register", json={"email": "x@example.com", "password": "short"})
    assert r.status_code == 422


def test_register_email_case_insensitive(client):
    u = make_user(client)
    r = client.post("/auth/register", json={"email": u["email"].upper(), "password": "altra-password-9"})
    assert r.status_code == 409


def test_login_ok(client):
    u = make_user(client)
    r = client.post("/auth/login", json={"email": u["email"], "password": "password-segreta-1"})
    assert r.status_code == 200
    assert r.json()["token"]


def test_login_wrong_password_401(client):
    u = make_user(client)
    r = client.post("/auth/login", json={"email": u["email"], "password": "sbagliata-123"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_email_401(client):
    r = client.post("/auth/login", json={"email": "ghost@example.com", "password": "qualunque-123"})
    assert r.status_code == 401


def test_missing_token_401(client):
    assert client.get("/projects").status_code == 401


def test_garbage_token_401(client):
    r = client.get("/projects", headers={"Authorization": "Bearer immondizia"})
    assert r.status_code == 401


def test_expired_token_401(client):
    import uuid
    from datetime import datetime, timedelta, timezone

    import jwt as pyjwt
    now = datetime.now(timezone.utc)
    token = pyjwt.encode({"sub": str(uuid.uuid4()), "iat": int((now - timedelta(days=2)).timestamp()),
                          "exp": int((now - timedelta(days=1)).timestamp())},
                         "test-secret-not-for-production", algorithm="HS256")
    r = client.get("/projects", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
