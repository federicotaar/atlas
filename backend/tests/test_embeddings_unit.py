"""Test unitari puri per la cosine similarity degli embeddings."""
import math

from app.services.embeddings import cosine


def test_cosine_identical_vectors():
    assert abs(cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors():
    assert abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_opposite_vectors():
    assert abs(cosine([1.0, 0.0], [-1.0, 0.0]) + 1.0) < 1e-9


def test_cosine_zero_vector_is_safe():
    assert cosine([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert cosine([1.0, 2.0], [0.0, 0.0]) == 0.0


def test_cosine_known_angle():
    assert abs(cosine([1.0, 0.0], [1.0, 1.0]) - math.sqrt(0.5)) < 1e-9
