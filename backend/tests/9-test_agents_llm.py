"""Planner con LLM opzionale: se un modello e' configurato e risponde, il
piano viene da lui; altrimenti resta la baseline rule-based (mai bloccarsi
per colpa del modello). Il percorso usato e' sempre registrato (mode)."""
import pytest

from app.agents import core
from app.agents.schemas import PlanInput
from app.config import get_settings
from app.services.embeddings import ModelUnavailable


class FakeLLM:
    name = "fake-llm"

    def __init__(self, text):
        self.text = text

    def answer(self, question, memories):
        return self.text


class BrokenLLM:
    name = "broken-llm"

    def answer(self, question, memories):
        raise ModelUnavailable("provider giu'")


@pytest.fixture()
def llm_generator(monkeypatch):
    """ModelRouter costruisce il generatore dalla factory: la sostituiamo."""
    import app.services.router as router_mod

    def set_gen(gen):
        monkeypatch.setattr(router_mod, "build_generator", lambda settings: gen)
    return set_gen


def test_planner_uses_llm_when_available(db_session, db_user, llm_generator):
    llm_generator(FakeLLM("1. Raccogliere i requisiti\n2. Scrivere la bozza\n3. Revisionare"))
    out = core.run_planner(db_session, get_settings(), db_user.id,
                           PlanInput(goal="organizzare il lancio", facts=["Il lancio e a giugno"]))
    assert [t.title for t in out.tasks] == [
        "Raccogliere i requisiti", "Scrivere la bozza", "Revisionare"]
    assert out.mode == "llm:fake-llm"


def test_planner_falls_back_when_llm_down(db_session, db_user, llm_generator):
    llm_generator(BrokenLLM())
    out = core.run_planner(db_session, get_settings(), db_user.id,
                           PlanInput(goal="preparare la relazione e poi inviarla",
                                     facts=["La relazione scade venerdi"]))
    assert out.mode == "rule-based"
    assert [t.title for t in out.tasks] == ["preparare la relazione", "inviarla"]


def test_planner_falls_back_on_unparseable_answer(db_session, db_user, llm_generator):
    llm_generator(FakeLLM("Non ho capito la domanda, puoi ripetere?"))
    out = core.run_planner(db_session, get_settings(), db_user.id,
                           PlanInput(goal="studiare il capitolo", facts=["Esame lunedi"]))
    assert out.mode == "rule-based"
    assert [t.title for t in out.tasks] == ["studiare il capitolo"]


def test_planner_without_facts_stays_empty(db_session, db_user, llm_generator):
    llm_generator(FakeLLM("1. Qualcosa"))
    out = core.run_planner(db_session, get_settings(), db_user.id,
                           PlanInput(goal="obiettivo qualsiasi", facts=[]))
    assert out.tasks == [] and out.based_on_facts is False
    assert out.mode == "rule-based"


def test_planner_rule_based_without_llm_configured(db_session, db_user):
    """Default dei test (provider extractive): baseline meccanica dichiarata."""
    out = core.run_planner(db_session, get_settings(), db_user.id,
                           PlanInput(goal="fare A e poi fare B", facts=["fatto uno"]))
    assert out.mode == "rule-based"
    assert [t.title for t in out.tasks] == ["fare A", "fare B"]
