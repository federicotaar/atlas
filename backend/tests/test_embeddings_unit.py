"""Test unitari puri per la cosine similarity degli embeddings."""
import math

from app.services.embeddings import LocalHashEmbedder, cosine


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

def test_local_hash_deterministic():
    e = LocalHashEmbedder(64)
    assert e.embed("ciao mondo") == e.embed("ciao mondo")


def test_local_hash_dim_and_unit_norm():
    v = LocalHashEmbedder(64).embed("ciao mondo")
    assert len(v) == 64
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-9


def test_local_hash_empty_text_zero_vector():
    assert LocalHashEmbedder(64).embed("") == [0.0] * 64


def test_local_hash_lexical_overlap_scores_higher():
    e = LocalHashEmbedder(256)
    a = e.embed("il progetto Mercurio usa PostgreSQL")
    b = e.embed("PostgreSQL e il database del progetto Mercurio")
    c = e.embed("la ricetta della torta di mele")
    assert cosine(a, b) > cosine(a, c)


def test_local_hash_rejects_bad_dim():
    import pytest
    with pytest.raises(ValueError):
        LocalHashEmbedder(0)
