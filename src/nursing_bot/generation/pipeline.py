"""The RAG answer pipeline.

    guard ─► condense ─► retrieve ─► gate ─► assemble ─► generate ─► verify
     │                                │                                  │
     └── blocked: refusal             └── fails: abstention              └── fails: abstention

`aquery` returns one finished answer; `astream_query` yields events for SSE.
Both share `_prepare()`, so the safety steps can never drift apart. Streaming
sends tokens as they arrive, then an `end` event whose `final_answer` is the
verified text. Clients must display `final_answer`, so an answer that fails
verification is retracted on screen.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.generation.assembler import AssembledContext, ContextAssembler
from nursing_bot.generation.condenser import HistoryCondenser
from nursing_bot.generation.guardrails import InputGuard, expand_synonyms
from nursing_bot.generation.verifier import CitationCoverageVerifier
from nursing_bot.models.litellm_client import LiteLLMClient, StreamUsage
from nursing_bot.observability import tracer
from nursing_bot.retrieval.gate import RelevanceGate
from nursing_bot.retrieval.retriever import PostgresHybridRetriever


@dataclass
class RAGResponse:
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    is_grounded: bool = True
    gate_passed: bool = True
    top_score: float = 0.0
    applied_filters: dict[str, Any] = field(default_factory=dict)
    abstention_reason: str | None = None
    standalone_query: str | None = None
    tokens_used: int = 0
    context_text: str = (
        ""  # the sources shown to the LLM (used by the evaluation judge)
    )


@dataclass
class UsageMeter:
    """LLM tokens spent so far by one request.

    Kept current during streaming, so the caller can charge the session budget
    even when the client disconnects before the answer finishes.
    """

    tokens: int = 0


@dataclass
class _Prepared:
    """Everything decided before generation. `early` is set when we must not generate."""

    filters: dict[str, Any]
    tokens_used: int = 0
    standalone_query: str = ""
    assembled: AssembledContext | None = None
    top_score: float = 0.0
    early: RAGResponse | None = None


def build_user_prompt(question: str, context_text: str) -> str:
    return (
        f"Sources:\n\n{context_text}\n\nQuestion: {question}\n\nAnswer (cite sources):"
    )


class GenerationPipeline:
    def __init__(
        self,
        retriever: PostgresHybridRetriever,
        llm_client: LiteLLMClient,
        gate: RelevanceGate | None = None,
        condenser: HistoryCondenser | None = None,
        default_top_k: int = 8,
    ):
        settings = get_settings()
        self.retriever = retriever
        self.llm_client = llm_client
        self.gate = gate or RelevanceGate(
            settings.GATE_THRESHOLD_SIMILARITY, settings.GATE_THRESHOLD_RERANK
        )
        self.condenser = condenser or HistoryCondenser(
            llm_client, settings.MAX_RECENT_CONTEXT_TURNS
        )
        self.default_top_k = default_top_k

    async def _prepare(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None,
        filters: dict[str, Any] | None,
        top_k: int | None,
    ) -> _Prepared:
        settings = get_settings()
        prepared = _Prepared(filters=filters or {})

        guard = InputGuard.check_and_sanitize(query)
        if not guard.is_safe:
            reason = guard.rejection_reason or "Request blocked."
            prepared.early = RAGResponse(
                answer=reason,
                is_grounded=False,
                gate_passed=False,
                abstention_reason=reason,
            )
            return prepared

        question = expand_synonyms(guard.sanitized_query, settings.domain_synonyms)
        with tracer.start_as_current_span("rag.condense"):
            standalone, prepared.tokens_used = await self.condenser.condense(
                question, InputGuard.sanitize_history(history)
            )
        prepared.standalone_query = standalone

        retrieval = await self.retriever.aretrieve(
            standalone, session, filters=filters, top_k=top_k or self.default_top_k
        )
        # Retrieval is read-only. Ending the transaction returns the connection
        # to the pool for the (slow) LLM call instead of holding it idle.
        await session.commit()
        decision = self.gate.evaluate(retrieval.top_similarity, retrieval.top_rerank)
        prepared.top_score = decision.top_score
        if not decision.passed or not retrieval.documents:
            prepared.early = RAGResponse(
                answer=settings.abstention_message,
                is_grounded=False,
                gate_passed=False,
                top_score=decision.top_score,
                applied_filters=prepared.filters,
                abstention_reason=decision.reason,
                standalone_query=standalone,
                tokens_used=prepared.tokens_used,
            )
            return prepared

        prepared.assembled = ContextAssembler.assemble(retrieval.documents)
        return prepared

    def _finish(self, prepared: _Prepared, raw_answer: str, tokens: int) -> RAGResponse:
        settings = get_settings()
        assert prepared.assembled is not None
        verification = CitationCoverageVerifier.verify(
            answer=raw_answer,
            citations=prepared.assembled.citations,
            abstention_message=settings.abstention_message,
            min_coverage=settings.CITATION_MIN_COVERAGE,
        )
        return RAGResponse(
            answer=verification.final_answer,
            citations=prepared.assembled.citations if verification.is_grounded else [],
            is_grounded=verification.is_grounded,
            gate_passed=True,
            top_score=prepared.top_score,
            applied_filters=prepared.filters,
            abstention_reason=None if verification.is_grounded else verification.notes,
            standalone_query=prepared.standalone_query,
            tokens_used=prepared.tokens_used + tokens,
            context_text=prepared.assembled.context_text,
        )

    async def aquery(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
        meter: UsageMeter | None = None,
    ) -> RAGResponse:
        meter = meter if meter is not None else UsageMeter()
        with tracer.start_as_current_span("rag.query"):
            prepared = await self._prepare(session, query, history, filters, top_k)
            meter.tokens = prepared.tokens_used
            if prepared.early is not None:
                return prepared.early
            assert prepared.assembled is not None
            with tracer.start_as_current_span("rag.generate"):
                raw, tokens = await self.llm_client.acomplete_with_usage(
                    prompt=build_user_prompt(
                        prepared.standalone_query, prepared.assembled.context_text
                    ),
                    system_prompt=get_settings().system_prompt,
                    temperature=0.0,
                )
            response = self._finish(prepared, raw, tokens)
            meter.tokens = response.tokens_used
            return response

    async def astream_query(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
        meter: UsageMeter | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield {"type": "citations"|"token"|"end", ...} events.

        `meter` is updated as tokens stream (estimated), then set to the
        provider's exact count at the end.
        """
        meter = meter if meter is not None else UsageMeter()
        prepared = await self._prepare(session, query, history, filters, top_k)
        meter.tokens = prepared.tokens_used
        if prepared.early is not None:
            yield {"type": "token", "content": prepared.early.answer}
            yield _end_event(prepared.early)
            return
        assert prepared.assembled is not None

        yield {"type": "citations", "citations": prepared.assembled.citations}
        system_prompt = get_settings().system_prompt
        prompt = build_user_prompt(
            prepared.standalone_query, prepared.assembled.context_text
        )
        # The prompt is billed as soon as it is sent, whatever happens next.
        meter.tokens += self.llm_client.count_tokens(f"{system_prompt}\n{prompt}")
        usage = StreamUsage()
        pieces: list[str] = []
        with tracer.start_as_current_span("rag.generate"):
            async for piece in self.llm_client.astream(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.0,
                usage=usage,
            ):
                pieces.append(piece)
                meter.tokens += self.llm_client.count_tokens(piece)
                yield {"type": "token", "content": piece}

        response = self._finish(prepared, "".join(pieces), usage.total_tokens)
        meter.tokens = response.tokens_used
        yield _end_event(response)


def _end_event(response: RAGResponse) -> dict[str, Any]:
    return {
        "type": "end",
        "final_answer": response.answer,
        "citations": response.citations,
        "is_grounded": response.is_grounded,
        "gate_passed": response.gate_passed,
        "top_score": response.top_score,
        "applied_filters": response.applied_filters,
        "abstention_reason": response.abstention_reason,
        "tokens_used": response.tokens_used,
    }
