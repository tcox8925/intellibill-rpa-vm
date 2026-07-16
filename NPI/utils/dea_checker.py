from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

# --- Configurable DEA keyword list (will be normalized for matching) ---
DEA_KEYWORDS = [
    # Strong indicators
    "CONTROLLED SUBSTANCE", "CONTROLLED SUBSTANCES",
    "DRUG DIVERSION", "DIVERSION",
    "NARCOTIC", "NARCOTICS",
    "OPIOID", "OPIOIDS",
    "DRUG ENFORCEMENT ADMINISTRATION", "DEA",
    "PRESCRIPTION FRAUD", "RX FRAUD", "PRESCRIPTION MISUSE",
    "ILLEGAL PRESCRIBING", "UNAUTHORIZED PRESCRIBING",
    "SCHEDULE II", "SCHEDULE 2", "SCHEDULE III", "SCHEDULE 3",
    "FENTANYL", "MORPHINE", "HEROIN", "OXYCODONE", "HYDROCODONE",
    # Soft but explicit per your rule: any 'DRUG' counts
    "DRUG", "DRUGS",
]

# Constants for sources and table names
SRC_OIG = "OIG"
SRC_TMB = "Texas Medical Board"
DEA_SOURCE_PASS = f"{SRC_OIG}, {SRC_TMB}"  # for DEA=Pass rows, per your rule
TBL_VALIDATION = "wpo.pch_regulatory_validation"
TBL_FAIL_DETAILS = "wpo.pch_regulatory_fail_details"

# --- Helpers ---

def _now_ts_str() -> str:
    """UTC timestamp string to millisecond precision (matches your pattern)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def _now_compact() -> str:
    """UTC timestamp compact for audit_id suffix."""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

_NORMALIZE_RE = re.compile(r"[^A-Z0-9]+")
def _normalize(s: Optional[str]) -> str:
    """
    Uppercase and remove all non-alphanumeric chars (including spaces and punctuation).
    """
    if not s:
        return ""
    s_up = s.upper()
    return _NORMALIZE_RE.sub("", s_up)

# Pre-normalize keywords once
DEA_KEYWORDS_N = tuple(_normalize(k) for k in DEA_KEYWORDS if k and k.strip())

def _text_has_dea_hit(text: Optional[str]) -> bool:
    """
    Return True if any normalized DEA keyword appears as a substring
    in the normalized text (space-insensitive, case-insensitive).
    """
    norm = _normalize(text)
    if not norm:
        return False
    for kw in DEA_KEYWORDS_N:
        if kw and kw in norm:
            return True
    return False

def _fetch_latest_fail_detail_for_source(
    get_postgres_connection: Callable[[], "psycopg2.extensions.connection"],
    txn_id_provider: str,
    source: str,
) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """
    Fetch the latest fail-details row for a provider+source.
    Returns tuple: (description, check_type, action_date) or None.

    We order by created_on DESC (reliable) then action_date DESC (if present),
    and only look at this source in fail-details table.
    """
    sql = f"""
        SELECT description, check_type, action_date
        FROM {TBL_FAIL_DETAILS}
        WHERE txn_id_provider = %s
          AND source = %s
        ORDER BY created_on DESC NULLS LAST, action_date DESC NULLS LAST
        LIMIT 1
    """
    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (txn_id_provider, source))
            row = cur.fetchone()
            if not row:
                return None
            desc, check_type, action_date = row[0], row[1], row[2]
            return (desc, check_type, action_date)
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _insert_dea_validation_row(
    get_postgres_connection: Callable[[], "psycopg2.extensions.connection"],
    txn_id_provider: str,
    status: str,
    source: str,
) -> None:
    """
    Insert DEA summary row (always insert a new record).
    """
    audit_id = f"DEA_{txn_id_provider}_{_now_compact()}"
    sql = f"""
        INSERT INTO {TBL_VALIDATION}
          (txn_id, audit_id, status, source, date_time, txn_id_provider)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    params = (
        str(uuid.uuid4()),
        audit_id,
        status,
        source,
        _now_ts_str(),
        txn_id_provider,
    )
    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

# --- Public entry point ---

def run_dea_analysis(
    get_postgres_connection: Callable[[], "psycopg2.extensions.connection"],
    txn_id_provider: str,
    logger: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Evaluate DEA status for a provider based on latest OIG & TMB fail-details,
    insert a fresh DEA row into pch_regulatory_validation, and return a summary.

    Returns a dict like:
    {
        "status": "Fail" | "Pass",
        "source": "OIG" | "Texas Medical Board" | "OIG, Texas Medical Board",
        "oig_dea_hit": bool,
        "tmb_dea_hit": bool,
        "oig_checked": bool,
        "tmb_checked": bool
    }
    """
    log = (lambda m: None) if logger is None else logger

    # 1) Fetch latest fail-details for each source
    latest_oig = _fetch_latest_fail_detail_for_source(get_postgres_connection, txn_id_provider, SRC_OIG)
    latest_tmb = _fetch_latest_fail_detail_for_source(get_postgres_connection, txn_id_provider, SRC_TMB)

    oig_desc = latest_oig[0] if latest_oig else None
    tmb_desc = latest_tmb[0] if latest_tmb else None

    oig_hit = _text_has_dea_hit(oig_desc)
    tmb_hit = _text_has_dea_hit(tmb_desc)

    log(f"[DEA] Provider={txn_id_provider} | OIG hit={oig_hit} | TMB hit={tmb_hit}")

    # 2) Determine status + source per locked rules
    if oig_hit and tmb_hit:
        status = "Fail"
        source = f"{SRC_OIG}, {SRC_TMB}"
    elif oig_hit:
        status = "Fail"
        source = SRC_OIG
    elif tmb_hit:
        status = "Fail"
        source = SRC_TMB
    else:
        status = "Pass"
        source = DEA_SOURCE_PASS  # "OIG, Texas Medical Board"

    # 3) Insert new DEA validation row (always insert; never upsert)
    _insert_dea_validation_row(get_postgres_connection, txn_id_provider, status, source)
    log(f"[DEA] Inserted DEA row: status={status} source={source}")

    # 4) Return summary for the runner
    return {
        "status": status,
        "source": source,
        "oig_dea_hit": bool(oig_hit),
        "tmb_dea_hit": bool(tmb_hit),
        "oig_checked": bool(latest_oig is not None),
        "tmb_checked": bool(latest_tmb is not None),
    }
