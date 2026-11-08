"""Rilevamento su richiesta dalla voce in-app "Importa dati da una versione
precedente" (POST /setup/migration-probe).

Regole di progetto verificate qui:
- distingue SEMPRE "nessuna fonte nota" (state=none) da "fonte nota ma
  irraggiungibile" (state=unreachable con l'errore): un DB spento non viene
  mai spacciato per "non c'e' nulla da importare";
- il rilevamento scatta solo in modalita' desktop e non scrive nulla
  (l'utente puo' premere "riprova" quante volte vuole);
- la fonte configurata via env ha la precedenza sul rilevamento;
- l'esito registrato (imported/dismissed) viene riportato ma non blocca una
  nuova verifica: un dismiss non e' un punto di non ritorno.
"""
import pytest

from app.config import get_settings
from app.services import migrate

from .conftest import make_user


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_probe_nessuna_fonte_nota(client, monkeypatch):
    """Niente env, niente candidato stock trovato: risposta onesta 'none'."""
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(migrate, "detect_legacy", lambda: (None, None))
    u = make_user(client, "probe_none")
    r = client.post("/setup/migration-probe", headers=u["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "none"
    assert body["state"] == "none"


def test_probe_irraggiungibile_non_e_vuoto(client, monkeypatch):
    """Il caso Fred: vecchio backend spento. Mai 'none': unreachable + errore."""
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    url = migrate.LEGACY_AUTODETECT_CANDIDATES[0]
    monkeypatch.setattr(
        migrate, "detect_legacy",
        lambda: (url, migrate.ProbeResult(state="unreachable",
                                          error="connection refused")))
    u = make_user(client, "probe_down")
    r = client.post("/setup/migration-probe", headers=u["headers"])
    body = r.json()
    assert body["source"] == "detected"
    assert body["state"] == "unreachable"
    assert "refused" in body["error"]


def test_probe_con_dati(client, monkeypatch):
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    url = migrate.LEGACY_AUTODETECT_CANDIDATES[0]
    monkeypatch.setattr(
        migrate, "detect_legacy",
        lambda: (url, migrate.ProbeResult(state="has_data",
                                          counts={"users": 1, "memories": 7})))
    u = make_user(client, "probe_data")
    r = client.post("/setup/migration-probe", headers=u["headers"])
    body = r.json()
    assert body["source"] == "detected"
    assert body["state"] == "has_data"
    assert body["counts"]["memories"] == 7


def test_probe_configurata_vince(client, monkeypatch):
    """ATLAS_LEGACY_DATABASE_URL esplicita: solo lei viene provata."""
    configured = "postgresql+psycopg://postgres@localhost:59999/nope"
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", configured)
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(
        migrate, "probe",
        lambda url: migrate.ProbeResult(state="unreachable", error="down"))

    def boom():
        raise AssertionError("detect_legacy non deve partire con fonte configurata")

    monkeypatch.setattr(migrate, "detect_legacy", boom)
    u = make_user(client, "probe_cfg")
    r = client.post("/setup/migration-probe", headers=u["headers"])
    body = r.json()
    assert body["source"] == "configured"
    assert body["state"] == "unreachable"


def test_probe_non_scrive_nulla_e_dopo_dismiss_funziona(client, monkeypatch):
    """Dismiss registrato: il probe continua a funzionare e lo riporta."""
    monkeypatch.setenv("ATLAS_LEGACY_DATABASE_URL", "")
    monkeypatch.setenv("ATLAS_DESKTOP", "1")
    get_settings.cache_clear()
    url = migrate.LEGACY_AUTODETECT_CANDIDATES[0]
    monkeypatch.setattr(
        migrate, "detect_legacy",
        lambda: (url, migrate.ProbeResult(state="has_data",
                                          counts={"users": 1, "memories": 2})))
    u = make_user(client, "probe_dismiss")
    r = client.post("/setup/migration-dismiss", headers=u["headers"])
    assert r.status_code == 200
    r = client.post("/setup/migration-probe", headers=u["headers"])
    body = r.json()
    assert body["state"] == "has_data"
    assert body["recorded"] == "dismissed"
    # ...e il probe non ha cambiato lo stato registrato.
    r = client.get("/setup/migration-status", headers=u["headers"])
    assert r.json()["state"] == "dismissed"


def test_detect_legacy_ordina_i_risultati(monkeypatch):
    """detect_legacy: preferisce il primo 'has_data'; in sua assenza riporta
    il primo 'unreachable'; altrimenti (None, None)."""
    two = (
        "postgresql+psycopg://atlas:atlas@localhost:5432/atlas",
        "postgresql+psycopg://atlas:atlas@localhost:5433/atlas",
    )
    monkeypatch.setattr(migrate, "LEGACY_AUTODETECT_CANDIDATES", two)

    def fake_probe(url):
        if "5432" in url:
            return migrate.ProbeResult(state="empty")
        return migrate.ProbeResult(state="has_data", counts={"users": 1})

    monkeypatch.setattr(migrate, "probe", fake_probe)
    url, result = migrate.detect_legacy()
    assert "5433" in url and result.state == "has_data"

    monkeypatch.setattr(
        migrate, "probe",
        lambda url: migrate.ProbeResult(state="empty"))
    assert migrate.detect_legacy() == (None, None)

    monkeypatch.setattr(
        migrate, "probe",
        lambda url: migrate.ProbeResult(state="unreachable", error="x"))
    url, result = migrate.detect_legacy()
    assert "5432" in url and result.state == "unreachable"

    def boom(url):
        raise OSError("si e' rotto tutto")

    monkeypatch.setattr(migrate, "probe", boom)
    url, result = migrate.detect_legacy()
    assert result.state == "unreachable" and "OSError" in result.error
