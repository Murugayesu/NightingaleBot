"""chat session budgets, eval_runs, unique content hash, 1-based page numbers

Revision ID: 003_sessions_eval_dedup
Revises: 002_fix_schema_drift
Create Date: 2026-11-06 00:00:00.000000

1. documents.content_hash becomes UNIQUE so two concurrent uploads of the same
   file cannot both be stored. Fails with a clear message if duplicates exist.
2. chat_sessions gains tokens_used (per-session LLM token budget) and expires_at.
3. New eval_runs table stores evaluation results (retrieval hit rate, answer checks).
4. PDF page numbers were stored 0-based ("Page: 0" in citations). Shift existing
   chunks to 1-based: the column, metadata.page and the "[Page: N]" header.
5. Drops active_embedding_model, which nothing ever used. Each chunk already
   records its embedding_model and dense search filters on it.
"""

import json
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "003_sessions_eval_dedup"
down_revision = "002_fix_schema_drift"
branch_labels = None
depends_on = None

_PAGE_HEADER = re.compile(r"\[Page: (\d+)\]")


def _shift_pages(delta: int) -> None:
    """Add `delta` to every stored page number, in the column, metadata and header text."""
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                "SELECT id, contextualised_text, metadata FROM chunks "
                "WHERE page_number IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        new_text = _PAGE_HEADER.sub(
            lambda m: f"[Page: {int(m.group(1)) + delta}]",
            row["contextualised_text"],
            count=1,
        )
        metadata = dict(row["metadata"] or {})
        if isinstance(metadata.get("page"), int):
            metadata["page"] += delta
        bind.execute(
            sa.text(
                "UPDATE chunks SET page_number = page_number + :delta, "
                "contextualised_text = :text, metadata = CAST(:metadata AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "delta": delta,
                "text": new_text,
                "metadata": json.dumps(metadata),
                "id": row["id"],
            },
        )


def upgrade() -> None:
    # 1. unique content hash
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT content_hash FROM documents GROUP BY content_hash HAVING count(*) > 1) THEN
            RAISE EXCEPTION 'documents contains duplicate content_hash values. Delete the duplicates '
                            '(SELECT content_hash, array_agg(id) FROM documents GROUP BY 1 HAVING count(*) > 1) '
                            'and run the migration again.';
          END IF;
        END $$;
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_documents_content_hash")
    op.create_index(
        "ix_documents_content_hash", "documents", ["content_hash"], unique=True
    )

    # 2. session budget + expiry
    op.add_column(
        "chat_sessions",
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. eval runs
    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="queued"
        ),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("results", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_eval_runs_created_at", "eval_runs", ["created_at"])

    # 4. 1-based page numbers
    _shift_pages(+1)

    # 5. unused table
    op.execute("DROP TABLE IF EXISTS active_embedding_model")


def downgrade() -> None:
    op.create_table(
        "active_embedding_model",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    _shift_pages(-1)
    op.drop_index("ix_eval_runs_created_at", table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_column("chat_sessions", "expires_at")
    op.drop_column("chat_sessions", "tokens_used")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])
