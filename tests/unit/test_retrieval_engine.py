import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document as LCDocument

from nursing_bot.retrieval.dense import run_dense_search
from nursing_bot.retrieval.fusion import reciprocal_rank_fusion
from nursing_bot.retrieval.gate import RelevanceGate
from nursing_bot.retrieval.retriever import PostgresHybridRetriever
from nursing_bot.retrieval.sparse import run_sparse_search


def test_reciprocal_rank_fusion():
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    id3 = uuid.uuid4()

    dense_ranks = {id1: 1, id2: 2}
    sparse_ranks = {id2: 1, id3: 2}

    fused = reciprocal_rank_fusion(dense_ranks, sparse_ranks, k=60)
    assert fused[id2] > fused[id1]
    assert fused[id2] > fused[id3]
    assert fused[id1] == pytest.approx(1.0 / (60 + 1))
    assert fused[id2] == pytest.approx(1.0 / (60 + 2) + 1.0 / (60 + 1))


def test_relevance_gate():
    gate = RelevanceGate(rrf_threshold=0.02, rerank_threshold=0.50)
    assert gate.evaluate([0.035, 0.015]).passed is True
    assert gate.evaluate([0.015, 0.012]).passed is False
    assert gate.evaluate([]).passed is False
    # Reranking mode threshold check
    assert gate.evaluate([0.40], rerank_enabled=True).passed is False
    assert gate.evaluate([0.60], rerank_enabled=True).passed is True


@pytest.mark.asyncio
async def test_run_dense_search_sql():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_row = {
        "id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "content": "Test content",
        "metadata": {"doc_type": "policy"},
        "page_number": 1,
        "heading_hierarchy": ["Intro"],
    }
    mock_result.mappings.return_value.all.return_value = [mock_row]
    session.execute.return_value = mock_result

    results = await run_dense_search(
        session=session,
        query_vector=[0.1] * 1536,
        limit=5,
        filters={"doc_type": "policy"},
    )
    assert len(results) == 1
    assert results[0]["content"] == "Test content"
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_run_sparse_search_sql():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_row = {
        "id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "content": "Lexical match",
        "metadata": {},
        "page_number": 2,
        "heading_hierarchy": ["Section 2"],
    }
    mock_result.mappings.return_value.all.return_value = [mock_row]
    session.execute.return_value = mock_result

    results = await run_sparse_search(
        session=session,
        query="data retention",
        limit=5,
        filters=None,
    )
    assert len(results) == 1
    assert results[0]["content"] == "Lexical match"
    session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_postgres_hybrid_retriever_ainvoke():
    mock_llm_client = AsyncMock()
    mock_llm_client.aembed_query = AsyncMock(return_value=[0.1] * 1536)

    chunk_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    mock_chunk_row = {
        "id": chunk_id,
        "document_id": doc_id,
        "content": "High score chunk",
        "metadata": {"category": "test"},
        "page_number": 1,
        "heading_hierarchy": ["Overview"],
    }

    session = AsyncMock()
    mock_dense_res = MagicMock()
    mock_dense_res.mappings.return_value.all.return_value = [mock_chunk_row]
    mock_sparse_res = MagicMock()
    mock_sparse_res.mappings.return_value.all.return_value = [mock_chunk_row]

    session.execute.side_effect = [
        MagicMock(),
        mock_dense_res,
        mock_sparse_res,
    ]

    retriever = PostgresHybridRetriever(
        session=session,
        llm_client=mock_llm_client,
        top_k=5,
        rrf_k=60,
    )

    docs = await retriever.ainvoke("hybrid search test")
    assert len(docs) == 1
    assert isinstance(docs[0], LCDocument)
    assert docs[0].page_content == "High score chunk"
    assert docs[0].metadata["chunk_id"] == str(chunk_id)
    assert docs[0].metadata["document_id"] == str(doc_id)
    assert docs[0].metadata["score"] > 0
