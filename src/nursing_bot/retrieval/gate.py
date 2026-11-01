from dataclasses import dataclass


@dataclass
class GateDecision:
    passed: bool
    top_score: float
    threshold: float
    reason: str


class RelevanceGate:
    """Evaluates retrieval quality against confidence thresholds to prevent hallucinations."""

    def __init__(self, rrf_threshold: float = 0.02):
        self.rrf_threshold = rrf_threshold

    def evaluate(self, scores: list[float]) -> GateDecision:
        if not scores:
            return GateDecision(
                passed=False,
                top_score=0.0,
                threshold=self.rrf_threshold,
                reason="No matching documents found in knowledge base.",
            )

        top_score = max(scores)
        if top_score < self.rrf_threshold:
            return GateDecision(
                passed=False,
                top_score=top_score,
                threshold=self.rrf_threshold,
                reason=f"Top retrieval score {top_score:.4f} is below threshold {self.rrf_threshold:.4f}.",
            )

        return GateDecision(
            passed=True,
            top_score=top_score,
            threshold=self.rrf_threshold,
            reason="Retrieval score meets confidence threshold.",
        )
