from typing import Any

from nursing_bot.models.litellm_client import LiteLLMClient


async def rerank_candidates(
    llm_client: LiteLLMClient,
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Rerank retrieval candidate chunks using cross-encoder."""
    if not candidates:
        return []

    documents = [c["content"] for c in candidates]
    rerank_results = await llm_client.arerank(
        query=query,
        documents=documents,
        top_n=top_n,
    )

    reranked: list[dict[str, Any]] = []
    for item in rerank_results:
        idx = item.get("index") if isinstance(item, dict) else getattr(item, "index", 0)
        score = item.get("relevance_score") if isinstance(item, dict) else getattr(item, "relevance_score", 0.0)
        if 0 <= idx < len(candidates):
            c_copy = dict(candidates[idx])
            c_copy["rerank_score"] = score
            reranked.append(c_copy)

    return reranked or candidates[:top_n]
