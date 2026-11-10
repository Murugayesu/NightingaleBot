import math

from nursing_bot.api.schemas import FilterPayload
from nursing_bot.models.litellm_client import fit_vector
from nursing_bot.retrieval.fusion import reciprocal_rank_fusion
from nursing_bot.retrieval.gate import RelevanceGate
from nursing_bot.retrieval.metadata import build_filter_sql, normalize_filter_dict
from nursing_bot.retrieval.sparse import _any_word_query


def test_api_filter_payload_does_not_add_empty_extra_clause():
    """Regression (quality report H1): an empty `extra` used to filter out every chunk."""
    filters = FilterPayload(doc_type="syllabus").model_dump(exclude_none=True)
    params: dict = {}
    sql = build_filter_sql(filters, params)
    assert "extra" not in str(params)
    assert sql == "c.status = :chunk_status AND (LOWER(d.doc_type) = :f_0_0)"
    assert params["f_0_0"] == "syllabus"


def test_extra_filters_are_flattened():
    assert normalize_filter_dict(
        {"doc_type": "faq", "extra": {"year": 2024}, "district": None}
    ) == {
        "doc_type": "faq",
        "year": 2024,
    }


def test_empty_values_are_dropped():
    assert normalize_filter_dict(
        {"a": "", "b": [], "c": [None, ""], "d": ["x", None]}
    ) == {"d": ["x"]}


def test_multiple_values_are_ored_and_metadata_keys_are_bound():
    params: dict = {}
    sql = build_filter_sql(
        {"district": ["Chennai", "Madurai"], "doc_type": "x"}, params
    )
    assert (
        "(c.metadata @> CAST(:f_0_0 AS jsonb) OR c.metadata @> CAST(:f_0_1 AS jsonb))"
        in sql
    )
    assert params["f_0_0"] == '{"district": "Chennai"}'


def test_unknown_filter_key_never_reaches_sql_text():
    params: dict = {}
    sql = build_filter_sql({"x'; DROP TABLE chunks; --": "v"}, params)
    assert "DROP" not in sql and "DROP" in params["f_0_0"]


def test_rrf_rewards_agreement():
    scores = reciprocal_rank_fusion({"a": 1, "b": 2}, {"b": 1, "c": 2}, k=60)
    assert scores["b"] > scores["a"] > scores["c"]
    assert math.isclose(scores["a"], 1 / 61)


def test_gate_uses_similarity_and_rerank_thresholds():
    gate = RelevanceGate(similarity_threshold=0.3, rerank_threshold=0.5)
    assert gate.evaluate(0.45).passed
    assert not gate.evaluate(0.2).passed
    assert gate.evaluate(0.1, top_rerank=0.8).signal == "rerank"
    assert not gate.evaluate(0.9, top_rerank=0.2).passed
    none = gate.evaluate(None)
    assert not none.passed and none.signal == "none"


def test_any_word_query_drops_operators_and_punctuation():
    assert _any_word_query("What is the fee - and NOT the renewal?") == "fee or renewal"
    assert _any_word_query("a b") == ""


def test_fit_vector_preserves_cosine_when_padding():
    a, b = [0.6, 0.8], [0.8, 0.6]
    pa, pb = fit_vector(a, 5), fit_vector(b, 5)
    assert len(pa) == 5 and pa[2:] == [0.0, 0.0, 0.0]
    assert math.isclose(
        sum(x * y for x, y in zip(a, b)), sum(x * y for x, y in zip(pa, pb))
    )


def test_fit_vector_truncates_and_renormalises():
    out = fit_vector([3.0, 4.0, 12.0], 2)
    assert math.isclose(math.hypot(*out), 1.0)
