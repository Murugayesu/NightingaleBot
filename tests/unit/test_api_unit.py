"""API behaviour that needs no database: auth, validation, headers, errors."""

import pytest
from httpx import ASGITransport, AsyncClient

from nursing_bot import __version__
from nursing_bot.api.main import app

ADMIN = {"X-Admin-API-Key": "test-admin-key"}


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def test_health(client):
    response = await client.get("/health")
    assert response.json() == {"status": "ok", "version": __version__}


async def test_root_describes_the_api(client):
    response = await client.get("/")
    assert response.status_code == 200 and response.json()["health"] == "/health"


async def test_app_config_is_public(client):
    body = (await client.get("/api/v1/app/config")).json()
    assert body["require_session"] is True and body["max_query_chars"] == 4000
    assert body["sign_in_methods"] == ["password"]
    assert body["guest_chat_enabled"] is True and body["signup_enabled"] is True
    assert "not from official TNNMC records" in body["general_answer_label"]


async def test_the_old_widget_and_static_ui_are_gone(client):
    for path in ("/ui/widget/chat.html", "/ui/admin/", "/api/v1/widget/config"):
        assert (await client.get(path)).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/documents"),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000"),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000/chunks"),
        ("delete", "/api/v1/documents/00000000-0000-0000-0000-000000000000"),
        ("post", "/api/v1/documents/00000000-0000-0000-0000-000000000000/reingest"),
        ("get", "/api/v1/admin/health"),
        ("get", "/api/v1/admin/stats"),
        ("post", "/api/v1/admin/reindex"),
        ("get", "/api/v1/admin/eval/runs"),
    ],
)
async def test_admin_routes_require_key(client, method, path):
    response = await getattr(client, method)(path, headers={"X-Admin-API-Key": "wrong"})
    assert response.status_code == 401


async def test_upload_rejects_unsupported_type_before_reading(client):
    response = await client.post(
        "/api/v1/documents/upload",
        headers=ADMIN,
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 415


async def test_upload_rejects_non_object_metadata(client):
    response = await client.post(
        "/api/v1/documents/upload",
        headers=ADMIN,
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"metadata_json": "[1, 2]"},
    )
    assert response.status_code == 400 and "JSON object" in response.json()["detail"]


async def test_chat_requires_session_token(client):
    response = await client.post("/api/v1/chat", json={"query": "fee?"})
    assert response.status_code == 401


async def test_chat_rejects_forged_token(client):
    response = await client.post(
        "/api/v1/chat",
        json={"query": "fee?"},
        headers={"X-Session-Token": "abc.9999999999.forged"},
    )
    assert response.status_code == 401


async def test_chat_validates_query_length(client, settings_override):
    settings_override(CHAT_REQUIRE_SESSION="false")
    response = await client.post("/api/v1/chat", json={"query": "x" * 4001})
    assert response.status_code == 422


async def test_session_history_requires_matching_token(client):
    response = await client.get("/api/v1/chat/sessions/someone-else")
    assert response.status_code == 401


async def test_api_responses_carry_strict_security_headers(client):
    response = await client.get("/health")
    assert (
        response.headers["content-security-policy"]
        == "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_provider_outage_returns_503_json(client, settings_override):
    """Found by the live test: a down LLM gateway produced a bare 'Internal Server Error'."""
    import httpx as _httpx
    import openai

    from nursing_bot.api.dependencies import get_pipeline_dep

    class DownPipeline:
        async def aquery(self, *args, **kwargs):
            raise openai.APIConnectionError(
                request=_httpx.Request("POST", "http://gw/v1")
            )

    settings_override(CHAT_REQUIRE_SESSION="false")
    app.dependency_overrides[get_pipeline_dep] = lambda: DownPipeline()
    response = await client.post("/api/v1/chat", json={"query": "fee?"})
    assert response.status_code == 503
    assert response.json()["detail"].startswith(
        "The assistant is temporarily unavailable"
    )
    assert response.headers["retry-after"] == "30"


async def test_unexpected_error_returns_json_500(settings_override):
    from nursing_bot.api.dependencies import get_pipeline_dep

    class BrokenPipeline:
        async def aquery(self, *args, **kwargs):
            raise RuntimeError("bug")

    settings_override(CHAT_REQUIRE_SESSION="false")
    app.dependency_overrides[get_pipeline_dep] = lambda: BrokenPipeline()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.post("/api/v1/chat", json={"query": "fee?"})
    app.dependency_overrides.clear()
    assert response.status_code == 500 and response.json() == {
        "detail": "Internal server error."
    }


async def test_upload_without_admin_key_is_rejected_before_the_body_is_read(client):
    """Regression (final review H1): FastAPI parsed (and spooled to disk) the
    whole multipart body before the auth dependency ran."""
    response = await client.post(
        "/api/v1/documents/upload", files={"file": ("a.txt", b"x" * 1000, "text/plain")}
    )
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"  # still has headers


async def test_oversized_upload_is_rejected_from_content_length(
    client, settings_override
):
    settings_override(MAX_UPLOAD_SIZE_BYTES="1000")
    big = b"x" * (2 * 1024 * 1024)  # over 1000 bytes + 1 MiB multipart allowance
    response = await client.post(
        "/api/v1/documents/upload",
        headers=ADMIN,
        files={"file": ("a.txt", big, "text/plain")},
    )
    assert response.status_code == 413


async def test_oversized_json_body_is_rejected(client):
    response = await client.post(
        "/api/v1/chat",
        content=b'{"query": "' + b"x" * (1024 * 1024 + 10) + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_chunked_request_without_length_is_rejected(client):
    async def body():
        yield b'{"query": "fee?"}'

    response = await client.post(
        "/api/v1/chat", content=body(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 411


async def test_non_ascii_session_token_gives_401_not_500(client):
    """Regression (final review L1): compare_digest raised TypeError on non-ASCII."""
    response = await client.post(
        "/api/v1/chat",
        json={"query": "fee?"},
        headers={"X-Session-Token": "a.9999999999.éé".encode("latin-1")},
    )
    assert response.status_code == 401
