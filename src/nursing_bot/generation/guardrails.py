"""Inbound guardrails: length check, prompt-injection screen, PII redaction.

The injection screen is a speed bump, not a wall: a determined user can
rephrase around any phrase list. The real protection is structural. The model
may only answer from retrieved official text, every answer must cite it, and
uncited answers are replaced (see verifier.py).
"""

import re
from dataclasses import dataclass

from nursing_bot import privacy

MAX_QUERY_LENGTH = 4000

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above)\s+(?:instructions|rules|prompts?)",
        r"disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above|system)\s+(?:instructions|rules|prompts?)",
        r"(?:reveal|show|print|repeat|tell\s+me)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)",
        r"system\s+prompt\s+override",
        r"you\s+are\s+now\s+(?:dan|in\s+developer\s+mode|jailbroken)",
        r"bypass\s+(?:all\s+|your\s+)?(?:guardrails|safety|restrictions|filters)",
        r"act\s+as\s+(?:an?\s+)?(?:unrestricted|unfiltered)",
    )
]


@dataclass
class GuardrailDecision:
    is_safe: bool
    sanitized_query: str
    rejection_reason: str | None = None


class InputGuard:
    MAX_QUERY_LENGTH = MAX_QUERY_LENGTH

    @classmethod
    def looks_like_injection(cls, text: str) -> bool:
        return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)

    @classmethod
    def check_and_sanitize(cls, query: str) -> GuardrailDecision:
        trimmed = query.strip()
        if not trimmed:
            return GuardrailDecision(False, "", "Query is empty.")
        if len(trimmed) > cls.MAX_QUERY_LENGTH:
            return GuardrailDecision(
                False,
                "",
                f"Query exceeds the maximum length of {cls.MAX_QUERY_LENGTH} characters.",
            )
        if cls.looks_like_injection(trimmed):
            return GuardrailDecision(
                False,
                "",
                "I can only answer questions about the council's official information.",
            )
        return GuardrailDecision(True, privacy.redact(trimmed))

    @classmethod
    def sanitize_history(
        cls, history: list[dict[str, str]] | None
    ) -> list[dict[str, str]]:
        """Client-supplied history is untrusted: redact PII and drop injection attempts."""
        clean: list[dict[str, str]] = []
        for turn in history or []:
            role, content = turn.get("role"), (turn.get("content") or "").strip()
            if role not in ("user", "assistant") or not content:
                continue
            if cls.looks_like_injection(content):
                continue
            clean.append({"role": role, "content": privacy.redact(content)})
        return clean


def expand_synonyms(query: str, synonyms: dict[str, str]) -> str:
    """Expand domain abbreviations, e.g. {"GNM": "General Nursing and Midwifery (GNM)"}."""
    expanded = query
    for key, replacement in synonyms.items():
        if key and replacement:
            expanded = re.sub(
                rf"\b{re.escape(key)}\b", replacement, expanded, flags=re.IGNORECASE
            )
    return expanded
