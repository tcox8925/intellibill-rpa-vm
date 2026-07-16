"""
JAI Column Profiler - Automated column metadata extraction from PostgreSQL.

Profiles every column in a schema by querying pg_stats, information_schema,
and pg_description to produce rich metadata used for:
1. Embedding text generation (for semantic search over columns)
2. Automatic categorical value discovery
3. LLM prompt enrichment with real data distributions

Public API:
- profile_all_columns(db_session, schema) -> list[ColumnProfile]
- build_column_embedding_text(profile, aliases) -> str
- build_column_aliases_from_synonyms() -> dict[str, list[str]]
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# =====================================================
# Column name -> human-readable description inference
# =====================================================
# Fallback descriptions when pg_description has no comment.
# Covers common abbreviations found across the wpo schema.

_COLUMN_NAME_INFERENCES: Dict[str, str] = {
    "npn": "National Producer Number",
    "npi": "National Provider Identifier",
    "pk_id": "Primary key identifier",
    "fk_id": "Foreign key identifier",
    "created_at": "Record creation timestamp",
    "updated_at": "Record last-updated timestamp",
    "created_by": "User who created the record",
    "updated_by": "User who last updated the record",
    "is_active": "Whether the record is active",
    "full_name": "Full name of the person",
    "first_name": "First name",
    "last_name": "Last name",
    "middle_name": "Middle name",
    "email": "Email address",
    "phone": "Phone number",
    "dob": "Date of birth",
    "ssn": "Social Security Number",
    "ein": "Employer Identification Number",
    "caqh_number": "CAQH provider identifier number",
    "carrier_name": "Insurance carrier name",
    "company_name": "Company or entity name",
    "firm": "Agency or firm name",
    "line_of_business": "Line of business (e.g., Medicare, Medicaid)",
    "product": "Product name or type",
    "product_type_mapped": "Mapped product type category",
    "status": "Current status of the record",
    "state": "US state (2-letter code)",
    "zip_code": "ZIP or postal code",
    "address": "Street address",
    "city": "City name",
    "county": "County name",
    "writing_number": "Agent writing number for a carrier",
    "appointment_type": "Type of carrier appointment",
    "appointed_states": "States where agent is appointed (semicolon-separated)",
    "requested_states": "States where appointment is requested (semicolon-separated)",
    "payment": "Payment amount",
    "payment_type": "Type of payment (Commission, Override, Bonus, etc.)",
    "premium": "Insurance premium amount",
    "statement_total": "Total statement amount",
    "statement_month": "Month of the commission statement",
    "coverage_month": "Month of insurance coverage",
    "market": "Market segment or category",
    "agent_recruiter": "Recruiting agent name",
    "upline_full_name": "Direct upline agent full name",
    "top_upline_full_name": "Top-level upline agent full name",
    "field_sales_director": "Field sales director name",
    "primary_speciality": "Primary medical specialty",
    "professional_degree": "Professional degree held",
    "board_cert": "Board certification status",
    "job_owner_name": "Credentialing specialist or job owner",
    "amisys_number": "Member Amisys ID number",
    "risk_score": "Calculated risk score",
    "primary_risk_category": "Primary risk or diagnosis category",
    "population_health_category": "Population health classification",
    "pcp_npi": "Primary care provider NPI",
    "ed_visits": "Emergency department visit count",
    "all_admits": "Total hospital admissions count",
    "total_score": "QA assessment total score",
    "call_status": "Call assessment pass/fail status",
    "campaign": "Assessment campaign name",
    "account_number": "Policy or account number",
    "insured_name": "Name of insured/subscriber",
    "entity_id": "Associated entity identifier",
    "entity_name": "Associated entity name",
    "license_market": "Market for the license",
    "type": "Record type or classification",
    "compliance_overall_status": "Overall compliance status",
    "sale_or_not_sale": "Whether a sale was completed",
    "edited_compliance_score": "Edited compliance assessment score",
}


# =====================================================
# ColumnProfile dataclass
# =====================================================

@dataclass
class ColumnProfile:
    """Rich metadata profile for a single database column."""

    schema_name: str
    table_name: str
    column_name: str
    data_type: str
    description: str = ""
    distinct_count: int = 0
    null_rate: float = 0.0
    most_common_vals: list = field(default_factory=list)
    is_categorical: bool = False
    min_val: Any = None
    max_val: Any = None


# =====================================================
# Main profiler function
# =====================================================

def profile_all_columns(
    db_session: Session,
    schema: str = "wpo",
) -> List[ColumnProfile]:
    """Profile every column in the given schema using PostgreSQL catalog tables.

    Joins information_schema.columns with pg_stats for distribution data
    and pg_description for column comments.

    Args:
        db_session: Active SQLAlchemy session (from SessionLocal / get_db).
        schema: PostgreSQL schema to profile (default: "wpo").

    Returns:
        List of ColumnProfile objects, one per column.
    """
    # ------------------------------------------------------------------
    # Step 1: Fetch all columns with their comments from the catalog
    # ------------------------------------------------------------------
    columns_sql = text("""
        SELECT
            c.table_schema,
            c.table_name,
            c.column_name,
            c.data_type,
            d.description AS column_comment
        FROM information_schema.columns c
        LEFT JOIN pg_catalog.pg_class cls
            ON cls.relname = c.table_name
        LEFT JOIN pg_catalog.pg_namespace ns
            ON ns.oid = cls.relnamespace
            AND ns.nspname = c.table_schema
        LEFT JOIN pg_catalog.pg_attribute att
            ON att.attrelid = cls.oid
            AND att.attname = c.column_name
            AND att.attnum > 0
            AND NOT att.attisdropped
        LEFT JOIN pg_catalog.pg_description d
            ON d.objoid = cls.oid
            AND d.objsubid = att.attnum
        WHERE c.table_schema = :schema
        ORDER BY c.table_name, c.ordinal_position
    """)

    # ------------------------------------------------------------------
    # Step 2: Fetch pg_stats for distribution metadata
    # ------------------------------------------------------------------
    stats_sql = text("""
        SELECT
            s.tablename,
            s.attname,
            s.null_frac,
            s.n_distinct,
            s.most_common_vals::text AS mcv_text
        FROM pg_stats s
        WHERE s.schemaname = :schema
    """)

    try:
        col_rows = db_session.execute(columns_sql, {"schema": schema}).fetchall()
    except Exception as e:
        logger.error("Failed to query information_schema.columns: %s", e)
        return []

    try:
        stats_rows = db_session.execute(stats_sql, {"schema": schema}).fetchall()
    except Exception as e:
        logger.warning("Failed to query pg_stats (proceeding without stats): %s", e)
        stats_rows = []

    # Build a lookup: (table_name, column_name) -> stats row
    stats_lookup: Dict[tuple, Any] = {}
    for row in stats_rows:
        key = (row[0], row[1])  # tablename, attname
        stats_lookup[key] = row

    # ------------------------------------------------------------------
    # Step 3: Fetch min/max for numeric and date columns
    # ------------------------------------------------------------------
    min_max_lookup = _fetch_min_max(db_session, schema, col_rows)

    # ------------------------------------------------------------------
    # Step 4: Assemble ColumnProfile objects
    # ------------------------------------------------------------------
    profiles: List[ColumnProfile] = []
    for row in col_rows:
        schema_name = row[0]
        table_name = row[1]
        column_name = row[2]
        data_type = row[3]
        column_comment = row[4] or ""

        # Resolve description: prefer DB comment, then inferred
        description = column_comment
        if not description:
            description = _infer_description(column_name)

        # Fetch stats if available
        stats = stats_lookup.get((table_name, column_name))

        null_rate = 0.0
        distinct_count = 0
        most_common_vals: list = []

        if stats is not None:
            null_frac = stats[2]  # null_frac
            n_distinct = stats[3]  # n_distinct
            mcv_text = stats[4]  # most_common_vals::text

            null_rate = float(null_frac) if null_frac is not None else 0.0

            # n_distinct: positive = exact count, negative = fraction of rows
            # For our purposes, negative means high cardinality; we store
            # the absolute value as an approximate count indicator.
            if n_distinct is not None:
                if n_distinct >= 0:
                    distinct_count = int(n_distinct)
                else:
                    # Negative means "fraction of rows" (e.g., -1.0 = all unique).
                    # We store as a large sentinel; the actual count is unknown
                    # without a separate COUNT(DISTINCT).  Use 9999 to signal
                    # "high cardinality" which ensures is_categorical = False.
                    distinct_count = max(int(abs(n_distinct) * 100_000), 51)

            most_common_vals = _parse_mcv(mcv_text)

        is_categorical = distinct_count < 50

        # Min/max for numeric and date types
        min_val = None
        max_val = None
        mm = min_max_lookup.get((table_name, column_name))
        if mm is not None:
            min_val, max_val = mm

        profiles.append(ColumnProfile(
            schema_name=schema_name,
            table_name=table_name,
            column_name=column_name,
            data_type=data_type,
            description=description,
            distinct_count=distinct_count,
            null_rate=round(null_rate, 4),
            most_common_vals=most_common_vals,
            is_categorical=is_categorical,
            min_val=min_val,
            max_val=max_val,
        ))

    logger.info(
        "Profiled %d columns across schema '%s' (%d had pg_stats data)",
        len(profiles),
        schema,
        len(stats_lookup),
    )
    return profiles


# =====================================================
# Internal helpers
# =====================================================

_NUMERIC_TYPES = {
    "integer", "bigint", "smallint", "numeric", "decimal",
    "real", "double precision", "money",
}

_DATE_TYPES = {
    "date", "timestamp without time zone", "timestamp with time zone",
    "timestamp", "timestamptz",
}


def _is_numeric(data_type: str) -> bool:
    return data_type.lower() in _NUMERIC_TYPES


def _is_date(data_type: str) -> bool:
    return data_type.lower() in _DATE_TYPES


def _fetch_min_max(
    db_session: Session,
    schema: str,
    col_rows: list,
) -> Dict[tuple, tuple]:
    """Fetch MIN/MAX for numeric and date columns via batched queries.

    Groups columns by table and issues one query per table containing
    any numeric or date columns.  This avoids N+1 queries.

    Returns:
        Dict mapping (table_name, column_name) -> (min_val, max_val).
    """
    # Collect columns needing min/max, grouped by table
    table_columns: Dict[str, List[tuple]] = defaultdict(list)
    for row in col_rows:
        table_name = row[1]
        column_name = row[2]
        data_type = row[3]
        if _is_numeric(data_type) or _is_date(data_type):
            table_columns[table_name].append((column_name, data_type))

    result: Dict[tuple, tuple] = {}

    for table_name, columns in table_columns.items():
        # Build SELECT with MIN/MAX for each column
        select_parts = []
        for col_name, _dt in columns:
            # Use quoted identifiers to handle reserved words
            safe_col = f'"{col_name}"'
            select_parts.append(f"MIN({safe_col})")
            select_parts.append(f"MAX({safe_col})")

        if not select_parts:
            continue

        query = f'SELECT {", ".join(select_parts)} FROM "{schema}"."{table_name}"'

        try:
            row = db_session.execute(text(query)).fetchone()
            if row is not None:
                for i, (col_name, _dt) in enumerate(columns):
                    min_val = row[i * 2]
                    max_val = row[i * 2 + 1]
                    if min_val is not None or max_val is not None:
                        result[(table_name, col_name)] = (min_val, max_val)
        except Exception as e:
            # Some tables/columns may be inaccessible (e.g., views with
            # functions that fail). Log and continue.
            logger.warning(
                "MIN/MAX query failed for %s.%s: %s",
                schema, table_name, e,
            )

    return result


def _parse_mcv(mcv_text: Optional[str]) -> list:
    """Parse pg_stats most_common_vals text representation into a Python list.

    PostgreSQL stores most_common_vals as a text array literal, e.g.:
        {Active,"Non-Resident",Pending}
    or None if no stats are available.
    """
    if not mcv_text:
        return []

    # Strip outer braces
    inner = mcv_text.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]

    if not inner:
        return []

    # Parse CSV with awareness of quoted values
    values: list = []
    current: list = []
    in_quotes = False

    for char in inner:
        if char == '"' and not in_quotes:
            in_quotes = True
        elif char == '"' and in_quotes:
            in_quotes = False
        elif char == "," and not in_quotes:
            values.append("".join(current).strip())
            current = []
        else:
            current.append(char)

    # Don't forget the last element
    if current:
        values.append("".join(current).strip())

    # Strip surrounding quotes and handle escaped quotes
    cleaned: list = []
    for v in values:
        v = v.strip('"').replace('""', '"')
        if v.upper() == "NULL":
            continue  # Skip NULL entries from the MCV list
        cleaned.append(v)

    return cleaned


def _infer_description(column_name: str) -> str:
    """Attempt to infer a human-readable description from the column name.

    Checks the static inference map first, then falls back to a simple
    underscore-to-space title-case transformation.
    """
    # Exact match in inference map
    lookup = _COLUMN_NAME_INFERENCES.get(column_name)
    if lookup:
        return lookup

    # Lowercase match (handles casing variations)
    lookup = _COLUMN_NAME_INFERENCES.get(column_name.lower())
    if lookup:
        return lookup

    # Fallback: convert snake_case to readable form
    return column_name.replace("_", " ").strip().capitalize()


# =====================================================
# Synonym inversion for column aliases
# =====================================================

def build_column_aliases_from_synonyms() -> Dict[str, List[str]]:
    """Invert the SYNONYMS dict to produce column_name -> [alias1, alias2, ...].

    The SYNONYMS dict in business_knowledge.py maps:
        "business term" -> "db_column_name"

    This function inverts it to:
        "db_column_name" -> ["business term 1", "business term 2", ...]

    Returns:
        Dict keyed by column name, values are deduplicated alias lists.
    """
    from app.utils.jay.business_knowledge import SYNONYMS

    inverted: Dict[str, List[str]] = defaultdict(list)
    for business_term, db_column in SYNONYMS.items():
        # Avoid duplicates (case-insensitive check)
        existing_lower = {a.lower() for a in inverted[db_column]}
        if business_term.lower() not in existing_lower:
            inverted[db_column].append(business_term)

    return dict(inverted)


# =====================================================
# Embedding text builder
# =====================================================

def build_column_embedding_text(
    profile: ColumnProfile,
    aliases: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Build a rich text representation of a column for embedding/indexing.

    Produces a multi-line description suitable for vector embedding that
    captures the column's identity, data characteristics, and business
    aliases.

    Args:
        profile: A ColumnProfile instance with metadata.
        aliases: Optional dict of column_name -> list of alias strings.
                 If None, will be built from SYNONYMS automatically.

    Returns:
        Multi-line string suitable for embedding.
    """
    if aliases is None:
        aliases = build_column_aliases_from_synonyms()

    lines: list = [
        f"Table: {profile.table_name}",
        f"Column: {profile.column_name}",
        f"Description: {profile.description or 'No description available'}",
        f"Type: {profile.data_type}",
    ]

    # --- Sample values ---
    if profile.most_common_vals:
        if profile.is_categorical:
            # Categorical (distinct < 50): include ALL values
            vals = profile.most_common_vals
        elif profile.distinct_count > 50:
            # High-cardinality: limit to top 20
            vals = profile.most_common_vals[:20]
        else:
            vals = profile.most_common_vals

        lines.append(f"Sample values: {', '.join(str(v) for v in vals)}")

    # --- Range for numeric columns ---
    if _is_numeric(profile.data_type) and profile.min_val is not None and profile.max_val is not None:
        try:
            min_fmt = f"{float(profile.min_val):.2f}"
            max_fmt = f"{float(profile.max_val):.2f}"
            lines.append(f"Range: {min_fmt} to {max_fmt}")
        except (ValueError, TypeError):
            lines.append(f"Range: {profile.min_val} to {profile.max_val}")

    # --- Range for date columns ---
    if _is_date(profile.data_type) and profile.min_val is not None and profile.max_val is not None:
        try:
            min_str = str(profile.min_val)[:10]  # YYYY-MM-DD
            max_str = str(profile.max_val)[:10]
            lines.append(f"Date range: {min_str} to {max_str}")
        except (ValueError, TypeError):
            lines.append(f"Date range: {profile.min_val} to {profile.max_val}")

    # --- Aliases ---
    col_aliases = aliases.get(profile.column_name, [])
    if col_aliases:
        lines.append(f"Also known as: {', '.join(col_aliases)}")

    return "\n".join(lines)
