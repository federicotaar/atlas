"""CORS: aperto solo per le origini configurate (default: desktop Tauri)."""


def test_preflight_tauri_origin_allowed(client):
    r = client.options(
        "/health",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://tauri.localhost"


def test_preflight_unknown_origin_rejected(client):
    r = client.options(
        "/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.headers.get("access-control-allow-origin") is None


def test_same_origin_request_has_no_cors_headers(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_actual_request_from_tauri_origin_allowed(client):
    r = client.get("/health", headers={"Origin": "tauri://localhost"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "tauri://localhost"
