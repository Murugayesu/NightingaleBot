from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.retrieval.metadata import build_filter_sql


async def run_dense_search(
    session: AsyncSession,
    query_vector: list[float],
    limit: int = 20,
    filters: dict[str, Any] | None = None,
    chunk_status: str = "active",
) -> list[dict[str, Any]]:
    """Execute dense vector similarity search using pgvector HNSW cosine distance."""
    params: dict[str, Any] = {
        "vector": str(query_vector),
        "limit": limit,
    }
    filter_clause = build_filter_sql(filters, params, chunk_status=chunk_status)

    query_sql = f"""
        SELECT c.id, c.document_id, c.content,
               c.metadata, c.page_number, c.heading_hierarchy
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE {filter_clause}
        ORDER BY c.embedding <=> CAST(:vector AS vector)
        LIMIT :limit
    """
    result = await session.execute(text(query_sql), params)
    rows = result.mappings().all()
    return [dict(row) for row in rows]
