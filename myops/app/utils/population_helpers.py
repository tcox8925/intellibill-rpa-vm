"""Helpers specific to the Population Dashboard."""

import re
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.helpers import parse_dob_any_format, calculate_age


def _exec(db: Session, sql: str, params: dict) -> list[dict]:
    """Execute raw SQL and return list of dicts."""
    result = db.execute(text(sql), params)
    return [dict(row) for row in result.mappings().all()]


def money_to_float(val) -> float:
    """Convert currency string like '$1,234.56' or '($123.45)' to float."""
    if val is None:
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[\$,\(\)]", "", s)
    try:
        result = float(s)
        return -result if negative else result
    except (ValueError, TypeError):
        return 0.0


def safe_float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def safe_int(val) -> int:
    try:
        return int(val) if val is not None else 0
    except (ValueError, TypeError):
        return 0


def calculate_age_group(dob_str) -> str:
    """Return age bucket: '0-17', '18-39', '40-64', '65+', or 'Unknown'."""
    dob = parse_dob_any_format(dob_str)
    if not dob:
        return "Unknown"
    age = calculate_age(dob)
    if age is None:
        return "Unknown"
    if age <= 17:
        return "0-17"
    if age <= 39:
        return "18-39"
    if age <= 64:
        return "40-64"
    return "65+"


def normalize_gender_value(val) -> str:
    """Normalize gender to M/F/UNKNOWN for SQL filtering."""
    if not val:
        return "UNKNOWN"
    v = str(val).strip().upper()
    if v in ("M", "MALE"):
        return "M"
    if v in ("F", "FEMALE"):
        return "F"
    return "UNKNOWN"


def resolve_report_date(db: Session, entity_id: str, input_date: str | None) -> str | None:
    """Validate input date or return MAX report_date from roster."""
    if input_date:
        return input_date
    rows = _exec(
        db,
        "SELECT MAX(report_date) AS max_date FROM wpo.pch_member_roster WHERE company_id = :entity_id",
        {"entity_id": entity_id},
    )
    if rows and rows[0].get("max_date"):
        return str(rows[0]["max_date"])
    return None


def safe_divide(numerator, denominator):
    """Safe division returning None when denominator is zero/None."""
    if not denominator:
        return None
    try:
        return round(float(numerator) / float(denominator), 2)
    except (ValueError, TypeError, ZeroDivisionError):
        return None
