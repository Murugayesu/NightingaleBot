import os
from collections.abc import AsyncIterator
from typing import Any

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm

litellm.drop_params = True
litellm.telemetry = False


class LiteLLMClient:
    """Unified LLM, Embedding, and Reranking gateway client using LiteLLM."""

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
    ):
        if "/" not in model and base_url:
            self.model = f"openai/{model}"
        else:
            self.model = model

        eff_embed_base = embedding_base_url or base_url
        if "/" not in embedding_model and eff_embed_base:
            self.embedding_model = f"openai/{embedding_model}"
        else:
            self.embedding_model = embedding_model

        self.rerank_model = rerank_model
        self.api_key = api_key
        self.base_url = base_url
        self.embedding_base_url = embedding_base_url
        self.embedding_api_key = embedding_api_key or api_key
        self.embedding_dimensions = embedding_dimensions

    async def acomplete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        call_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["base_url"] = self.base_url

        response = await litellm.acompletion(**call_kwargs)
        return response.choices[0].message.content or ""

    async def astream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        call_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["base_url"] = self.base_url

        response_stream = await litellm.acompletion(**call_kwargs)
        async for chunk in response_stream:
            if hasattr(chunk, "choices") and chunk.choices:
                delta = getattr(chunk.choices[0], "delta", None)
                if delta and getattr(delta, "content", None):
                    yield delta.content

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        call_kwargs: dict[str, Any] = {
            "model": self.embedding_model,
            "input": texts,
        }
        if self.embedding_dimensions:
            call_kwargs["dimensions"] = self.embedding_dimensions
        if self.embedding_api_key:
            call_kwargs["api_key"] = self.embedding_api_key
        embed_base = self.embedding_base_url or self.base_url
        if embed_base:
            call_kwargs["api_base"] = embed_base

        response = await litellm.aembedding(**call_kwargs)
        results = []
        for item in response.data:
            emb = item["embedding"]
            if self.embedding_dimensions and len(emb) > self.embedding_dimensions:
                emb = emb[: self.embedding_dimensions]
                norm = sum(x * x for x in emb) ** 0.5
                if norm > 0:
                    emb = [x / norm for x in emb]
            results.append(emb)
        return results

    async def aembed_query(self, query: str) -> list[float]:
        vectors = await self.aembed_texts([query])
        return vectors[0]

    async def arerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Rerank documents using LiteLLM cross-encoder."""
        if not documents:
            return []

        call_kwargs: dict[str, Any] = {
            "model": self.rerank_model or self.model,
            "query": query,
            "documents": documents,
            **kwargs,
        }
        if top_n is not None:
            call_kwargs["top_n"] = top_n
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["api_base"] = self.base_url

        response = await litellm.arerank(**call_kwargs)
        results = getattr(response, "results", response)
        if isinstance(results, list):
            return results
        return list(results)
