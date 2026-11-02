import secrets

from fastapi import Header, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from nursing_bot.config import get_settings


def get_real_client_ip(request: Request) -> str:
    """Extract real client IP behind reverse proxies (Caddy, Nginx, Cloudflare)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


limiter = Limiter(key_func=get_real_client_ip)


async def verify_admin_key(
    x_admin_api_key: str | None = Header(None, alias="X-Admin-API-Key"),
) -> str:
    """Security dependency ensuring admin mutating operations provide a valid API key with constant-time comparison."""
    settings = get_settings()
    raw_key = settings.ADMIN_API_KEY
    configured_key = (
        raw_key.get_secret_value()
        if hasattr(raw_key, "get_secret_value")
        else str(raw_key)
        if raw_key
        else None
    )

    if (
        not configured_key
        or not x_admin_api_key
        or not secrets.compare_digest(x_admin_api_key, configured_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-API-Key header.",
        )
    return x_admin_api_key
