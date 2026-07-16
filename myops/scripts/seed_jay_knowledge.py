"""
Seed JAI configuration tables from hardcoded business knowledge.

Creates 4 config tables in wpo schema and upserts all data from:
- business_knowledge.py (SYNONYMS, BUSINESS_GLOSSARY)
- semantic_registry.py (MODULES filter valid_values)
- table_catalog.py (QUERY_EXAMPLES) + semantic_registry sample_queries

Usage: python -m scripts.seed_jay_knowledge
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from sqlalchemy import text
from app.db.session import engine, SessionLocal
from app.models.BaseClasses import Base

# Import models to register them on Base.metadata
from app.models.jay.JayConfig import (
    JaySynonym,
    JayGlossaryEntry,
    JayFilterValue,
    JayQueryExample,
)

# Import source data
from app.utils.jay.business_knowledge import SYNONYMS, BUSINESS_GLOSSARY
from app.utils.jay.semantic_registry import MODULES
from app.utils.jay.table_catalog import QUERY_EXAMPLES


def create_tables():
    """Create the 4 config tables in wpo schema."""
    config_tables = [
        Base.metadata.tables.get("wpo.jay_config_synonyms"),
        Base.metadata.tables.get("wpo.jay_config_glossary"),
        Base.metadata.tables.get("wpo.jay_config_filter_values"),
        Base.metadata.tables.get("wpo.jay_config_query_examples"),
    ]
    config_tables = [t for t in config_tables if t is not None]

    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS wpo"))
        conn.commit()

    Base.metadata.create_all(engine, tables=config_tables, checkfirst=True)
    print(f"[OK] Created {len(config_tables)} config tables")


def seed_synonyms(session):
    """Upsert synonyms from SYNONYMS dict."""
    count = 0
    for term, db_column in SYNONYMS.items():
        existing = session.query(JaySynonym).filter(JaySynonym.term == term).first()
        if existing:
            existing.db_column = db_column
            existing.updated_at = datetime.now(timezone.utc)
        else:
            session.add(JaySynonym(
                term=term,
                db_column=db_column,
                is_active=True,
            ))
        count += 1
    session.commit()
    print(f"[OK] Synonyms: {count} rows upserted")
    return count


def seed_glossary(session):
    """Upsert glossary entries from BUSINESS_GLOSSARY dict."""
    count = 0
    for term, info in BUSINESS_GLOSSARY.items():
        existing = session.query(JayGlossaryEntry).filter(
            JayGlossaryEntry.term == term
        ).first()
        if existing:
            existing.description = info.get("description", "")
            existing.sql_hint = info.get("sql_hint", "")
            existing.module = info.get("module", "")
            existing.updated_at = datetime.now(timezone.utc)
        else:
            session.add(JayGlossaryEntry(
                term=term,
                description=info.get("description", ""),
                sql_hint=info.get("sql_hint", ""),
                module=info.get("module", ""),
                is_active=True,
            ))
        count += 1
    session.commit()
    print(f"[OK] Glossary: {count} rows upserted")
    return count


def seed_filter_values(session):
    """Upsert filter values from MODULES dict."""
    count = 0
    for module_name, module_cfg in MODULES.items():
        filters = module_cfg.get("filters", {})
        for filter_name, filter_cfg in filters.items():
            valid_values = filter_cfg.get("valid_values")
            if not valid_values:
                continue
            column_name = filter_cfg.get("column", filter_name)
            filter_type = filter_cfg.get("type", "categorical_strict")
            for idx, value in enumerate(valid_values):
                existing = session.query(JayFilterValue).filter(
                    JayFilterValue.module == module_name,
                    JayFilterValue.filter_name == filter_name,
                    JayFilterValue.valid_value == value,
                ).first()
                if existing:
                    existing.column_name = column_name
                    existing.filter_type = filter_type
                    existing.sort_order = idx
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    session.add(JayFilterValue(
                        module=module_name,
                        filter_name=filter_name,
                        column_name=column_name,
                        filter_type=filter_type,
                        valid_value=value,
                        sort_order=idx,
                        is_active=True,
                    ))
                count += 1
    session.commit()
    print(f"[OK] Filter values: {count} rows upserted")
    return count


def seed_query_examples(session):
    """Upsert query examples from QUERY_EXAMPLES dict + module sample_queries."""
    count = 0
    # Track pending inserts to handle duplicates across modules
    # (e.g. "How many active agents?" in both agents_contracts and agents)
    seen = set()

    # SQL examples from table_catalog.py
    for name, sql_text in QUERY_EXAMPLES.items():
        key = (name, "sql_example")
        existing = session.query(JayQueryExample).filter(
            JayQueryExample.name == name,
            JayQueryExample.example_type == "sql_example",
        ).first()
        if existing:
            existing.sql_text = sql_text
            existing.updated_at = datetime.now(timezone.utc)
        elif key not in seen:
            session.add(JayQueryExample(
                name=name,
                example_type="sql_example",
                sql_text=sql_text,
                is_active=True,
            ))
        seen.add(key)
        count += 1

    # Flush so sample_query upserts can find sql_example rows
    session.flush()

    # Sample queries from semantic_registry MODULES
    for module_name, module_cfg in MODULES.items():
        sample_queries = module_cfg.get("sample_queries", [])
        for query_text in sample_queries:
            key = (query_text, "sample_query")
            if key in seen:
                # Already added in this batch — skip duplicate
                continue
            existing = session.query(JayQueryExample).filter(
                JayQueryExample.name == query_text,
                JayQueryExample.example_type == "sample_query",
            ).first()
            if existing:
                existing.module_tag = module_name
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(JayQueryExample(
                    name=query_text,
                    example_type="sample_query",
                    natural_query=query_text,
                    module_tag=module_name,
                    is_active=True,
                ))
            seen.add(key)
            count += 1

    session.commit()
    print(f"[OK] Query examples: {count} rows upserted")
    return count


def verify(session):
    """Print row counts for all 4 tables."""
    tables = [
        ("jay_config_synonyms", JaySynonym),
        ("jay_config_glossary", JayGlossaryEntry),
        ("jay_config_filter_values", JayFilterValue),
        ("jay_config_query_examples", JayQueryExample),
    ]
    print("\n--- Verification ---")
    for name, model in tables:
        count = session.query(model).count()
        print(f"  {name}: {count} rows")


if __name__ == "__main__":
    print("=== Seeding JAI Knowledge Config Tables ===\n")
    create_tables()

    session = SessionLocal()
    try:
        seed_synonyms(session)
        seed_glossary(session)
        seed_filter_values(session)
        seed_query_examples(session)
        verify(session)
        print("\n[DONE] All tables seeded successfully.")
    except Exception as e:
        session.rollback()
        print(f"\n[ERROR] {e}")
        raise
    finally:
        session.close()
