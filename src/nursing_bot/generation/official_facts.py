"""Check a general-knowledge answer for anything that looks like an official fact.

A general answer is never cited, so it must not state what only an official
source can: money amounts, calendar dates, phone numbers, e-mail addresses,
web links, or claims about what the council or an institution requires. When
any of these appear, the answer is withdrawn and the question is declined.

Ordinary clinical numbers ("120/80 mmHg", "every 4 hours", "37 °C", "5
rights", "1860") are not matched.
"""

import re
from dataclasses import dataclass

_MONTHS = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

_CHECKS: dict[str, re.Pattern[str]] = {
    "money amount": re.compile(
        r"₹|\brs\.?\s*\d|\binr\s*\d|\b\d[\d,]*\s*(?:rupees|rs\b|inr\b|/-)"
        r"|\brupees\s+\d|\$\s*\d",
        re.IGNORECASE,
    ),
    "calendar date": re.compile(
        r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})\b"
        rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}\.?,?\s+\d{{4}}\b"
        rf"|\b{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
        re.IGNORECASE,
    ),
    "phone number": re.compile(
        r"(?:\+91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b"
        r"|\b0\d{2,4}[\s-]\d{6,8}\b"
        r"|\b1800[\s-]?\d{3}[\s-]?\d{3,4}\b"
    ),
    "e-mail address": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "web link": re.compile(
        r"\bhttps?://|\bwww\.\S+|\b[\w-]+\.(?:gov|nic|ac|org|edu)\.in\b",
        re.IGNORECASE,
    ),
    "official requirement": re.compile(
        r"\b(?:tnnmc|indian nursing council|nursing council|the council|"
        r"\binc\b|the registrar|the university|the government)\b[^.\n]{0,80}?"
        r"\b(?:requires?|required|mandates?|mandatory|charges?|stipulates?|"
        r"prescribes?|fixes|fixed|allows?|permits?|deadline|last date|"
        r"eligib\w+|fees?)\b",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True)
class OfficialFactsResult:
    passed: bool
    findings: tuple[str, ...] = ()

    @property
    def notes(self) -> str:
        if self.passed:
            return "No official-looking facts found."
        return "General answer contained official-looking facts: " + ", ".join(
            self.findings
        )


def check_general_answer(answer: str) -> OfficialFactsResult:
    findings = tuple(
        name for name, pattern in _CHECKS.items() if pattern.search(answer)
    )
    return OfficialFactsResult(passed=not findings, findings=findings)
