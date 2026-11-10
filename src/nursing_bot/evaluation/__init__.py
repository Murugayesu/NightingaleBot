"""Evaluation against eval/evalset.csv. Heavy modules are imported on use."""

from nursing_bot.evaluation.dataset import EvalCase, load_cases
from nursing_bot.evaluation.metrics import (
    fact_recall,
    first_hit_rank,
    suggest_threshold,
)

__all__ = [
    "EvalCase",
    "fact_recall",
    "first_hit_rank",
    "load_cases",
    "suggest_threshold",
]
