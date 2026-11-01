from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.evaluation.golden_set import GOLDEN_BENCHMARK_CASES, GoldenTestCase
from nursing_bot.generation.pipeline import GenerationPipeline


@dataclass
class TestCaseResult:
    case_id: str
    category: str
    question: str
    answer: str
    passed: bool
    should_abstain: bool
    did_abstain: bool
    keyword_match_rate: float
    notes: str = ""


@dataclass
class EvalReport:
    total_cases: int
    passed_cases: int
    accuracy_rate: float
    abstention_accuracy: float
    average_keyword_match: float
    results: list[TestCaseResult] = field(default_factory=list)


class EvaluationRunner:
    """Automated benchmark evaluation runner validating retrieval accuracy and abstention compliance."""

    def __init__(self, pipeline: GenerationPipeline):
        self.pipeline = pipeline

    async def run(
        self,
        session: AsyncSession,
        test_cases: list[GoldenTestCase] = GOLDEN_BENCHMARK_CASES,
    ) -> EvalReport:
        results: list[TestCaseResult] = []
        abstention_correct = 0
        abstention_total = 0
        keyword_matches: list[float] = []

        for case in test_cases:
            filters = (
                {"doc_type": case.expected_doc_type} if case.expected_doc_type else None
            )
            response = await self.pipeline.aquery(
                session=session,
                query=case.question,
                filters=filters,
            )

            did_abstain = (not response.gate_passed) or (not response.is_grounded)

            if case.should_abstain:
                abstention_total += 1
                case_passed = did_abstain
                if case_passed:
                    abstention_correct += 1
                match_rate = 1.0 if did_abstain else 0.0
            else:
                lower_answer = response.answer.lower()
                matched = [
                    kw
                    for kw in case.expected_answer_keywords
                    if kw.lower() in lower_answer
                ]
                match_rate = (
                    len(matched) / len(case.expected_answer_keywords)
                    if case.expected_answer_keywords
                    else 1.0
                )
                case_passed = (not did_abstain) and (match_rate >= 0.5)

            keyword_matches.append(match_rate)
            results.append(
                TestCaseResult(
                    case_id=case.id,
                    category=case.category,
                    question=case.question,
                    answer=response.answer,
                    passed=case_passed,
                    should_abstain=case.should_abstain,
                    did_abstain=did_abstain,
                    keyword_match_rate=match_rate,
                )
            )

        passed_count = sum(1 for r in results if r.passed)
        total = len(test_cases)
        return EvalReport(
            total_cases=total,
            passed_cases=passed_count,
            accuracy_rate=passed_count / total if total > 0 else 0.0,
            abstention_accuracy=abstention_correct / abstention_total
            if abstention_total > 0
            else 1.0,
            average_keyword_match=sum(keyword_matches) / len(keyword_matches)
            if keyword_matches
            else 0.0,
            results=results,
        )
