"""Relevance gate: decide whether retrieval found something worth answering from.

Why not gate on the RRF score? RRF scores depend only on *rank*: the top chunk
always scores 1/(k+1) whether it is a perfect match or nonsense. So an RRF gate
really measured "did any keyword match", rejecting good semantic matches
(paraphrases, Tamil questions) and passing off-topic questions that shared a
common word. This gate uses calibrated signals instead:

  * reranker relevance score, when reranking is enabled and succeeded
  * otherwise the best dense cosine similarity between question and chunk

Thresholds are model-specific. Tune them with the retrieval eval, which prints
a suggested value: `python -m nursing_bot.cli eval retrieval`.
"""

from dataclasses import dataclass


@dataclass
class GateDecision:
    passed: bool
    top_score: float
    threshold: float
    signal: str  # "rerank" | "similarity" | "none"
    reason: str


class RelevanceGate:
    def __init__(
        self, similarity_threshold: float = 0.30, rerank_threshold: float = 0.50
    ):
        self.similarity_threshold = similarity_threshold
        self.rerank_threshold = rerank_threshold

    def evaluate(
        self, top_similarity: float | None, top_rerank: float | None = None
    ) -> GateDecision:
        if top_rerank is not None:
            signal, score, threshold = "rerank", top_rerank, self.rerank_threshold
        elif top_similarity is not None:
            signal, score, threshold = (
                "similarity",
                top_similarity,
                self.similarity_threshold,
            )
        else:
            return GateDecision(
                passed=False,
                top_score=0.0,
                threshold=self.similarity_threshold,
                signal="none",
                reason="No matching documents found in the knowledge base.",
            )

        if score < threshold:
            return GateDecision(
                passed=False,
                top_score=score,
                threshold=threshold,
                signal=signal,
                reason=f"Best {signal} score {score:.3f} is below the threshold {threshold:.3f}.",
            )
        return GateDecision(
            passed=True,
            top_score=score,
            threshold=threshold,
            signal=signal,
            reason=f"Best {signal} score {score:.3f} meets the threshold {threshold:.3f}.",
        )
