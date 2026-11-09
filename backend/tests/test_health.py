import app as app_pkg


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "up", "app": "atlas", "api": "v1",
                        "version": app_pkg.__version__}


def test_health_version_is_current(client):
    """Il desktop riusa un backend esistente solo se dichiara una versione
    compatibile: /health deve esporla, senza autenticazione."""
    r = client.get("/health")
    assert r.json()["version"] == "3.0.4"


def test_health_no_auth_needed(client):
    assert client.get("/health").status_code == 200
