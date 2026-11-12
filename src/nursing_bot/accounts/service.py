"""Account operations shared by the API and the CLI.

Every function takes an AsyncSession and commits its own changes, so callers
never leave half-finished account changes behind.
"""

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.accounts.errors import (
    AccountDisabledError,
    AccountError,
    AccountLockedError,
    EmailTakenError,
    InvalidCredentialsError,
    InvalidEmailError,
    UserNotFoundError,
    WeakPasswordError,
)
from nursing_bot.accounts.passwords import (
    check_password_policy,
    hash_password,
    needs_rehash,
    verify_password,
)
from nursing_bot.accounts.sessions import end_all_sessions
from nursing_bot.config import get_settings
from nursing_bot.storage.models import User, UserRole

# Deliberately loose: the address is never e-mailed, only used as a login name.
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$")
# No look-alike characters (0/O, 1/l/I), so a password read aloud or copied
# from a screen is typed correctly.
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


INVALID_CREDENTIALS = "Incorrect e-mail or password."


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise InvalidEmailError("Enter a valid e-mail address, like name@example.com.")
    return email


def generate_temporary_password() -> str:
    """Four groups of four, e.g. "k7mq-x2ae-9tcn-pw4h" (~78 bits)."""
    groups = (
        "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(4)) for _ in range(4)
    )
    return "-".join(groups)


async def find_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    email: str,
    password: str,
    *,
    display_name: str | None = None,
    role: str = UserRole.USER,
    must_change_password: bool = False,
    privacy_accepted: bool = True,
    enforce_policy: bool = True,
) -> User:
    normalized = normalize_email(email)
    if enforce_policy:
        check_password_policy(password, normalized, get_settings().PASSWORD_MIN_LENGTH)
    if await find_user_by_email(db, normalized) is not None:
        raise EmailTakenError("An account with this e-mail address already exists.")
    user = User(
        id=uuid.uuid4(),
        email=normalized,
        password_hash=hash_password(password),
        display_name=(display_name or "").strip() or None,
        role=role,
        must_change_password=must_change_password,
        privacy_accepted_at=utcnow() if privacy_accepted else None,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:  # a concurrent sign-up with the same address
        await db.rollback()
        raise EmailTakenError(
            "An account with this e-mail address already exists."
        ) from exc
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    """Return the user for a correct e-mail + password, else raise.

    After LOGIN_MAX_FAILURES wrong passwords in a row, the account refuses
    every attempt (even a correct one) for LOGIN_LOCKOUT_MINUTES, so a
    password cannot be guessed from many IP addresses.
    """
    settings = get_settings()
    try:
        normalized = normalize_email(email)
    except InvalidEmailError:
        verify_password(None, password)  # same timing as a real attempt
        raise InvalidCredentialsError(INVALID_CREDENTIALS) from None
    user = await find_user_by_email(db, normalized)
    if user is None:
        verify_password(None, password)
        raise InvalidCredentialsError(INVALID_CREDENTIALS)

    now = utcnow()
    window = timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    recent_failure = (
        user.last_failed_login_at is not None
        and now - user.last_failed_login_at < window
    )
    if recent_failure and user.failed_logins >= settings.LOGIN_MAX_FAILURES:
        assert user.last_failed_login_at is not None
        wait = int((user.last_failed_login_at + window - now).total_seconds()) + 1
        raise AccountLockedError(
            "Too many incorrect attempts. Try again in "
            f"{max(1, wait // 60)} minute(s), or ask an administrator to reset "
            "your password.",
            retry_after_seconds=wait,
        )

    if not verify_password(user.password_hash, password):
        # Atomic, so parallel guesses all count. An old failure streak restarts.
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                failed_logins=(User.failed_logins + 1) if recent_failure else 1,
                last_failed_login_at=now,
            )
        )
        await db.commit()
        raise InvalidCredentialsError(INVALID_CREDENTIALS)

    if user.disabled_at is not None:
        await db.commit()
        raise AccountDisabledError(
            "This account has been disabled. Contact the administrator."
        )

    user.failed_logins = 0
    user.last_failed_login_at = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    await db.commit()
    return user


async def change_password(
    db: AsyncSession,
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
    *,
    keep_session_hash: str | None = None,
) -> None:
    """Change a password after checking the current one.

    Every other signed-in session of the user is ended; the one making the
    change (keep_session_hash) stays signed in.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFoundError("Account not found.")
    if not verify_password(user.password_hash, current_password):
        raise InvalidCredentialsError("Your current password is incorrect.")
    if new_password == current_password:
        raise WeakPasswordError("Choose a password different from the current one.")
    check_password_policy(new_password, user.email, get_settings().PASSWORD_MIN_LENGTH)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    await db.commit()
    await end_all_sessions(db, user.id, keep_hash=keep_session_hash)


@dataclass
class TemporaryPassword:
    email: str
    password: str


async def admin_reset_password(
    db: AsyncSession, user_id: uuid.UUID
) -> TemporaryPassword:
    """Give the user a one-time temporary password.

    The user is signed out everywhere and must choose a new password at the
    next sign-in. The lockout counter is cleared so they can sign in at once.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFoundError("Account not found.")
    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.failed_logins = 0
    user.last_failed_login_at = None
    await db.commit()
    await end_all_sessions(db, user.id)
    return TemporaryPassword(email=user.email, password=temporary)


async def set_disabled(db: AsyncSession, user_id: uuid.UUID, disabled: bool) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFoundError("Account not found.")
    user.disabled_at = (user.disabled_at or utcnow()) if disabled else None
    await db.commit()
    if disabled:
        await end_all_sessions(db, user.id)
    return user


async def set_role(db: AsyncSession, user_id: uuid.UUID, role: str) -> User:
    if role not in (UserRole.USER, UserRole.ADMIN):
        raise AccountError("Role must be 'user' or 'admin'.")
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFoundError("Account not found.")
    user.role = role
    await db.commit()
    return user


async def delete_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete the account and, by cascade, its sessions, chats and feedback."""
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFoundError("Account not found.")
    await db.delete(user)
    await db.commit()
