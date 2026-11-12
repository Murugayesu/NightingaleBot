"""Accounts against a real PostgreSQL: sign-up, sign-in, sessions, resets, admin."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from nursing_bot.cli import main as cli_main
from nursing_bot.storage.database import get_session
from nursing_bot.storage.models import AuthSession, User
from tests.integration.conftest import FEES_TXT

pytestmark = pytest.mark.integration

APP = {"X-Requested-With": "nursingbot"}
PASSWORD = "jasmine tea at marina"


async def signup(client, email="priya@example.org", password=PASSWORD, **extra):
    return await client.post(
        "/api/v1/auth/signup",
        headers=APP,
        json={"email": email, "password": password, "accept_privacy": True, **extra},
    )


async def login(client, email="priya@example.org", password=PASSWORD):
    return await client.post(
        "/api/v1/auth/login", headers=APP, json={"email": email, "password": password}
    )


async def make_admin(email: str) -> None:
    async with get_session() as db:
        await db.execute(update(User).where(User.email == email).values(role="admin"))
        await db.commit()


# ---------------------------------------------------------------- sign-up
async def test_signup_sets_an_httponly_cookie_and_signs_in(client):
    response = await signup(client, email="  Priya@Example.ORG ", display_name="Priya")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "priya@example.org" and body["role"] == "user"
    cookie = response.headers["set-cookie"]
    assert "nb_session=" in cookie and "HttpOnly" in cookie
    assert "SameSite=lax" in cookie and "Path=/api" in cookie
    assert "Secure" not in cookie, "plain HTTP by default (AUTH_COOKIE_SECURE=false)"
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["display_name"] == "Priya"
    session = (await client.get("/api/v1/auth/session")).json()
    assert session["user"]["email"] == "priya@example.org"
    async with get_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        assert user.password_hash.startswith("$argon2id$")
        assert user.privacy_accepted_at is not None
        stored = (await db.execute(select(AuthSession))).scalar_one()
        token = client.cookies.get("nb_session")
        assert token and stored.token_hash != token, "only the hash is stored"


async def test_secure_cookie_flag_follows_the_setting(client, settings_override):
    settings_override(AUTH_COOKIE_SECURE="true")
    response = await signup(client)
    assert "Secure" in response.headers["set-cookie"]


async def test_signup_rules(client, settings_override):
    assert (await signup(client, password="password123")).status_code == 422
    no_consent = await client.post(
        "/api/v1/auth/signup",
        headers=APP,
        json={"email": "a@example.org", "password": PASSWORD},
    )
    assert no_consent.status_code == 422 and "privacy" in no_consent.json()["detail"]
    assert (await signup(client)).status_code == 201
    duplicate = await signup(client, email="PRIYA@example.org")
    assert duplicate.status_code == 409
    settings_override(SIGNUP_ENABLED="false")
    assert (await signup(client, email="new@example.org")).status_code == 403


async def test_state_changing_requests_need_the_csrf_header(client):
    no_header = await client.post(
        "/api/v1/auth/signup",
        json={"email": "a@example.org", "password": PASSWORD, "accept_privacy": True},
    )
    assert no_header.status_code == 403
    cross_site = await client.post(
        "/api/v1/auth/login",
        headers={**APP, "Origin": "https://evil.example"},
        json={"email": "a@example.org", "password": PASSWORD},
    )
    assert cross_site.status_code == 403


# ---------------------------------------------------------------- sign-in
async def test_login_logout_and_wrong_passwords(client):
    await signup(client)
    await client.post("/api/v1/auth/logout", headers=APP)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    guest = await client.get("/api/v1/auth/session")
    assert guest.status_code == 200 and guest.json() == {"user": None}
    wrong = await login(client, password="not the right one")
    unknown = await login(client, email="nobody@example.org")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"], "no account probing"
    assert (await login(client, email="PRIYA@example.org")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_account_locks_after_repeated_failures(client, settings_override):
    settings_override(LOGIN_MAX_FAILURES="3", LOGIN_RATE_LIMIT="100/minute")
    await signup(client)
    for _ in range(3):
        assert (await login(client, password="wrong password!")).status_code == 401
    locked = await login(client)  # even the right password waits
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    async with get_session() as db:  # the lockout window passes
        await db.execute(
            update(User).values(
                last_failed_login_at=datetime.now(UTC) - timedelta(hours=1)
            )
        )
        await db.commit()
    assert (await login(client)).status_code == 200


async def test_login_is_rate_limited_per_ip(client, settings_override):
    settings_override(LOGIN_RATE_LIMIT="2/minute")
    codes = [(await login(client)).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


async def test_expired_or_revoked_sessions_are_rejected(client):
    await signup(client)
    async with get_session() as db:
        await db.execute(
            update(AuthSession).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await db.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_logout_all_signs_out_every_device(client):
    await signup(client)
    other_device = client.cookies.get("nb_session")
    await login(client)  # a second session
    assert (
        await client.post("/api/v1/auth/logout-all", headers=APP)
    ).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    client.cookies.set(
        "nb_session", other_device or "", domain="test.local", path="/api"
    )
    assert (await client.get("/api/v1/auth/me")).status_code == 401


# -------------------------------------------------------------- passwords
async def test_change_password_keeps_this_session_and_ends_others(client):
    await signup(client)
    async with get_session() as db:
        other = AuthSession(
            token_hash="f" * 64,
            user_id=(await db.execute(select(User.id))).scalar_one(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(other)
        await db.commit()
    bad = await client.post(
        "/api/v1/auth/password/change",
        headers=APP,
        json={"current_password": "wrong one!!", "new_password": "a whole new phrase"},
    )
    assert bad.status_code == 401
    weak = await client.post(
        "/api/v1/auth/password/change",
        headers=APP,
        json={"current_password": PASSWORD, "new_password": "qwertyuiop"},
    )
    assert weak.status_code == 422
    ok = await client.post(
        "/api/v1/auth/password/change",
        headers=APP,
        json={"current_password": PASSWORD, "new_password": "a whole new phrase"},
    )
    assert ok.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    async with get_session() as db:
        other_row = await db.get(AuthSession, "f" * 64)
        assert other_row is not None and other_row.revoked_at is not None
    await client.post("/api/v1/auth/logout", headers=APP)
    assert (await login(client, password="a whole new phrase")).status_code == 200


async def test_admin_reset_forces_a_new_password(client):
    await signup(client, email="admin@example.org")
    await make_admin("admin@example.org")
    await client.post("/api/v1/auth/logout", headers=APP)
    await signup(client, email="nurse@example.org")
    nurse_id = (await client.get("/api/v1/auth/me")).json()["id"]
    await client.post("/api/v1/auth/logout", headers=APP)

    await login(client, email="admin@example.org")
    reset = await client.post(
        f"/api/v1/admin/users/{nurse_id}/reset-password", headers=APP
    )
    assert reset.status_code == 200, reset.text
    temporary = reset.json()["temporary_password"]
    await client.post("/api/v1/auth/logout", headers=APP)

    assert (await login(client, email="nurse@example.org")).status_code == 401
    signed_in = await login(client, email="nurse@example.org", password=temporary)
    assert signed_in.json()["must_change_password"] is True
    blocked = await client.get("/api/v1/conversations")
    assert blocked.status_code == 403 and "new password" in blocked.json()["detail"]
    changed = await client.post(
        "/api/v1/auth/password/change",
        headers=APP,
        json={"current_password": temporary, "new_password": "my own new phrase"},
    )
    assert changed.status_code == 204
    assert (await client.get("/api/v1/conversations")).status_code == 200


def test_cli_creates_admins_and_resets_passwords(db_clean_sync, capsys):
    assert cli_main(["create-user", "boss@example.org", "--admin"]) == 0
    out = capsys.readouterr().out
    assert "Temporary password" in out and "admin account boss@example.org" in out
    assert cli_main(["create-user", "boss@example.org"]) == 1, "duplicate"
    assert cli_main(["reset-password", "BOSS@example.org"]) == 0
    assert "Temporary password" in capsys.readouterr().out
    assert cli_main(["set-role", "boss@example.org", "user"]) == 0
    assert "is now a user" in capsys.readouterr().out
    assert cli_main(["reset-password", "ghost@example.org"]) == 1


# ------------------------------------------------------------------- admin
async def test_admin_accounts_can_use_the_admin_api(client, storage):
    await signup(client)
    plain = await client.get("/api/v1/admin/stats")
    assert plain.status_code == 403, "a normal user is not an admin"
    await make_admin("priya@example.org")
    stats = await client.get("/api/v1/admin/stats")
    assert stats.status_code == 200 and stats.json()["total_users"] == 1
    # Uploads authenticate before the body is read: cookie + CSRF header.
    no_csrf = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("fees.txt", FEES_TXT, "text/plain")},
    )
    assert no_csrf.status_code == 401
    uploaded = await client.post(
        "/api/v1/documents/upload",
        headers=APP,
        files={"file": ("fees.txt", FEES_TXT, "text/plain")},
        data={"doc_type": "faq"},
    )
    assert uploaded.status_code == 201, uploaded.text


async def test_admin_user_management_guards(client):
    await signup(client, email="admin@example.org")
    await make_admin("admin@example.org")
    me = (await client.get("/api/v1/auth/me")).json()
    users = (await client.get("/api/v1/admin/users")).json()
    assert users["total"] == 1 and users["users"][0]["email"] == "admin@example.org"
    demote_self = await client.patch(
        f"/api/v1/admin/users/{me['id']}", headers=APP, json={"role": "user"}
    )
    assert demote_self.status_code == 400
    reset_self = await client.post(
        f"/api/v1/admin/users/{me['id']}/reset-password", headers=APP
    )
    assert reset_self.status_code == 400


async def test_disabling_an_account_signs_it_out(client):
    await signup(client, email="admin@example.org")
    await make_admin("admin@example.org")
    await client.post("/api/v1/auth/logout", headers=APP)
    await signup(client, email="nurse@example.org")
    nurse_cookie = client.cookies.get("nb_session")
    nurse_id = (await client.get("/api/v1/auth/me")).json()["id"]
    await login(client, email="admin@example.org")
    disabled = await client.patch(
        f"/api/v1/admin/users/{nurse_id}", headers=APP, json={"disabled": True}
    )
    assert disabled.status_code == 200 and disabled.json()["disabled"] is True
    client.cookies.clear()
    client.cookies.set(
        "nb_session", nurse_cookie or "", domain="test.local", path="/api"
    )
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await login(client, email="nurse@example.org")).status_code == 403


# ------------------------------------------------------------- data rights
async def test_export_and_delete_my_account(client):
    await signup(client)
    conversation = (
        await client.post("/api/v1/conversations", headers=APP, json={"title": "Fees"})
    ).json()
    export = await client.get("/api/v1/me/export")
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
    data = export.json()
    assert data["account"]["email"] == "priya@example.org"
    assert data["conversations"][0]["id"] == conversation["id"]
    assert "password_hash" not in str(data)

    wrong = await client.request(
        "DELETE", "/api/v1/me", headers=APP, json={"password": "not my password"}
    )
    assert wrong.status_code == 401
    deleted = await client.request(
        "DELETE", "/api/v1/me", headers=APP, json={"password": PASSWORD}
    )
    assert deleted.status_code == 204
    async with get_session() as db:
        assert (await db.execute(select(User))).first() is None
    assert (await client.get("/api/v1/auth/me")).status_code == 401
