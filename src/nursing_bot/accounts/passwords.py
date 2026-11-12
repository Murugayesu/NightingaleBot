"""Password hashing (Argon2id) and the password policy.

Argon2id is the current OWASP recommendation: memory-hard, so guessing a
leaked hash on GPUs is expensive. argon2-cffi's defaults follow RFC 9106's
"low memory" profile (64 MiB, 3 passes, 4 lanes).

The policy follows NIST SP 800-63B: a minimum length, a maximum to bound the
hashing cost, a block list of common passwords, and no composition rules
(they make passwords harder to remember, not harder to guess).
"""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from nursing_bot.accounts.common_passwords import COMMON_PASSWORDS
from nursing_bot.accounts.errors import WeakPasswordError

MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher()
# Verified against when an e-mail is unknown, so a login attempt takes the
# same time whether or not the account exists.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """True when the password matches. Never raises on a bad or missing hash."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(
            password_hash
        )
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with weaker parameters than today's."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _is_trivial(password: str) -> bool:
    lowered = password.lower()
    if len(set(lowered)) <= 2:  # "aaaaaaaaaa", "ababababab"
        return True
    digits = "01234567890123456789"
    letters = "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz"
    keyboard = "qwertyuiopasdfghjklzxcvbnm"
    return any(
        lowered in seq or lowered in seq[::-1] for seq in (digits, letters, keyboard)
    )


def check_password_policy(password: str, email: str, min_length: int) -> None:
    """Raise WeakPasswordError with a user-facing reason, or return None."""
    if len(password) < min_length:
        raise WeakPasswordError(
            f"Use at least {min_length} characters. A short phrase is easiest to remember."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or _is_trivial(password):
        raise WeakPasswordError(
            "This password is too common and easy to guess. Choose another."
        )
    # Letters and digits only, so "priya.nurse@example.org" matches too.
    bare = re.sub(r"[^a-z0-9]", "", lowered)
    bare_email = re.sub(r"[^a-z0-9]", "", email.lower())
    bare_local = re.sub(r"[^a-z0-9]", "", email.split("@", 1)[0].lower())
    if bare == bare_email or (
        len(bare_local) >= 4 and re.sub(r"\d+$", "", bare) == bare_local
    ):
        raise WeakPasswordError("Your password must not be your e-mail address.")
