"""Post-generation check: is the answer actually backed by citations?

An answer passes when:
  * it is not the model's own "not available" reply,
  * every [Source N] tag points at a source we really provided, and
  * at least CITATION_MIN_COVERAGE of its factual sentences carry a tag
    (a tag at the end of a list or paragraph covers the lines before it).

Answers that fail are replaced by the abstention message.
"""

import re
from dataclasses import dataclass
from typing import Any

SOURCE_TAG = re.compile(r"\[Source\s+(\d+)\]", re.IGNORECASE)
# "[Sources 1, 2]", "[Source 1; Source 3]", "[Sources 1 and 2]" → "[Source 1] [Source 2]"
_SOURCE_GROUP = re.compile(
    r"\[Sources?\s+\d+(?:\s*(?:,|;|&|\band\b)\s*(?:Sources?\s+)?\d+)*\]", re.IGNORECASE
)
_ANY_SOURCE_MENTION = re.compile(r"\[Sources?\b", re.IGNORECASE)
# Sentence ends: ". " "! " "? " or a line break, but not after abbreviations
# common in council documents ("Rs. 365", "No. 12", "Dr. Rao", "e.g. this").
_SENTENCE_SPLIT = re.compile(
    r"(?<!\bRs\.)(?<!\bNo\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bvs\.)(?<!\bMr\.)(?<!\bMs\.)"
    r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<=[.!?])\s+|\n+"
)
_MODEL_ABSTAINED = re.compile(
    r"(not available in the records|no information (?:is )?available|cannot find|could not find)",
    re.IGNORECASE,
)
_MIN_FACTUAL_WORDS = 3


@dataclass
class VerificationResult:
    is_grounded: bool
    final_answer: str
    coverage: float
    notes: str = ""


def normalize_citations(answer: str) -> str:
    return _SOURCE_GROUP.sub(
        lambda m: " ".join(f"[Source {int(n)}]" for n in re.findall(r"\d+", m.group())),
        answer,
    )


def _is_factual(sentence: str) -> bool:
    """A statement worth citing: at least 3 words (tags excluded), not a lead-in like 'Steps:'."""
    words = SOURCE_TAG.sub(" ", sentence).split()
    return len(words) >= _MIN_FACTUAL_WORDS and not sentence.rstrip().endswith(":")


def citation_coverage(answer: str) -> float:
    """Share of factual sentences that carry, or are followed within their
    paragraph by, a [Source N] tag.

    A tag later in the same paragraph counts, so one tag after a short list
    covers the list. Answers too short to contain a "factual sentence" (for
    example "Five years [Source 1].") count as fully covered: the caller has
    already checked that at least one valid tag is present.
    """
    coverage_flags: list[bool] = []
    for paragraph in re.split(r"\n\s*\n", answer):
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]
        for i, sentence in enumerate(sentences):
            if _is_factual(sentence):
                coverage_flags.append(any(SOURCE_TAG.search(s) for s in sentences[i:]))
    return sum(coverage_flags) / len(coverage_flags) if coverage_flags else 1.0


class CitationCoverageVerifier:
    @classmethod
    def verify(
        cls,
        answer: str,
        citations: list[dict[str, Any]],
        abstention_message: str,
        min_coverage: float = 0.5,
    ) -> VerificationResult:
        def fail(notes: str, coverage: float = 0.0) -> VerificationResult:
            return VerificationResult(False, abstention_message, coverage, notes)

        if not answer.strip() or not citations:
            return fail("Empty answer or no sources.")

        normalized = normalize_citations(answer)
        tags = [int(n) for n in SOURCE_TAG.findall(normalized)]
        if not tags:
            if _MODEL_ABSTAINED.search(normalized):
                return fail(
                    "The model reported that the sources do not contain the answer."
                )
            return fail("Answer has no [Source N] citations.")

        valid = {c["source_index"] for c in citations}
        if any(t not in valid for t in tags):
            return fail("Answer cites a source that was not provided.")
        if len(tags) < len(_ANY_SOURCE_MENTION.findall(normalized)):
            return fail("Answer contains malformed citation tags.")

        coverage = citation_coverage(normalized)
        if coverage < min_coverage:
            return fail(
                f"Only {coverage:.0%} of factual sentences are cited.", coverage
            )

        return VerificationResult(
            True, normalized, coverage, "Answer verified against sources."
        )
