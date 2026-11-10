"""OpenTelemetry tracing: app → OTel Collector → Arize Phoenix.

What gets traced:
  * every HTTP request          (FastAPI instrumentation, API only)
  * every SQL statement         (SQLAlchemy instrumentation)
  * every LLM / embedding call  (OpenInference LiteLLM instrumentation)
  * RAG stages                  (manual spans via `tracer` in retrieval/generation)

PII is redacted from span attributes before export.
"""

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider

from nursing_bot import privacy
from nursing_bot.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Manual spans use this tracer. Without a configured provider it is a no-op,
# so instrumented code costs nothing when tracing is disabled (e.g. in tests).
tracer = trace.get_tracer("nursing_bot")

_provider: TracerProvider | None = None


class PIIRedactionSpanProcessor(SpanProcessor):
    """Redacts PII from text attributes when a span ends, before exporters see it.

    Must be registered BEFORE the exporting processor: processors run in
    registration order. The OTel SDK exposes no public API to rewrite the
    attributes of an ended span, so this replaces the span's internal
    `_attributes` mapping. The unit tests pin this behaviour, so an SDK
    upgrade that changes it will fail loudly instead of leaking data.
    """

    TEXT_KEY_MARKERS = ("input", "output", "prompt", "message", "query", "content")

    @classmethod
    def redact_value(cls, val: Any) -> Any:
        if isinstance(val, str):
            return privacy.redact(val, style="trace")
        if isinstance(val, (list, tuple)):
            return type(val)(cls.redact_value(item) for item in val)
        return val

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        attributes = getattr(span, "_attributes", None)
        if not attributes:
            return
        redacted = dict(attributes)
        for key, val in redacted.items():
            if any(marker in key for marker in self.TEXT_KEY_MARKERS):
                redacted[key] = self.redact_value(val)
        span._attributes = redacted

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def setup_tracing(
    service_name: str, settings: Settings | None = None
) -> TracerProvider | None:
    """Configure the global tracer provider once per process. Safe to call twice."""
    global _provider
    s = settings or get_settings()
    if not s.PHOENIX_ENABLED:
        logger.info("Tracing disabled (PHOENIX_ENABLED=false).")
        return None
    if _provider is not None:
        return _provider

    try:
        from openinference.instrumentation.litellm import LiteLLMInstrumentor
        from openinference.semconv.resource import ResourceAttributes
        from opentelemetry.sdk.resources import Resource
        from phoenix.otel import BatchSpanProcessor
        from phoenix.otel import TracerProvider as PhoenixTracerProvider

        provider = PhoenixTracerProvider(
            resource=Resource.create(
                {
                    ResourceAttributes.PROJECT_NAME: s.PHOENIX_PROJECT_NAME,
                    "service.name": service_name,
                }
            ),
            verbose=False,
        )
        provider.add_span_processor(PIIRedactionSpanProcessor())
        provider.add_span_processor(
            BatchSpanProcessor(
                endpoint=s.PHOENIX_COLLECTOR_ENDPOINT, protocol="http/protobuf"
            )
        )
        trace.set_tracer_provider(provider)
        LiteLLMInstrumentor().instrument(tracer_provider=provider)

        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from nursing_bot.storage.database import get_engine

        SQLAlchemyInstrumentor().instrument(
            engine=get_engine().sync_engine, tracer_provider=provider
        )
    except Exception as exc:  # noqa: BLE001 - tracing must never take the app down
        logger.warning("Tracing setup failed, continuing without it: %s", exc)
        return None

    _provider = provider
    logger.info(
        "Tracing enabled: service=%s project=%s endpoint=%s",
        service_name,
        s.PHOENIX_PROJECT_NAME,
        s.PHOENIX_COLLECTOR_ENDPOINT,
    )
    return provider


def instrument_fastapi(app: Any) -> None:
    """Add per-request spans to the FastAPI app (only if tracing is active)."""
    if _provider is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=_provider, excluded_urls="health,ui/.*"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("FastAPI instrumentation failed: %s", exc)
