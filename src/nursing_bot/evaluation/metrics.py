"""Pure scoring helpers: no I/O, easy to unit-test."""

import re
import unicodedata
from pathlib import Path

_NON_WORD = re.compile(r"[^\w%+]+", re.UNICODE)


_SPACED_PERCENT = re.compile(r"(\d)\s+%")


def normalize(text: str) -> str:
    """Lowercase and collapse punctuation, so 'Rs. 365' matches 'rs 365'.

    Unicode look-alikes are folded first (NFKC): models write "45\u202f%" with
    a narrow no-break space, which must still read as "45%".
    """
    text = _SPACED_PERCENT.sub(r"\1%", unicodedata.normalize("NFKC", text))
    return " ".join(_NON_WORD.sub(" ", text.casefold()).split())


# Words that carry no fact; ignored when comparing a fact with an answer.
_FILLER = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "and",
        "or",
        "is",
        "are",
        "be",
        "your",
        "my",
        "from",
        "as",
        "it",
        "its",
        "this",
        "that",
    ]
)


def _tokens(text: str) -> list[str]:
    """Normalised tokens. Numbers lose leading zeros ("09" = "9", "044" = "44"),
    and an Indian country code before another number is dropped, so
    "+91-44-4678 6539" and "044-4678 6539" are the same phone number."""
    raw = normalize(text).split()
    tokens: list[str] = []
    for i, token in enumerate(raw):
        following = raw[i + 1] if i + 1 < len(raw) else ""
        if token in ("91", "+91") and following.isdigit():
            continue
        if token.isdigit():
            token = token.lstrip("0") or "0"
        tokens.append(token)
    return tokens


def fact_present(fact: str, answer: str) -> bool:
    """Is a fact stated in the answer, however it is worded?

    Numbers are the facts that matter most (fees, dates, phone numbers,
    percentages), so every number in the fact must appear in the answer as a
    whole token: "365" never matches "3650". The other key words may be
    reworded: at least 75% of them must appear, so "online credit card debit
    card net banking" matches "online through credit card, debit card, or net
    banking", but "NM" never matches "TNNMC".
    """
    needle = normalize(fact)
    if not needle:
        return False
    if f" {needle} " in f" {normalize(answer)} ":
        return True
    key = [t for t in _tokens(fact) if t not in _FILLER]
    if not key:
        return False
    found = set(_tokens(answer))
    numbers = [t for t in key if any(ch.isdigit() for ch in t)]
    words = [t for t in key if t not in numbers]
    if any(number not in found for number in numbers):
        return False
    if not words:
        return True
    return sum(word in found for word in words) / len(words) >= 0.75


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
