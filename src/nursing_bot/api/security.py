from fastapi import Header, HTTPException, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from nursing_bot.config import get_settings

limiter = Limiter(key_func=get_remote_address)


async def verify_admin_key(
    x_admin_api_key: str | None = Header(None, alias="X-Admin-API-Key"),
) -> str:
    """Security dependency ensuring admin mutating operations provide a valid API key."""
    settings = get_settings()
    raw_key = settings.ADMIN_API_KEY
    configured_key = (
        raw_key.get_secret_value()
        if hasattr(raw_key, "get_secret_value")
        else str(raw_key)
        if raw_key
        else None
    )

    if not configured_key or not x_admin_api_key or x_admin_api_key != configured_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-API-Key header.",
        )
    return x_admin_api_key
