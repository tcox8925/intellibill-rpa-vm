"""
Seed JAI knowledge embeddings from config tables.

Runs the migration SQL to create wpo.jay_knowledge_embeddings, then seeds
vector embeddings from the 3 existing config tables:
  - jay_config_synonyms  -> category='synonym'
  - jay_config_glossary  -> category='glossary'
  - jay_config_query_examples -> category='example'

Usage: python -m scripts.seed_knowledge_embeddings
"""

import sys
import os
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from sqlalchemy import text
from app.db.session import engine, SessionLocal
from app.utils.jay.llm_client import embed_texts

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Path to the migration SQL file
_MIGRATION_SQL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "migrations",
    "create_jay_knowledge_embeddings.sql",
)

# Embedding batch size (stay within Azure OpenAI limits)
_EMBED_BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Step 1: Run migration
# ---------------------------------------------------------------------------

def run_migration():
    """Execute the migration SQL to create the knowledge embeddings table."""
    with open(_MIGRATION_SQL_PATH, "r") as f:
        sql = f.read()

    # Split on semicolons and execute each statement individually
    # (psycopg2 cannot execute multiple statements with CREATE INDEX IF NOT EXISTS)
    statements = [s.strip() for s in sql.split(";") if s.strip()]

    with engine.connect() as conn:
        for stmt in statements:
            # Skip pure comment blocks
            lines = [l for l in stmt.split("\n") if l.strip() and not l.strip().startswith("--")]
            if not lines:
                continue
            try:
                conn.execute(text(stmt))
            except Exception as e:
                # Log but continue (idempotent — table/index may already exist)
                logger.warning("Migration statement skipped: %s", str(e)[:120])
        conn.commit()

    print("[OK] Migration complete: wpo.jay_knowledge_embeddings table ready")


# ---------------------------------------------------------------------------
# Step 2: Load data from config tables
# ---------------------------------------------------------------------------

def _load_synonyms(session) -> list:
    """Load synonym rows from jay_config_synonyms and build embedding entries."""
    rows = session.execute(text("""
        SELECT term, db_column, notes
        FROM wpo.jay_config_synonyms
        WHERE is_active = TRUE
    """)).fetchall()

    entries = []
    for row in rows:
        term, db_column, notes = row[0], row[1], row[2] or ""
        embed_text = f"Business term '{term}' means database column '{db_column}'."
        if notes:
            embed_text += f" {notes}"
        entries.append({
            "category": "synonym",
            "text": embed_text,
            "metadata": json.dumps({
                "term": term,
                "db_column": db_column,
                "notes": notes,
            }),
            "score_weight": 1.0,
        })

    return entries


def _load_glossary(session) -> list:
    """Load glossary rows from jay_config_glossary and build embedding entries."""
    rows = session.execute(text("""
        SELECT term, description, sql_hint, module
        FROM wpo.jay_config_glossary
        WHERE is_active = TRUE
    """)).fetchall()

    entries = []
    for row in rows:
        term, description, sql_hint, module = row[0], row[1] or "", row[2] or "", row[3] or ""
        embed_text = f"Business concept: {term}. {description}."
        if sql_hint:
            embed_text += f" SQL pattern: {sql_hint}"
        entries.append({
            "category": "glossary",
            "text": embed_text,
            "metadata": json.dumps({
                "term": term,
                "description": description,
                "sql_hint": sql_hint,
                "module": module,
            }),
            "score_weight": 1.0,
        })

    return entries


def _load_query_examples(session) -> list:
    """Load query example rows from jay_config_query_examples and build embedding entries."""
    rows = session.execute(text("""
        SELECT name, example_type, sql_text, natural_query, domain_tags, module_tag
        FROM wpo.jay_config_query_examples
        WHERE is_active = TRUE
    """)).fetchall()

    entries = []
    for row in rows:
        name = row[0]
        example_type = row[1] or "sql_example"
        sql_text = row[2] or ""
        natural_query = row[3] or ""
        domain_tags = row[4] or ""
        module_tag = row[5] or ""

        # Build embedding text based on what's available
        if natural_query and sql_text:
            embed_text = f"Example query: {natural_query}. SQL: {sql_text}"
        elif sql_text:
            embed_text = f"Example query: {name}. SQL: {sql_text}"
        elif natural_query:
            embed_text = f"Example query: {natural_query}"
        else:
            embed_text = f"Example query: {name}"

        entries.append({
            "category": "example",
            "text": embed_text,
            "metadata": json.dumps({
                "name": name,
                "example_type": example_type,
                "sql_text": sql_text,
                "natural_query": natural_query,
                "domain_tags": domain_tags,
                "module_tag": module_tag,
            }),
            "score_weight": 1.0,
        })

    return entries


# ---------------------------------------------------------------------------
# Step 3: Embed and upsert
# ---------------------------------------------------------------------------

# We need a unique constraint for upsert — create it if missing
_CREATE_UNIQUE_CONSTRAINT = text("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'jay_knowledge_emb_category_text_uq'
        ) THEN
            -- Use an MD5 hash of text to keep index size manageable
            ALTER TABLE wpo.jay_knowledge_embeddings
                ADD CONSTRAINT jay_knowledge_emb_category_text_uq
                UNIQUE (category, md5(text));
        END IF;
    END $$;
""")

# Upsert using category + md5(text) as the conflict target
_UPSERT_SQL = text("""
    INSERT INTO wpo.jay_knowledge_embeddings (
        category, text, metadata, embedding, score_weight, is_active,
        created_at, updated_at
    ) VALUES (
        :category, :text, :metadata::jsonb, :embedding::vector,
        :score_weight, TRUE, :now, :now
    )
    ON CONFLICT (category, md5(text))
    DO UPDATE SET
        metadata     = EXCLUDED.metadata,
        embedding    = EXCLUDED.embedding,
        score_weight = EXCLUDED.score_weight,
        is_active    = TRUE,
        updated_at   = EXCLUDED.updated_at
""")


def _embed_and_upsert(session, entries: list) -> int:
    """Embed all entries and upsert into jay_knowledge_embeddings.

    Args:
        session: Active SQLAlchemy session.
        entries: List of dicts with keys: category, text, metadata, score_weight.

    Returns:
        Number of rows upserted.
    """
    if not entries:
        return 0

    # Extract texts for embedding
    texts = [e["text"] for e in entries]

    # Generate embeddings in batches
    all_embeddings = []
    for batch_start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch_end = min(batch_start + _EMBED_BATCH_SIZE, len(texts))
        batch_texts = texts[batch_start:batch_end]
        try:
            batch_embeddings = embed_texts(batch_texts)
            all_embeddings.extend(batch_embeddings)
            print(f"  Embedded batch [{batch_start}:{batch_end}] ({len(batch_texts)} texts)")
        except Exception as e:
            logger.error("Embedding failed for batch [%d:%d]: %s", batch_start, batch_end, e)
            raise

    # Upsert each entry
    now = datetime.now(timezone.utc)
    upserted = 0

    for entry, embedding in zip(entries, all_embeddings):
        try:
            session.execute(_UPSERT_SQL, {
                "category": entry["category"],
                "text": entry["text"],
                "metadata": entry["metadata"],
                "embedding": str(embedding),  # pgvector accepts text '[0.1, 0.2, ...]'
                "score_weight": entry["score_weight"],
                "now": now,
            })
            upserted += 1
        except Exception as e:
            logger.error(
                "Upsert failed for %s entry (text=%s...): %s",
                entry["category"],
                entry["text"][:60],
                e,
            )

    session.commit()
    return upserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("JAI Knowledge Embeddings - Seed Script")
    print("=" * 60)

    # Step 1: Run migration
    print("\n[1/5] Running migration...")
    run_migration()

    session = SessionLocal()
    try:
        # Step 1b: Ensure unique constraint exists for upsert
        print("\n[2/5] Ensuring unique constraint...")
        session.execute(_CREATE_UNIQUE_CONSTRAINT)
        session.commit()
        print("[OK] Unique constraint ready")

        # Step 2: Load data from config tables
        print("\n[3/5] Loading data from config tables...")
        synonym_entries = _load_synonyms(session)
        glossary_entries = _load_glossary(session)
        example_entries = _load_query_examples(session)

        print(f"  Synonyms:  {len(synonym_entries)} entries")
        print(f"  Glossary:  {len(glossary_entries)} entries")
        print(f"  Examples:  {len(example_entries)} entries")

        total_entries = synonym_entries + glossary_entries + example_entries
        if not total_entries:
            print("\n[WARN] No entries found in config tables. "
                  "Run 'python -m scripts.seed_jay_knowledge' first.")
            return

        # Step 3: Embed and upsert
        print(f"\n[4/5] Embedding and upserting {len(total_entries)} entries...")
        upserted = _embed_and_upsert(session, total_entries)

        # Step 4: Verify
        print(f"\n[5/5] Verification...")
        result = session.execute(text("""
            SELECT category, COUNT(*), ROUND(AVG(score_weight)::numeric, 2)
            FROM wpo.jay_knowledge_embeddings
            WHERE is_active = TRUE
            GROUP BY category
            ORDER BY category
        """)).fetchall()

        print(f"\n  {'Category':<15} {'Count':>6} {'Avg Weight':>12}")
        print(f"  {'-'*15} {'-'*6} {'-'*12}")
        total = 0
        for row in result:
            print(f"  {row[0]:<15} {row[1]:>6} {row[2]:>12}")
            total += row[1]
        print(f"  {'TOTAL':<15} {total:>6}")

        print(f"\n[DONE] {upserted} knowledge embeddings seeded successfully.")

    except Exception as e:
        session.rollback()
        print(f"\n[ERROR] {e}")
        logger.exception("Seeding failed")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
