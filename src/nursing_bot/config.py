from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global configuration settings for nursing_bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Environment
    ENVIRONMENT: Literal["development", "production", "test"] = "development"
    LOG_LEVEL: str = "INFO"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    INGESTION_MODE: Literal["inline", "queue"] = "inline"
    CORS_ALLOWED_ORIGINS: str = "*"

    # Security & Rate Limiting
    ADMIN_API_KEY: str = "admin:admin@123"
    CHAT_RATE_LIMIT: str = "20/minute"

    # Domain & Organization Presentation (Defaults tailored for TNNMC Nursing)
    KNOWLEDGE_BASE_NAME: str = "TNNMC Nursing Knowledge Base"
    KNOWLEDGE_AUTHORITY_NAME: str = "Tamil Nadu Nurses and Midwives Council"
    KNOWLEDGE_CONTACT_URL: str = "https://www.tamilnadunursingcouncil.com/contact"
    RAG_SYSTEM_PROMPT: str = (
        "Answer only from the verified context provided. Cite every factual statement "
        "with inline tags such as [Source 1]. If the context is insufficient, state honestly "
        "that the information is not available in the records. Do not invent or assume facts."
    )
    RAG_ABSTENTION_MESSAGE: str = "I could not find sufficient information in the verified records to answer that question."
    DOMAIN_SYNONYMS_JSON: str = "{}"
    PROGRAM_ALIASES_JSON: str = "{}"

    @property
    def domain_synonyms(self) -> dict[str, str]:
        import json

        try:
            return json.loads(self.DOMAIN_SYNONYMS_JSON)
        except (json.JSONDecodeError, TypeError):
            return {}

    @property
    def program_aliases(self) -> dict[str, str]:
        import json

        try:
            return json.loads(self.PROGRAM_ALIASES_JSON)
        except (json.JSONDecodeError, TypeError):
            return {}

    # PostgreSQL 16/18 + pgvector
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/nursing_bot"
    )
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Object Storage (MinIO, S3, R2)
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_ACCESS_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "nursing-documents"
    S3_REGION: str = "us-east-1"

    # AI Models via LiteLLM Gateway
    LLM_MODEL: str = "auto"
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: str | None = None
    EMBEDDING_MODEL: str = "auto"
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_DIMENSIONS: int = 1536
    EMBEDDING_BATCH_SIZE: int = 64

    # Reranking (Cohere / Jina / Voyage)
    RERANK_ENABLED: bool = False
    RERANK_MODEL: str = "cohere/rerank-v3.5"
    RERANK_API_KEY: str | None = None
    RERANK_TOP_K: int = 5

    # Retrieval & Confidence Gates
    GATE_THRESHOLD_RRF: float = 0.02
    GATE_THRESHOLD_RERANK: float = 0.50
    RETRIEVAL_TOP_K: int = 10
    RRF_K: int = 60

    # Observability (Arize Phoenix)
    PHOENIX_ENABLED: bool = True
    PHOENIX_COLLECTOR_ENDPOINT: str = "http://localhost:6006"
    PHOENIX_PROJECT_NAME: str = "nursing-bot-rag"

    # Ingestion Controls
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MiB

    # Chat Session Architecture (Client-Side Ephemeral Sliding Window)
    ENABLE_DATABASE_CHAT_HISTORY: bool = False
    MAX_RECENT_CONTEXT_TURNS: int = 2

    def validate_production(self) -> None:
        """Validate safety requirements when running in production."""
        if self.ENVIRONMENT == "production":
            insecure_keys = {
                "admin:admin@123",
                "admin-secret-key",
                "change-me",
                "root",
                "admin",
                "",
            }
            if self.ADMIN_API_KEY in insecure_keys:
                raise ValueError(
                    "ADMIN_API_KEY must be set to a secure secret in production."
                )
            if self.DATABASE_URL.startswith("sqlite"):
                raise ValueError("SQLite is not supported in production.")
            if "postgres:postgres@" in self.DATABASE_URL:
                raise ValueError(
                    "Default PostgreSQL credentials ('postgres:postgres') must not be used in production."
                )


@lru_cache
def get_settings() -> Settings:
    """Return a cached application settings singleton."""
    return Settings()
