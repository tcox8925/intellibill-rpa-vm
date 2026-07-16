"""
Fix and populate jay_knowledge_embeddings.

Issues from initial run:
1. Unique index creation failed (ALTER TABLE doesn't support expression-based unique)
2. Upsert failed because :metadata::jsonb confused SQLAlchemy's bind parsing

This script:
  - Creates the unique index properly
  - Uses CAST() instead of :: for the jsonb cast
  - Re-seeds all knowledge embeddings

Usage:
    python -m scripts.fix_knowledge_embeddings
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


def create_unique_index():
    """Create the expression-based unique index."""
    print("\n[1/3] Creating unique index...")
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS jay_knowledge_emb_category_text_uq
                    ON wpo.jay_knowledge_embeddings (category, md5(text))
            """))
            conn.commit()
            print("  OK: Unique index created")
        except Exception as e:
            conn.rollback()
            print(f"  SKIP: {e}")


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
            "metadata": {"term": term, "db_column": db_column, "notes": notes},
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
            "metadata": {"term": term, "description": description, "sql_hint": sql_hint, "module": module},
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
            "metadata": {
                "name": name,
                "example_type": example_type,
                "sql_text": sql_text,
                "natural_query": natural_query,
                "domain_tags": domain_tags,
                "module_tag": module_tag,
            },
            "score_weight": 1.0,
        })
    return entries


def populate_knowledge_embeddings():
    """Seed knowledge embeddings using individual INSERT statements."""
    print("\n[2/3] Seeding knowledge embeddings...")

    from app.utils.jay.llm_client import embed_texts

    session = SessionLocal()
    try:
        synonym_entries = _load_synonyms(session)
        glossary_entries = _load_glossary(session)
        example_entries = _load_query_examples(session)

        print(f"  Synonyms:  {len(synonym_entries)}")
        print(f"  Glossary:  {len(glossary_entries)}")
        print(f"  Examples:  {len(example_entries)}")

        all_entries = synonym_entries + glossary_entries + example_entries
        if not all_entries:
            print("  WARN: No entries found in config tables.")
            return

        # Embed in batches
        texts = [e["text"] for e in all_entries]
        all_embeddings = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                batch_embs = embed_texts(batch)
                all_embeddings.extend(batch_embs)
                print(f"  Embedded batch [{i}:{i+len(batch)}]")
            except Exception as e:
                print(f"  ERROR embedding batch [{i}:{i+len(batch)}]: {e}")
                raise

        # Upsert one at a time using raw connection to avoid SQLAlchemy bind param issues
        now = datetime.now(timezone.utc)
        upserted = 0
        with engine.connect() as conn:
            for entry, embedding in zip(all_entries, all_embeddings):
                try:
                    # Use %s style params via raw connection to avoid :: vs : conflicts
                    conn.execute(
                        text("""
                            INSERT INTO wpo.jay_knowledge_embeddings (
                                category, text, metadata, embedding, score_weight, is_active,
                                created_at, updated_at
                            ) VALUES (
                                :category, :txt, CAST(:meta AS jsonb), :emb,
                                :score_weight, TRUE, :now, :now
                            )
                            ON CONFLICT (category, md5(text))
                            DO UPDATE SET
                                metadata     = CAST(EXCLUDED.metadata AS jsonb),
                                embedding    = EXCLUDED.embedding,
                                score_weight = EXCLUDED.score_weight,
                                is_active    = TRUE,
                                updated_at   = EXCLUDED.updated_at
                        """),
                        {
                            "category": entry["category"],
                            "txt": entry["text"],
                            "meta": json.dumps(entry["metadata"]),
                            "emb": json.dumps(embedding),
                            "score_weight": entry["score_weight"],
                            "now": now,
                        },
                    )
                    upserted += 1
                except Exception as e:
                    err_str = str(e)[:200]
                    print(f"  ERROR upserting {entry['category']}: {err_str}")

            conn.commit()
        print(f"  Upserted {upserted} knowledge embeddings")

    except Exception as e:
        print(f"  ERROR: {e}")
        logger.exception("Knowledge embedding seed failed")
    finally:
        session.close()


def verify():
    print("\n[3/3] Verifying...")
    session = SessionLocal()
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
        print(f"  ERROR: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Fix Knowledge Embeddings")
    print("=" * 60)

    create_unique_index()
    populate_knowledge_embeddings()
    verify()

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)
