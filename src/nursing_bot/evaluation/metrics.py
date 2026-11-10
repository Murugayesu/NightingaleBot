"""Pure scoring helpers: no I/O, easy to unit-test."""

import re
from pathlib import Path

_NON_WORD = re.compile(r"[^\w%+]+", re.UNICODE)


def normalize(text: str) -> str:
    """Lowercase and collapse punctuation, so 'Rs. 365' matches 'rs 365'."""
    return " ".join(_NON_WORD.sub(" ", text.casefold()).split())


def fact_present(fact: str, answer: str) -> bool:
    """Whole-word match: '365' must not match '3650', nor 'NM' match 'TNNMC'."""
    needle = normalize(fact)
    return bool(needle) and f" {needle} " in f" {normalize(answer)} "


def fact_recall(facts: list[str], answer: str) -> float:
    if not facts:
        return 1.0
    return sum(fact_present(f, answer) for f in facts) / len(facts)


def same_document(a: str, b: str) -> bool:
    """Filenames match ignoring case and extension (a .docx copy of a .pdf counts)."""
    return Path(a).stem.casefold().strip() == Path(b).stem.casefold().strip()


def first_hit_rank(
    expected_sources: list[str], retrieved_titles: list[str]
) -> int | None:
    """1-based rank of the first retrieved chunk from an expected source, else None."""
    for rank, title in enumerate(retrieved_titles, start=1):
        if any(same_document(title, source) for source in expected_sources):
            return rank
    return None


def suggest_threshold(
    should_pass: list[float], should_block: list[float]
) -> dict[str, float] | None:
    """Pick the gate threshold that best separates answerable from unanswerable questions.

    Maximises balanced accuracy: mean of (answerable questions passing) and
    (unanswerable questions blocked). Returns None without both kinds of data.
    """
    if not should_pass or not should_block:
        return None
    candidates = sorted(set(should_pass + should_block))
    best: dict[str, float] | None = None
    for threshold in candidates:
        pass_rate = sum(s >= threshold for s in should_pass) / len(should_pass)
        block_rate = sum(s < threshold for s in should_block) / len(should_block)
        balanced = (pass_rate + block_rate) / 2
        if best is None or balanced > best["balanced_accuracy"]:
            best = {
                "threshold": round(threshold, 4),
                "answerable_pass_rate": round(pass_rate, 4),
                "unanswerable_block_rate": round(block_rate, 4),
                "balanced_accuracy": round(balanced, 4),
            }
    return best
