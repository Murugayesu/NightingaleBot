import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from nursing_bot.api.dependencies import get_db, get_pipeline_dep, get_storage_dep
from nursing_bot.api.main import app
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
        assert res.json()["version"] == "0.2.0"

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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/documents/upload",
            headers={"X-Admin-API-Key": "admin-secret-key"},
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
