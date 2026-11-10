"""fix schema drift, ensure tsv trigger, repair swapped chunk text columns (BUG-001)

Revision ID: 002_fix_schema_drift
Revises: 001_initial_schema
Create Date: 2026-11-04 00:00:00.000000

Why this migration is written defensively
-----------------------------------------
Existing deployments were NOT created by migration 001. They were created by
SQLAlchemy `Base.metadata.create_all()` from the ORM models and then stamped
with `alembic_version = '001_initial_schema'`. The two schemas differ, so this
revision must run correctly against either shape:

  * a 001-built database  -> chat_sessions.metadata missing, ids are varchar(128)
  * a create_all database -> metadata present, ids varchar(64), NO TRIGGERS

Every statement below is therefore conditional or idempotent.

What it does
------------
1. Guarantees the chunks_tsv_update() trigger exists. create_all() never creates
   triggers, so on those databases `tsv` was only ever populated because the
   application wrote it explicitly. The application no longer does (the trigger
   is the single source of truth), so without this step newly ingested chunks
   would get tsv = NULL and sparse retrieval would silently return nothing.

2. Repairs BUG-001. Ingestion used to put the context-prefixed text in `content`
   and the raw text in `contextualised_text` -- the opposite of what the names
   mean. The swap is predicated on the prefix, so it cannot double-apply.

3. Rebuilds `tsv` from `contextualised_text` for every row.

4. Aligns chat_sessions / chat_messages with the ORM models, and adds the
   composite index declared on the model.

Out of scope
------------
`tsv` is still built with the 'english' text search configuration. The corpus is
bilingual (see .artifacts/corpus-findings.md) and PostgreSQL ships no Tamil
configuration, so Tamil terms are not usefully stemmed. That needs its own
decision and its own migration.
"""

from alembic import op

revision = "002_fix_schema_drift"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. tsv trigger: create or replace unconditionally so both database
    #    shapes converge on the same definition.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION chunks_tsv_update() RETURNS trigger AS $$
        BEGIN
          NEW.tsv := to_tsvector('english', coalesce(NEW.contextualised_text, ''));
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS tsvector_chunks_tsv_trigger ON chunks")
    op.execute(
        """
        CREATE TRIGGER tsvector_chunks_tsv_trigger
        BEFORE INSERT OR UPDATE OF contextualised_text ON chunks
        FOR EACH ROW EXECUTE FUNCTION chunks_tsv_update();
        """
    )

    # ------------------------------------------------------------------
    # 2. BUG-001 repair. PostgreSQL evaluates every right-hand side against
    #    the OLD row, so one statement swaps the columns. The WHERE clause
    #    makes it self-limiting: after the swap `content` no longer carries
    #    the prefix, so re-running changes nothing.
    # ------------------------------------------------------------------
    op.execute(
        """
        UPDATE chunks
        SET content = contextualised_text,
            contextualised_text = content
        WHERE content LIKE '[Document:%'
          AND contextualised_text NOT LIKE '[Document:%'
        """
    )

    # 3. Rebuild tsv for every row from the correct column.
    op.execute(
        "UPDATE chunks "
        "SET contextualised_text = contextualised_text"  # fires the trigger
    )

    # ------------------------------------------------------------------
    # 4. chat_sessions / chat_messages alignment, all conditional.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE chat_sessions "
        "ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'"
    )

    # Narrow the id/FK pair to varchar(64) only if it is currently wider.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'chat_messages' AND column_name = 'session_id'
              AND character_maximum_length <> 64
          ) THEN
            ALTER TABLE chat_messages ALTER COLUMN session_id TYPE VARCHAR(64);
          END IF;
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'chat_sessions' AND column_name = 'id'
              AND character_maximum_length <> 64
          ) THEN
            ALTER TABLE chat_sessions ALTER COLUMN id TYPE VARCHAR(64);
          END IF;
        END $$;
        """
    )

    # citations is Optional on the model; 001 made it NOT NULL DEFAULT '[]'.
    op.execute("ALTER TABLE chat_messages ALTER COLUMN citations DROP NOT NULL")
    op.execute("ALTER TABLE chat_messages ALTER COLUMN citations DROP DEFAULT")

    # 5. Composite index declared on the Chunk model.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_doc_status "
        "ON chunks (document_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_doc_status")
    op.execute("ALTER TABLE chat_messages ALTER COLUMN citations SET DEFAULT '[]'")
    op.execute(
        "UPDATE chat_messages SET citations = '[]'::jsonb WHERE citations IS NULL"
    )
    op.execute("ALTER TABLE chat_messages ALTER COLUMN citations SET NOT NULL")

    # Put the text columns back the way they were.
    op.execute(
        """
        UPDATE chunks
        SET content = contextualised_text,
            contextualised_text = content
        WHERE contextualised_text LIKE '[Document:%'
          AND content NOT LIKE '[Document:%'
        """
    )
    # The tsv trigger is left in place on purpose: revision 001 creates it too,
    # so dropping it here would leave a "001" database without full-text indexing.
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS metadata")
