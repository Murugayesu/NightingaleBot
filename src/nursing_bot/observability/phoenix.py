import logging
import re
from typing import Any

from openinference.semconv.resource import ResourceAttributes
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

from nursing_bot.config import get_settings

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)
_PHONE_PATTERN = re.compile(
    r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
_ID_PATTERN = re.compile(
    r"\b(?:\d{4}[-\s]?\d{4}[-\s]?\d{4}|\d{3}-\d{2}-\d{4})\b"
)


class PIIRedactionSpanProcessor(SpanProcessor):
    """OpenTelemetry SpanProcessor redacting sensitive PII before export to Arize Phoenix."""

    PII_KEYS = frozenset(
        {
            "input.value",
            "output.value",
            "llm.prompts",
            "llm.invocation_parameters",
        }
    )

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        if not hasattr(span, "_attributes") or span._attributes is None:
            return

        redacted_attributes = dict(span._attributes)
        for key, val in list(redacted_attributes.items()):
            if isinstance(val, str) and (
                key in self.PII_KEYS or "prompt" in key or "input" in key
            ):
                redacted = _PHONE_PATTERN.sub("[PHONE_REDACTED]", val)
                redacted = _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", redacted)
                redacted = _ID_PATTERN.sub("[ID_REDACTED]", redacted)
                redacted_attributes[key] = redacted

        span._attributes = redacted_attributes

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def setup_phoenix(settings: Any | None = None) -> TracerProvider | None:
    """Configures Phoenix OpenInference tracer provider if enabled."""
    s = settings or get_settings()
    if not s.PHOENIX_ENABLED:
        logger.info("Arize Phoenix observability disabled via config.")
        return None

    try:
        from openinference.instrumentation.litellm import LiteLLMInstrumentor
        from phoenix.otel import BatchSpanProcessor
        from phoenix.otel import TracerProvider as PhoenixTracerProvider

        tracer_provider = PhoenixTracerProvider(
            resource=Resource.create(
                {ResourceAttributes.PROJECT_NAME: s.PHOENIX_PROJECT_NAME}
            ),
            verbose=False,
        )
        tracer_provider.add_span_processor(PIIRedactionSpanProcessor())
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                endpoint=s.PHOENIX_COLLECTOR_ENDPOINT,
                protocol="http/protobuf",
            )
        )

        trace.set_tracer_provider(tracer_provider)
        LiteLLMInstrumentor().instrument(tracer_provider=tracer_provider)
        logger.info("Arize Phoenix instrumented for project '%s'.", s.PHOENIX_PROJECT_NAME)
        return tracer_provider
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to initialize Phoenix tracer: %s", exc)
        return None
