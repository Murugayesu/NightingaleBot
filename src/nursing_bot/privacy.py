"""PII detection and redaction, shared by input guardrails, chat history and tracing.

Kept in one place so every layer redacts the same things the same way.
Patterns are tuned for an Indian audience and deliberately conservative:
redacting a registration number by mistake damages retrieval, so we only
redact values that are very likely to be personal data.
"""

import re

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Indian mobile numbers: optional +91 / 91 / 0 prefix, then 10 digits starting
# 6-9, written 10 / 5+5 / 3+3+4. Landlines are not redacted: the numbers people
# ask about are the council's own helplines (e.g. 044-2493 4792).
INDIAN_MOBILE = re.compile(
    r"(?<!\d)(?<!\d\.)(?:\+?91[-\s]?|0)?"
    r"(?:[6-9]\d{4}[-\s]?\d{5}|[6-9]\d{2}[-\s]?\d{3}[-\s]?\d{4})"
    r"(?!\d|\.\d)"  # not part of a longer number or a decimal
)
# A number right after these words is money, not a phone ("fees 60000-70000").
_MONEY_CONTEXT = re.compile(
    r"(?:rs\.?|₹|inr|rupees?|fees?|salary|stipend|cost|amount|price)\W{0,3}$",
    re.IGNORECASE,
)

# International numbers written with an explicit country code, e.g. +1 415 555 0100.
INTERNATIONAL_PHONE = re.compile(
    r"(?<![\w+])\+\d{1,3}[-\s]?\d{2,4}[-\s]?\d{3,4}[-\s]?\d{3,4}(?!\d)"
)

# Aadhaar: 12 digits in groups of 4, first digit 2-9. Candidates are confirmed
# with the Verhoeff checksum, so "2023 2024 2025" is not redacted.
_AADHAAR_CANDIDATE = re.compile(r"(?<!\d)([2-9]\d{3})[-\s]?(\d{4})[-\s]?(\d{4})(?!\d)")

# Verhoeff tables (multiplication d, permutation p).
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(digits: str) -> bool:
    """True if the digit string carries a valid Verhoeff check digit."""
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(ch)]]
    return check == 0


def _redact_aadhaar(text: str, label: str) -> str:
    def replace(match: re.Match[str]) -> str:
        digits = "".join(match.groups())
        return label if verhoeff_valid(digits) else match.group(0)

    return _AADHAAR_CANDIDATE.sub(replace, text)


def _redact_mobile(text: str, label: str) -> str:
    def replace(match: re.Match[str]) -> str:
        before = text[max(0, match.start() - 20) : match.start()]
        return match.group(0) if _MONEY_CONTEXT.search(before) else label

    return INDIAN_MOBILE.sub(replace, text)


def redact(text: str, *, style: str = "short") -> str:
    """Replace personal data in `text` with placeholders.

    style="short" gives [EMAIL] [PHONE] [ID]; style="trace" gives *_REDACTED
    labels, which are easier to spot in trace viewers.
    """
    suffix = "_REDACTED" if style == "trace" else ""
    result = EMAIL.sub(f"[EMAIL{suffix}]", text)
    result = _redact_aadhaar(result, f"[ID{suffix}]")
    result = INTERNATIONAL_PHONE.sub(f"[PHONE{suffix}]", result)
    result = _redact_mobile(result, f"[PHONE{suffix}]")
    return result
