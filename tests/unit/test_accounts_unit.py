"""Password hashing and policy, e-mail rules, temporary passwords, CSRF checks."""

import re

import pytest
from starlette.requests import Request

from nursing_bot.accounts.errors import InvalidEmailError, WeakPasswordError
from nursing_bot.accounts.passwords import (
    check_password_policy,
    hash_password,
    needs_rehash,
    verify_password,
)
from nursing_bot.accounts.service import generate_temporary_password, normalize_email
from nursing_bot.accounts.sessions import hash_token
from nursing_bot.api.auth import csrf_ok


def test_hashes_are_argon2id_salted_and_verifiable():
    first, second = (
        hash_password("correct horse battery"),
        hash_password("correct horse battery"),
    )
    assert first.startswith("$argon2id$") and first != second
    assert verify_password(first, "correct horse battery")
    assert not verify_password(first, "wrong horse battery")
    assert not needs_rehash(first)


@pytest.mark.parametrize("stored", [None, "", "not-a-hash", "$argon2id$broken"])
def test_verify_never_raises_on_bad_hashes(stored):
    assert verify_password(stored, "anything at all") is False


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("short", "at least 10"),
        ("x" * 129, "at most 128"),
        ("password123", "too common"),
        ("Qwertyuiop", "too common"),
        ("1234567890", "too common"),
        ("aaaaaaaaaaaa", "too common"),
        ("abababababab", "too common"),
        ("priya.nurse@example.org", "e-mail"),
        ("priyanurse2026", "e-mail"),
    ],
)
def test_weak_passwords_are_rejected_with_a_reason(password, reason):
    with pytest.raises(WeakPasswordError, match=reason):
        check_password_policy(password, "priyanurse@example.org", 10)


@pytest.mark.parametrize(
    "password", ["tea with jasmine flowers", "Marina-beach-at-6am", "x7#Kq9!vLm2p"]
)
def test_reasonable_passwords_pass(password):
    check_password_policy(password, "priyanurse@example.org", 10)


def test_emails_are_normalised_and_validated():
    assert normalize_email("  Priya.N@Example.ORG ") == "priya.n@example.org"
    for bad in ("", "priya", "priya@", "@example.org", "a b@example.org", "a@b"):
        with pytest.raises(InvalidEmailError):
            normalize_email(bad)


def test_temporary_passwords_are_long_and_unambiguous():
    passwords = {generate_temporary_password() for _ in range(50)}
    assert len(passwords) == 50
    for password in passwords:
        assert re.fullmatch(r"[a-z2-9]{4}(-[a-z2-9]{4}){3}", password)
        assert not set(password) & set("01ilo")


def test_session_tokens_are_stored_hashed():
    assert hash_token("abc") == hash_token("abc") != hash_token("abd")
    assert len(hash_token("abc")) == 64


def _request(method: str, headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": method, "headers": raw, "path": "/"})


@pytest.mark.parametrize(
    ("method", "headers", "allowed"),
    [
        ("GET", {}, True),
        ("POST", {}, False),
        ("POST", {"X-Requested-With": "nursingbot", "Host": "bot.local"}, True),
        (
            "POST",
            {
                "X-Requested-With": "nursingbot",
                "Host": "bot.local",
                "Origin": "http://bot.local",
            },
            True,
        ),
        (
            "POST",
            {
                "X-Requested-With": "nursingbot",
                "Host": "bot.local",
                "Origin": "https://evil.example",
            },
            False,
        ),
        (
            "DELETE",
            {"X-Requested-With": "nursingbot", "Host": "bot.local", "Origin": "null"},
            False,
        ),
        ("PATCH", {"X-Requested-With": "XMLHttpRequest", "Host": "bot.local"}, False),
    ],
)
def test_csrf_requires_the_header_and_a_same_site_origin(method, headers, allowed):
    assert csrf_ok(_request(method, headers)) is allowed
