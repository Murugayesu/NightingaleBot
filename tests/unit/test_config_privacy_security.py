import time

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from nursing_bot import privacy
from nursing_bot.api.security import (
    get_client_ip,
    issue_session_token,
    verify_session_token,
)
from nursing_bot.config import Settings, get_settings

# --------------------------------------------------------------------- config


def _prod(**overrides: str) -> Settings:
    values = {
        "ENVIRONMENT": "production",
        "ADMIN_API_KEY": "x" * 32,
        "SESSION_SECRET": "y" * 40,
        "DATABASE_URL": "postgresql+asyncpg://app:strong-password@db:5432/nursing_bot",
        "S3_SECRET_ACCESS_KEY": "z" * 32,
        "CORS_ALLOWED_ORIGINS": "",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_valid_production_settings_pass():
    _prod().validate_production()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"ADMIN_API_KEY": "admin:admin@123"}, "ADMIN_API_KEY"),
        ({"ADMIN_API_KEY": "short"}, "ADMIN_API_KEY"),
        ({"SESSION_SECRET": "dev-session-secret-change-me"}, "SESSION_SECRET"),
        ({"DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db/x"}, "PostgreSQL"),
        ({"S3_SECRET_ACCESS_KEY": "minioadmin"}, "S3_SECRET_ACCESS_KEY"),
        ({"CORS_ALLOWED_ORIGINS": "*"}, "CORS"),
    ],
)
def test_production_rejects_unsafe_defaults(override, message):
    with pytest.raises(ValueError, match=message):
        _prod(**override).validate_production()


def test_development_does_not_validate():
    Settings(ENVIRONMENT="development", ADMIN_API_KEY="admin").validate_production()


def test_embedding_dimensions_must_fit_vector_column():
    with pytest.raises(ValidationError):
        Settings(EMBEDDING_DIMENSIONS=3072)


def test_prompt_placeholders_are_filled(settings_override):
    settings_override(
        RAG_ABSTENTION_MESSAGE="Not found. Ask the {authority}: {contact_url}"
    )
    s = get_settings()
    assert s.KNOWLEDGE_AUTHORITY_NAME in s.system_prompt
    assert s.abstention_message == (
        f"Not found. Ask the {s.KNOWLEDGE_AUTHORITY_NAME}: {s.KNOWLEDGE_CONTACT_URL}"
    )


def test_domain_synonyms_tolerates_bad_json(settings_override):
    settings_override(DOMAIN_SYNONYMS_JSON="not json")
    assert get_settings().domain_synonyms == {}
    settings_override(DOMAIN_SYNONYMS_JSON='["a list"]')
    assert get_settings().domain_synonyms == {}


# -------------------------------------------------------------------- privacy


def test_redacts_email_and_indian_mobile():
    text = "Mail me at nurse.kumar@example.com or call +91 98765 43210 / 9876543210."
    redacted = privacy.redact(text)
    assert "example.com" not in redacted and "98765" not in redacted
    assert redacted.count("[PHONE]") == 2 and "[EMAIL]" in redacted


def test_aadhaar_redaction_uses_checksum():
    # 2345 6789 0124 has a valid Verhoeff check digit; this run of years does not.
    # (Random 12-digit numbers pass the checksum 1 time in 10: some false positives remain.)
    assert privacy.verhoeff_valid("234567890124")
    assert privacy.redact("My Aadhaar is 2345 6789 0124") == "My Aadhaar is [ID]"
    assert (
        privacy.redact("Batches 2021 2022 2023 are eligible")
        == "Batches 2021 2022 2023 are eligible"
    )


def test_fees_and_short_numbers_survive():
    text = "The fee is Rs. 365 and the helpline is open 10 to 5."
    assert privacy.redact(text) == text


def test_trace_style_labels():
    assert privacy.redact("a@b.co", style="trace") == "[EMAIL_REDACTED]"


# ------------------------------------------------------------------- security


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_client_ip_ignores_forwarded_header_from_untrusted_peer():
    assert get_client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_client_ip_uses_forwarded_header_from_trusted_proxy():
    assert get_client_ip(_request("172.18.0.5", "198.51.100.7")) == "198.51.100.7"


def test_client_ip_skips_spoofed_left_entries():
    # Client forged "1.1.1.1"; the trusted proxy appended the real address.
    assert (
        get_client_ip(_request("172.18.0.5", "1.1.1.1, 198.51.100.7")) == "198.51.100.7"
    )


def test_session_token_roundtrip_and_tamper_detection():
    expires = int(time.time()) + 60
    token = issue_session_token("abc123", expires)
    assert verify_session_token(token) == "abc123"
    assert verify_session_token(token.replace("abc123", "evil99")) is None
    assert verify_session_token(token + "x") is None
    assert verify_session_token("garbage") is None


def test_expired_session_token_is_rejected():
    token = issue_session_token("abc123", int(time.time()) - 1)
    assert verify_session_token(token) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "'self'"),
        (
            "https://www.tamilnadunursingcouncil.com",
            "'self' https://www.tamilnadunursingcouncil.com",
        ),
        (
            "'self' https://a.org, https://b.org:8443/",
            "'self' https://a.org https://b.org:8443",
        ),
        ("https://*.gov.in", "'self' https://*.gov.in"),
    ],
)
def test_widget_frame_ancestors_are_normalised(raw, expected):
    """Regression: quoted CSP keywords were mangled by env-file parsers."""
    assert Settings(WIDGET_FRAME_ANCESTORS=raw).widget_frame_ancestors == expected


@pytest.mark.parametrize(
    "bad", ["*", "javascript:alert(1)", "https://a.org/path", "a.org"]
)
def test_widget_frame_ancestors_rejects_non_origins(bad):
    with pytest.raises(ValidationError):
        Settings(WIDGET_FRAME_ANCESTORS=bad)


@pytest.mark.parametrize(
    "phone",
    [
        "9876543210",
        "+91 98765 43210",
        "919876543210",
        "09876543210",
        "987 654 3210",
        "98765-43210",
    ],
)
def test_mobile_number_formats_are_redacted(phone):
    """Regression (independent review): 91-prefixed and 3-3-4 formats were missed."""
    assert privacy.redact(f"call me on {phone} please") == "call me on [PHONE] please"


@pytest.mark.parametrize(
    "text",
    [
        "Are fees 60000-70000 per year?",
        "The stipend is Rs. 9000 to Rs. 12000.",
        "Helpline 044-2493 4792 is open 10 am to 5 pm.",
        "Registration number 2021/123456 and fee Rs. 365.",
    ],
)
def test_money_and_council_numbers_are_not_redacted(text):
    assert privacy.redact(text) == text


def test_docker_secrets_beat_env_and_empty_env_is_ignored(tmp_path, monkeypatch):
    """Regression (final review M2): env vars, even empty ones, overrode /run/secrets."""
    (tmp_path / "ADMIN_API_KEY").write_text("from-secret-file-0123456789")
    (tmp_path / "LLM_API_KEY").write_text("sk-from-secret")
    monkeypatch.setenv("ADMIN_API_KEY", "admin:admin@123")  # leftover default in .env
    monkeypatch.setenv("LLM_API_KEY", "")  # `LLM_API_KEY=` from the template
    s = Settings(_secrets_dir=tmp_path)  # type: ignore[call-arg]
    assert s.ADMIN_API_KEY.get_secret_value() == "from-secret-file-0123456789"
    assert (
        s.LLM_API_KEY is not None
        and s.LLM_API_KEY.get_secret_value() == "sk-from-secret"
    )


def test_env_example_has_no_empty_values_with_inline_comments():
    """Regression (final review H2): Docker Compose reads `KEY=   # note` as the
    value '# note', which broke LLM_BASE_URL and BACKUP_TARGET_URL."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    bad = [
        line
        for line in (root / ".env.example").read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Z_]+=\s+#", line)
    ]
    assert bad == []
