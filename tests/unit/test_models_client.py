import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nursing_bot.models.litellm_client import LiteLLMClient


@pytest.fixture
def client():
    return LiteLLMClient(
        model="openai/gpt-4o-mini",
        embedding_model="openai/text-embedding-3-small",
        rerank_model="cohere/rerank-v3.5",
        api_key="sk-test-key",
        base_url="http://localhost:4000",
        embedding_base_url="http://localhost:4001",
        embedding_api_key="sk-embed-key",
        embedding_dimensions=1536,
    )


@pytest.mark.asyncio
async def test_acomplete(client):
    mock_choice = MagicMock()
    mock_choice.message.content = "Here is the verified answer."
    mock_response = MagicMock(choices=[mock_choice])

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_response

        res = await client.acomplete(
            "What is the policy?", system_prompt="You are an enterprise AI assistant"
        )
        assert res == "Here is the verified answer."
        mock_acompletion.assert_called_once()
        kwargs = mock_acompletion.call_args[1]
        assert kwargs["model"] == "openai/gpt-4o-mini"
        assert len(kwargs["messages"]) == 2
        assert kwargs["messages"][0]["role"] == "system"
        assert kwargs["messages"][1]["role"] == "user"
        assert kwargs["api_key"] == "sk-test-key"
        assert kwargs["base_url"] == "http://localhost:4000"


@pytest.mark.asyncio
async def test_astream(client):
    class MockChunk:
        def __init__(self, content):
            self.choices = [MagicMock(delta=MagicMock(content=content))]

    async def mock_generator():
        for token in ["TNNMC", " ", "Nursing", " ", "Bot"]:
            yield MockChunk(token)

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_generator()

        chunks = []
        async for chunk in client.astream("Hello"):
            chunks.append(chunk)

        assert "".join(chunks) == "TNNMC Nursing Bot"


@pytest.mark.asyncio
async def test_aembed_texts(client):
    mock_response = MagicMock(
        data=[
            {"embedding": [0.01] * 1536},
            {"embedding": [0.02] * 1536},
        ]
    )

    with patch("litellm.aembedding", new_callable=AsyncMock) as mock_aembedding:
        mock_aembedding.return_value = mock_response

        embeddings = await client.aembed_texts(["Chunk 1", "Chunk 2"])
        assert len(embeddings) == 2
        assert embeddings[0] == [0.01] * 1536
        assert embeddings[1] == [0.02] * 1536
        mock_aembedding.assert_called_once()
        kwargs = mock_aembedding.call_args[1]
        assert kwargs["model"] == "openai/text-embedding-3-small"
        assert kwargs["dimensions"] == 1536
        assert kwargs["api_base"] == "http://localhost:4001"
        assert kwargs["api_key"] == "sk-embed-key"


@pytest.mark.asyncio
async def test_aembed_texts_empty(client):
    res = await client.aembed_texts([])
    assert res == []


@pytest.mark.asyncio
async def test_aembed_query(client):
    mock_response = MagicMock(data=[{"embedding": [0.5] * 1536}])

    with patch("litellm.aembedding", new_callable=AsyncMock) as mock_aembedding:
        mock_aembedding.return_value = mock_response

        res = await client.aembed_query("Query test")
        assert res == [0.5] * 1536


@pytest.mark.asyncio
async def test_arerank(client):
    mock_response = MagicMock(
        results=[
            {"index": 1, "relevance_score": 0.95},
            {"index": 0, "relevance_score": 0.42},
        ]
    )

    with patch("litellm.arerank", new_callable=AsyncMock) as mock_arerank:
        mock_arerank.return_value = mock_response

        results = await client.arerank(
            query="test query",
            documents=["Document A", "Document B"],
            top_n=2,
        )
        assert len(results) == 2
        assert results[0]["index"] == 1
        assert results[0]["relevance_score"] == 0.95
        mock_arerank.assert_called_once_with(
            model="cohere/rerank-v3.5",
            query="test query",
            documents=["Document A", "Document B"],
            top_n=2,
            api_key="sk-test-key",
            api_base="http://localhost:4000",
        )
