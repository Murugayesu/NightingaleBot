from dataclasses import dataclass


@dataclass
class GateDecision:
    passed: bool
    top_score: float
    threshold: float
    reason: str


class RelevanceGate:
    """Evaluates retrieval quality against confidence thresholds to prevent hallucinations."""

    def __init__(self, rrf_threshold: float = 0.02, rerank_threshold: float = 0.50):
        self.rrf_threshold = rrf_threshold
        self.rerank_threshold = rerank_threshold

    def evaluate(
        self, scores: list[float], rerank_enabled: bool = False
    ) -> GateDecision:
        threshold = self.rerank_threshold if rerank_enabled else self.rrf_threshold

        if not scores:
            return GateDecision(
                passed=False,
                top_score=0.0,
                threshold=threshold,
                reason="No matching documents found in knowledge base.",
            )

        top_score = max(scores)
        if top_score < threshold:
            return GateDecision(
                passed=False,
                top_score=top_score,
                threshold=threshold,
                reason=f"Top retrieval score {top_score:.4f} is below threshold {threshold:.4f}.",
            )

        return GateDecision(
            passed=True,
            top_score=top_score,
            threshold=threshold,
            reason="Retrieval score meets confidence threshold.",
        )
