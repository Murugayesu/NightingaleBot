"""Optional cross-encoder reranking of fused candidates (Cohere / Jina / Voyage via LiteLLM)."""

from typing import Any

from nursing_bot.models.litellm_client import LiteLLMClient


async def rerank_candidates(
    llm_client: LiteLLMClient,
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Return the best `top_n` candidates, best first, each with a `rerank_score`.

    Raises if the rerank provider fails; the retriever decides how to degrade.
    """
    if not candidates:
        return []
    results = await llm_client.arerank(
        query=query,
        documents=[c.get("contextualised_text") or c["content"] for c in candidates],
        top_n=top_n,
    )
    reranked = []
    for result in results[:top_n]:
        candidate = dict(candidates[result.index])
        candidate["rerank_score"] = result.relevance_score
        reranked.append(candidate)
    return reranked
