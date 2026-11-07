"""Test unitari puri: niente database (girano ovunque)."""
import math
from datetime import datetime, timedelta, timezone

from app.services.classifier import classify, extract_statement
from app.services.embeddings import LocalHashEmbedder, cosine
from app.services.retrieval import combine_score, recency_score

# --- embeddings locali ---

def test_local_embedder_dimension():
    assert len(LocalHashEmbedder().embed("ciao mondo")) == 768


def test_local_embedder_deterministic():
    e = LocalHashEmbedder()
    assert e.embed("stessa frase") == e.embed("stessa frase")


def test_local_embedder_normalized():
    v = LocalHashEmbedder().embed("una frase di prova")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


def test_local_embedder_similar_texts_closer_than_distant():
    e = LocalHashEmbedder()
    a = e.embed("il database del progetto e PostgreSQL")
    b = e.embed("PostgreSQL e il database del progetto")
    c = e.embed("zanzara filobus meridiano quantum")
    assert cosine(a, b) > cosine(a, c)


def test_cosine_identical_is_one():
    e = LocalHashEmbedder()
    v = e.embed("test")
    assert abs(cosine(v, v) - 1.0) < 1e-9


def test_empty_text_zero_vector():
    assert LocalHashEmbedder().embed("") == [0.0] * 768


# --- classificatore ---

def test_classify_questions():
    assert classify("Quale database usa il progetto?") == "question"
    assert classify("a che punto sono?") == "project_status"
    assert classify("When is the deadline?") == "question"


def test_classify_new_info():
    assert classify("Ricorda che la scadenza e il 30 novembre") == "new_info"
    assert classify("Il prossimo sprint inizia lunedi") == "new_info"
    assert classify("Remember that the server is on Oracle Cloud") == "new_info"


def test_classify_action_requests():
    assert classify("Invia una email a Mario") == "action_request"
    assert classify("Cancella l'evento di domani") == "action_request"
    assert classify("Paga la bolletta") == "action_request"


def test_classify_analysis():
    assert classify("Analizza i progressi del mese") == "analysis"


def test_extract_statement_strips_imperative():
    assert extract_statement("Ricorda che la scadenza e il 30") == "la scadenza e il 30"
    assert extract_statement("ricorda: il server e su Oracle") == "il server e su Oracle"
    assert extract_statement("Remember that it works") == "it works"


def test_extract_statement_no_imperative():
    assert extract_statement("Il cielo e blu") == "Il cielo e blu"


# --- punteggi ---

def test_recency_fresh_is_one():
    now = datetime.now(timezone.utc)
    assert abs(recency_score(now, 30.0) - 1.0) < 1e-6


def test_recency_half_life():
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    assert abs(recency_score(old, 30.0) - 0.5) < 1e-6


def test_recency_future_clamped():
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=5)
    assert recency_score(future, 30.0) == 1.0


def test_recency_naive_datetime():
    now = datetime.now(timezone.utc)
    naive = (now - timedelta(days=30)).replace(tzinfo=None)
    assert abs(recency_score(naive, 30.0) - 0.5) < 1e-6


def test_combine_score_relevance_excludes_priors():
    relevance, score = combine_score(0.0, 0.0, 1.0, 1.0, 0.55, 0.25, 0.1, 0.1)
    assert relevance == 0.0
    assert score == 0.2  # solo recency + confidence


def test_combine_score_weights():
    relevance, score = combine_score(1.0, 1.0, 1.0, 1.0, 0.55, 0.25, 0.1, 0.1)
    assert relevance == 0.8
    assert abs(score - 1.0) < 1e-9
