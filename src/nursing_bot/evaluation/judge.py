"""LLM-as-judge metrics, implementing the same ideas as the Ragas library.

* faithfulness: share of the answer's claims that the retrieved sources support
  (Ragas `Faithfulness`). Catches hallucinations that still carry citations.
* answer_relevancy: generate questions the answer would answer, embed them and
  average their cosine similarity to the real question (Ragas `ResponseRelevancy`).
  Catches answers that are true but off-topic.

Why not import Ragas itself? ragas 0.4.3, the newest release, fails to import
with the LangChain versions this project pins. These are about 80 lines, run
through our own LiteLLM client (any provider), and are easy to read.
"""

import json
import math
from dataclasses import dataclass

from nursing_bot.models.litellm_client import LiteLLMClient

_FAITHFULNESS_PROMPT = """Sources:
{sources}

Answer to check:
{answer}

Split the answer into its individual factual claims (ignore [Source N] tags).
For each claim decide whether the sources explicitly support it.
Reply with JSON only: {{"claims": [{{"claim": "...", "supported": true|false}}]}}"""

_QUESTIONS_PROMPT = """Answer:
{answer}

Write 3 different questions that this answer responds to.
Reply with JSON only: {{"questions": ["...", "...", "..."]}}"""


@dataclass
class JudgeScores:
    faithfulness: float | None
    answer_relevancy: float | None


def _json(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class LLMJudge:
    def __init__(self, llm_client: LiteLLMClient):
        self.llm = llm_client

    async def faithfulness(self, answer: str, sources: str) -> float | None:
        raw = await self.llm.acomplete(
            _FAITHFULNESS_PROMPT.format(sources=sources, answer=answer), temperature=0.0
        )
        claims = [c for c in _json(raw).get("claims", []) if isinstance(c, dict)]
        if not claims:
            return None
        return sum(bool(c.get("supported")) for c in claims) / len(claims)

    async def answer_relevancy(self, question: str, answer: str) -> float | None:
        raw = await self.llm.acomplete(
            _QUESTIONS_PROMPT.format(answer=answer), temperature=0.0
        )
        generated = [
            q
            for q in _json(raw).get("questions", [])
            if isinstance(q, str) and q.strip()
        ]
        if not generated:
            return None
        vectors = await self.llm.aembed_texts([question, *generated])
        return sum(_cosine(vectors[0], v) for v in vectors[1:]) / len(generated)

    async def score(self, question: str, answer: str, sources: str) -> JudgeScores:
        return JudgeScores(
            faithfulness=await self.faithfulness(answer, sources),
            answer_relevancy=await self.answer_relevancy(question, answer),
        )
