from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.generation.assembler import ContextAssembler
from nursing_bot.generation.condenser import HistoryCondenser
from nursing_bot.generation.guardrails import InputGuard, expand_synonyms
from nursing_bot.generation.verifier import CitationCoverageVerifier
from nursing_bot.models.litellm_client import LiteLLMClient
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


class GenerationPipeline:
    """End-to-end RAG generation pipeline enforcing safety, grounding, and citation integrity."""

    def __init__(
        self,
        retriever: PostgresHybridRetriever,
        llm_client: LiteLLMClient,
        gate: RelevanceGate | None = None,
        condenser: HistoryCondenser | None = None,
        default_top_k: int = 5,
    ):
        self.retriever = retriever
        self.llm_client = llm_client
        self.gate = gate or RelevanceGate()
        self.condenser = condenser or HistoryCondenser(llm_client)
        self.default_top_k = default_top_k

    async def aquery(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> RAGResponse:
        settings = get_settings()

        guard = InputGuard.check_and_sanitize(query)
        if not guard.is_safe:
            return RAGResponse(
                answer=guard.rejection_reason or "Request blocked.",
                is_grounded=False,
                gate_passed=False,
                abstention_reason=guard.rejection_reason,
            )

        sanitized_query = guard.sanitized_query
        expanded_query = expand_synonyms(sanitized_query, settings.domain_synonyms)
        standalone_query = await self.condenser.condense(expanded_query, history)

        documents = await self.retriever.asearch(
            query=standalone_query,
            session=session,
            filters=filters,
            top_k=top_k or self.default_top_k,
        )

        scores = [doc.metadata.get("score", 0.0) for doc in documents]
        gate_decision = self.gate.evaluate(
            scores, rerank_enabled=self.retriever.rerank_enabled
        )
        if not gate_decision.passed:
            return RAGResponse(
                answer=settings.RAG_ABSTENTION_MESSAGE,
                citations=[],
                is_grounded=False,
                gate_passed=False,
                top_score=gate_decision.top_score,
                applied_filters=filters or {},
                abstention_reason=gate_decision.reason,
            )

        assembled = ContextAssembler.assemble(documents)

        user_prompt = (
            f"Official Context:\n{assembled.context_text}\n\n"
            f"User Question: {sanitized_query}\nAnswer:"
        )
        raw_answer = await self.llm_client.acomplete(
            prompt=user_prompt,
            system_prompt=settings.RAG_SYSTEM_PROMPT,
            temperature=0.0,
        )

        verification = CitationCoverageVerifier.verify(
            answer=raw_answer,
            citations=assembled.citations,
            gate_passed=True,
        )

        return RAGResponse(
            answer=verification.final_answer,
            citations=assembled.citations if verification.is_grounded else [],
            is_grounded=verification.is_grounded,
            gate_passed=True,
            top_score=gate_decision.top_score,
            applied_filters=filters or {},
            abstention_reason=verification.notes
            if not verification.is_grounded
            else None,
        )

    async def astream_query(
        self,
        session: AsyncSession,
        query: str,
        history: list[dict[str, str]] | None = None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        settings = get_settings()

        guard = InputGuard.check_and_sanitize(query)
        if not guard.is_safe:
            msg = guard.rejection_reason or "Request blocked."
            yield {"type": "token", "content": msg}
            yield {
                "type": "end",
                "citations": [],
                "gate_passed": False,
                "is_grounded": False,
                "applied_filters": filters or {},
                "abstention_reason": msg,
            }
            return

        sanitized_query = guard.sanitized_query
        expanded_query = expand_synonyms(sanitized_query, settings.domain_synonyms)
        standalone_query = await self.condenser.condense(expanded_query, history)

        documents = await self.retriever.asearch(
            query=standalone_query,
            session=session,
            filters=filters,
            top_k=top_k or self.default_top_k,
        )
        scores = [doc.metadata.get("score", 0.0) for doc in documents]
        gate_decision = self.gate.evaluate(
            scores, rerank_enabled=self.retriever.rerank_enabled
        )

        if not gate_decision.passed:
            yield {"type": "token", "content": settings.RAG_ABSTENTION_MESSAGE}
            yield {
                "type": "end",
                "citations": [],
                "gate_passed": False,
                "is_grounded": False,
                "applied_filters": filters or {},
                "abstention_reason": gate_decision.reason,
            }
            return

        assembled = ContextAssembler.assemble(documents)

        # Yield citations immediately as retrieval and gate passed
        if assembled.citations:
            yield {"type": "citations", "citations": assembled.citations}

        user_prompt = (
            f"Official Context:\n{assembled.context_text}\n\n"
            f"User Question: {sanitized_query}\nAnswer:"
        )

        tokens: list[str] = []
        async for token in self.llm_client.astream(
            prompt=user_prompt,
            system_prompt=settings.RAG_SYSTEM_PROMPT,
            temperature=0.0,
        ):
            tokens.append(token)
            yield {"type": "token", "content": token}

        raw_answer = "".join(tokens)
        verification = CitationCoverageVerifier.verify(
            answer=raw_answer,
            citations=assembled.citations,
            gate_passed=True,
        )

        if not verification.is_grounded:
            yield {
                "type": "token",
                "content": f"\n\n[Warning: {settings.RAG_ABSTENTION_MESSAGE}]",
            }

        yield {
            "type": "end",
            "citations": assembled.citations if verification.is_grounded else [],
            "gate_passed": True,
            "is_grounded": verification.is_grounded,
            "applied_filters": filters or {},
            "abstention_reason": verification.notes
            if not verification.is_grounded
            else None,
        }
