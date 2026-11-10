"""Shared test setup.

Environment variables are set BEFORE any nursing_bot import. A developer's .env
is not read at all (NURSING_BOT_ENV_FILE=""), so tests never pick up real
provider URLs or keys.
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
        "ADMIN_API_KEY": "test-admin-key",
        "SESSION_SECRET": "test-session-secret-0123456789abcdef",
        "CHAT_REQUIRE_SESSION": "true",
        "ENABLE_DATABASE_CHAT_HISTORY": "true",
        "SESSION_TOKEN_BUDGET": "50000",
        "LLM_MODEL": "openai/gpt-4o-mini",
        "LLM_BASE_URL": "",
        "LLM_API_KEY": "",
        "EMBEDDING_MODEL": "test/hash-embed",
        "EMBEDDING_BASE_URL": "",
        "EMBEDDING_DIMENSIONS": "1536",
        "RERANK_ENABLED": "false",
        "CONTEXTUAL_HEADERS_ENABLED": "false",
        "GATE_THRESHOLD_SIMILARITY": "0.30",
        "CITATION_MIN_COVERAGE": "0.5",
        "RETRIEVAL_TOP_K": "8",
        "MAX_RECENT_CONTEXT_TURNS": "2",
        "DOMAIN_SYNONYMS_JSON": "{}",
        "CORS_ALLOWED_ORIGINS": "",
        "S3_ENDPOINT_URL": "http://127.0.0.1:9",
        "EVAL_DATASET_PATH": "eval/evalset.csv",
    }
)

import asyncio
import sys

import pytest

if sys.platform == "win32":
    # psycopg (used by the Procrastinate queue) cannot run on the Proactor loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from nursing_bot.api.security import limiter
from nursing_bot.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_settings_and_limits():
    """Every test starts with default settings and no rate-limit history."""
    get_settings.cache_clear()
    limiter.reset()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings_override(monkeypatch):
    """Change settings for one test: settings_override(CHAT_REQUIRE_SESSION="false")."""

    def apply(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()

    return apply
