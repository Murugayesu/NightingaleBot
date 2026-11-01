import re
from dataclasses import dataclass
from typing import Any

from nursing_bot.config import get_settings


@dataclass
class VerificationResult:
    is_grounded: bool
    final_answer: str
    confidence_score: float
    notes: str = ""


class CitationCoverageVerifier:
    """Verifies and normalizes inline citation tags ([Source N])."""

    SOURCE_TAG = re.compile(r"\[Source\s+(\d+)\]")
    SOURCE_MENTION = re.compile(r"\[Sources?\b", re.IGNORECASE)
    SOURCE_GROUP = re.compile(
        r"\[Sources?\s+\d+(?:\s*(?:,|;|&|\band\b)\s*(?:Sources?\s+)?\d+)*\]",
        re.IGNORECASE,
    )

    ABSTENTION_MESSAGE = get_settings().RAG_ABSTENTION_MESSAGE

    @classmethod
    def verify(
        cls,
        answer: str,
        citations: list[dict[str, Any]],
        gate_passed: bool,
    ) -> VerificationResult:
        if not gate_passed or not citations:
            return VerificationResult(
                is_grounded=False,
                final_answer=cls.ABSTENTION_MESSAGE,
                confidence_score=0.0,
                notes="Retrieval gate failed or no supporting citations present.",
            )

        normalized_answer = cls.SOURCE_GROUP.sub(
            lambda match: " ".join(
                f"[Source {int(index)}]" for index in re.findall(r"\d+", match.group())
            ),
            answer,
        )

        valid_indices = {c["source_index"] for c in citations}
        tags = cls.SOURCE_TAG.findall(normalized_answer)

        if (
            not answer.strip()
            or not tags
            or len(tags) != len(cls.SOURCE_MENTION.findall(normalized_answer))
            or any(int(index) not in valid_indices for index in tags)
        ):
            return VerificationResult(
                is_grounded=False,
                final_answer=cls.ABSTENTION_MESSAGE,
                confidence_score=0.0,
                notes="Answer has no valid inline citations or cites an unknown source.",
            )

        return VerificationResult(
            is_grounded=True,
            final_answer=normalized_answer,
            confidence_score=0.95,
            notes="Answer verified with valid inline source citations.",
        )
