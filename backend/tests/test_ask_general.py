"""Chat generale con provider selezionato (fase bugfix "il bot non risponde").

Un utente che ha scelto un provider generativo riceve una risposta dal
modello anche quando nessuna memoria e' pertinente: le memorie aiutano la
risposta, non decidono se rispondere. Il default estrattivo (nessuna scelta)
mantiene il comportamento solo-memorie ("Non lo so")."""
import pytest

from app.services import llm_providers
from app.services.embeddings import ModelUnavailable

from .conftest import make_memory, make_source, make_user


class _FakeGenerator:
    """Sostituisce il generator risolto per l'utente: niente rete."""

    def __init__(self, general="Sto bene, grazie!", grounded="Uso PostgreSQL, come da memoria [M1].",
                 fail=False):
        self.name = "ollama_local"
        self.provider = "ollama_local"
        self._general = general
        self._grounded = grounded
        self._fail = fail
        self.general_calls = 0
        self.grounded_calls = 0

    def answer(self, question, memories):
        self.grounded_calls += 1
        if self._fail:
            raise ModelUnavailable("ollama down")
        return self._grounded

    def answer_general(self, question):
        self.general_calls += 1
        if self._fail:
            raise ModelUnavailable("ollama down")
        return self._general


@pytest.fixture()
def fake_gen(monkeypatch):
    holder = {}

    def _install(gen):
        holder["gen"] = gen
        monkeypatch.setattr(llm_providers, "resolve_user_generator", lambda db, s, uid: gen)
        return gen

    return _install


def test_general_chat_uses_selected_provider_without_memories(client, fake_gen):
    u = make_user(client)
    gen = fake_gen(_FakeGenerator())
    r = client.post("/ask", json={"text": "come va"}, headers=u["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == "Sto bene, grazie!"
    assert "Non lo so" not in data["answer"]
    assert data["model"] == "ollama_local"
    assert data["citations"] == []
    assert gen.general_calls == 1 and gen.grounded_calls == 0


def test_relevant_memory_grounding_still_works_with_provider(client, fake_gen):
    u = make_user(client)
    sid = make_source(client, u["headers"], label="chat")
    make_memory(client, u["headers"], sid, "Il progetto ATLAS usa PostgreSQL 16 con pgvector")
    gen = fake_gen(_FakeGenerator())
    r = client.post("/ask", json={"text": "Quale database usa il progetto ATLAS?"}, headers=u["headers"])
    data = r.json()
    assert data["answer"] == "Uso PostgreSQL, come da memoria [M1]."
    assert len(data["citations"]) >= 1
    assert gen.grounded_calls == 1 and gen.general_calls == 0


def test_extractive_default_keeps_memory_only_behavior(client):
    # Nessun provider selezionato: comportamento invariato.
    u = make_user(client)
    r = client.post("/ask", json={"text": "come va"}, headers=u["headers"])
    assert "Non lo so" in r.json()["answer"]


def test_provider_failure_is_visible_not_silent_idk(client, fake_gen):
    u = make_user(client)
    fake_gen(_FakeGenerator(fail=True))
    r = client.post("/ask", json={"text": "come va"}, headers=u["headers"])
    assert r.status_code == 502
    body = r.json()["error"]
    assert body["code"] == "model_unavailable"
    assert "ollama_local" in body["message"]


def test_memory_save_still_saves_without_llm(client, fake_gen):
    u = make_user(client)
    gen = fake_gen(_FakeGenerator())
    r = client.post("/ask", json={"text": "Ricorda che la scadenza e il 30 novembre"},
                    headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["classification"] == "new_info"
    assert gen.general_calls == 0 and gen.grounded_calls == 0
