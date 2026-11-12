"""Account endpoints: sign-up, sign-in, sign-out, password change, data rights.

Every state-changing route requires the CSRF header (see api/auth.py).
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.accounts import service
from nursing_bot.accounts.errors import InvalidCredentialsError
from nursing_bot.accounts.passwords import verify_password
from nursing_bot.accounts.sessions import (
    SignedInUser,
    end_all_sessions,
    end_session,
    hash_token,
    start_session,
)
from nursing_bot.api.auth import (
    clear_session_cookie,
    get_current_user,
    require_csrf,
    require_user,
    require_user_allow_pending,
    session_cookie,
    set_session_cookie,
)
from nursing_bot.api.dependencies import get_db
from nursing_bot.api.schemas import (
    DeleteAccountRequest,
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdate,
    SessionResponse,
    SignupRequest,
    UserResponse,
)
from nursing_bot.api.security import limiter
from nursing_bot.config import get_settings
from nursing_bot.storage.models import Conversation, Message, MessageFeedback, User

router = APIRouter(tags=["Account"])


def _user_response(user: User | SignedInUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


async def _replace_session(
    request: Request, response: Response, db: AsyncSession, user: User
) -> None:
    """End the browser's previous session (if any) and start a fresh one."""
    previous = session_cookie(request)
    if previous:
        await end_session(db, hash_token(previous))
    token = await start_session(db, user.id, request.headers.get("user-agent"))
    set_session_cookie(response, token)


@router.post(
    "/auth/signup",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
@limiter.limit(lambda: get_settings().SIGNUP_RATE_LIMIT)
async def signup(
    request: Request,
    response: Response,
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    if not get_settings().SIGNUP_ENABLED:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "New accounts are not being accepted."
        )
    if not body.accept_privacy:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Please read and accept the privacy notice to create an account.",
        )
    user = await service.create_user(
        db, body.email, body.password, display_name=body.display_name
    )
    await _replace_session(request, response, db, user)
    return _user_response(user)


@router.post(
    "/auth/login", response_model=UserResponse, dependencies=[Depends(require_csrf)]
)
@limiter.limit(lambda: get_settings().LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await service.authenticate(db, body.email, body.password)
    await _replace_session(request, response, db, user)
    return _user_response(user)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def logout(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    token = session_cookie(request)
    if token:
        await end_session(db, hash_token(token))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.post(
    "/auth/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def logout_all(
    user: SignedInUser = Depends(require_user_allow_pending),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await end_all_sessions(db, user.id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


@router.get("/auth/session", response_model=SessionResponse)
async def current_session(
    user: SignedInUser | None = Depends(get_current_user),
) -> SessionResponse:
    """Who is signed in, if anyone. Always 200, so a guest's page load is clean."""
    return SessionResponse(user=_user_response(user) if user else None)


@router.get("/auth/me", response_model=UserResponse)
async def me(user: SignedInUser | None = Depends(get_current_user)) -> Any:
    """The signed-in user, or 401 (the app then shows guest mode)."""
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    return _user_response(user)


@router.post(
    "/auth/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
@limiter.limit(lambda: get_settings().LOGIN_RATE_LIMIT)
async def change_password(
    request: Request,
    body: PasswordChangeRequest,
    user: SignedInUser = Depends(require_user_allow_pending),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Also completes an admin reset. Other devices are signed out."""
    await service.change_password(
        db,
        user.id,
        body.current_password,
        body.new_password,
        keep_session_hash=user.session_hash,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/me", response_model=UserResponse, dependencies=[Depends(require_csrf)])
async def update_profile(
    body: ProfileUpdate,
    user: SignedInUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    row = await db.get(User, user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    row.display_name = (body.display_name or "").strip() or None
    await db.commit()
    return _user_response(row)


@router.get("/me/export")
async def export_my_data(
    user: SignedInUser = Depends(require_user), db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    """Everything stored about the signed-in user, as one JSON file."""
    account = await db.get(User, user.id)
    assert account is not None
    conversations = (
        await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.created_at)
        )
    ).scalars()
    exported: list[dict[str, Any]] = []
    for conversation in conversations:
        messages = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at)
            )
        ).scalars()
        exported.append(
            {
                "id": str(conversation.id),
                "title": conversation.title,
                "pinned": conversation.pinned,
                "archived_at": _iso(conversation.archived_at),
                "created_at": _iso(conversation.created_at),
                "updated_at": _iso(conversation.updated_at),
                "messages": [
                    {
                        "id": str(m.id),
                        "role": m.role,
                        "content": m.content,
                        "answer_mode": m.answer_mode,
                        "citations": m.citations_json,
                        "status": m.status,
                        "imported": m.is_imported,
                        "created_at": _iso(m.created_at),
                    }
                    for m in messages
                ],
            }
        )
    feedback = (
        await db.execute(
            select(MessageFeedback).where(MessageFeedback.user_id == user.id)
        )
    ).scalars()
    payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "account": {
            "id": str(account.id),
            "email": account.email,
            "display_name": account.display_name,
            "role": account.role,
            "created_at": _iso(account.created_at),
            "last_login_at": _iso(account.last_login_at),
            "privacy_accepted_at": _iso(account.privacy_accepted_at),
        },
        "conversations": exported,
        "feedback": [
            {
                "message_id": str(f.message_id),
                "rating": f.rating,
                "comment": f.comment,
                "created_at": _iso(f.created_at),
            }
            for f in feedback
        ],
    }
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": 'attachment; filename="nursingbot-my-data.json"',
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/me", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_csrf)]
)
@limiter.limit(lambda: get_settings().LOGIN_RATE_LIMIT)
async def delete_my_account(
    request: Request,
    body: DeleteAccountRequest,
    user: SignedInUser = Depends(require_user_allow_pending),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Permanently delete the account and all its chats (password required)."""
    account = await db.get(User, user.id)
    if account is None or not verify_password(account.password_hash, body.password):
        raise InvalidCredentialsError("Your password is incorrect.")
    await service.delete_user(db, user.id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response)
    return response


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
