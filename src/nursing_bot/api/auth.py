"""Who is calling: signed-in users (cookie), admins (cookie or API key), CSRF.

Cookie: `HttpOnly; SameSite=Lax; Path=/api`, Secure when AUTH_COOKIE_SECURE.
Scripts cannot read it, and browsers do not attach it to cross-site POSTs.

CSRF: every state-changing request from the browser app must carry
`X-Requested-With: nursingbot`, and its Origin (when sent) must be this
site. A cross-site page can send neither: a custom header forces a CORS
preflight, which this API never approves for other origins.
"""

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.accounts.sessions import SignedInUser, resolve_session
from nursing_bot.api.dependencies import get_db
from nursing_bot.api.security import admin_key_valid, get_client_ip
from nursing_bot.config import get_settings
from nursing_bot.storage.database import get_session

CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "nursingbot"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PASSWORD_CHANGE_REQUIRED = "Choose a new password before continuing."


# ---------------------------------------------------------------- cookies
def session_cookie(request: Request) -> str | None:
    return request.cookies.get(get_settings().AUTH_COOKIE_NAME)


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.AUTH_COOKIE_NAME,
        token,
        max_age=s.AUTH_SESSION_DAYS * 24 * 60 * 60,
        path="/api",
        httponly=True,
        samesite="lax",
        secure=s.AUTH_COOKIE_SECURE,
    )


def clear_session_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        s.AUTH_COOKIE_NAME,
        path="/api",
        httponly=True,
        samesite="lax",
        secure=s.AUTH_COOKIE_SECURE,
    )


def session_rate_key(request: Request) -> str:
    """Rate-limit key: the signed-in session if there is one, else the client IP."""
    token = session_cookie(request)
    if token:
        return "session:" + hashlib.sha256(token.encode()).hexdigest()[:32]
    return get_client_ip(request)


# ------------------------------------------------------------------- CSRF
def csrf_ok(request: Request) -> bool:
    if request.method in _SAFE_METHODS:
        return True
    if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
        return False
    origin = request.headers.get("origin")
    if origin is None:  # same-origin requests may omit it; the header suffices
        return True
    if origin == "null":
        return False
    if urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower():
        return True
    return origin.rstrip("/") in get_settings().cors_origins


async def require_csrf(request: Request) -> None:
    if not csrf_ok(request):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cross-site request blocked. Reload the page and try again.",
        )


# ------------------------------------------------------------------ users
async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> SignedInUser | None:
    return await resolve_session(db, session_cookie(request))


async def require_user_allow_pending(
    user: SignedInUser | None = Depends(get_current_user),
) -> SignedInUser:
    """Signed in, even if an admin reset means a new password is still due."""
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return user


async def require_user(
    user: SignedInUser = Depends(require_user_allow_pending),
) -> SignedInUser:
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, PASSWORD_CHANGE_REQUIRED)
    return user


# ------------------------------------------------------------------ admins
@dataclass(frozen=True)
class AdminPrincipal:
    """Either automation holding the API key, or a signed-in admin account."""

    via: str  # "api_key" | "account"
    user: SignedInUser | None = None


async def require_admin(
    request: Request,
    x_admin_api_key: str | None = Header(None, alias="X-Admin-API-Key"),
    user: SignedInUser | None = Depends(get_current_user),
) -> AdminPrincipal:
    if admin_key_valid(x_admin_api_key):
        return AdminPrincipal("api_key")
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Sign in with an admin account, or send a valid X-Admin-API-Key header.",
        )
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required.")
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, PASSWORD_CHANGE_REQUIRED)
    await require_csrf(request)
    return AdminPrincipal("account", user)


async def is_admin_request(request: Request) -> bool:
    """For middleware that must decide before any body is read (uploads)."""
    if admin_key_valid(request.headers.get("x-admin-api-key")):
        return True
    token = session_cookie(request)
    if not token or not csrf_ok(request):
        return False
    async with get_session() as db:
        user = await resolve_session(db, token)
    return user is not None and user.is_admin and not user.must_change_password
