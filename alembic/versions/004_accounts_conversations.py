"""user accounts, sessions, conversations, feedback and daily token usage

Revision ID: 004_accounts_conversations
Revises: 003_sessions_eval_dedup
Create Date: 2026-11-12 00:00:00.000000

NursingBot 2.0 turns the embeddable widget into a standalone chatbot:

1. users: email + Argon2id password accounts with a role (user | admin).
   E-mail is unique case-insensitively (index on lower(email)).
2. auth_sessions: signed-in browser sessions; only a SHA-256 of the cookie
   token is stored.
3. conversations / messages: a signed-in user's saved chats. Each assistant
   message records how it was answered (answer_mode) and its citations.
4. message_feedback: thumbs up / down per user and message.
5. daily_token_usage: LLM tokens per UTC day for each user and for the whole
   service (quotas and the global spending cap).

Guest chat (chat_sessions) is unchanged.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "004_accounts_conversations"
down_revision = "003_sessions_eval_dedup"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("display_name", sa.String(length=80), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("privacy_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failed_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")

    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "title", sa.String(length=200), nullable=False, server_default="New chat"
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "summary_message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        *_timestamps(),
    )
    op.create_index(
        "idx_conversations_user_updated",
        "conversations",
        ["user_id", sa.text("updated_at DESC")],
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("answer_mode", sa.String(length=16), nullable=True),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="complete"
        ),
        sa.Column(
            "is_imported", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.CheckConstraint(
            "status IN ('complete', 'stopped', 'error')", name="ck_messages_status"
        ),
        sa.CheckConstraint(
            "answer_mode IS NULL OR answer_mode IN "
            "('grounded', 'general', 'declined', 'refused', 'out_of_scope')",
            name="ck_messages_answer_mode",
        ),
    )
    op.create_index(
        "idx_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "message_feedback",
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("rating IN (-1, 1)", name="ck_message_feedback_rating"),
    )
    op.create_index("ix_message_feedback_created", "message_feedback", ["created_at"])

    op.create_table(
        "daily_token_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("subject", sa.String(length=64), primary_key=True),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("daily_token_usage")
    op.drop_index("ix_message_feedback_created", table_name="message_feedback")
    op.drop_table("message_feedback")
    op.drop_index("idx_messages_conversation_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_conversations_user_updated", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")
    op.drop_table("users")
