"""Sparse (keyword) search: PostgreSQL full-text search over the `tsv` column.

Two tiers:
  1. all words must match (websearch_to_tsquery: Google-like syntax, never errors)
  2. if nothing matched, any word may match ("fee or registration or ...")

Tier 2 boosts recall for long questions. It does not decide whether we answer;
the relevance gate uses dense similarity for that.
"""

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.retrieval.metadata import SELECT_COLUMNS, build_filter_sql

# Words that carry no meaning on their own (also websearch operators).
_STOP_WORDS = frozenset(
    {
        "and",
        "or",
        "not",
        "the",
        "what",
        "how",
        "who",
        "when",
        "where",
        "which",
        "can",
        "for",
        "are",
        "does",
        "with",
    }
)
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


def _any_word_query(query: str) -> str:
    words = [
        w
        for w in _PUNCTUATION.sub(" ", query).split()
        if len(w) > 2 and w.lower() not in _STOP_WORDS
    ]
    return " or ".join(dict.fromkeys(words))  # keep order, drop duplicates


async def _search(
    session: AsyncSession,
    tsquery_text: str,
    limit: int,
    filters: dict[str, Any] | None,
    chunk_status: str,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"query": tsquery_text, "limit": limit}
    where = build_filter_sql(filters, params, chunk_status=chunk_status)
    sql = f"""
        SELECT {SELECT_COLUMNS},
               ts_rank_cd(c.tsv, websearch_to_tsquery('english', :query)) AS rank
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.tsv @@ websearch_to_tsquery('english', :query) AND {where}
        ORDER BY rank DESC
        LIMIT :limit
    """
    result = await session.execute(text(sql), params)
    return [dict(row) for row in result.mappings().all()]


async def run_sparse_search(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    filters: dict[str, Any] | None = None,
    chunk_status: str = "active",
) -> list[dict[str, Any]]:
    """Return up to `limit` rows ranked by full-text relevance."""
    if not query.strip():
        return []
    rows = await _search(session, query, limit, filters, chunk_status)
    if not rows:
        any_word = _any_word_query(query)
        if any_word:
            rows = await _search(session, any_word, limit, filters, chunk_status)
    return rows
