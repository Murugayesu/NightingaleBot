"""Signed-in browser sessions.

The browser holds a random 256-bit token in an HttpOnly cookie; the database
holds only its SHA-256. Sessions slide: each visit (at most once every few
minutes, to avoid a write per request) pushes the expiry AUTH_SESSION_DAYS
into the future. Revoking is immediate because every request looks the
session up.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.storage.models import AuthSession, User

_TOUCH_INTERVAL = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(frozen=True)
class SignedInUser:
    """A detached snapshot of the signed-in user for one request."""

    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    must_change_password: bool
    created_at: datetime
    session_hash: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def start_session(
    db: AsyncSession, user_id: uuid.UUID, user_agent: str | None = None
) -> str:
    """Create a session and return the raw token for the cookie."""
    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(
        AuthSession(
            token_hash=hash_token(token),
            user_id=user_id,
            user_agent=(user_agent or "")[:256] or None,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=get_settings().AUTH_SESSION_DAYS),
        )
    )
    await db.commit()
    return token


async def resolve_session(db: AsyncSession, token: str | None) -> SignedInUser | None:
    """The user behind a cookie token, or None if it is unknown, expired,
    revoked, or belongs to a disabled account."""
    if not token or len(token) > 256:
        return None
    token_hash = hash_token(token)
    row = (
        await db.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == token_hash)
        )
    ).first()
    if row is None:
        return None
    auth_session, user = row
    now = _now()
    if (
        auth_session.revoked_at is not None
        or auth_session.expires_at <= now
        or user.disabled_at is not None
    ):
        return None
    if now - auth_session.last_seen_at > _TOUCH_INTERVAL:
        auth_session.last_seen_at = now
        auth_session.expires_at = now + timedelta(days=get_settings().AUTH_SESSION_DAYS)
    snapshot = SignedInUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        session_hash=token_hash,
    )
    # Always end the transaction: requests go on to make slow provider calls
    # and must not keep a pooled connection checked out meanwhile.
    await db.commit()
    return snapshot


async def end_session(db: AsyncSession, session_hash: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == session_hash)
        .where(AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.commit()


async def end_all_sessions(
    db: AsyncSession, user_id: uuid.UUID, keep_hash: str | None = None
) -> int:
    """Revoke every live session of a user (except keep_hash). Returns how many."""
    statement = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id)
        .where(AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    if keep_hash is not None:
        statement = statement.where(AuthSession.token_hash != keep_hash)
    result = await db.execute(statement)
    await db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
