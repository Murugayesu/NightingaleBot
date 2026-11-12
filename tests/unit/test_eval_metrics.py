from pathlib import Path

import pytest

from nursing_bot.evaluation.dataset import load_cases
from nursing_bot.evaluation.judge import LLMJudge
from nursing_bot.evaluation.metrics import (
    fact_present,
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


@pytest.mark.parametrize(
    ("fact", "answer"),
    [
        # Found by the live evaluation: correct answers scored 0% because the
        # expected fact was worded differently.
        (
            "Online credit card debit card net banking",
            "You can pay online through credit card, debit card, or net banking.",
        ),
        ("Rs. 350 plus Rs. 15 bank charge", "The fee is Rs. 350 + Bank Charge Rs. 15."),
        ("91-44-4678 6539", "Call 044-4678 6539 for help."),
        ("3 MB each", "The maximum size for each document is 3 MB."),
        (
            "on or before 21-09-2026 upto 05.00 P.M.",
            "The last date is 21-09-2026 upto 05.00 P.M.",
        ),
        (
            "surrender parental Registration Certificate",
            "after surrendering your parental registration certificate",
        ),
        ("B.Sc 45% for OC and 40% for SC ST", "B.Sc: 45% for OC, 40% for SC/SCA/ST."),
        # Narrow no-break spaces and non-breaking hyphens, as models write them.
        ("45% aggregate marks", "at least **45 %** aggregate marks"),
        ("Higher-Secondary", "the Higher‑Secondary subjects"),
    ],
)
def test_reworded_facts_are_found(fact, answer):
    assert fact_present(fact, answer)


@pytest.mark.parametrize(
    ("fact", "answer"),
    [
        ("Rs. 350 plus Rs. 15 bank charge", "The fee is Rs. 350 + Bank Charge Rs. 50."),
        ("91-44-4678 6539", "Call 044-4678 6593 for help."),
        ("10am to 5pm", "Call the office for help."),
        ("paid at nearest Indian Bank", "The fee is Rs. 350 [Source 1]."),
        ("on or before 21-09-2026", "The last date is 21-10-2026."),
        ("B.Sc 45% for OC", "B.Sc: 40% for OC."),
    ],
)
def test_wrong_or_missing_facts_are_not_found(fact, answer):
    """Numbers must match exactly; most key words must be there."""
    assert not fact_present(fact, answer)


async def test_judge_tolerates_null_fields():
    """Regression (final review M4): {"questions": null} raised TypeError."""
    judge = LLMJudge(FakeLLMClient('{"claims": null, "questions": null}'))
    scores = await judge.score("q", "a", "s")
    assert scores.faithfulness is None and scores.answer_relevancy is None


async def test_eval_cases_retry_transient_provider_errors():
    """Found by the live evaluation: free-tier providers fail in bursts."""
    from nursing_bot.evaluation.runner import _with_retries

    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("502 upstream fetch failed")
        return "ok"

    assert await _with_retries("c-1", flaky) == "ok" and attempts == 3

    async def broken() -> str:
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="still down"):
        await _with_retries("c-2", broken)
