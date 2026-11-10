"""initial_schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-11-01 12:00:00.000000

"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

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

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("s3_version_id", sa.String(length=256), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column(
            "doc_type", sa.String(length=64), nullable=False, server_default="general"
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
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
    )
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contextualised_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "embedding_model",
            sa.String(length=128),
            nullable=False,
            server_default="openai/text-embedding-3-small",
        ),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column(
            "heading_hierarchy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="active"
        ),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
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
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_status", "chunks", ["status"])
    op.create_index("idx_chunks_doc_status", "chunks", ["document_id", "status"])

    # HNSW index for vector cosine similarity (m=16, ef_construction=64)
    op.execute(
        "CREATE INDEX idx_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )

    # GIN index for full-text search
    op.create_index("idx_chunks_tsv_gin", "chunks", ["tsv"], postgresql_using="gin")

    # GIN index for JSONB metadata filtering
    op.execute(
        "CREATE INDEX idx_chunks_metadata_gin ON chunks USING gin (metadata jsonb_path_ops);"
    )

    # Trigger to maintain tsv column from contextualised_text
    op.execute(
        "CREATE OR REPLACE FUNCTION chunks_tsv_update() RETURNS trigger AS $$\n"
        "BEGIN\n"
        "  NEW.tsv := to_tsvector('english', coalesce(NEW.contextualised_text, ''));\n"
        "  RETURN NEW;\n"
        "END\n"
        "$$ LANGUAGE plpgsql;"
    )
    op.execute(
        "CREATE TRIGGER tsvector_chunks_tsv_trigger "
        "BEFORE INSERT OR UPDATE OF contextualised_text ON chunks "
        "FOR EACH ROW EXECUTE FUNCTION chunks_tsv_update();"
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=128), primary_key=True),
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
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=128),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.execute("DROP TRIGGER IF EXISTS tsvector_chunks_tsv_trigger ON chunks;")
    op.execute("DROP FUNCTION IF EXISTS chunks_tsv_update();")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("active_embedding_model")
