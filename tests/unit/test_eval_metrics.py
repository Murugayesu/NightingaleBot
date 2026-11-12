from pathlib import Path

from nursing_bot.evaluation.dataset import load_cases
from nursing_bot.evaluation.judge import LLMJudge
from nursing_bot.evaluation.metrics import (
    fact_recall,
    first_hit_rank,
    same_document,
    suggest_threshold,
)
from tests.fakes import FakeLLMClient

EVALSET = Path(__file__).resolve().parents[2] / "eval" / "evalset.csv"


def test_real_evalset_loads():
    cases = load_cases(EVALSET)
    assert len(cases) == 77
    behaviours = {c.expected_behavior for c in cases}
    assert behaviours == {"answer", "abstain", "refuse", "general", "out_of_scope"}
    multi = [c for c in cases if len(c.source_files) > 1]
    assert multi, "cases citing two documents are split on '+'"
    assert all(c.expected_facts for c in cases if c.should_answer)


def test_fact_recall_ignores_case_and_punctuation():
    assert (
        fact_recall(
            ["Rs. 365", "Indian Bank"], "The fee is rs 365, payable at INDIAN BANK."
        )
        == 1.0
    )
    assert fact_recall(["Rs. 365", "Rs. 250"], "The fee is Rs. 365.") == 0.5
    assert fact_recall([], "anything") == 1.0


def test_source_matching_ignores_extension():
    assert same_document("TNNMC CONTACT DETAILS.docx", "tnnmc contact details.pdf")
    assert first_hit_rank(["b.pdf"], ["a.pdf", "B.pdf", "b.pdf"]) == 2
    assert first_hit_rank(["z.pdf"], ["a.pdf"]) is None


def test_threshold_suggestion_separates_classes():
    best = suggest_threshold(should_pass=[0.6, 0.55, 0.7], should_block=[0.2, 0.3])
    assert best is not None
    assert 0.3 < best["threshold"] <= 0.55 and best["balanced_accuracy"] == 1.0
    assert suggest_threshold([0.5], []) is None


async def test_judge_scores_faithfulness_and_relevancy():
    def answer(prompt: str, _system: str | None) -> str:
        if "factual claims" in prompt:
            return '{"claims": [{"claim": "fee 365", "supported": true}, {"claim": "x", "supported": false}]}'
        return '{"questions": ["What is the registration fee?", "registration fee amount", "fee"]}'

    judge = LLMJudge(FakeLLMClient(answer))
    scores = await judge.score(
        "What is the registration fee?", "The fee is Rs. 365 [Source 1].", "sources"
    )
    assert scores.faithfulness == 0.5
    assert scores.answer_relevancy is not None and scores.answer_relevancy > 0.5


async def test_judge_handles_garbage_output():
    judge = LLMJudge(FakeLLMClient("not json"))
    scores = await judge.score("q", "a", "s")
    assert scores.faithfulness is None and scores.answer_relevancy is None


def test_fact_matching_is_whole_word():
    """Regression (independent review): substring matching inflated the pass rate."""
    assert fact_recall(["365"], "The fee is Rs. 3650.") == 0.0
    assert fact_recall(["NM"], "Contact TNNMC.") == 0.0
    assert fact_recall(["no"], "It is not required.") == 0.0
    assert fact_recall(["NM", "no"], "NM course: no, it is not.") == 1.0


async def test_judge_tolerates_null_fields():
    """Regression (final review M4): {"questions": null} raised TypeError."""
    judge = LLMJudge(FakeLLMClient('{"claims": null, "questions": null}'))
    scores = await judge.score("q", "a", "s")
    assert scores.faithfulness is None and scores.answer_relevancy is None
