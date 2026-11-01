import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nursing_bot.evaluation.golden_set import GoldenTestCase
from nursing_bot.evaluation.runner import EvaluationRunner
from nursing_bot.generation.pipeline import RAGResponse
from nursing_bot.observability.phoenix import PIIRedactionSpanProcessor


def test_pii_redaction_span_processor():
    processor = PIIRedactionSpanProcessor()
    mock_span = MagicMock()
    mock_span._attributes = {
        "input.value": "Contact test user at alice@example.com or 555-432-1098",
        "llm.prompts": "User phone: 555-432-1098",
        "other.key": "Unmodified text",
    }

    processor.on_end(mock_span)

    assert "alice@example.com" not in mock_span._attributes["input.value"]
    assert "[EMAIL_REDACTED]" in mock_span._attributes["input.value"]
    assert "555-432-1098" not in mock_span._attributes["input.value"]
    assert "[PHONE_REDACTED]" in mock_span._attributes["input.value"]
    assert mock_span._attributes["other.key"] == "Unmodified text"


@pytest.mark.asyncio
async def test_evaluation_runner_metrics():
    mock_pipeline = AsyncMock()
    runner = EvaluationRunner(mock_pipeline)
    test_cases = [
        GoldenTestCase(
            id="tc-1",
            category="general",
            question="What guidelines apply?",
            expected_answer_keywords=["standard", "2026"],
            should_abstain=False,
        ),
        GoldenTestCase(
            id="tc-2",
            category="unknown",
            question="What is the secret key?",
            expected_answer_keywords=[],
            should_abstain=True,
        ),
    ]

    session = AsyncMock()
    mock_pipeline.aquery.side_effect = [
        RAGResponse(
            answer="The system operates under standard 2026 guidelines [Source 1].",
            citations=[{"source_index": 1}],
            is_grounded=True,
            gate_passed=True,
        ),
        RAGResponse(
            answer="I could not find sufficient information.",
            citations=[],
            is_grounded=False,
            gate_passed=False,
        ),
    ]

    report = await runner.run(session, test_cases)
    assert report.total_cases == 2
    assert report.passed_cases == 2
    assert report.accuracy_rate == 1.0
    assert report.abstention_accuracy == 1.0


@pytest.mark.asyncio
async def test_worker_ingest_task():
    from nursing_bot.workers.tasks import ingest_document_task

    mock_doc = MagicMock()
    mock_session = AsyncMock()
    mock_session.get.return_value = mock_doc

    with (
        patch("nursing_bot.workers.tasks.get_session") as mock_get_session,
        patch("nursing_bot.workers.tasks.IngestionPipeline") as mock_pipeline_cls,
    ):
        mock_get_session.return_value.__aenter__.return_value = mock_session
        mock_pipeline_instance = AsyncMock()
        mock_pipeline_cls.return_value = mock_pipeline_instance

        doc_id = str(uuid.uuid4())
        await ingest_document_task(doc_id)

        mock_pipeline_instance.ingest_document.assert_called_once()
