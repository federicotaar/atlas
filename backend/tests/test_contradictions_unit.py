"""Test unitari puri per services/contradictions.py (euristica dichiarata)."""
from decimal import Decimal

from app.services.contradictions import _numbers, is_possible_contradiction


def test_thousands_separator_is_not_decimal():
    assert _numbers("1.000 euro") == {Decimal("1000")}
    assert _numbers("2,5 kg") == {Decimal("2.5")}
    assert _numbers("1.234,56") == {Decimal("1234.56")}
    assert _numbers("3.14") == {Decimal("3.14")}


def test_thousands_detect_real_change():
    conflict, _ = is_possible_contradiction(
        "Il budget del progetto e 1.000 euro",
        "Il budget del progetto e 2.000 euro")
    assert conflict


def test_same_amount_two_spellings_no_conflict():
    conflict, _ = is_possible_contradiction(
        "Il budget del progetto e 1.000 euro",
        "Il budget del progetto e 1000 euro")
    assert not conflict

def test_weekday_change_conflicts():
    conflict, shared = is_possible_contradiction(
        "Allenamento in palestra lunedi",
        "Allenamento in palestra giovedi")
    assert conflict
    assert "allenamento" in shared and "palestra" in shared


def test_month_change_conflicts():
    conflict, _ = is_possible_contradiction(
        "La scadenza del bando arriva a gennaio",
        "La scadenza del bando arriva a marzo")
    assert conflict


def test_different_subjects_no_conflict():
    conflict, _ = is_possible_contradiction(
        "La scadenza del bando arriva a gennaio",
        "Il compleanno di Sara e a gennaio")
    assert not conflict


def test_no_values_no_conflict():
    conflict, _ = is_possible_contradiction(
        "Il gatto di Marco dorme sempre",
        "Il gatto di Marco gioca sempre")
    assert not conflict


def test_content_words_skip_short_and_stopwords():
    from app.services.contradictions import _content_words
    assert _content_words("Il server con la porta aperta") == {"server", "porta", "aperta"}


def test_single_shared_word_is_not_a_conflict():
    conflict, shared = is_possible_contradiction("Riunione giovedi", "Riunione venerdi")
    assert conflict is False and shared == []


def test_english_and_italian_weekday_agree():
    conflict, _ = is_possible_contradiction("Riunione settimanale progetto giovedi", "Weekly progetto riunione thursday")
    assert conflict is False


def test_decimal_comma_values():
    from decimal import Decimal
    assert _numbers("pesa 2,5 kg") == {Decimal("2.5")}
    conflict, shared = is_possible_contradiction("Il pacco pesa 2,5 chili", "Il pacco pesa 3,5 chili")
    assert conflict is True and "pacco" in shared


def test_shared_terms_are_sorted():
    _, shared = is_possible_contradiction("Il bonifico affitto costa 500 euro", "Il bonifico affitto costa 600 euro")
    assert shared == sorted(shared) and "affitto" in shared
