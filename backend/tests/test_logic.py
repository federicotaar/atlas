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


def test_classify_declarative_with_question_word_is_new_info():
    assert classify("Mi chiedo quando parte il treno") == "new_info"
    assert classify("Ho capito come funziona il deploy") == "new_info"


def test_cosine_zero_vector_is_zero_not_error():
    e = LocalHashEmbedder()
    assert cosine(e.embed(""), e.embed("test")) == 0.0


def test_embedder_bigrams_make_word_order_matter():
    e = LocalHashEmbedder()
    ab = e.embed("cane gatto")
    ba = e.embed("gatto cane")
    assert ab == e.embed("cane gatto")
    assert cosine(ab, ba) < 1.0


def test_extractive_answer_lists_memories_with_citations():
    from app.services.generation import ExtractiveGenerator
    out = ExtractiveGenerator().answer("q?", [{"content": "il deploy e venerdi"},
                                              {"content": "il server e Oracle"}])
    assert "1. il deploy e venerdi [M1]" in out
    assert "2. il server e Oracle [M2]" in out

def test_action_words_do_not_match_inside_unrelated_words():
    from app.services.classifier import classify
    for text in ('What is a bookmark?', 'How does a paycheck work?', 'When is the sender available?'):
        assert classify(text)=='question'

def test_actions_match_at_punctuation_boundaries():
    from app.services.classifier import classify
    for text in ('send: hello','Please BUY milk','manda, per favore'):
        assert classify(text)=='action_request'

def test_standard_analysis_spellings():
    from app.services.classifier import classify
    for text in ('analyze this text','analyse this text','summarise this text'):
        assert classify(text)=='analysis'

def test_statement_prefix_preserves_the_fact():
    from app.services.classifier import extract_statement
    assert extract_statement('Remember that I like tea')=='I like tea'
    assert extract_statement('  a plain fact  ')=='a plain fact'
    assert extract_statement('ricorda che domani studio')=='domani studio'

def test_contradiction_calendar_aliases_and_boundaries():
    from app.services.contradictions import is_possible_contradiction
    for first, second in (("lunedì", "lunedi"), ("lunedì", "monday"),
                          ("marzo", "march"), ("MARCH", "march")):
        assert not is_possible_contradiction("Consegna database prevista " + first,
                                             "Consegna database prevista " + second)[0]
    assert is_possible_contradiction("Consegna database prevista monday",
                                      "Consegna database prevista tuesday")[0]
    assert is_possible_contradiction("Consegna database prevista aprile",
                                      "Consegna database prevista march")[0]
    assert not is_possible_contradiction("Consegna database prevista marciano",
                                         "Consegna database prevista aprile")[0]


def test_contradiction_equivalent_decimal_values():
    from app.services.contradictions import is_possible_contradiction
    for first, second in (("1.0", "1"), ("1,50", "1.5"), ("01", "1")):
        assert not is_possible_contradiction("Budget progetto database " + first,
                                             "Budget progetto database " + second)[0]
    assert is_possible_contradiction("Budget progetto database 1.5",
                                      "Budget progetto database 2.5")[0]

def test_reminder_is_saved_not_actioned():
    assert classify("Ricordami di comprare il latte") == "new_info"
    assert classify("Ricorda di inviare il report") == "new_info"
    assert classify("Non dimenticare di pagare la bolletta") == "new_info"


def test_extract_statement_ricordami():
    assert extract_statement("Ricordami di chiamare mamma") == "chiamare mamma"
    assert extract_statement("Non dimenticare: il treno parte alle 8") == "il treno parte alle 8"
