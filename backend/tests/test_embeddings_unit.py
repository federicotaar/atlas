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


def test_local_hash_is_deterministic():
    emb = LocalHashEmbedder(64)
    assert emb.embed("il server e su Oracle") == emb.embed("il server e su Oracle")


def test_local_hash_vectors_have_unit_norm():
    import math
    vec = LocalHashEmbedder(128).embed("una frase abbastanza lunga per riempire il vettore")
    assert abs(math.sqrt(sum(v * v for v in vec)) - 1.0) < 1e-9


def test_local_hash_empty_text_is_zero_vector():
    vec = LocalHashEmbedder(16).embed("")
    assert vec == [0.0] * 16
    assert LocalHashEmbedder(16).embed("   ...  ") == [0.0] * 16


def test_local_hash_ignores_case():
    emb = LocalHashEmbedder(64)
    assert emb.embed("Progetto ATLAS") == emb.embed("progetto atlas")


def test_cosine_basic_cases():
    from app.services.embeddings import cosine
    assert abs(cosine([1.0, 2.0], [2.0, 4.0]) - 1.0) < 1e-12
    assert abs(cosine([1.0, 0.0], [-1.0, 0.0]) + 1.0) < 1e-12
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_word_order_matters_via_bigrams():
    emb = LocalHashEmbedder(256)
    assert emb.embed("cane morde uomo") != emb.embed("uomo morde cane")


def test_dot_equals_cosine_for_normalized_vectors():
    from app.services.embeddings import cosine, dot
    emb = LocalHashEmbedder(128)
    a, b = emb.embed("memoria di prova uno"), emb.embed("memoria di prova due")
    assert abs(dot(a, b) - cosine(a, b)) < 1e-9
    assert dot([1.0, 2.0], [3.0, 4.0]) == 11.0


def test_euclidean_distance():
    import pytest
    from app.services.embeddings import euclidean
    assert euclidean([0.0, 0.0], [3.0, 4.0]) == 5.0
    assert euclidean([1.0], [1.0]) == 0.0
    with pytest.raises(ValueError):
        euclidean([1.0], [1.0, 2.0])


def test_top_k_orders_by_similarity():
    from app.services.embeddings import top_k
    emb = LocalHashEmbedder(256)
    items = [("a", emb.embed("progetto atlas database postgres")),
             ("b", emb.embed("ricetta della torta al cioccolato")),
             ("c", emb.embed("progetto atlas server oracle"))]
    ranked = top_k(emb.embed("database del progetto atlas"), items, k=2)
    assert [cid for cid, _ in ranked][0] == "a"
    assert len(ranked) == 2
    assert ranked[0][1] >= ranked[1][1]


def test_top_k_edge_cases():
    from app.services.embeddings import top_k
    assert top_k([1.0, 0.0], [("x", [1.0, 0.0])], k=0) == []
    assert top_k([1.0, 0.0], [("x", [0.0, 1.0])]) == []
    assert top_k([1.0, 0.0], [("x", [1.0, 1.0])], min_score=0.9) == []
    tie = top_k([1.0, 0.0], [("b", [1.0, 0.0]), ("a", [1.0, 0.0])])
    assert [cid for cid, _ in tie] == ["a", "b"]
