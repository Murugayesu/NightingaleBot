"""Hybrid retriever: dense + sparse search, fused with RRF, optionally reranked.

    question ──embed──► dense search  ─┐
            └─────────► sparse search ─┴► RRF fusion ─► (rerank) ─► top-k chunks

Implements LangChain's BaseRetriever (async), so it also plugs into LangChain
chains; the RAG pipeline calls `aretrieve()` directly to get the gate signals.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document as LCDocument
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.observability import tracer
from nursing_bot.retrieval.dense import run_dense_search
from nursing_bot.retrieval.fusion import reciprocal_rank_fusion
from nursing_bot.retrieval.rerank import rerank_candidates
from nursing_bot.retrieval.sparse import run_sparse_search
from nursing_bot.storage.database import enable_hnsw_iterative_scan

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 300


@dataclass
class RetrievalResult:
    documents: list[LCDocument] = field(default_factory=list)
    top_similarity: float | None = None  # best dense cosine similarity
    top_rerank: float | None = None  # best rerank score (None if not reranked)


class PostgresHybridRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm_client: LiteLLMClient
    session: AsyncSession | None = None  # used only by the LangChain interface
    top_k: int = 8
    rrf_k: int = 60
    rerank_enabled: bool = False
    rerank_top_n: int = 5
    # Batch jobs (evaluation) wait out embedding rate limits; live chat fails fast.
    patient_embeddings: bool = False
    default_filters: dict[str, Any] = Field(default_factory=dict)

    # ---------------------------------------------------- LangChain interface
    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[LCDocument]:
        raise NotImplementedError(
            "PostgresHybridRetriever is async-only; use ainvoke()."
        )

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[LCDocument]:
        if self.session is None:
            raise ValueError(
                "Set `session` on the retriever to use the LangChain interface."
            )
        result = await self.aretrieve(
            query, self.session, self.default_filters, self.top_k
        )
        return result.documents

    # ------------------------------------------------------------- main API
    async def aretrieve(
        self,
        query: str,
        session: AsyncSession,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> RetrievalResult:
        k = top_k or self.top_k
        active_filters = filters if filters is not None else self.default_filters

        with tracer.start_as_current_span("retrieval.hybrid") as span:
            span.set_attribute("retrieval.query", query)
            span.set_attribute("retrieval.top_k", k)

            await enable_hnsw_iterative_scan(session)
            (query_vector,) = await self.llm_client.aembed_texts(
                [query], patient=self.patient_embeddings
            )
            dense = await run_dense_search(
                session,
                query_vector,
                embedding_model=self.llm_client.embedding_model,
                limit=k * 2,
                filters=active_filters,
            )
            sparse = await run_sparse_search(
                session, query, limit=k * 2, filters=active_filters
            )

            top_similarity = max((float(r["similarity"]) for r in dense), default=None)
            dense_by_id = {r["id"]: r for r in dense}

            rrf = reciprocal_rank_fusion(
                {r["id"]: i + 1 for i, r in enumerate(dense)},
                {r["id"]: i + 1 for i, r in enumerate(sparse)},
                k=self.rrf_k,
            )
            payloads = {r["id"]: r for r in sparse} | dense_by_id
            candidates = []
            for chunk_id in sorted(rrf, key=lambda cid: rrf[cid], reverse=True)[:k]:
                candidate = dict(payloads[chunk_id])
                candidate["rrf_score"] = rrf[chunk_id]
                candidate["similarity"] = dense_by_id.get(chunk_id, {}).get(
                    "similarity"
                )
                candidates.append(candidate)

            top_rerank = None
            if self.rerank_enabled and candidates:
                try:
                    candidates = await rerank_candidates(
                        self.llm_client, query, candidates, top_n=self.rerank_top_n
                    )
                    top_rerank = max(
                        (c["rerank_score"] for c in candidates), default=None
                    )
                except Exception as exc:  # noqa: BLE001 - degrade to the similarity gate
                    logger.warning("Reranking failed, using fused order: %s", exc)

            span.set_attribute("retrieval.dense_hits", len(dense))
            span.set_attribute("retrieval.sparse_hits", len(sparse))
            span.set_attribute("retrieval.top_similarity", top_similarity or 0.0)

        return RetrievalResult(
            documents=[_to_document(i, c) for i, c in enumerate(candidates, start=1)],
            top_similarity=top_similarity,
            top_rerank=top_rerank,
        )


def _best_score(candidate: dict[str, Any]) -> float:
    """The single score shown to users: the strongest calibrated signal available."""
    for key in ("rerank_score", "similarity"):
        if candidate.get(key) is not None:
            return float(candidate[key])
    return 0.0


def _to_document(rank: int, candidate: dict[str, Any]) -> LCDocument:
    similarity = candidate.get("similarity")
    metadata = dict(candidate.get("metadata") or {})
    metadata.update(
        {
            "source_index": rank,
            "chunk_id": str(candidate["id"]),
            "document_id": str(candidate["document_id"]),
            "title": candidate.get("filename") or metadata.get("source", ""),
            "page_number": candidate.get("page_number"),
            "heading_hierarchy": candidate.get("heading_hierarchy") or [],
            "rrf_score": candidate.get("rrf_score"),
            "similarity": float(similarity) if similarity is not None else None,
            "rerank_score": candidate.get("rerank_score"),
            "score": _best_score(candidate),
            "snippet": (candidate.get("content") or "")[:SNIPPET_CHARS],
        }
    )
    # The LLM sees the context-prefixed text: the same string that was embedded.
    return LCDocument(
        page_content=candidate.get("contextualised_text")
        or candidate.get("content", ""),
        metadata=metadata,
    )
