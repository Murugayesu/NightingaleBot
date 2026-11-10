import json
import logging

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nursing_bot.observability.logging import JsonFormatter, configure_logging
from nursing_bot.observability.phoenix import PIIRedactionSpanProcessor


def test_pii_is_redacted_before_export():
    """Pins the SDK behaviour PIIRedactionSpanProcessor relies on (mutating span._attributes)."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(PIIRedactionSpanProcessor())  # must come first
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("llm") as span:
        span.set_attribute("input.value", "email nurse@example.com phone 9876543210")
        span.set_attribute(
            "llm.input_messages.0.message.content", "aadhaar 2345 6789 0124"
        )
        span.set_attribute("retrieval.top_k", 8)
        span.set_attribute("http.route", "/api/v1/chat")

    attributes = exporter.get_finished_spans()[0].attributes
    assert attributes["input.value"] == "email [EMAIL_REDACTED] phone [PHONE_REDACTED]"
    assert attributes["llm.input_messages.0.message.content"] == "aadhaar [ID_REDACTED]"
    assert (
        attributes["retrieval.top_k"] == 8
        and attributes["http.route"] == "/api/v1/chat"
    )


def test_redact_value_handles_sequences():
    assert PIIRedactionSpanProcessor.redact_value(("a@b.co", 3)) == (
        "[EMAIL_REDACTED]",
        3,
    )


def test_json_log_format():
    record = logging.LogRecord(
        "nursing_bot.x", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello world" and payload["level"] == "INFO"


def test_configure_logging_is_idempotent():
    configure_logging("INFO", "json")
    configure_logging("WARNING", "text")
    ours = [
        h for h in logging.getLogger().handlers if getattr(h, "_nursing_bot", False)
    ]
    assert len(ours) == 1 and logging.getLogger().level == logging.WARNING
