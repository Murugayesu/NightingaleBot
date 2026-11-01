import uuid


def reciprocal_rank_fusion(
    dense_ranks: dict[uuid.UUID, int],
    sparse_ranks: dict[uuid.UUID, int],
    k: int = 60,
) -> dict[uuid.UUID, float]:
    """Calculate Reciprocal Rank Fusion (RRF) scores across dense and sparse ranking sets."""
    rrf_scores: dict[uuid.UUID, float] = {}
    all_ids = set(dense_ranks.keys()) | set(sparse_ranks.keys())

    for item_id in all_ids:
        score = 0.0
        if item_id in dense_ranks:
            score += 1.0 / (k + dense_ranks[item_id])
        if item_id in sparse_ranks:
            score += 1.0 / (k + sparse_ranks[item_id])
        rrf_scores[item_id] = score

    return rrf_scores
