import re
from dataclasses import dataclass

_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)
_PHONE_PATTERN = re.compile(
    r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt\s+override", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+dan", re.IGNORECASE),
    re.compile(r"bypass\s+all\s+guardrails", re.IGNORECASE),
    re.compile(r"show\s+me\s+(?:your\s+)?system\s+prompt", re.IGNORECASE),
]


@dataclass
class GuardrailDecision:
    is_safe: bool
    sanitized_query: str
    rejection_reason: str | None = None


class InputGuard:
    """Inbound safety validator and PII sanitizer."""

    MAX_QUERY_LENGTH: int = 4000

    @classmethod
    def check_and_sanitize(cls, query: str) -> GuardrailDecision:
        trimmed = query.strip()
        if not trimmed:
            return GuardrailDecision(
                is_safe=False,
                sanitized_query="",
                rejection_reason="Query is empty.",
            )

        if len(trimmed) > cls.MAX_QUERY_LENGTH:
            return GuardrailDecision(
                is_safe=False,
                sanitized_query="",
                rejection_reason=f"Query exceeds maximum character limit of {cls.MAX_QUERY_LENGTH}.",
            )

        for pattern in _INJECTION_PATTERNS:
            if pattern.search(trimmed):
                return GuardrailDecision(
                    is_safe=False,
                    sanitized_query="",
                    rejection_reason="Query flagged by security policy.",
                )

        sanitized = _EMAIL_PATTERN.sub("[EMAIL]", trimmed)
        sanitized = _PHONE_PATTERN.sub("[PHONE]", sanitized)

        return GuardrailDecision(
            is_safe=True,
            sanitized_query=sanitized,
            rejection_reason=None,
        )
