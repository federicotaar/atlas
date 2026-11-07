def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "up"}


def test_health_no_auth_needed(client):
    assert client.get("/health").status_code == 200
