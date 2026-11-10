"""The only module that talks to AI providers. Everything goes through LiteLLM.

LiteLLM gives one API for OpenAI, Anthropic, Gemini, Mistral, Cohere, Voyage,
Jina and many others. The rest of the code only sees this small class:

    acomplete()   prompt  → (answer text, tokens used)
    astream()     prompt  → answer text pieces (usage reported via StreamUsage)
    aembed_texts  texts   → vectors (always VECTOR_COLUMN_DIMENSIONS long)
    arerank()     query + documents → relevance scores

Change provider with environment variables only; no code changes needed.
If LLM_BASE_URL points at a LiteLLM proxy server, this works unchanged.
"""

import asyncio
import logging
import math
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

# Use the cost map bundled with the package instead of downloading it at import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm

from nursing_bot.config import VECTOR_COLUMN_DIMENSIONS

litellm.drop_params = True  # silently drop params a provider doesn't support
litellm.telemetry = False

logger = logging.getLogger(__name__)


@dataclass
class StreamUsage:
    """Filled in by `astream` once the stream ends."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class RerankResult:
    index: int
    relevance_score: float


def fit_vector(
    vector: list[float], size: int = VECTOR_COLUMN_DIMENSIONS
) -> list[float]:
    """Make a vector exactly `size` long without changing cosine similarity.

    Longer: truncate then renormalise (valid for Matryoshka-trained models like
    OpenAI text-embedding-3 and Gemini; for others prefer a native size <= size).
    Shorter: pad with zeros, which leaves norms and dot products unchanged.
    """
    if len(vector) > size:
        head = vector[:size]
        norm = math.sqrt(sum(x * x for x in head))
        return [x / norm for x in head] if norm > 0 else head
    if len(vector) < size:
        return vector + [0.0] * (size - len(vector))
    return vector


def _field(item: Any, key: str) -> Any:
    """Read `key` from a dict or an object (providers return either)."""
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _with_provider_prefix(model: str, base_url: str | None) -> str:
    """A bare model name behind a custom base URL is treated as OpenAI-compatible."""
    return f"openai/{model}" if "/" not in model and base_url else model


class LiteLLMClient:
    def __init__(
        self,
        model: str,
        embedding_model: str,
        rerank_model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        embedding_base_url: str | None = None,
        embedding_api_key: str | None = None,
        embedding_dimensions: int | None = None,
        rerank_api_key: str | None = None,
        rerank_base_url: str | None = None,
        max_tokens: int | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        rate_limit_retries: int = 6,
        rate_limit_base_delay: float = 5.0,
    ):
        self.base_url = base_url or None
        self.api_key = api_key or None
        self.model = _with_provider_prefix(model, self.base_url)

        self.embedding_base_url = embedding_base_url or self.base_url
        self.embedding_api_key = embedding_api_key or self.api_key
        self.embedding_model = _with_provider_prefix(
            embedding_model, self.embedding_base_url
        )
        self.embedding_dimensions = embedding_dimensions

        # Reranking usually runs at a different provider than the LLM, so it
        # never inherits the LLM's key or base URL.
        self.rerank_model = rerank_model
        self.rerank_api_key = rerank_api_key or None
        self.rerank_base_url = rerank_base_url or None

        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limit_retries = rate_limit_retries
        self.rate_limit_base_delay = rate_limit_base_delay

    # ------------------------------------------------------------------ chat
    def _chat_kwargs(
        self, prompt: str, system_prompt: str | None, temperature: float, **extra: Any
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {
            "model": extra.pop("model", None) or self.model,
            "messages": messages,
            "temperature": temperature,
            "timeout": self.timeout,
            "num_retries": self.max_retries,
            **extra,
        }
        if self.max_tokens and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.max_tokens
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url
        return kwargs

    async def acomplete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        text, _ = await self.acomplete_with_usage(
            prompt, system_prompt, temperature, **kwargs
        )
        return text

    async def acomplete_with_usage(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> tuple[str, int]:
        """Return (answer text, total tokens used)."""
        response = await litellm.acompletion(
            **self._chat_kwargs(prompt, system_prompt, temperature, **kwargs)
        )
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        total = int(getattr(usage, "total_tokens", 0) or 0)
        return text, total

    async def astream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        usage: StreamUsage | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Yield answer text pieces. Pass a StreamUsage to receive token counts."""
        call_kwargs = self._chat_kwargs(
            prompt,
            system_prompt,
            temperature,
            stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )
        response_stream = await litellm.acompletion(**call_kwargs)
        pieces: list[str] = []
        reported = False
        async for chunk in response_stream:
            chunk_usage = getattr(chunk, "usage", None)
            if (
                usage is not None
                and chunk_usage
                and getattr(chunk_usage, "total_tokens", 0)
            ):
                usage.prompt_tokens = int(chunk_usage.prompt_tokens or 0)
                usage.completion_tokens = int(chunk_usage.completion_tokens or 0)
                reported = True
            choices = getattr(chunk, "choices", None)
            if choices:
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta else None
                if content:
                    pieces.append(content)
                    yield content
        if usage is not None and not reported:
            # Provider didn't report usage while streaming: estimate it.
            usage.prompt_tokens = self.count_tokens(
                "\n".join(m["content"] for m in call_kwargs["messages"])
            )
            usage.completion_tokens = self.count_tokens("".join(pieces))

    def count_tokens(self, text: str) -> int:
        """Token count for budgeting; falls back to ~4 chars/token if unknown."""
        if not text:
            return 0
        try:
            return int(litellm.token_counter(model=self.model, text=text))
        except Exception:  # noqa: BLE001
            return max(1, len(text) // 4)

    # ------------------------------------------------------------ embeddings
    async def aembed_texts(
        self, texts: list[str], *, patient: bool = False
    ) -> list[list[float]]:
        """Embed texts. `patient=True` (bulk ingestion) rides out provider rate
        limits with exponential back-off instead of failing the whole document;
        interactive calls fail fast so no user waits minutes for an answer."""
        if not texts:
            return []
        attempts = self.rate_limit_retries if patient else 0
        for attempt in range(attempts + 1):
            try:
                return await self._embed_once(texts)
            except litellm.RateLimitError:
                if attempt == attempts:
                    raise
                delay = min(60.0, self.rate_limit_base_delay * 2**attempt)
                logger.warning(
                    "Embedding rate-limited; retry %d/%d in %.0fs",
                    attempt + 1,
                    attempts,
                    delay,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _embed_once(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict[str, Any] = {
            "model": self.embedding_model,
            "input": texts,
            "timeout": self.timeout,
            "num_retries": self.max_retries,
        }
        if self.embedding_dimensions:
            kwargs["dimensions"] = self.embedding_dimensions
        if self.embedding_api_key:
            kwargs["api_key"] = self.embedding_api_key
        if self.embedding_base_url:
            kwargs["api_base"] = self.embedding_base_url

        response = await litellm.aembedding(**kwargs)
        vectors = [fit_vector(list(item["embedding"])) for item in response.data]
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding provider returned {len(vectors)} vectors for {len(texts)} texts."
            )
        return vectors

    async def aembed_query(self, query: str) -> list[float]:
        return (await self.aembed_texts([query]))[0]

    # --------------------------------------------------------------- rerank
    async def arerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[RerankResult]:
        """Cross-encoder relevance scores, best first."""
        if not documents:
            return []
        if not self.rerank_model:
            raise RuntimeError("Reranking requested but RERANK_MODEL is not set.")
        kwargs: dict[str, Any] = {
            "model": self.rerank_model,
            "query": query,
            "documents": documents,
            "timeout": self.timeout,
            "num_retries": self.max_retries,
        }
        if top_n is not None:
            kwargs["top_n"] = top_n
        if self.rerank_api_key:
            kwargs["api_key"] = self.rerank_api_key
        if self.rerank_base_url:
            kwargs["api_base"] = self.rerank_base_url

        response = await litellm.arerank(**kwargs)
        raw = getattr(response, "results", None) or []
        results: list[RerankResult] = []
        for item in raw:
            index, score = _field(item, "index"), _field(item, "relevance_score")
            if (
                isinstance(index, int)
                and 0 <= index < len(documents)
                and score is not None
            ):
                results.append(RerankResult(index=index, relevance_score=float(score)))
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results
