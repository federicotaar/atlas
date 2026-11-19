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
