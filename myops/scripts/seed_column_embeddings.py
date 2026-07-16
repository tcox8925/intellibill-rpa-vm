"""
Seed JAI column embeddings from live database metadata.

Run from the backend root:
    python -m scripts.seed_column_embeddings

This script:
1. Enables pgvector extension and creates the jay_column_embeddings table
2. Profiles all columns in the wpo schema and generates embeddings
3. Verifies the results with summary statistics
"""

import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import engine, SessionLocal


def main():
    print("=" * 60)
    print("JAI Column Embeddings Setup")
    print("=" * 60)

    # ---------------------------------------------------------
    # Step 1: Enable pgvector + create table
    # ---------------------------------------------------------
    print("\n[1/3] Creating jay_column_embeddings table...")
    migration_sql_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "migrations", "create_jay_column_embeddings.sql",
    )

    if not os.path.isfile(migration_sql_path):
        print(f"  ERROR: Migration file not found at {migration_sql_path}")
        print("  Please ensure scripts/migrations/create_jay_column_embeddings.sql exists.")
        sys.exit(1)

    with open(migration_sql_path, "r") as f:
        sql = f.read()

    with engine.connect() as conn:
        # Check for pgvector extension availability before running migration
        try:
            result = conn.execute(text(
                "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
            )).fetchone()
            if result is None:
                print(
                    "  ERROR: pgvector extension is not available on this PostgreSQL server.\n"
                    "  To install pgvector:\n"
                    "    - On Ubuntu/Debian: sudo apt install postgresql-16-pgvector\n"
                    "    - On macOS (Homebrew): brew install pgvector\n"
                    "    - On Azure Database for PostgreSQL: enable via Azure Portal "
                    "(Server parameters > azure.extensions > add VECTOR)\n"
                    "    - From source: https://github.com/pgvector/pgvector#installation\n"
                    "  After installing, restart PostgreSQL and re-run this script."
                )
                sys.exit(1)
        except Exception as e:
            print(f"  WARNING: Could not check pgvector availability: {e}")
            print("  Proceeding anyway -- the CREATE EXTENSION statement may fail.")

        # Execute each statement separately (split on semicolons)
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement and not statement.startswith("--"):
                try:
                    conn.execute(text(statement))
                except Exception as e:
                    print(f"  SKIP: {statement[:60]}... ({e})")
        conn.commit()
    print("  Table created/verified.")

    # ---------------------------------------------------------
    # Step 2: Profile and embed all columns
    # ---------------------------------------------------------
    print("\n[2/3] Profiling and embedding columns...")

    try:
        from app.utils.jay.column_embedding_sync import sync_column_embeddings
    except ImportError as e:
        print(f"  ERROR: Could not import column_embedding_sync: {e}")
        print("  Please ensure app/utils/jay/column_embedding_sync.py exists")
        print("  (Task 1.5 of the JAI Intelligence Upgrade must be completed first).")
        print("\n  Skipping profiling/embedding step. Table was created successfully.")
        print("  Re-run this script after column_embedding_sync is implemented.")
        # Still run verification to confirm table exists
        _verify()
        sys.exit(0)

    session = SessionLocal()
    try:
        stats = sync_column_embeddings(session, force=True)
        print(f"  Total columns:    {stats['total_columns']}")
        print(f"  Changed columns:  {stats['changed_columns']}")
        print(f"  Embedded columns: {stats['embedded_columns']}")
        print(f"  Skipped:          {stats['skipped_columns']}")
        if stats.get("errors"):
            print(f"  Errors:           {stats['errors']}")
    except Exception as e:
        print(f"  ERROR during profiling/embedding: {e}")
        print("  The table was created but embedding failed.")
        print("  Check your Azure OpenAI credentials and JAI_EMBEDDING_DEPLOYMENT env var.")
        raise
    finally:
        session.close()

    # ---------------------------------------------------------
    # Step 3: Verify
    # ---------------------------------------------------------
    _verify()

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


def _verify():
    """Query jay_column_embeddings and print summary statistics."""
    print("\n[3/3] Verifying...")
    session = SessionLocal()
    try:
        result = session.execute(text(
            "SELECT COUNT(*), "
            "COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END), "
            "COUNT(DISTINCT table_name) "
            "FROM wpo.jay_column_embeddings"
        ))
        row = result.fetchone()
        print(f"  Total rows:        {row[0]}")
        print(f"  With embeddings:   {row[1]}")
        print(f"  Distinct tables:   {row[2]}")

        # Show top tables by column count
        result = session.execute(text(
            "SELECT table_name, COUNT(*) as col_count "
            "FROM wpo.jay_column_embeddings "
            "GROUP BY table_name "
            "ORDER BY col_count DESC "
            "LIMIT 10"
        ))
        rows = result.fetchall()
        if rows:
            print(f"\n  Top tables by column count:")
            for r in rows:
                print(f"    {r[0]}: {r[1]} columns")
    except Exception as e:
        print(f"  Verification query failed: {e}")
        print("  The table may not have been created successfully.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
