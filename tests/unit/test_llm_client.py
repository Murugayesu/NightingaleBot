from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nursing_bot.config import VECTOR_COLUMN_DIMENSIONS
from nursing_bot.models import litellm_client as module
from nursing_bot.models.litellm_client import LiteLLMClient, StreamUsage


def _client(**kwargs) -> LiteLLMClient:
    defaults = {
        "model": "openai/gpt-4o-mini",
        "embedding_model": "openai/text-embedding-3-small",
    }
    defaults.update(kwargs)
    return LiteLLMClient(**defaults)


async def test_completion_passes_timeout_retries_and_token_cap(monkeypatch):
    fake = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
            usage=SimpleNamespace(total_tokens=42),
        )
    )
    monkeypatch.setattr(module.litellm, "acompletion", fake)
    client = _client(api_key="k", max_tokens=256, timeout=9, max_retries=4)
    text, tokens = await client.acomplete_with_usage("q", system_prompt="s")
    kwargs = fake.call_args.kwargs
    assert (text, tokens) == ("hi", 42)
    assert (
        kwargs["timeout"] == 9
        and kwargs["num_retries"] == 4
        and kwargs["max_tokens"] == 256
    )
    assert kwargs["messages"][0] == {"role": "system", "content": "s"}
    assert kwargs["api_key"] == "k"


def test_bare_model_behind_base_url_gets_openai_prefix():
    client = _client(
        model="llama3", embedding_model="nomic", base_url="http://local:11434/v1"
    )
    assert client.model == "openai/llama3" and client.embedding_model == "openai/nomic"


def test_rerank_never_inherits_llm_credentials():
    client = _client(
        api_key="llm-key", base_url="http://gateway", rerank_model="cohere/rerank-v3.5"
    )
    assert client.rerank_api_key is None and client.rerank_base_url is None


async def test_rerank_uses_its_own_key_and_tolerates_missing_index(monkeypatch):
    fake = AsyncMock(
        return_value=SimpleNamespace(
            results=[
                {"index": 1, "relevance_score": 0.9},
                {"relevance_score": 0.8},  # malformed: no index
                SimpleNamespace(index=0, relevance_score=0.2),
            ]
        )
    )
    monkeypatch.setattr(module.litellm, "arerank", fake)
    client = _client(
        api_key="llm-key", rerank_model="cohere/rerank-v3.5", rerank_api_key="co-key"
    )
    results = await client.arerank("q", ["d0", "d1"], top_n=2)
    assert [(r.index, r.relevance_score) for r in results] == [(1, 0.9), (0, 0.2)]
    assert fake.call_args.kwargs["api_key"] == "co-key"


async def test_embeddings_are_fitted_to_column_size(monkeypatch):
    fake = AsyncMock(
        return_value=SimpleNamespace(
            data=[{"embedding": [1.0] * 1024}, {"embedding": [1.0] * 3072}]
        )
    )
    monkeypatch.setattr(module.litellm, "aembedding", fake)
    vectors = await _client(embedding_dimensions=None).aembed_texts(["a", "b"])
    assert [len(v) for v in vectors] == [VECTOR_COLUMN_DIMENSIONS] * 2


async def test_embedding_count_mismatch_raises(monkeypatch):
    monkeypatch.setattr(
        module.litellm, "aembedding", AsyncMock(return_value=SimpleNamespace(data=[]))
    )
    with pytest.raises(RuntimeError):
        await _client().aembed_texts(["a"])


async def test_stream_reports_usage(monkeypatch):
    async def chunks():
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"))], usage=None
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))], usage=None
        )
        yield SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=2, total_tokens=12
            ),
        )

    monkeypatch.setattr(module.litellm, "acompletion", AsyncMock(return_value=chunks()))
    usage = StreamUsage()
    pieces = [p async for p in _client().astream("q", usage=usage)]
    assert pieces == ["Hel", "lo"] and usage.total_tokens == 12


async def test_stream_estimates_usage_when_provider_is_silent(monkeypatch):
    async def chunks():
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello there"))],
            usage=None,
        )

    monkeypatch.setattr(module.litellm, "acompletion", AsyncMock(return_value=chunks()))
    usage = StreamUsage()
    _ = [p async for p in _client().astream("question", usage=usage)]
    assert usage.prompt_tokens > 0 and usage.completion_tokens > 0


async def test_bulk_embedding_waits_out_rate_limits(monkeypatch):
    """Found by the live test: a free gateway's per-minute limit failed whole documents."""
    import litellm

    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise litellm.RateLimitError("slow down", llm_provider="openai", model="m")
        return SimpleNamespace(data=[{"embedding": [1.0, 0.0]}])

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(module.litellm, "aembedding", flaky)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    client = _client(rate_limit_retries=4, rate_limit_base_delay=5)
    vectors = await client.aembed_texts(["a"], patient=True)
    assert len(vectors) == 1 and sleeps == [5, 10]


async def test_interactive_embedding_fails_fast_on_rate_limit(monkeypatch):
    import litellm

    async def limited(**kwargs):
        raise litellm.RateLimitError("slow down", llm_provider="openai", model="m")

    monkeypatch.setattr(module.litellm, "aembedding", limited)
    with pytest.raises(litellm.RateLimitError):
        await _client().aembed_texts(["a"])


async def test_per_call_model_gets_provider_prefix(monkeypatch):
    """Regression (final review L3): CONTEXTUAL_HEADERS_MODEL skipped the prefix."""
    fake = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None
        )
    )
    monkeypatch.setattr(module.litellm, "acompletion", fake)
    client = _client(model="auto", base_url="http://gateway/v1")
    await client.acomplete("q", model="gemini-3.5-flash")
    assert fake.call_args.kwargs["model"] == "openai/gemini-3.5-flash"
    await client.acomplete("q", model="anthropic/claude-x")
    assert fake.call_args.kwargs["model"] == "anthropic/claude-x"
