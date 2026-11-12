"""The answer pipeline: official documents first, labelled general knowledge second.

    guard ─► safety rules ─► condense ─► retrieve ─► gate ─► grounded answer ─► verify
      │           │                                   │                           │
      │           └ emergency / own health: REFUSED   │ fails                     │ fails
      └ blocked: REFUSED                              ▼                           ▼
                                                  ROUTER (rules + one small LLM call)
                                                   ├ official_specific → DECLINED
                                                   ├ personal_medical  → REFUSED (safety)
                                                   ├ out_of_scope      → OUT_OF_SCOPE
                                                   └ general_nursing   → GENERAL answer
                                                                          └ official-facts
                                                                            check fails → DECLINED

`aquery` returns one finished answer; `astream_query` yields events for SSE.
Both share `_prepare()` and `_fallback()`, so the safety steps cannot drift
apart. Streaming sends tokens as they arrive. If a streamed attempt is
withdrawn (a grounded answer fails verification) a `reset` event tells the
client to clear it. The `end` event's `final_answer` is always the text to
keep, so an answer that fails a check is retracted on screen.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.generation.assembler import AssembledContext, ContextAssembler
from nursing_bot.generation.condenser import HistoryCondenser
from nursing_bot.generation.guardrails import InputGuard, expand_synonyms
from nursing_bot.generation.official_facts import check_general_answer
from nursing_bot.generation.router import AnswerRouter, Route, classify_by_rules
from nursing_bot.generation.verifier import CitationCoverageVerifier
from nursing_bot.models.litellm_client import LiteLLMClient, StreamUsage
from nursing_bot.observability import tracer
from nursing_bot.retrieval.gate import RelevanceGate
from nursing_bot.retrieval.retriever import PostgresHybridRetriever
from nursing_bot.storage.models import AnswerMode

_LABELS = {
    AnswerMode.GROUNDED: "From official records",
    AnswerMode.DECLINED: "Not in the official records",
    AnswerMode.REFUSED: "Can't help with this",
    AnswerMode.OUT_OF_SCOPE: "Outside what I can help with",
}


def answer_label(mode: str) -> str:
    """The badge text the client shows for an answer mode."""
    if mode == AnswerMode.GENERAL:
        return get_settings().general_answer_label
    return _LABELS.get(mode, "")


@dataclass
class RAGResponse:
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    answer_mode: str = AnswerMode.GROUNDED
    is_grounded: bool = True
    gate_passed: bool = True
    top_score: float = 0.0
    applied_filters: dict[str, Any] = field(default_factory=dict)
    abstention_reason: str | None = None
    standalone_query: str | None = None
    route: str | None = None  # the router's category, when it was consulted
    tokens_used: int = 0
    context_text: str = (
        ""  # the sources shown to the LLM (used by the evaluation judge)
    )

    @property
    def label(self) -> str:
        return answer_label(self.answer_mode)


@dataclass
class UsageMeter:
    """LLM tokens spent so far by one request.

    Kept current during streaming, so the caller can charge the budget even
    when the client disconnects before the answer finishes.
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
    gate_passed: bool = False
    route: str | None = None
    general: bool = False  # answer from general knowledge (gate failed, router agreed)
    early: RAGResponse | None = None


def build_user_prompt(question: str, context_text: str) -> str:
    return (
        f"Sources:\n\n{context_text}\n\nQuestion: {question}\n\nAnswer (cite sources):"
    )


def build_general_prompt(question: str) -> str:
    return f"Question: {question}\n\nAnswer:"


class GenerationPipeline:
    def __init__(
        self,
        retriever: PostgresHybridRetriever,
        llm_client: LiteLLMClient,
        gate: RelevanceGate | None = None,
        condenser: HistoryCondenser | None = None,
        default_top_k: int = 8,
        router: AnswerRouter | None = None,
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
        self.router = router or AnswerRouter(llm_client)
        self.default_top_k = default_top_k

    # --------------------------------------------------------------- shared
    def _early(
        self,
        prepared: _Prepared,
        mode: str,
        answer: str,
        reason: str | None = None,
    ) -> RAGResponse:
        return RAGResponse(
            answer=answer,
            answer_mode=mode,
            is_grounded=False,
            gate_passed=prepared.gate_passed,
            top_score=prepared.top_score,
            applied_filters=prepared.filters,
            abstention_reason=reason,
            standalone_query=prepared.standalone_query or None,
            route=prepared.route,
            tokens_used=prepared.tokens_used,
        )

    async def _prepare(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None,
        filters: dict[str, Any] | None,
        top_k: int | None,
        summary: str | None = None,
    ) -> _Prepared:
        settings = get_settings()
        prepared = _Prepared(filters=filters or {})

        guard = InputGuard.check_and_sanitize(query)
        if not guard.is_safe:
            reason = guard.rejection_reason or "Request blocked."
            prepared.early = self._early(prepared, AnswerMode.REFUSED, reason, reason)
            return prepared

        question = expand_synonyms(guard.sanitized_query, settings.domain_synonyms)
        # Emergencies and personal medical questions never reach retrieval:
        # a document match must not turn them into an "official" answer.
        if classify_by_rules(question) == Route.MEDICAL:
            prepared.route = Route.MEDICAL.value
            prepared.standalone_query = question
            prepared.early = self._early(
                prepared,
                AnswerMode.REFUSED,
                settings.safety_message,
                "Personal medical question.",
            )
            return prepared

        with tracer.start_as_current_span("rag.condense"):
            standalone, prepared.tokens_used = await self.condenser.condense(
                question, InputGuard.sanitize_history(history), summary=summary
            )
        prepared.standalone_query = standalone

        retrieval = await self.retriever.aretrieve(
            standalone, session, filters=filters, top_k=top_k or self.default_top_k
        )
        # Retrieval is read-only. Ending the transaction returns the connection
        # to the pool for the (slow) LLM calls instead of holding it idle.
        await session.commit()
        decision = self.gate.evaluate(retrieval.top_similarity, retrieval.top_rerank)
        prepared.top_score = decision.top_score
        if decision.passed and retrieval.documents:
            prepared.gate_passed = True
            prepared.assembled = ContextAssembler.assemble(retrieval.documents)
            return prepared

        prepared.early = await self._fallback(prepared, decision.reason)
        return prepared

    async def _fallback(
        self, prepared: _Prepared, reason: str | None
    ) -> RAGResponse | None:
        """The documents do not answer the question: decide what to do instead.

        Returns the final response, or None when a general answer should be
        generated (prepared.general is then set).
        """
        settings = get_settings()
        with tracer.start_as_current_span("rag.route"):
            decision = await self.router.route(prepared.standalone_query)
        prepared.tokens_used += decision.tokens_used
        prepared.route = decision.route.value
        if decision.route == Route.MEDICAL:
            return self._early(
                prepared, AnswerMode.REFUSED, settings.safety_message, reason
            )
        if decision.route == Route.OUT_OF_SCOPE:
            return self._early(
                prepared, AnswerMode.OUT_OF_SCOPE, settings.out_of_scope_message, reason
            )
        if decision.route == Route.GENERAL and settings.GENERAL_ANSWERS_ENABLED:
            prepared.general = True
            return None
        return self._early(
            prepared, AnswerMode.DECLINED, settings.abstention_message, reason
        )

    def _finish_grounded(
        self, prepared: _Prepared, raw_answer: str, tokens: int
    ) -> RAGResponse:
        settings = get_settings()
        assert prepared.assembled is not None
        verification = CitationCoverageVerifier.verify(
            answer=raw_answer,
            citations=prepared.assembled.citations,
            abstention_message=settings.abstention_message,
            min_coverage=settings.CITATION_MIN_COVERAGE,
        )
        prepared.tokens_used += tokens
        return RAGResponse(
            answer=verification.final_answer,
            citations=prepared.assembled.citations if verification.is_grounded else [],
            answer_mode=AnswerMode.GROUNDED
            if verification.is_grounded
            else AnswerMode.DECLINED,
            is_grounded=verification.is_grounded,
            gate_passed=True,
            top_score=prepared.top_score,
            applied_filters=prepared.filters,
            abstention_reason=None if verification.is_grounded else verification.notes,
            standalone_query=prepared.standalone_query,
            tokens_used=prepared.tokens_used,
            context_text=prepared.assembled.context_text,
        )

    def _finish_general(
        self, prepared: _Prepared, raw_answer: str, tokens: int
    ) -> RAGResponse:
        settings = get_settings()
        prepared.tokens_used += tokens
        answer = raw_answer.strip()
        facts = check_general_answer(answer)
        if not answer or not facts.passed:
            return self._early(
                prepared,
                AnswerMode.DECLINED,
                settings.abstention_message,
                facts.notes if answer else "Empty general answer.",
            )
        return RAGResponse(
            answer=answer,
            answer_mode=AnswerMode.GENERAL,
            is_grounded=False,
            gate_passed=prepared.gate_passed,
            top_score=prepared.top_score,
            applied_filters=prepared.filters,
            standalone_query=prepared.standalone_query,
            route=prepared.route,
            tokens_used=prepared.tokens_used,
        )

    # ------------------------------------------------------------ one-shot
    async def aquery(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
        meter: UsageMeter | None = None,
        summary: str | None = None,
    ) -> RAGResponse:
        meter = meter if meter is not None else UsageMeter()
        settings = get_settings()
        with tracer.start_as_current_span("rag.query"):
            prepared = await self._prepare(
                session, query, history, filters, top_k, summary
            )
            meter.tokens = prepared.tokens_used
            if prepared.early is not None:
                return prepared.early

            if not prepared.general:
                assert prepared.assembled is not None
                with tracer.start_as_current_span("rag.generate"):
                    raw, tokens = await self.llm_client.acomplete_with_usage(
                        prompt=build_user_prompt(
                            prepared.standalone_query, prepared.assembled.context_text
                        ),
                        system_prompt=settings.system_prompt,
                        temperature=0.0,
                    )
                response = self._finish_grounded(prepared, raw, tokens)
                meter.tokens = prepared.tokens_used
                if response.is_grounded:
                    return response
                # Related documents, but no supported answer: route it.
                early = await self._fallback(prepared, response.abstention_reason)
                meter.tokens = prepared.tokens_used
                if early is not None:
                    early.context_text = response.context_text
                    return early

            with tracer.start_as_current_span("rag.generate_general"):
                raw, tokens = await self.llm_client.acomplete_with_usage(
                    prompt=build_general_prompt(prepared.standalone_query),
                    system_prompt=settings.general_system_prompt,
                    temperature=0.2,
                )
            response = self._finish_general(prepared, raw, tokens)
            meter.tokens = response.tokens_used
            return response

    # ------------------------------------------------------------ streaming
    async def _stream_generation(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        meter: UsageMeter,
        pieces: list[str],
        usage: StreamUsage,
    ) -> AsyncIterator[dict[str, Any]]:
        # The prompt is billed as soon as it is sent, whatever happens next.
        meter.tokens += self.llm_client.count_tokens(f"{system_prompt}\n{prompt}")
        async for piece in self.llm_client.astream(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            usage=usage,
        ):
            pieces.append(piece)
            meter.tokens += self.llm_client.count_tokens(piece)
            yield {"type": "token", "content": piece}

    async def astream_query(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
        meter: UsageMeter | None = None,
        summary: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield {"type": "mode"|"citations"|"token"|"reset"|"end", ...} events.

        `meter` is updated as tokens stream (estimated), then set to the
        provider's exact count when each generation finishes.
        """
        meter = meter if meter is not None else UsageMeter()
        settings = get_settings()
        prepared = await self._prepare(session, query, history, filters, top_k, summary)
        meter.tokens = prepared.tokens_used
        if prepared.early is not None:
            yield _mode_event(prepared.early.answer_mode)
            yield {"type": "token", "content": prepared.early.answer}
            yield _end_event(prepared.early)
            return

        if not prepared.general:
            assert prepared.assembled is not None
            yield _mode_event(AnswerMode.GROUNDED)
            yield {"type": "citations", "citations": prepared.assembled.citations}
            pieces: list[str] = []
            usage = StreamUsage()
            with tracer.start_as_current_span("rag.generate"):
                async for event in self._stream_generation(
                    build_user_prompt(
                        prepared.standalone_query, prepared.assembled.context_text
                    ),
                    settings.system_prompt,
                    0.0,
                    meter,
                    pieces,
                    usage,
                ):
                    yield event
            response = self._finish_grounded(
                prepared, "".join(pieces), usage.total_tokens
            )
            meter.tokens = prepared.tokens_used
            if response.is_grounded:
                yield _end_event(response)
                return
            early = await self._fallback(prepared, response.abstention_reason)
            meter.tokens = prepared.tokens_used
            yield {"type": "reset"}
            if early is not None:
                yield _mode_event(early.answer_mode)
                yield {"type": "token", "content": early.answer}
                yield _end_event(early)
                return

        yield _mode_event(AnswerMode.GENERAL)
        general_pieces: list[str] = []
        general_usage = StreamUsage()
        with tracer.start_as_current_span("rag.generate_general"):
            async for event in self._stream_generation(
                build_general_prompt(prepared.standalone_query),
                settings.general_system_prompt,
                0.2,
                meter,
                general_pieces,
                general_usage,
            ):
                yield event
        response = self._finish_general(
            prepared, "".join(general_pieces), general_usage.total_tokens
        )
        meter.tokens = response.tokens_used
        yield _end_event(response)


def _mode_event(mode: str) -> dict[str, Any]:
    return {"type": "mode", "answer_mode": mode, "label": answer_label(mode)}


def _end_event(response: RAGResponse) -> dict[str, Any]:
    return {
        "type": "end",
        "final_answer": response.answer,
        "answer_mode": response.answer_mode,
        "label": response.label,
        "citations": response.citations,
        "is_grounded": response.is_grounded,
        "gate_passed": response.gate_passed,
        "top_score": response.top_score,
        "applied_filters": response.applied_filters,
        "abstention_reason": response.abstention_reason,
        "tokens_used": response.tokens_used,
    }
