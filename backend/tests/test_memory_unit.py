"""Test unitari puri per la validazione delle memorie (niente DB)."""
import pytest

from app.errors import AtlasError
from app.services.memory import validate_memory_input


def test_valid_input_passes():
    validate_memory_input("fact", "stated", 0.9, "Il server e su Oracle Cloud")
    validate_memory_input("task", "inferred", 0.0, "x")
    validate_memory_input("hypothesis", "imported", 1.0, "x")


def test_invalid_kind_rejected():
    with pytest.raises(AtlasError) as exc:
        validate_memory_input("secret", "stated", 0.9, "x")
    assert exc.value.code == "invalid_kind"


def test_invalid_origin_rejected():
    with pytest.raises(AtlasError) as exc:
        validate_memory_input("fact", "guessed", 0.9, "x")
    assert exc.value.code == "invalid_origin"


def test_confidence_out_of_range_rejected():
    for bad in (-0.1, 1.1):
        with pytest.raises(AtlasError) as exc:
            validate_memory_input("fact", "stated", bad, "x")
        assert exc.value.code == "invalid_confidence"


def test_empty_content_rejected():
    with pytest.raises(AtlasError) as exc:
        validate_memory_input("fact", "stated", 0.9, "   ")
    assert exc.value.code == "empty_content"
