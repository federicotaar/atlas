"""Test unitari puri per services/entities.py (pattern e candidate: niente DB)."""
from app.services.entities import _REL_PATTERNS, _candidates, _guess_kind


def _match(text):
    for rx, relation in _REL_PATTERNS:
        m = rx.match(text)
        if m:
            return relation, m.group(1).strip(), m.group(2).strip().rstrip(".")
    return None


def test_existing_relation_patterns_still_match():
    assert _match("Giulia lavora da Acme") == ("lavora_per", "Giulia", "Acme")
    assert _match("Il backend usa PostgreSQL") == ("usa", "Il backend", "PostgreSQL")
    assert _match("Atlas dipende da Ollama") == ("dipende_da", "Atlas", "Ollama")
    assert _match("Marco porta avanti Atlas") == ("porta_avanti", "Marco", "Atlas")


def test_new_relation_patterns():
    assert _match("Giulia studia al Copernico") == ("studia_a", "Giulia", "Copernico")
    assert _match("Marco studia all'Universita di Modena") == (
        "studia_a", "Marco", "Universita di Modena")
    assert _match("Federico abita a Modena") == ("abita_a", "Federico", "Modena")
    assert _match("Sara fa parte del team Atlas") == ("fa_parte_di", "Sara", "team Atlas")


def test_plain_sentence_has_no_relation():
    assert _match("Il cielo e sereno stasera") is None


def test_candidates_drop_sentence_opener():
    found = _candidates("Il progetto Mercurio usa PostgreSQL.")
    assert "Mercurio" in found and "PostgreSQL" in found
    assert "Il" not in found


def test_candidates_skip_known_non_entities():
    assert _candidates("Domani Marco arriva") == []

def test_extract_relations_returns_pure_triples():
    from app.services.entities import extract_relations
    assert extract_relations("Sara fa parte del team Atlas") == [
        ("Sara", "fa_parte_di", "team Atlas")]
    assert extract_relations("Federico abita a Modena") == [
        ("Federico", "abita_a", "Modena")]
    assert extract_relations("Giulia studia al Copernico") == [
        ("Giulia", "studia_a", "Copernico")]
    assert extract_relations("Il cielo e sereno") == []


def test_extract_relations_strips_trailing_period():
    from app.services.entities import extract_relations
    assert extract_relations("Marco porta avanti Atlas.") == [
        ("Marco", "porta_avanti", "Atlas")]

def test_candidates_skip_discourse_openers():
    assert _candidates("Forse Maria Rossi viene") == []
    assert _candidates("Ecco Marco Rossi arriva") == []
    assert _candidates("Invece Giulia Bianchi resta") == []


def test_candidates_keep_real_names():
    assert _candidates("Maria Rossi viene") == ["Maria Rossi"]
