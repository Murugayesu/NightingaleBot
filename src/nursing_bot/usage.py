"""Daily LLM token accounting: per-user quotas and a service-wide spending cap.

Counters live in `daily_token_usage`, one row per (UTC day, subject), where
subject is "global" or "user:<uuid>". Charging is a single atomic upsert, so
concurrent requests never lose each other's tokens.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.config import get_settings
from nursing_bot.storage.models import DailyTokenUsage

GLOBAL = "global"


def today() -> date:
    return datetime.now(UTC).date()


def user_subject(user_id: uuid.UUID) -> str:
    return f"user:{user_id}"


class UsageLimitError(Exception):
    """A quota or the global cap is used up. `status_code` maps it to HTTP."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class Allowance:
    user_tokens_today: int | None = None
    user_quota: int | None = None

    @property
    def user_remaining(self) -> int | None:
        if self.user_quota is None or self.user_tokens_today is None:
            return None
        return max(0, self.user_quota - self.user_tokens_today)


async def _tokens(db: AsyncSession, subject: str, day: date) -> int:
    value = await db.scalar(
        select(DailyTokenUsage.tokens).where(
            DailyTokenUsage.day == day, DailyTokenUsage.subject == subject
        )
    )
    return int(value or 0)


async def check_allowance(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> Allowance:
    """Raise UsageLimitError when the global cap or the user's quota is spent."""
    settings = get_settings()
    day = today()
    cap = settings.GLOBAL_DAILY_TOKEN_CAP
    if cap > 0 and await _tokens(db, GLOBAL, day) >= cap:
        raise UsageLimitError(
            "The assistant has reached today's usage limit. Please try again tomorrow.",
            503,
        )
    allowance = Allowance()
    if user_id is not None and settings.USER_DAILY_TOKEN_QUOTA > 0:
        used = await _tokens(db, user_subject(user_id), day)
        allowance = Allowance(used, settings.USER_DAILY_TOKEN_QUOTA)
        if used >= settings.USER_DAILY_TOKEN_QUOTA:
            raise UsageLimitError(
                "You have reached today's usage limit. It resets at 00:00 UTC "
                "(05:30 IST).",
                429,
            )
    return allowance


async def _add(db: AsyncSession, subject: str, day: date, tokens: int) -> int:
    statement = insert(DailyTokenUsage).values(day=day, subject=subject, tokens=tokens)
    upsert = statement.on_conflict_do_update(
        index_elements=[DailyTokenUsage.day, DailyTokenUsage.subject],
        set_={"tokens": DailyTokenUsage.tokens + statement.excluded.tokens},
    ).returning(DailyTokenUsage.tokens)
    return int((await db.execute(upsert)).scalar_one())


async def record_usage(
    db: AsyncSession, tokens: int, user_id: uuid.UUID | None = None
) -> int | None:
    """Add tokens to today's global total (and the user's). Does not commit.

    Returns the user's new total for today, or None without a user.
    """
    if tokens <= 0:
        return None
    day = today()
    await _add(db, GLOBAL, day, tokens)
    if user_id is None:
        return None
    return await _add(db, user_subject(user_id), day, tokens)
