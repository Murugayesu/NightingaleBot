"""Dense (semantic) search: nearest chunks by cosine distance using the pgvector HNSW index."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.retrieval.metadata import SELECT_COLUMNS, build_filter_sql


async def run_dense_search(
    session: AsyncSession,
    query_vector: list[float],
    embedding_model: str,
    limit: int = 20,
    filters: dict[str, Any] | None = None,
    chunk_status: str = "active",
) -> list[dict[str, Any]]:
    """Return up to `limit` rows, closest first, each with a `similarity` in [-1, 1].

    Only chunks embedded with `embedding_model` are compared: vectors from
    different models live in different spaces, so mixing them is meaningless.
    After switching models, re-index (POST /api/v1/admin/reindex).
    """
    params: dict[str, Any] = {
        "vector": "[" + ",".join(f"{x:.8f}" for x in query_vector) + "]",
        "limit": limit,
        "embedding_model": embedding_model,
    }
    where = build_filter_sql(filters, params, chunk_status=chunk_status)
    sql = f"""
        SELECT {SELECT_COLUMNS},
               1 - (c.embedding <=> CAST(:vector AS vector)) AS similarity
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE {where} AND c.embedding_model = :embedding_model
        ORDER BY c.embedding <=> CAST(:vector AS vector)
        LIMIT :limit
    """
    result = await session.execute(text(sql), params)
    return [dict(row) for row in result.mappings().all()]
