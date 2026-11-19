"""La forma d'errore API e' unica: {"error": {"code", "message"}}."""
from .conftest import make_source, make_user


def test_invalid_kind_uses_stable_error_shape(client):
    u = make_user(client, "errshape")
    h = u["headers"]
    src = make_source(client, h)
    r = client.post("/memories", json={
        "content": "qualcosa", "source_id": src, "kind": "secret",
        "origin": "stated", "confidence": 0.9,
    }, headers=h)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "invalid_kind"
    assert body["error"]["message"]


def test_unknown_route_is_an_error(client):
    u = make_user(client, "errshape404")
    r = client.get("/non-esiste", headers=u["headers"])
    assert r.status_code == 404
