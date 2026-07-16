"""
JAI v2 database migration.

Run from the backend root:
    python -m scripts.migrate_jay_v2

This script (idempotent — safe to run multiple times):
1. Creates wpo.jay_table_metadata (embeddings stored as TEXT, similarity computed in Python)
2. Creates wpo.jay_query_cache (embeddings stored as TEXT, similarity computed in Python)
3. Adds fk_references, join_examples, and tags columns to wpo.jay_column_embeddings
4. Verifies all tables and new columns exist

NOTE: Embeddings are stored as TEXT (JSON array strings) instead of vector(1536)
because pgvector is not allow-listed on the Azure PostgreSQL instance.
Cosine similarity is computed in Python (fine for our dataset size of ~10 tables).
"""

import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import engine, SessionLocal


# ─────────────────────────────────────────────────────────────
# DDL Statements
# ─────────────────────────────────────────────────────────────

# Each entry is (label, sql) for clear status messages.
CREATE_STATEMENTS = [
    # ── jay_table_metadata ──
    (
        "wpo.jay_table_metadata table",
        """
        CREATE TABLE IF NOT EXISTS wpo.jay_table_metadata (
            id              SERIAL PRIMARY KEY,
            schema_name     VARCHAR(100) NOT NULL DEFAULT 'wpo',
            table_name      VARCHAR(200) NOT NULL,
            description     TEXT,
            columns         JSONB DEFAULT '[]'::jsonb,
            primary_keys    JSONB DEFAULT '[]'::jsonb,
            foreign_keys    JSONB DEFAULT '[]'::jsonb,
            join_examples   JSONB DEFAULT '[]'::jsonb,
            sample_queries  JSONB DEFAULT '[]'::jsonb,
            tags            TEXT[] DEFAULT '{}',
            embedding       TEXT,
            row_count       INTEGER,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT jay_table_metadata_uq UNIQUE (schema_name, table_name)
        )
        """,
    ),

    # ── jay_query_cache ──
    (
        "wpo.jay_query_cache table",
        """
        CREATE TABLE IF NOT EXISTS wpo.jay_query_cache (
            id              SERIAL PRIMARY KEY,
            query_text      TEXT NOT NULL,
            technical_spec  TEXT,
            sql_text        TEXT NOT NULL,
            module          VARCHAR(100),
            domains         TEXT[] DEFAULT '{}',
            embedding       TEXT,
            execution_count INTEGER DEFAULT 1,
            avg_exec_ms     FLOAT DEFAULT 0,
            last_used_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            success_count   INTEGER DEFAULT 0,
            failure_count   INTEGER DEFAULT 0,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
    ),

    # ── ALTER wpo.jay_column_embeddings ──
    (
        "wpo.jay_column_embeddings.fk_references column",
        """
        ALTER TABLE wpo.jay_column_embeddings
            ADD COLUMN IF NOT EXISTS fk_references JSONB DEFAULT '[]'::jsonb
        """,
    ),
    (
        "wpo.jay_column_embeddings.join_examples column",
        """
        ALTER TABLE wpo.jay_column_embeddings
            ADD COLUMN IF NOT EXISTS join_examples JSONB DEFAULT '[]'::jsonb
        """,
    ),
    (
        "wpo.jay_column_embeddings.tags column",
        """
        ALTER TABLE wpo.jay_column_embeddings
            ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}'
        """,
    ),
]


# ─────────────────────────────────────────────────────────────
# Step 1: Ensure wpo schema and pgvector extension exist
# ─────────────────────────────────────────────────────────────

def ensure_prerequisites():
    """Verify wpo schema exists."""
    print("\n[1/3] Checking prerequisites...")

    with engine.connect() as conn:
        # Ensure wpo schema exists
        try:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS wpo"))
            conn.commit()
            print("  wpo schema: ready")
        except Exception as e:
            print(f"  ERROR: Could not create wpo schema: {e}")
            sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Step 2: Run all DDL statements
# ─────────────────────────────────────────────────────────────

def run_migrations():
    """Execute each DDL statement with individual error handling."""
    print("\n[2/3] Running migration statements...")

    with engine.connect() as conn:
        for label, sql in CREATE_STATEMENTS:
            try:
                conn.execute(text(sql.strip()))
                conn.commit()
                print(f"  OK:   {label}")
            except Exception as e:
                # Rollback the failed statement so the connection stays usable
                conn.rollback()
                print(f"  SKIP: {label} -- {e}")

    print("\n  All migration statements processed.")


# ─────────────────────────────────────────────────────────────
# Step 3: Verify
# ─────────────────────────────────────────────────────────────

def verify():
    """Confirm tables exist and new columns are present."""
    print("\n[3/3] Verifying...")

    session = SessionLocal()
    try:
        # Row counts for new tables
        new_tables = [
            "wpo.jay_table_metadata",
            "wpo.jay_query_cache",
        ]
        for table in new_tables:
            try:
                result = session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                print(f"  {table}: {count} rows")
            except Exception as e:
                print(f"  {table}: ERROR - {e}")
                session.rollback()

        # Confirm new columns on jay_column_embeddings
        new_columns = ["fk_references", "join_examples", "tags"]
        for col in new_columns:
            try:
                session.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = 'wpo' "
                        "  AND table_name   = 'jay_column_embeddings' "
                        "  AND column_name  = :col"
                    ),
                    {"col": col},
                )
                # If query succeeds without error, check for a row
                result = session.execute(
                    text(
                        "SELECT column_name, data_type "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'wpo' "
                        "  AND table_name   = 'jay_column_embeddings' "
                        "  AND column_name  = :col"
                    ),
                    {"col": col},
                )
                row = result.fetchone()
                if row:
                    print(f"  wpo.jay_column_embeddings.{col}: present ({row[1]})")
                else:
                    print(f"  wpo.jay_column_embeddings.{col}: MISSING")
            except Exception as e:
                print(f"  wpo.jay_column_embeddings.{col}: ERROR - {e}")
                session.rollback()

    finally:
        session.close()


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("JAI v2 Migration Script")
    print("=" * 60)

    ensure_prerequisites()
    run_migrations()
    verify()

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)
