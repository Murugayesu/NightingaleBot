import asyncio
import uuid
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document as LCDocument
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

from nursing_bot.retrieval.dense import run_dense_search
from nursing_bot.retrieval.fusion import reciprocal_rank_fusion
from nursing_bot.retrieval.rerank import rerank_candidates
from nursing_bot.retrieval.sparse import run_sparse_search
from nursing_bot.storage.database import enable_hnsw_iterative_scan


class PostgresHybridRetriever(BaseRetriever):
    """PostgreSQL hybrid search retriever implementing LangChain's BaseRetriever interface."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: Any
    llm_client: Any
    top_k: int = 10
    rrf_k: int = 60
    rerank_enabled: bool = False
    rerank_top_n: int = 5
    default_filters: dict[str, Any] = Field(default_factory=dict)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[LCDocument]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            try:
                import nest_asyncio

                nest_asyncio.apply()
                return loop.run_until_complete(
                    self._aget_relevant_documents(query, run_manager=run_manager)
                )
            except Exception:  # noqa: BLE001, S110
                pass
        return asyncio.run(
            self._aget_relevant_documents(query, run_manager=run_manager)
        )

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[LCDocument]:
        return await self.asearch(
            query=query,
            session=self.session,
            filters=self.default_filters,
            top_k=self.top_k,
        )

    async def asearch(
        self,
        query: str,
        session: Any = None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> list[LCDocument]:
        active_session = session if session is not None else self.session
        active_filters = filters if filters is not None else self.default_filters
        active_top_k = top_k or self.top_k

        if active_session is not None:
            await enable_hnsw_iterative_scan(active_session)

        # 1. Dense vector search
        query_vector = await self.llm_client.aembed_query(query)
        dense_results = await run_dense_search(
            session=active_session,
            query_vector=query_vector,
            limit=active_top_k * 2,
            filters=active_filters,
        )

        # 2. Sparse full-text search
        sparse_results = await run_sparse_search(
            session=active_session,
            query=query,
            limit=active_top_k * 2,
            filters=active_filters,
        )

        # 3. Reciprocal Rank Fusion
        dense_ranks = {item["id"]: idx + 1 for idx, item in enumerate(dense_results)}
        sparse_ranks = {item["id"]: idx + 1 for idx, item in enumerate(sparse_results)}
        rrf_scores = reciprocal_rank_fusion(dense_ranks, sparse_ranks, k=self.rrf_k)

        payloads: dict[uuid.UUID, dict[str, Any]] = {}
        for item in dense_results + sparse_results:
            payloads[item["id"]] = item

        sorted_ids = sorted(
            rrf_scores.keys(),
            key=lambda x: rrf_scores[x],
            reverse=True,
        )[:active_top_k]

        candidates: list[dict[str, Any]] = []
        for chunk_id in sorted_ids:
            payload = dict(payloads[chunk_id])
            payload["score"] = rrf_scores[chunk_id]
            candidates.append(payload)

        # 4. Optional Cross-Encoder Reranking
        if self.rerank_enabled and candidates:
            candidates = await rerank_candidates(
                llm_client=self.llm_client,
                query=query,
                candidates=candidates,
                top_n=self.rerank_top_n,
            )

        # 5. Build standardized LangChain Documents
        documents: list[LCDocument] = []
        for rank_idx, cand in enumerate(candidates, start=1):
            metadata = dict(cand.get("metadata") or {})
            metadata.update(
                {
                    "source_index": rank_idx,
                    "chunk_id": str(cand["id"]),
                    "document_id": str(cand["document_id"]),
                    "score": cand.get("rerank_score", cand.get("score", 0.0)),
                    "heading_hierarchy": cand.get("heading_hierarchy", []),
                    "page_number": cand.get("page_number"),
                }
            )
            doc = LCDocument(
                page_content=cand["content"],
                metadata=metadata,
            )
            documents.append(doc)

        return documents
