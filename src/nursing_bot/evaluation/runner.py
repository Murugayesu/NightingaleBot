"""Evaluation runner: measure retrieval and answers against eval/evalset.csv.

Two modes, run them in this order:

retrieval (no LLM calls, cheap, run on every change)
    For each answerable question: is a chunk from the expected source file in
    the top-k? Reports hit rate and MRR. It also records the gate signal
    (best similarity) for answerable vs unanswerable questions and suggests
    the gate threshold that separates them best.

answer (one pipeline call per question, plus judge calls if enabled)
    answer cases:   not abstained, ≥ 50% of expected facts present, and a
                    citation points at an expected source file
    abstain cases:  the bot abstained
    refuse cases:   the bot refused or abstained
    Optional LLM-judge metrics: faithfulness and answer relevancy.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.evaluation.dataset import EvalCase, load_cases
from nursing_bot.evaluation.judge import LLMJudge
from nursing_bot.evaluation.metrics import (
    fact_recall,
    first_hit_rank,
    same_document,
    suggest_threshold,
)
from nursing_bot.generation.pipeline import GenerationPipeline
from nursing_bot.retrieval.gate import RelevanceGate
from nursing_bot.retrieval.retriever import PostgresHybridRetriever
from nursing_bot.services import get_llm_client
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import EvalRun

logger = logging.getLogger(__name__)

FACT_RECALL_TO_PASS = 0.5


@dataclass
class EvalOutcome:
    metrics: dict[str, Any]
    results: list[dict[str, Any]] = field(default_factory=list)


def build_retriever() -> PostgresHybridRetriever:
    s = get_settings()
    return PostgresHybridRetriever(
        llm_client=get_llm_client(),
        top_k=s.RETRIEVAL_TOP_K,
        rrf_k=s.RRF_K,
        rerank_enabled=s.RERANK_ENABLED,
        rerank_top_n=s.RERANK_TOP_K,
        patient_embeddings=True,
    )


def build_pipeline() -> GenerationPipeline:
    s = get_settings()
    return GenerationPipeline(
        retriever=build_retriever(),
        llm_client=get_llm_client(),
        gate=RelevanceGate(s.GATE_THRESHOLD_SIMILARITY, s.GATE_THRESHOLD_RERANK),
        default_top_k=s.RETRIEVAL_TOP_K,
    )


def _errored(case: EvalCase, exc: Exception) -> dict[str, Any]:
    """A case that crashed (provider outage, timeout ...) counts as failed, with the reason."""
    logger.warning("Eval case %s errored: %s: %s", case.id, type(exc).__name__, exc)
    return {
        "case_id": case.id,
        "category": case.category,
        "question": case.question,
        "expected_behavior": case.expected_behavior,
        "passed": False,
        "errored": True,
        "gate_passed": False,
        "detail": f"error: {type(exc).__name__}: {str(exc)[:200]}",
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


# ---------------------------------------------------------------- retrieval
async def evaluate_retrieval(
    session: AsyncSession,
    retriever: PostgresHybridRetriever,
    cases: list[EvalCase],
    gate: RelevanceGate,
    top_k: int | None = None,
) -> EvalOutcome:
    results: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    answerable_scores: list[float] = []
    unanswerable_scores: list[float] = []

    for case in cases:
        try:
            retrieval = await retriever.aretrieve(case.question, session, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - one bad case must not abort the run
            await session.rollback()
            results.append(_errored(case, exc))
            if case.should_answer:
                reciprocal_ranks.append(0.0)  # an error counts as a miss
            continue
        titles = [d.metadata.get("title", "") for d in retrieval.documents]
        signal = (
            retrieval.top_rerank
            if retrieval.top_rerank is not None
            else retrieval.top_similarity
        )
        score = float(signal) if signal is not None else 0.0
        gate_passed = gate.evaluate(
            retrieval.top_similarity, retrieval.top_rerank
        ).passed

        if case.should_answer:
            answerable_scores.append(score)
            rank = (
                first_hit_rank(case.source_files, titles) if case.source_files else None
            )
            reciprocal_ranks.append(1.0 / rank if rank else 0.0)
            passed = rank is not None
            detail = (
                f"hit at rank {rank}"
                if rank
                else f"expected {case.source_files}, got {titles[:3]}"
            )
        else:
            unanswerable_scores.append(score)
            rank = None
            passed = not gate_passed
            detail = "gate blocked" if passed else f"gate passed (score {score:.3f})"

        results.append(
            {
                "case_id": case.id,
                "category": case.category,
                "question": case.question,
                "expected_behavior": case.expected_behavior,
                "passed": passed,
                "rank": rank,
                "gate_score": round(score, 4),
                "gate_passed": gate_passed,
                "detail": detail,
            }
        )
    await session.commit()

    answer_results = [r for r in results if r["expected_behavior"] == "answer"]
    other_results = [r for r in results if r["expected_behavior"] != "answer"]
    metrics: dict[str, Any] = {
        "cases": len(results),
        "errored_cases": sum(1 for r in results if r.get("errored")),
        "hit_rate": _mean([float(r["passed"]) for r in answer_results]),
        "mrr": _mean(reciprocal_ranks),
        "answerable_gate_pass_rate": _mean(
            [float(r["gate_passed"]) for r in answer_results]
        ),
        "unanswerable_gate_block_rate": _mean(
            [float(not r["gate_passed"]) for r in other_results]
        ),
        "gate_signal": "rerank" if retriever.rerank_enabled else "similarity",
        "suggested_threshold": suggest_threshold(
            answerable_scores, unanswerable_scores
        ),
    }
    return EvalOutcome(metrics=metrics, results=results)


# ------------------------------------------------------------------ answers
async def evaluate_answers(
    session: AsyncSession,
    pipeline: GenerationPipeline,
    cases: list[EvalCase],
    use_judge: bool = False,
) -> EvalOutcome:
    judge = LLMJudge(pipeline.llm_client) if use_judge else None
    results: list[dict[str, Any]] = []
    faithfulness: list[float] = []
    relevancy: list[float] = []

    for case in cases:
        try:
            response = await pipeline.aquery(session=session, query=case.question)
        except Exception as exc:  # noqa: BLE001 - one bad case must not abort the run
            await session.rollback()
            results.append(_errored(case, exc))
            continue
        abstained = not (response.gate_passed and response.is_grounded)
        row: dict[str, Any] = {
            "case_id": case.id,
            "category": case.category,
            "question": case.question,
            "expected_behavior": case.expected_behavior,
            "answer": response.answer,
            "abstained": abstained,
        }
        if case.should_answer:
            recall = fact_recall(case.expected_facts, response.answer)
            cited_titles = [c.get("title", "") for c in response.citations]
            cites_source = not case.source_files or any(
                same_document(t, s) for t in cited_titles for s in case.source_files
            )
            row["fact_recall"] = round(recall, 4)
            row["cites_expected_source"] = cites_source
            row["passed"] = (
                (not abstained) and recall >= FACT_RECALL_TO_PASS and cites_source
            )
            row["detail"] = (
                "abstained"
                if abstained
                else f"facts {recall:.0%}, cites expected source: {'yes' if cites_source else 'no'}"
            )
            if judge is not None and not abstained:
                try:
                    scores = await judge.score(
                        case.question, response.answer, response.context_text
                    )
                except Exception as exc:  # noqa: BLE001 - judge trouble must not abort the run
                    logger.warning(
                        "Judge failed on %s: %s: %s", case.id, type(exc).__name__, exc
                    )
                    row["judge_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                else:
                    row["faithfulness"] = scores.faithfulness
                    row["answer_relevancy"] = scores.answer_relevancy
                    if scores.faithfulness is not None:
                        faithfulness.append(scores.faithfulness)
                    if scores.answer_relevancy is not None:
                        relevancy.append(scores.answer_relevancy)
        else:
            row["passed"] = abstained
            row["detail"] = (
                "correctly declined"
                if abstained
                else f"answered instead of {case.expected_behavior}"
            )
        results.append(row)
    await session.commit()

    def rate(behavior: str) -> float | None:
        return _mean(
            [float(r["passed"]) for r in results if r["expected_behavior"] == behavior]
        )

    metrics: dict[str, Any] = {
        "cases": len(results),
        "errored_cases": sum(1 for r in results if r.get("errored")),
        "pass_rate": _mean([float(r["passed"]) for r in results]),
        "answer_pass_rate": rate("answer"),
        "abstain_pass_rate": rate("abstain"),
        "refuse_pass_rate": rate("refuse"),
        "mean_fact_recall": _mean(
            [r["fact_recall"] for r in results if "fact_recall" in r]
        ),
    }
    if use_judge:
        metrics["faithfulness"] = _mean(faithfulness)
        metrics["answer_relevancy"] = _mean(relevancy)
    return EvalOutcome(metrics=metrics, results=results)


# ------------------------------------------------------------ orchestration
async def run_evaluation(
    mode: str, use_judge: bool = False, cases: list[EvalCase] | None = None
) -> EvalOutcome:
    settings = get_settings()
    cases = cases if cases is not None else load_cases(settings.EVAL_DATASET_PATH)
    async with get_session() as session:
        if mode == "retrieval":
            return await evaluate_retrieval(
                session,
                build_retriever(),
                cases,
                RelevanceGate(
                    settings.GATE_THRESHOLD_SIMILARITY, settings.GATE_THRESHOLD_RERANK
                ),
            )
        if mode == "answer":
            return await evaluate_answers(
                session, build_pipeline(), cases, use_judge=use_judge
            )
    raise ValueError(f"Unknown eval mode '{mode}'")


def _run_config(mode: str, use_judge: bool) -> dict[str, Any]:
    s = get_settings()
    return {
        "mode": mode,
        "judge": use_judge,
        "dataset": s.EVAL_DATASET_PATH,
        "llm_model": s.LLM_MODEL,
        "embedding_model": s.EMBEDDING_MODEL,
        "top_k": s.RETRIEVAL_TOP_K,
        "gate_threshold_similarity": s.GATE_THRESHOLD_SIMILARITY,
        "rerank_enabled": s.RERANK_ENABLED,
        "chunk_size": s.CHUNK_SIZE,
        "contextual_headers": s.CONTEXTUAL_HEADERS_ENABLED,
    }


async def create_eval_run(
    session: AsyncSession, mode: str, use_judge: bool = False
) -> EvalRun:
    run = EvalRun(
        id=uuid.uuid4(),
        mode=mode,
        status="queued",
        config_json=_run_config(mode, use_judge),
    )
    session.add(run)
    await session.commit()
    return run


async def execute_eval_run(run_id: uuid.UUID) -> EvalRun:
    """Run a stored EvalRun and save its metrics and per-case results."""
    async with get_session() as session:
        run = await session.get(EvalRun, run_id)
        if run is None:
            raise ValueError(f"Eval run {run_id} not found")
        run.status = "running"
        await session.commit()
        mode, use_judge = run.mode, bool(run.config_json.get("judge"))

    try:
        outcome = await run_evaluation(mode, use_judge=use_judge)
        status, error = "completed", None
    except Exception as exc:
        logger.exception("Eval run %s failed", run_id)
        outcome, status, error = (
            EvalOutcome(metrics={}),
            "failed",
            f"{type(exc).__name__}: {exc}",
        )

    async with get_session() as session:
        run = await session.get(EvalRun, run_id)
        assert run is not None
        run.status = status
        run.error = error
        run.metrics_json = outcome.metrics
        run.results_json = outcome.results
        run.finished_at = datetime.now(UTC)
        await session.commit()
        return run


__all__ = [
    "EvalOutcome",
    "create_eval_run",
    "evaluate_answers",
    "evaluate_retrieval",
    "execute_eval_run",
    "run_evaluation",
]
