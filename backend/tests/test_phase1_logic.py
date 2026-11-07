"""Test unitari puri (nessun DB): euristica contraddizioni ed estrazione entita."""
from app.services.contradictions import is_possible_contradiction
from app.services.entities import _candidates, _guess_kind


def test_contradiction_detects_changed_day():
    conflict, shared = is_possible_contradiction(
        "La riunione del progetto Mercurio e il martedi alle 10",
        "La riunione del progetto Mercurio e il giovedi alle 15",
    )
    assert conflict
    assert "riunione" in shared


def test_contradiction_detects_changed_value():
    conflict, _ = is_possible_contradiction(
        "Il budget mensile del server staging e 200 euro",
        "Il budget mensile del server staging e 350 euro",
    )
    assert conflict


def test_no_contradiction_when_unrelated():
    conflict, _ = is_possible_contradiction(
        "Mario corre ogni mattina nel parco",
        "La bicicletta di Mario e rossa",
    )
    assert not conflict


def test_no_contradiction_when_same_values():
    conflict, _ = is_possible_contradiction(
        "La riunione del progetto Mercurio e il martedi alle 10",
        "La riunione del progetto Mercurio resta il martedi alle 10",
    )
    assert not conflict


def test_entity_candidates_skip_sentence_start():
    cands = _candidates("Il progetto Mercurio usa PostgreSQL.")
    assert "Il" not in cands
    assert "Mercurio" in cands
    assert "PostgreSQL" in cands


def test_entity_candidates_person_two_words():
    cands = _candidates("Giulia Bianchi e la designer del progetto.")
    assert "Giulia Bianchi" in cands


def test_entity_candidates_skip_months_and_weekdays():
    cands = _candidates("La consegna era prevista per Marzo, poi spostata a Martedi.")
    assert "Marzo" not in cands
    assert "Martedi" not in cands


def test_guess_kind_project():
    assert _guess_kind("Mercurio", {"mercurio"}) == "project"
    assert _guess_kind("Giulia Bianchi", set()) == "person"
    assert _guess_kind("PostgreSQL", set()) == "concept"
