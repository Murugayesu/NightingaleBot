"""Integration fixtures: a real PostgreSQL + pgvector, migrated with Alembic.

Start one locally with:
    docker run -d --name nursingbot-test-pg -e POSTGRES_PASSWORD=testpass \
      -e POSTGRES_DB=nursing_bot_test -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.7-pg18-trixie
or point TEST_DATABASE_URL at any disposable database. THE DATABASE IS WIPED.
Tests are skipped when the database is unreachable.
"""

import asyncio

import asyncpg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from alembic import command
from nursing_bot.api.dependencies import get_llm_client_dep, get_storage_dep
from nursing_bot.api.main import app
from nursing_bot.storage.database import dispose_engine, get_session
from tests.conftest import TEST_DATABASE_URL
from tests.fakes import FakeLLMClient, InMemoryStorage, cited_answer_from_prompt

ADMIN = {"X-Admin-API-Key": "test-admin-key"}
_TABLES = "chat_messages, chat_sessions, chunks, documents, eval_runs"


def _libpq_url() -> str:
    return TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _reset_schema() -> None:
    connection = await asyncpg.connect(_libpq_url())
    try:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def migrated_database() -> None:
    try:
        asyncio.run(_reset_schema())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Test PostgreSQL not reachable at {TEST_DATABASE_URL}: {exc}")
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
async def db_clean(migrated_database):
    async with get_session() as session:
        await session.execute(text(f"TRUNCATE {_TABLES} CASCADE"))
        await session.commit()
    yield
    await dispose_engine()  # each test has its own event loop


@pytest.fixture
def db_clean_sync(migrated_database):
    """For synchronous tests (e.g. CLI commands that call asyncio.run themselves)."""

    async def truncate() -> None:
        async with get_session() as session:
            await session.execute(text(f"TRUNCATE {_TABLES} CASCADE"))
            await session.commit()
        await dispose_engine()

    asyncio.run(truncate())


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient(cited_answer_from_prompt)


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def wired(monkeypatch, fake_llm, storage):
    """Route every code path (API, jobs, eval, CLI) to the fakes."""
    for target in (
        "nursing_bot.services.get_llm_client",
        "nursing_bot.jobs.get_llm_client",
        "nursing_bot.evaluation.runner.get_llm_client",
    ):
        monkeypatch.setattr(target, lambda: fake_llm)
    for target in ("nursing_bot.services.get_storage", "nursing_bot.jobs.get_storage"):
        monkeypatch.setattr(target, lambda: storage)
    app.dependency_overrides[get_llm_client_dep] = lambda: fake_llm
    app.dependency_overrides[get_storage_dep] = lambda: storage
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client(db_clean, wired, settings_override):
    # The fake bag-of-words embeddings score lower than real models; off-topic
    # questions still score ~0, so 0.2 keeps the gate meaningful in tests.
    settings_override(GATE_THRESHOLD_SIMILARITY="0.2")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def upload(
    client: AsyncClient, name: str, content: bytes, doc_type: str = "faq", **data: str
):
    return await client.post(
        "/api/v1/documents/upload",
        headers=ADMIN,
        files={"file": (name, content, "text/plain")},
        data={"doc_type": doc_type, **data},
    )


FEES_TXT = (
    b"Online registration with TNNMC.\n\n"
    b"The online registration fee is Rs. 365. Payment can be made by credit card, "
    b"debit card or net banking, or offline by triplicate challan at Indian Bank."
)
RENEWAL_TXT = (
    b"Renewal of nurse registration.\n\n"
    b"Registration must be renewed every five years. The renewal fee is Rs. 250 "
    b"and the renewed ID card is sent by post."
)
CONTACT_TXT = (
    b"Contact details.\n\n"
    b"The council office in Chennai is open Monday to Friday from 10 am to 5 pm. "
    b"Email the registrar for help with corrections."
)
