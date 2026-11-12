"""Load eval/evalset.csv, the hand-verified test set (see eval/README.md)."""

import csv
from dataclasses import dataclass, field
from pathlib import Path

# answer        official question the documents answer: grounded, cited
# abstain       official question they do not answer: must be declined
# refuse        unsafe or abusive request: must not be answered
# general       general nursing knowledge: a labelled general answer
# out_of_scope  not about nursing: the scope message
BEHAVIORS = ("answer", "abstain", "refuse", "general", "out_of_scope")


@dataclass
class EvalCase:
    id: str
    category: str
    question: str
    expected_behavior: str  # answer | abstain | refuse | general | out_of_scope
    expected_facts: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    source_locator: str = ""
    notes: str = ""

    @property
    def should_answer(self) -> bool:
        return self.expected_behavior == "answer"


def _split(value: str, separator: str) -> list[str]:
    return [part.strip() for part in (value or "").split(separator) if part.strip()]


def load_cases(path: str | Path) -> list[EvalCase]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    cases = []
    for row in rows:
        behavior = (row.get("expected_behavior") or "").strip().lower()
        if behavior not in BEHAVIORS:
            raise ValueError(f"{row.get('id')}: unknown expected_behavior '{behavior}'")
        cases.append(
            EvalCase(
                id=row["id"].strip(),
                category=(row.get("category") or "").strip(),
                question=row["question"].strip(),
                expected_behavior=behavior,
                expected_facts=_split(row.get("expected_facts", ""), "|"),
                source_files=_split(row.get("source_file", ""), "+"),
                source_locator=(row.get("source_locator") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
        )
    return cases
