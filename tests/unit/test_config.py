import pytest

from nursing_bot.config import Settings


def test_default_config():
    settings = Settings()
    assert settings.ENVIRONMENT == "development"
    assert settings.ADMIN_API_KEY == "admin-secret-key"
    assert settings.CHAT_RATE_LIMIT == "20/minute"
    assert settings.EMBEDDING_DIMENSIONS == 1536
    assert "TNNMC" in settings.KNOWLEDGE_BASE_NAME


def test_production_safety_check():
    with pytest.raises(ValueError, match="ADMIN_API_KEY must be set"):
        Settings(ENVIRONMENT="production", ADMIN_API_KEY="admin-secret-key").validate_production()

    # Valid production settings
    prod = Settings(
        ENVIRONMENT="production",
        ADMIN_API_KEY="super-secret-production-random-key-12345",
        DATABASE_URL="postgresql+asyncpg://postgres:pass@prod-db:5432/nursing_bot",
    )
    prod.validate_production()
