"""
Create and populate missing JAI tables:
  - wpo.jay_column_embeddings  (column metadata + embeddings)
  - wpo.jay_knowledge_embeddings (synonyms/glossary/examples embeddings)

Embeddings stored as TEXT (JSON arrays) — pgvector not available on Azure.

Usage:
    python -m scripts.setup_missing_tables
"""

import sys
import os
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from sqlalchemy import text
from app.db.session import engine, SessionLocal

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

_EMBED_BATCH_SIZE = 100


# ═══════════════════════════════════════════════════════════
# DDL Statements
# ═══════════════════════════════════════════════════════════

DDL_STATEMENTS = [
    # ── jay_column_embeddings ──
    (
        "wpo.jay_column_embeddings table",
        """
        CREATE TABLE IF NOT EXISTS wpo.jay_column_embeddings (
            id              SERIAL PRIMARY KEY,
            schema_name     VARCHAR(100) NOT NULL,
            table_name      VARCHAR(200) NOT NULL,
            column_name     VARCHAR(200) NOT NULL,
            data_type       VARCHAR(100),
            description     TEXT,
            sample_values   JSONB,
            distinct_count  INTEGER,
            null_rate       REAL,
            is_categorical  BOOLEAN DEFAULT FALSE,
            business_aliases JSONB,
            profile_hash    VARCHAR(64),
            embedding_text  TEXT NOT NULL DEFAULT '',
            embedding       TEXT,
            profiled_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            embedded_at     TIMESTAMP WITH TIME ZONE,
            fk_references   JSONB DEFAULT '[]'::jsonb,
            join_examples   JSONB DEFAULT '[]'::jsonb,
            tags            TEXT[] DEFAULT '{}',
            CONSTRAINT uq_jay_col_emb_schema_table_col
                UNIQUE (schema_name, table_name, column_name)
        )
        """,
    ),
    (
        "jay_column_embeddings profile_hash index",
        """
        CREATE INDEX IF NOT EXISTS ix_jay_col_emb_profile_hash
            ON wpo.jay_column_embeddings (profile_hash)
        """,
    ),
    (
        "jay_column_embeddings table_name index",
        """
        CREATE INDEX IF NOT EXISTS ix_jay_col_emb_table_name
            ON wpo.jay_column_embeddings (table_name)
        """,
    ),

    # ── jay_knowledge_embeddings ──
    (
        "wpo.jay_knowledge_embeddings table",
        """
        CREATE TABLE IF NOT EXISTS wpo.jay_knowledge_embeddings (
            id              SERIAL PRIMARY KEY,
            category        VARCHAR(50) NOT NULL,
            text            TEXT NOT NULL,
            metadata        JSONB,
            embedding       TEXT,
            score_weight    REAL DEFAULT 1.0,
            is_active       BOOLEAN DEFAULT TRUE,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
    ),
    (
        "jay_knowledge_embeddings category index",
        """
        CREATE INDEX IF NOT EXISTS ix_jay_knowledge_emb_category
            ON wpo.jay_knowledge_embeddings (category)
        """,
    ),
    (
        "jay_knowledge_embeddings is_active index",
        """
        CREATE INDEX IF NOT EXISTS ix_jay_knowledge_emb_is_active
            ON wpo.jay_knowledge_embeddings (is_active)
        """,
    ),
    (
        "jay_knowledge_embeddings unique index",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS jay_knowledge_emb_category_text_uq
            ON wpo.jay_knowledge_embeddings (category, md5(text))
        """,
    ),
]


# ═══════════════════════════════════════════════════════════
# Step 1: Create tables
# ═══════════════════════════════════════════════════════════

def create_tables():
    print("\n[1/4] Creating tables...")
    with engine.connect() as conn:
        for label, sql in DDL_STATEMENTS:
            try:
                conn.execute(text(sql.strip()))
                conn.commit()
                print(f"  OK:   {label}")
            except Exception as e:
                conn.rollback()
                print(f"  SKIP: {label} -- {str(e)[:100]}")


# ═══════════════════════════════════════════════════════════
# Step 2: Profile and embed columns
# ═══════════════════════════════════════════════════════════

def populate_column_embeddings():
    print("\n[2/4] Profiling and embedding columns...")

    try:
        from app.utils.jay.column_embedding_sync import sync_column_embeddings
    except ImportError as e:
        print(f"  SKIP: Could not import column_embedding_sync: {e}")
        return

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
        print(f"  ERROR: {e}")
        logger.exception("Column embedding sync failed")
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════
# Step 3: Seed knowledge embeddings from config tables
# ═══════════════════════════════════════════════════════════

def _load_synonyms(session) -> list:
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
    rows = session.execute(text("""
        SELECT name, example_type, sql_text, natural_query, domain_tags, module_tag
        FROM wpo.jay_config_query_examples
        WHERE is_active = TRUE
    """)).fetchall()
    entries = []
    for row in rows:
        name, example_type = row[0], row[1] or "sql_example"
        sql_text, natural_query = row[2] or "", row[3] or ""
        domain_tags, module_tag = row[4] or "", row[5] or ""

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


_UPSERT_KNOWLEDGE_SQL = text("""
    INSERT INTO wpo.jay_knowledge_embeddings (
        category, text, metadata, embedding, score_weight, is_active,
        created_at, updated_at
    ) VALUES (
        :category, :text, CAST(:metadata AS jsonb), :embedding,
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


def populate_knowledge_embeddings():
    print("\n[3/4] Seeding knowledge embeddings...")

    from app.utils.jay.llm_client import embed_texts

    session = SessionLocal()
    try:
        # Load from config tables
        synonym_entries = _load_synonyms(session)
        glossary_entries = _load_glossary(session)
        example_entries = _load_query_examples(session)

        print(f"  Synonyms:  {len(synonym_entries)}")
        print(f"  Glossary:  {len(glossary_entries)}")
        print(f"  Examples:  {len(example_entries)}")

        all_entries = synonym_entries + glossary_entries + example_entries
        if not all_entries:
            print("  WARN: No entries found in config tables. Run seed_jay_knowledge first.")
            return

        # Embed in batches
        texts = [e["text"] for e in all_entries]
        all_embeddings = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i:i + _EMBED_BATCH_SIZE]
            try:
                batch_embs = embed_texts(batch)
                all_embeddings.extend(batch_embs)
                print(f"  Embedded batch [{i}:{i+len(batch)}]")
            except Exception as e:
                print(f"  ERROR embedding batch [{i}:{i+len(batch)}]: {e}")
                raise

        # Upsert
        now = datetime.now(timezone.utc)
        upserted = 0
        for entry, embedding in zip(all_entries, all_embeddings):
            try:
                session.execute(_UPSERT_KNOWLEDGE_SQL, {
                    "category": entry["category"],
                    "text": entry["text"],
                    "metadata": entry["metadata"],
                    "embedding": json.dumps(embedding),  # TEXT, not vector
                    "score_weight": entry["score_weight"],
                    "now": now,
                })
                upserted += 1
            except Exception as e:
                logger.error("Upsert failed for %s: %s", entry["category"], str(e)[:100])

        session.commit()
        print(f"  Upserted {upserted} knowledge embeddings")

    except Exception as e:
        session.rollback()
        print(f"  ERROR: {e}")
        logger.exception("Knowledge embedding seed failed")
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════
# Step 4: Verify
# ═══════════════════════════════════════════════════════════

def verify():
    print("\n[4/4] Verifying...")
    session = SessionLocal()
    try:
        # Column embeddings
        try:
            row = session.execute(text(
                "SELECT COUNT(*), "
                "COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END), "
                "COUNT(DISTINCT table_name) "
                "FROM wpo.jay_column_embeddings"
            )).fetchone()
            print(f"\n  jay_column_embeddings:")
            print(f"    Total rows:      {row[0]}")
            print(f"    With embeddings: {row[1]}")
            print(f"    Distinct tables: {row[2]}")
        except Exception as e:
            print(f"  jay_column_embeddings: ERROR - {e}")
            session.rollback()

        # Knowledge embeddings
        try:
            rows = session.execute(text("""
                SELECT category, COUNT(*),
                       COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END)
                FROM wpo.jay_knowledge_embeddings
                WHERE is_active = TRUE
                GROUP BY category
                ORDER BY category
            """)).fetchall()
            print(f"\n  jay_knowledge_embeddings:")
            print(f"    {'Category':<15} {'Rows':>6} {'Embedded':>10}")
            print(f"    {'-'*15} {'-'*6} {'-'*10}")
            total_rows = 0
            total_emb = 0
            for r in rows:
                print(f"    {r[0]:<15} {r[1]:>6} {r[2]:>10}")
                total_rows += r[1]
                total_emb += r[2]
            print(f"    {'TOTAL':<15} {total_rows:>6} {total_emb:>10}")
        except Exception as e:
            print(f"  jay_knowledge_embeddings: ERROR - {e}")
            session.rollback()

    finally:
        session.close()


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("JAI Missing Tables Setup")
    print("=" * 60)

    create_tables()
    populate_column_embeddings()
    populate_knowledge_embeddings()
    verify()

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)
