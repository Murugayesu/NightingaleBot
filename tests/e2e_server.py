"""Run the real API with fake AI providers, for the browser end-to-end tests.

    uv run python -m tests.e2e_server          (listens on 127.0.0.1:8000)

Uses TEST_DATABASE_URL (the same disposable database as the integration
tests). THE DATABASE IS WIPED, migrated, and loaded with a three-document
corpus and two accounts:

    admin@example.org / e2e admin password     (admin)
    nurse@example.org / e2e nurse password     (user)

Playwright starts this automatically (frontend/playwright.config.ts).
"""

import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:testpass@127.0.0.1:55432/nursing_bot_test",
)
os.environ.update(
    {
        "NURSING_BOT_ENV_FILE": "",
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "WARNING",
        "PHOENIX_ENABLED": "false",
        "INGESTION_MODE": "inline",
        "DATABASE_URL": TEST_DATABASE_URL,
        "ADMIN_API_KEY": "e2e-admin-key",
        "SESSION_SECRET": "e2e-session-secret-0123456789abcdef",
        "EMBEDDING_MODEL": "test/hash-embed",
        "GATE_THRESHOLD_SIMILARITY": "0.2",
        "S3_ENDPOINT_URL": "http://127.0.0.1:9",
        # Browser tests sign in and out many times from one address.
        "LOGIN_RATE_LIMIT": "1000/minute",
        "SIGNUP_RATE_LIMIT": "1000/hour",
        "SESSION_CREATE_RATE_LIMIT": "1000/hour",
        "CHAT_RATE_LIMIT": "1000/minute",
        "USER_CHAT_RATE_LIMIT": "1000/minute",
    }
)

import asyncio
import hashlib
import sys

import asyncpg
import uvicorn
from alembic.config import Config

import nursing_bot.jobs
import nursing_bot.services  # noqa: F401
from alembic import command
from nursing_bot.accounts import service
from nursing_bot.api.dependencies import get_llm_client_dep, get_storage_dep
from nursing_bot.api.main import app
from nursing_bot.ingestion.pipeline import IngestionPipeline
from nursing_bot.storage.database import dispose_engine, get_session
from nursing_bot.storage.models import Document, DocumentStatus
from tests.fakes import FakeLLMClient, InMemoryStorage, smart_fake_answer

PORT = int(os.environ.get("E2E_API_PORT", "8000"))
CORPUS = {
    "TNNMC registration FAQ.txt": (
        b"Online registration with TNNMC.\n\n"
        b"The online registration fee is Rs. 365. Payment can be made by credit card, "
        b"debit card or net banking, or offline by triplicate challan at Indian Bank."
    ),
    "Renewal of registration.txt": (
        b"Renewal of nurse registration.\n\n"
        b"Registration must be renewed every five years. The renewal fee is Rs. 250 "
        b"and the renewed ID card is sent by post."
    ),
    "Contact details.txt": (
        b"Contact details.\n\n"
        b"The council office in Chennai is open Monday to Friday from 10 am to 5 pm. "
        b"Email the registrar for help with corrections."
    ),
}
llm = FakeLLMClient(smart_fake_answer)
storage = InMemoryStorage()


async def _prepare() -> None:
    connection = await asyncpg.connect(
        TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await connection.close()
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")

    pipeline = IngestionPipeline(llm_client=llm, storage=storage)  # type: ignore[arg-type]
    async with get_session() as db:
        for name, content in CORPUS.items():
            key = f"e2e/{name}"
            await storage.upload(key, content)
            document = Document(
                filename=name,
                s3_key=key,
                content_hash=hashlib.sha256(content).hexdigest(),
                content_type="prose",
                doc_type="faq",
                status=DocumentStatus.PENDING,
                metadata_json={},
            )
            db.add(document)
            await db.commit()
            await pipeline.ingest_document(db, document.id)
        await service.create_user(
            db, "admin@example.org", "e2e admin password", role="admin"
        )
        await service.create_user(db, "nurse@example.org", "e2e nurse password")
    await dispose_engine()


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_prepare())
    for target in (
        "nursing_bot.services.get_llm_client",
        "nursing_bot.jobs.get_llm_client",
    ):
        module, name = target.rsplit(".", 1)
        setattr(sys.modules[module], name, lambda: llm)
    for target in ("nursing_bot.services.get_storage", "nursing_bot.jobs.get_storage"):
        module, name = target.rsplit(".", 1)
        setattr(sys.modules[module], name, lambda: storage)
    app.dependency_overrides[get_llm_client_dep] = lambda: llm
    app.dependency_overrides[get_storage_dep] = lambda: storage
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
