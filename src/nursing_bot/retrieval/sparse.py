from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.retrieval.metadata import build_filter_sql


async def run_sparse_search(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    filters: dict[str, Any] | None = None,
    chunk_status: str = "active",
) -> list[dict[str, Any]]:
    """Execute sparse lexical full-text search using PostgreSQL tsvector and GIN index."""
    params: dict[str, Any] = {
        "query": query,
        "limit": limit,
    }
    filter_clause = build_filter_sql(filters, params, chunk_status=chunk_status)

    query_sql = f"""
        SELECT c.id, c.document_id, c.content,
               c.metadata, c.page_number, c.heading_hierarchy,
               ts_rank_cd(c.tsv, websearch_to_tsquery('english', :query)) as rank
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.tsv @@ websearch_to_tsquery('english', :query)
          AND {filter_clause}
        ORDER BY rank DESC
        LIMIT :limit
    """
    result = await session.execute(text(query_sql), params)
    rows = result.mappings().all()

    if not rows and query.strip():
        fallback_sql = f"""
            SELECT c.id, c.document_id, c.content,
                   c.metadata, c.page_number, c.heading_hierarchy,
                   ts_rank_cd(c.tsv, plainto_tsquery('english', :query)) as rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.tsv @@ plainto_tsquery('english', :query)
              AND {filter_clause}
            ORDER BY rank DESC
            LIMIT :limit
        """
        result = await session.execute(text(fallback_sql), params)
        rows = result.mappings().all()

    if not rows and query.strip():
        stop_words = {"not", "and", "or"}
        tokens = [
            t
            for t in "".join(c if c.isalnum() else " " for c in query).split()
            if len(t) > 2 and t.lower() not in stop_words
        ]
        if tokens:
            or_query = " | ".join(tokens)
            or_params = {"or_query": or_query, "limit": limit}
            or_filter = build_filter_sql(filters, or_params, chunk_status=chunk_status)
            or_sql = f"""
                SELECT c.id, c.document_id, c.content,
                       c.metadata, c.page_number, c.heading_hierarchy,
                       ts_rank_cd(c.tsv, to_tsquery('english', :or_query)) as rank
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.tsv @@ to_tsquery('english', :or_query)
                  AND {or_filter}
                ORDER BY rank DESC
                LIMIT :limit
            """
            try:
                async with session.begin_nested():
                    result = await session.execute(text(or_sql), or_params)
                    rows = result.mappings().all()
            except Exception:  # noqa: BLE001, S110
                pass

    return [dict(row) for row in rows]
