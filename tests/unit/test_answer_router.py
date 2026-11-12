"""The answer router, the official-facts check and the general-answer paths."""

import json

import pytest

from nursing_bot.config import get_settings
from nursing_bot.generation.official_facts import check_general_answer
from nursing_bot.generation.pipeline import answer_label
from nursing_bot.generation.router import (
    AnswerRouter,
    Route,
    _parse_category,
    classify_by_rules,
)
from tests.fakes import FakeLLMClient, FakeSession
from tests.unit.test_generation_pipeline import _pipeline

GENERAL_TEXT = "Hand hygiene means cleaning hands with soap or alcohol rub."


def _general_llm(general_answer: str = GENERAL_TEXT, category: str = "general_nursing"):
    def answer(prompt: str, system: str | None) -> str:
        system = system or ""
        if system.startswith("You route questions"):
            return json.dumps({"category": category})
        if system.startswith("You are an educational nursing assistant"):
            return general_answer
        return "The information is not available in the records."

    return FakeLLMClient(answer)


# ------------------------------------------------------------------ rules
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is the registration fee?", Route.OFFICIAL),
        ("When is the last date to renew my registration?", Route.OFFICIAL),
        ("What is the phone number of TNNMC?", Route.OFFICIAL),
        ("How many seats are there in B.Sc nursing?", Route.OFFICIAL),
        ("What is the stipend for interns?", Route.OFFICIAL),
        ("I have chest pain, what should I do?", Route.MEDICAL),
        ("Can I take paracetamol for my fever?", Route.MEDICAL),
        ("My child has a fever and a rash", Route.MEDICAL),
        ("I want to kill myself", Route.MEDICAL),
        # Everyday nursing vocabulary must reach the model, not a hard rule.
        ("What does a charge nurse do?", None),
        ("How do you calculate the due date of delivery?", None),
        ("What is the admission procedure for a patient in a ward?", None),
        ("Explain the intake output chart", None),
        ("I have an exam on infection control, any tips?", None),
        ("How should I calculate drug doses for exams?", None),
        ("What are the 5 rights of medication administration?", None),
    ],
)
def test_rules_route_only_what_they_are_sure_about(question, expected):
    assert classify_by_rules(question) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"category": "general_nursing"}', Route.GENERAL),
        ('Sure! {"category": "out_of_scope"}', Route.OUT_OF_SCOPE),
        ("personal_medical", Route.MEDICAL),
        ('"official_specific"', Route.OFFICIAL),
        ("I think it is general", None),
        ('{"category": 3}', None),
        # Found by the live evaluation: a reasoning model thinks aloud first,
        # quoting the instructions' example, and answers at the end.
        (
            (
                'We need to classify. The format is {"category": "<one of the four>"}. '
                "Aseptic technique is textbook knowledge, not an official fact, so not "
                '"official_specific".\n{"category": "general_nursing"}'
            ),
            Route.GENERAL,
        ),
        (
            (
                'Maybe {"category": "official_specific"}? No: it is about cooking.\n'
                '{"category": "out_of_scope"}'
            ),
            Route.OUT_OF_SCOPE,
        ),
        ('We need to classify. The question: "What is aseptic technique?" That', None),
    ],
)
def test_classifier_reply_parsing(raw, expected):
    assert _parse_category(raw) == expected


async def test_router_asks_the_model_when_no_rule_applies():
    llm = FakeLLMClient('{"category": "general_nursing"}')
    decision = await AnswerRouter(llm).route("What is hand hygiene?")
    assert decision.route == Route.GENERAL and decision.method == "llm"
    assert decision.tokens_used > 0


async def test_router_declines_when_the_model_fails_or_rambles():
    class Broken(FakeLLMClient):
        async def acomplete_with_usage(self, *args, **kwargs):
            raise RuntimeError("provider down")

    decision = await AnswerRouter(Broken()).route("What is hand hygiene?")
    assert (decision.route, decision.method) == (Route.OFFICIAL, "fallback")
    decision = await AnswerRouter(FakeLLMClient("no idea")).route("What is X?")
    assert (decision.route, decision.method) == (Route.OFFICIAL, "fallback")


# ----------------------------------------------------- official-facts check
@pytest.mark.parametrize(
    "text",
    [
        "The fee is Rs. 365.",
        "It costs ₹500.",
        "Pay 250 rupees at the counter.",
        "Apply before 15 March 2027.",
        "The deadline is 31/12/2026.",
        "Call 044-28524366.",
        "Ring 9876543210 for help.",
        "Write to registrar@example.org.",
        "See www.example.gov.in for details.",
        "TNNMC requires renewal every five years.",
        "The council charges a late fee.",
    ],
)
def test_official_looking_facts_are_caught(text):
    assert not check_general_answer(text).passed


@pytest.mark.parametrize(
    "text",
    [
        "Normal adult blood pressure is around 120/80 mmHg.",
        "Turn a bedridden patient every 2 hours to prevent pressure ulcers.",
        "Florence Nightingale founded modern nursing in the 1850s.",
        "The five rights are right patient, drug, dose, route and time.",
        "Body temperature is normally about 37 °C.",
    ],
)
def test_ordinary_clinical_numbers_pass(text):
    assert check_general_answer(text).passed


# ------------------------------------------------------ pipeline behaviour
async def test_general_question_gets_a_labelled_general_answer():
    llm = _general_llm()
    pipeline, _ = _pipeline(llm, similarity=0.05)
    response = await pipeline.aquery(
        session=FakeSession(), query="What is hand hygiene?"
    )  # type: ignore[arg-type]
    assert response.answer_mode == "general" and response.answer == GENERAL_TEXT
    assert response.citations == [] and response.is_grounded is False
    assert "not from official TNNMC records" in response.label
    assert llm.prompts[-1][1] == get_settings().general_system_prompt
    assert "Question: What is hand hygiene?" in llm.prompts[-1][0]


async def test_general_answer_with_an_official_fact_is_withdrawn():
    llm = _general_llm("Hand hygiene is required; the TNNMC fee is Rs. 365.")
    pipeline, _ = _pipeline(llm, similarity=0.05)
    response = await pipeline.aquery(
        session=FakeSession(), query="What is hand hygiene?"
    )  # type: ignore[arg-type]
    assert response.answer_mode == "declined"
    assert response.answer == get_settings().abstention_message
    assert "money amount" in (response.abstention_reason or "")


async def test_kill_switch_returns_to_documents_only(settings_override):
    settings_override(GENERAL_ANSWERS_ENABLED="false")
    llm = _general_llm()
    pipeline, _ = _pipeline(llm, similarity=0.05)
    response = await pipeline.aquery(
        session=FakeSession(), query="What is hand hygiene?"
    )  # type: ignore[arg-type]
    assert response.answer_mode == "declined"
    assert all(
        system != get_settings().general_system_prompt for _, system in llm.prompts
    )


async def test_personal_medical_question_never_reaches_retrieval():
    llm = _general_llm()
    pipeline, retriever = _pipeline(llm)
    response = await pipeline.aquery(
        session=FakeSession(), query="I have severe chest pain, should I take aspirin?"
    )  # type: ignore[arg-type]
    assert response.answer_mode == "refused"
    assert response.answer == get_settings().safety_message
    assert retriever.queries == [] and llm.prompts == []


async def test_out_of_scope_question_gets_the_scope_message():
    llm = _general_llm(category="out_of_scope")
    pipeline, _ = _pipeline(llm, similarity=0.05)
    response = await pipeline.aquery(
        session=FakeSession(), query="Best biryani in Madurai?"
    )  # type: ignore[arg-type]
    assert response.answer_mode == "out_of_scope"
    assert response.answer == get_settings().out_of_scope_message


async def test_model_says_classified_medical_is_refused():
    llm = _general_llm(category="personal_medical")
    pipeline, _ = _pipeline(llm, similarity=0.05)
    response = await pipeline.aquery(
        session=FakeSession(), query="Is this lump serious?"
    )  # type: ignore[arg-type]
    assert response.answer_mode == "refused"


async def test_unsupported_grounded_answer_falls_back_to_general_when_streaming():
    """Related documents but no supported answer: reset, then a general answer."""
    llm = _general_llm()
    pipeline, _ = _pipeline(llm)  # the gate passes; the model cannot cite
    events = [
        e
        async for e in pipeline.astream_query(
            session=FakeSession(), query="What is hand hygiene?"
        )
    ]  # type: ignore[arg-type]
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["mode", "citations"]
    reset = kinds.index("reset")
    assert events[reset + 1] == {
        "type": "mode",
        "answer_mode": "general",
        "label": answer_label("general"),
    }
    end = events[-1]
    assert end["answer_mode"] == "general" and end["final_answer"] == GENERAL_TEXT
    assert end["citations"] == [] and end["gate_passed"] is True


async def test_every_mode_has_a_label():
    for mode in ("grounded", "general", "declined", "refused", "out_of_scope"):
        assert answer_label(mode)
