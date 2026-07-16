"""
Jay Metadata Sync - Syncs the table catalog to the jay_schema_metadata DB table.

Enables future runtime catalog updates without code deploys
by storing domain metadata as JSONB in PostgreSQL with GIN indexes.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.jay.JaySchemaMetadata import JaySchemaMetadata
from app.utils.jay.table_catalog import TABLE_DOMAINS

logger = logging.getLogger(__name__)


def sync_table_catalog(db: Session) -> dict:
    """Upsert all domain metadata from TABLE_DOMAINS to jay_schema_metadata.

    Each domain is stored as one row:
        module_name = domain_name (e.g., "agent_profile")
        registry_json = full domain config (tables, DDLs, joins, examples)

    Returns:
        dict with sync summary (inserted, updated, total counts)
    """
    inserted = 0
    updated = 0

    for domain_name, domain_cfg in TABLE_DOMAINS.items():
        # Build serializable registry JSON (strip DDL text to keep JSONB lean)
        registry = {
            "description": domain_cfg["description"],
            "db_type": domain_cfg["db_type"],
            "tables": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "join_keys": t.get("join_keys", {}),
                    "scope_column": t.get("scope_column"),
                }
                for t in domain_cfg["tables"]
            ],
            "internal_joins": domain_cfg.get("internal_joins", []),
            "sample_queries": domain_cfg.get("sample_queries", []),
            "catalog_type": "domain",
        }

        # Upsert: insert if new, update if exists
        existing = db.query(JaySchemaMetadata).filter(
            JaySchemaMetadata.module_name == domain_name
        ).first()

        if existing:
            existing.registry_json = registry
            existing.updated_at = datetime.now(timezone.utc)
            updated += 1
        else:
            new_record = JaySchemaMetadata(
                module_name=domain_name,
                registry_json=registry,
                updated_at=datetime.now(timezone.utc),
            )
            db.add(new_record)
            inserted += 1

    try:
        db.commit()
        logger.info(
            f"Table catalog synced: {inserted} inserted, {updated} updated, "
            f"{len(TABLE_DOMAINS)} total domains"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to sync table catalog: {e}")
        raise

    return {
        "inserted": inserted,
        "updated": updated,
        "total": len(TABLE_DOMAINS),
    }
