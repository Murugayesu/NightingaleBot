"""Admin: user accounts (list, role, disable, password reset) and answer feedback.

Admins never see users' conversations. The only conversation text shown
here is an exchange a user chose to rate, without the user's identity.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nursing_bot.accounts import service
from nursing_bot.accounts.errors import AccountError, UserNotFoundError
from nursing_bot.api.auth import AdminPrincipal, require_admin
from nursing_bot.api.dependencies import get_db
from nursing_bot.api.schemas import (
    AdminUserItem,
    AdminUserList,
    AdminUserUpdate,
    FeedbackItem,
    FeedbackList,
    TemporaryPasswordResponse,
)
from nursing_bot.storage.models import (
    Conversation,
    Message,
    MessageFeedback,
    User,
    UserRole,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


def _item(user: User, conversations: int = 0) -> AdminUserItem:
    return AdminUserItem(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
        disabled=user.disabled_at is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        conversations=conversations,
    )


@router.get("/users", response_model=AdminUserList)
async def list_users(
    query: str | None = Query(None, max_length=254),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _: AdminPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserList:
    conversation_count = (
        select(func.count(Conversation.id))
        .where(Conversation.user_id == User.id)
        .scalar_subquery()
    )
    statement = select(User, conversation_count.label("conversations"))
    counter = select(func.count(User.id))
    if query and query.strip():
        pattern = "%" + query.strip().lower().replace("%", "").replace("_", "") + "%"
        statement = statement.where(User.email.like(pattern))
        counter = counter.where(User.email.like(pattern))
    rows = await db.execute(
        statement.order_by(User.created_at.desc()).offset(offset).limit(limit)
    )
    return AdminUserList(
        users=[_item(user, int(n or 0)) for user, n in rows.all()],
        total=int(await db.scalar(counter) or 0),
    )


async def _not_self(principal: AdminPrincipal, user_id: uuid.UUID, action: str) -> None:
    if principal.user is not None and principal.user.id == user_id:
        raise AccountError(f"You cannot {action} your own account here.")


async def _active_admins(db: AsyncSession) -> int:
    return int(
        await db.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.ADMIN, User.disabled_at.is_(None)
            )
        )
        or 0
    )


@router.patch("/users/{user_id}", response_model=AdminUserItem)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    principal: AdminPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserItem:
    """Change a role or disable / re-enable an account (signs it out everywhere)."""
    target = await db.get(User, user_id)
    if target is None:
        raise UserNotFoundError("Account not found.")
    removing_admin = target.role == UserRole.ADMIN and (
        body.role == UserRole.USER or body.disabled is True
    )
    if removing_admin:
        await _not_self(principal, user_id, "demote or disable")
        if await _active_admins(db) <= 1:
            raise AccountError("At least one active admin account must remain.")
    user = target
    if body.role is not None:
        user = await service.set_role(db, user_id, body.role)
    if body.disabled is not None:
        await _not_self(principal, user_id, "disable")
        user = await service.set_disabled(db, user_id, body.disabled)
    return _item(user)


@router.post(
    "/users/{user_id}/reset-password", response_model=TemporaryPasswordResponse
)
async def reset_password(
    user_id: uuid.UUID,
    principal: AdminPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TemporaryPasswordResponse:
    """Issue a one-time temporary password; the user is signed out everywhere."""
    await _not_self(principal, user_id, "reset the password of")
    temporary = await service.admin_reset_password(db, user_id)
    return TemporaryPasswordResponse(
        email=temporary.email, temporary_password=temporary.password
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    principal: AdminPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete an account and all its conversations (e.g. on a deletion request)."""
    await _not_self(principal, user_id, "delete")
    target = await db.get(User, user_id)
    is_admin = target is not None and target.role == UserRole.ADMIN
    if is_admin and await _active_admins(db) <= 1:
        raise AccountError("At least one active admin account must remain.")
    await service.delete_user(db, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/feedback", response_model=FeedbackList)
async def list_feedback(
    rating: int | None = Query(None, ge=-1, le=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _: AdminPrincipal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> FeedbackList:
    """Rated answers with their question, newest first. No user identities."""
    statement = select(MessageFeedback, Message).join(
        Message, Message.id == MessageFeedback.message_id
    )
    counter = select(func.count()).select_from(MessageFeedback)
    if rating in (-1, 1):
        statement = statement.where(MessageFeedback.rating == rating)
        counter = counter.where(MessageFeedback.rating == rating)
    rows = (
        await db.execute(
            statement.order_by(MessageFeedback.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = []
    for feedback, answer in rows:
        question = await db.scalar(
            select(Message.content)
            .where(
                Message.conversation_id == answer.conversation_id,
                Message.role == "user",
                Message.created_at < answer.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        items.append(
            FeedbackItem(
                message_id=answer.id,
                rating=feedback.rating,
                comment=feedback.comment,
                question=question or "",
                answer=answer.content,
                answer_mode=answer.answer_mode,
                created_at=feedback.created_at,
            )
        )
    return FeedbackList(items=items, total=int(await db.scalar(counter) or 0))
