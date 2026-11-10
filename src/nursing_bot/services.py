"""Process-wide service singletons shared by the API, the worker and the CLI.

Kept outside `api/` so that background workers do not depend on the web layer.
"""

from functools import lru_cache

from nursing_bot.config import get_settings, secret
from nursing_bot.models.litellm_client import LiteLLMClient
from nursing_bot.storage.object_store import S3Backend


@lru_cache
def get_storage() -> S3Backend:
    s = get_settings()
    return S3Backend(
        endpoint_url=s.S3_ENDPOINT_URL,
        access_key_id=s.S3_ACCESS_KEY_ID,
        secret_access_key=secret(s.S3_SECRET_ACCESS_KEY),
        bucket_name=s.S3_BUCKET_NAME,
        region=s.S3_REGION,
    )


@lru_cache
def get_llm_client() -> LiteLLMClient:
    s = get_settings()
    return LiteLLMClient(
        model=s.LLM_MODEL,
        embedding_model=s.EMBEDDING_MODEL,
        rerank_model=s.RERANK_MODEL,
        api_key=secret(s.LLM_API_KEY),
        base_url=s.LLM_BASE_URL,
        embedding_base_url=s.EMBEDDING_BASE_URL,
        embedding_api_key=secret(s.EMBEDDING_API_KEY),
        embedding_dimensions=s.EMBEDDING_DIMENSIONS,
        rerank_api_key=secret(s.RERANK_API_KEY),
        rerank_base_url=s.RERANK_BASE_URL,
        max_tokens=s.LLM_MAX_TOKENS,
        timeout=s.LLM_TIMEOUT_SECONDS,
        max_retries=s.LLM_MAX_RETRIES,
        rate_limit_retries=s.EMBEDDING_RATE_LIMIT_RETRIES,
    )
