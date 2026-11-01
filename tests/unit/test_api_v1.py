import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from nursing_bot import __version__
from nursing_bot.api.dependencies import get_db, get_pipeline_dep, get_storage_dep
from nursing_bot.api.main import app
from nursing_bot.api.security import limiter
from nursing_bot.config import get_settings
from nursing_bot.generation.pipeline import RAGResponse


@pytest.fixture
def mock_db():
    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one.return_value = 0
    mock_res.scalars.return_value.all.return_value = []
    mock_res.scalar_one_or_none.return_value = None
    mock_res.first.return_value = None
    mock_res.mappings.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_res)
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.upload = AsyncMock(return_value="s3://documents/sample.txt")
    storage.download = AsyncMock(return_value=b"File contents")
    storage.delete = AsyncMock(return_value=True)
    return storage


@pytest.fixture
def mock_pipeline():
    pipeline = AsyncMock()
    pipeline.aquery = AsyncMock(
        return_value=RAGResponse(
            answer="The verified policy requirement is 5 days [Source 1].",
            citations=[
                {
                    "source_index": 1,
                    "chunk_id": str(uuid.uuid4()),
                    "document_id": str(uuid.uuid4()),
                    "score": 0.042,
                    "metadata": {"doc_type": "policy"},
                }
            ],
            is_grounded=True,
            gate_passed=True,
            top_score=0.042,
        )
    )
    return pipeline


@pytest.mark.asyncio
async def test_root_and_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert res.json()["status"] == "online"
        assert res.json()["version"] == __version__

        health_res = await client.get("/health")
        assert health_res.status_code == 200
        assert health_res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_document_upload_unauthorized(mock_db, mock_storage):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_storage_dep] = lambda: mock_storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("test.txt", b"content", "text/plain")},
        )
        assert res.status_code == 401
        assert "X-Admin-API-Key" in res.json()["detail"]


@pytest.mark.asyncio
async def test_document_upload_authorized(mock_db, mock_storage):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_storage_dep] = lambda: mock_storage

    admin_key = get_settings().ADMIN_API_KEY
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/documents/upload",
            headers={"X-Admin-API-Key": admin_key},
            files={"file": ("test.txt", b"Valid document content", "text/plain")},
            data={"doc_type": "policy"},
        )
        assert res.status_code == 201
        assert res.json()["filename"] == "test.txt"


@pytest.mark.asyncio
async def test_chat_endpoint_success(mock_db, mock_pipeline):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_pipeline_dep] = lambda: mock_pipeline

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/chat",
            json={"query": "What is the policy?"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "5 days [Source 1]" in data["answer"]
        assert len(data["citations"]) == 1
        assert data["is_grounded"] is True


@pytest.mark.asyncio
async def test_chat_rate_limiting(mock_db, mock_pipeline):
    limiter.reset()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_pipeline_dep] = lambda: mock_pipeline

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = []
        for i in range(25):
            res = await client.post("/api/v1/chat", json={"query": f"question {i}"})
            responses.append(res.status_code)

        assert 200 in responses
        assert 429 in responses


@pytest.mark.asyncio
async def test_admin_endpoints_require_auth(mock_db, mock_storage):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_storage_dep] = lambda: mock_storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Without auth header
        res_health = await client.get("/api/v1/admin/health")
        assert res_health.status_code == 401

        res_stats = await client.get("/api/v1/admin/stats")
        assert res_stats.status_code == 401

        # With auth header
        admin_key = get_settings().ADMIN_API_KEY
        headers = {"X-Admin-API-Key": admin_key}
        res_health_auth = await client.get("/api/v1/admin/health", headers=headers)
        assert res_health_auth.status_code == 200

        res_stats_auth = await client.get("/api/v1/admin/stats", headers=headers)
        assert res_stats_auth.status_code == 200


@pytest.mark.asyncio
async def test_chat_endpoint_client_side_sliding_window(mock_db, mock_pipeline):
    limiter.reset()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_pipeline_dep] = lambda: mock_pipeline

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "query": "Where should that be displayed?",
            "recent_context": [
                {"role": "user", "content": "Tell me about registration certificate."},
                {"role": "assistant", "content": "You must obtain it from TNNMC."},
            ],
        }
        res = await client.post("/api/v1/chat", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["is_grounded"] is True
        # Verify pipeline was called with history passed from client
        mock_pipeline.aquery.assert_called_once()
        call_kwargs = mock_pipeline.aquery.call_args.kwargs
        assert len(call_kwargs["history"]) == 2
        assert call_kwargs["history"][0]["role"] == "user"
