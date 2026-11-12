"""Deterministic stand-ins for the LLM provider and object storage.

FakeLLMClient embeds text as a hashed bag of words, so texts that share words
get similar vectors. That makes retrieval tests behave like the real thing
(relevant chunks rank first, unrelated questions score low) with no network.
"""

import hashlib
import json
import math
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from nursing_bot.config import VECTOR_COLUMN_DIMENSIONS
from nursing_bot.models.litellm_client import LiteLLMClient, RerankResult, StreamUsage

_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "is",
    "for",
    "to",
    "in",
    "and",
    "what",
    "how",
    "do",
    "i",
    "my",
}


def hash_embed(text: str, dims: int = VECTOR_COLUMN_DIMENSIONS) -> list[float]:
    vector = [0.0] * dims
    for word in _WORD.findall(text.lower()):
        if word in _STOP:
            continue
        bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % dims
        vector[bucket] += 1.0
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class FakeLLMClient(LiteLLMClient):
    """Overrides every provider call of LiteLLMClient; never touches the network."""

    def __init__(
        self, answer: str | Callable[[str, str | None], str] = "Answer [Source 1]."
    ):
        super().__init__(model="fake/llm", embedding_model="test/hash-embed")
        self.model = "fake/llm"
        self.embedding_model = "test/hash-embed"
        self.answer = answer
        self.prompts: list[tuple[str, str | None]] = []
        self.embed_calls = 0

    def _answer_for(self, prompt: str, system_prompt: str | None) -> str:
        self.prompts.append((prompt, system_prompt))
        return (
            self.answer(prompt, system_prompt) if callable(self.answer) else self.answer
        )

    async def acomplete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        **_: Any,
    ) -> str:
        return self._answer_for(prompt, system_prompt)

    async def acomplete_with_usage(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        **_: Any,
    ) -> tuple[str, int]:
        text = self._answer_for(prompt, system_prompt)
        return text, self.count_tokens(prompt) + self.count_tokens(text)

    async def astream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        usage: StreamUsage | None = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        text = self._answer_for(prompt, system_prompt)
        for piece in re.findall(r"\S+\s*", text):
            yield piece
        if usage is not None:
            usage.prompt_tokens = self.count_tokens(prompt)
            usage.completion_tokens = self.count_tokens(text)

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4) if text else 0

    async def aembed_texts(
        self, texts: list[str], *, patient: bool = False
    ) -> list[list[float]]:
        self.embed_calls += 1
        return [hash_embed(t) for t in texts]

    async def aembed_query(self, query: str) -> list[float]:
        return hash_embed(query)

    async def arerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[RerankResult]:
        q = hash_embed(query)
        scored = [
            RerankResult(i, sum(a * b for a, b in zip(q, hash_embed(d), strict=True)))
            for i, d in enumerate(documents)
        ]
        scored.sort(key=lambda r: r.relevance_score, reverse=True)
        return scored[:top_n] if top_n else scored


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def upload(
        self, key: str, data: bytes, content_type: str | None = None
    ) -> str:
        self.objects[key] = data
        return f"s3://test/{key}"

    async def download(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def check(self) -> None:
        return None


def cited_answer_from_prompt(prompt: str, system_prompt: str | None = None) -> str:
    """A well-behaved fake model: quotes the sentence of Source 1 that best
    matches the question, with a citation."""
    question = prompt.rsplit("Question:", 1)[-1].split("\n", 1)[0]
    source_block = prompt.split("[Source 1]", 1)[-1].split("[Source 2]", 1)[0]
    source_block = source_block.split("Question:", 1)[0]
    body_lines = [
        line
        for line in source_block.split("\n")[1:]
        if line.strip() and not line.startswith("[")
    ]
    sentences = [
        s.strip()
        for s in re.split(r"(?<=\.)\s+(?=[A-Z])", " ".join(body_lines))
        if s.strip()
    ]
    if not sentences:
        return "The information is not available in the records."
    q_words = set(_WORD.findall(question.lower())) - _STOP
    best = max(sentences, key=lambda s: len(q_words & set(_WORD.findall(s.lower()))))
    return f"{best.rstrip('.')} [Source 1]."


OFF_TOPIC_WORDS = {"biryani", "recipe", "cricket", "movie", "python", "election"}


def route_for(question: str) -> str:
    """The fake classifier: off-topic words, nursing concepts, else official."""
    words = set(_WORD.findall(question.lower()))
    if words & OFF_TOPIC_WORDS:
        return "out_of_scope"
    if words & {"explain", "define", "what", "describe", "why"}:
        return "general_nursing"
    return "official_specific"


def smart_fake_answer(prompt: str, system_prompt: str | None = None) -> str:
    """A fake model that plays every role the app asks of it."""
    system = system_prompt or ""
    if system.startswith("You route questions"):
        question = json.loads(prompt)["question"]
        return json.dumps({"category": route_for(question)})
    if system.startswith("Write a short title"):
        return "Nursing registration question"
    if "running summary" in system:
        return "The user asked about nurse registration."
    if system.startswith("You are an educational nursing assistant"):
        question = prompt.split("Question:", 1)[-1].split("\n", 1)[0].strip()
        return f"In general nursing practice, {question.rstrip('?').lower()} is explained in textbooks."
    return cited_answer_from_prompt(prompt, system_prompt)


class FakeSession:
    """Stands in for an AsyncSession where only commit() is called."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1
