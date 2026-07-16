# ==========================================================
# utils/db_insertor.py
# ==========================================================
"""
Generic, schema-driven SQL INSERT/UPDATE utilities.

- Centralizes type normalization for each SQL column type
- Prevents GUID conversion failures and TDS param errors
- Works for any table defined in SCHEMA_MAP (add more as needed)

Usage:
  from utils.db_insertor import (
      insert_records, update_records,
      insert_queue_records, update_queue_where
  )

Notes:
- This module assumes pyodbc connection via utils.db_utils.get_synapse_connection()
- All timestamps are written in UTC (SYSUTCDATETIME() equivalent on our side)
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, date
from typing import Dict, List, Iterable, Tuple, Any, Optional

import numpy as np
import pandas as pd

from utils.db_utils import get_synapse_connection, get_postgres_connection

# ----------------------------------------------------------
# Schema map: add more tables here over time
#   Each entry is: Ordered list of (column_name, sql_type)
#   Supported sql_type keys: uniqueidentifier, nvarchar, bit,
#       int, date, datetime, datetime2
# ----------------------------------------------------------

SCHEMA_MAP: Dict[str, List[Tuple[str, str]]] = {
    # === Primary queue table (EXACT schema you provided) ===
    "wpo.ops_acc_process_queue": [
        ("txn_id", "uniqueidentifier"),
        ("run_id", "uniqueidentifier"),
        ("carrier_id", "nvarchar"),
        ("company_id", "nvarchar"),
        ("npn", "nvarchar"),
        ("agent_first_name", "nvarchar"),
        ("agent_last_name", "nvarchar"),
        ("resident_state", "nvarchar"),
        ("contract_id", "nvarchar"),
        ("contract_status", "nvarchar"),
        ("source_folder", "nvarchar"),
        ("contract_path", "nvarchar"),
        ("eo_path", "nvarchar"),
        ("eo_valid_until", "date"),
        ("crm_update_flag", "bit"),
        ("drive_upload_flag", "bit"),
        ("process_flag", "nvarchar"),
        ("retry_flag", "bit"),
        ("retry_count", "int"),
        ("error_reason", "nvarchar"),
        ("created_on", "datetime2"),
        ("updated_on", "datetime2"),
        ("process_start_time", "datetime2"),
        ("process_end_time", "datetime2"),
        ("processed_by", "nvarchar"),
        ("status", "nvarchar"),
        ("drive_url", "nvarchar"),
        ("eo_crm", "datetime"),
        ("id", "nvarchar"),
        ("agent_id", "nvarchar"),
        ("email", "nvarchar"),
        ("mailing_state", "nvarchar"),
        ("agent_type", "nvarchar"),
        ("status_date", "datetime"),
        ("w9_path","navchar"),
        ("special_incl", "bit"),
        ("agent_middle_name", "nvarchar"),
        ("phone", "nvarchar"),
        ("orph_principal", "bit"),
        ("crm_note", "nvarchar"),
        ("pk_id", "uniqueidentifier")
    ],

    # === Minimal examples (extend as you like) =============
    # Logs (keep minimal, just to show extension)
    "wpo.ops_rpa_script_logs": [
        ("run_id", "uniqueidentifier"),
        ("script_name", "nvarchar"),
        ("status", "nvarchar"),
        ("message", "nvarchar"),
        ("created_on", "datetime2"),
        ("updated_on", "datetime2"),
    ],

    # Regulatory validation (skeleton)
    "wpo.pch_regulatory_validation": [
        ("txn_id", "uniqueidentifier"),
        ("txn_id_provider", "nvarchar"),
        ("audit_id", "nvarchar"),
        ("status", "nvarchar"),
        ("source", "nvarchar"),
        ("created_on", "datetime2"),
    ],

    # Source tracking (skeleton)
    "wpo.pch_source_tracking": [
        ("txn_id", "uniqueidentifier"),
        ("txn_id_provider", "nvarchar"),
        ("sources_used", "nvarchar"),
        ("created_on", "datetime2"),
    ],
}


# ----------------------------------------------------------
# Helpers
# ----------------------------------------------------------

_GUID_RE = re.compile(
    r"^[{(]?[0-9a-fA-F]{8}[-]?[0-9a-fA-F]{4}[-]?[0-9a-fA-F]{4}[-]?[0-9a-fA-F]{4}[-]?[0-9a-fA-F]{12}[)}]?$"
)

def _is_guid_like(val: Any) -> bool:
    if isinstance(val, uuid.UUID):
        return True
    if isinstance(val, str) and _GUID_RE.match(val.strip()):
        return True
    return False

def _to_guid(val: Any) -> str:
    """
    Return a valid GUID string.
    - If val is a valid guid → normalized str(val)
    - If val is falsey or invalid → generate a new uuid4
    """
    if _is_guid_like(val):
        try:
            return str(uuid.UUID(str(val)))
        except Exception:
            pass
    return str(uuid.uuid4())

def _to_nvarchar(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (np.generic,)):
        val = val.item()
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "null", "none", "nat"}:
        return None
    return s

def _to_bit(val: Any) -> int:
    """
    Normalize truthy/falsey to 1/0.
    Accepts: 1/0, True/False, 'true'/'false'/'yes'/'no'/'1'/'0'
    """
    if val is None:
        return 0
    if isinstance(val, (np.generic,)):
        val = val.item()
    if isinstance(val, (int, bool)):
        return 1 if bool(val) else 0
    s = str(val).strip().lower()
    return 1 if s in {"1", "true", "yes", "y", "on"} else 0

def _to_int(val: Any) -> int:
    if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        return 0
    if isinstance(val, (np.generic,)):
        val = val.item()
    try:
        return int(val)
    except Exception:
        # best effort fallback
        s = _to_nvarchar(val)
        try:
            return int(s) if s is not None else 0
        except Exception:
            return 0

def _parse_datetime(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    if isinstance(val, np.datetime64):
        try:
            return pd.to_datetime(val, errors="coerce").to_pydatetime()
        except Exception:
            return None
    # string parse
    s = _to_nvarchar(val)
    if not s:
        return None
    dt = pd.to_datetime(s, errors="coerce", utc=False)
    if pd.isna(dt):
        return None
    return dt.to_pydatetime()

def _parse_date(val: Any) -> Optional[date]:
    dt = _parse_datetime(val)
    return dt.date() if dt else None

def _normalize_value(val: Any, sql_type: str) -> Any:
    """
    Convert a Python value to something SQL Server will accept
    for the specified column type.
    """
    sql_type = sql_type.lower()

    if sql_type == "uniqueidentifier":
        return _to_guid(val)

    if sql_type == "nvarchar":
        return _to_nvarchar(val)

    if sql_type == "bit":
        return _to_bit(val)

    if sql_type == "int":
        return _to_int(val)

    if sql_type in {"datetime", "datetime2"}:
        return _parse_datetime(val)

    if sql_type == "date":
        return _parse_date(val)

    # Fallback: stringify
    return _to_nvarchar(val)

def _normalize_record(table: str, record: Dict[str, Any]) -> List[Any]:
    """
    Returns a list of normalized values in the exact column order
    defined in SCHEMA_MAP[table].
    """
    cols = SCHEMA_MAP[table]
    normalized: List[Any] = []
    now = datetime.utcnow()

    for col, sql_type in cols:
        v = record.get(col, None)

        # Defaults for queue timestamps if missing
        if col in {"created_on", "updated_on"} and v is None:
            v = now

        normalized.append(_normalize_value(v, sql_type))

    return normalized

def _get_schema_key(table: str) -> str:
    """Accept table with or without brackets; match SCHEMA_MAP key."""
    t = table.strip()
    # normalize like [wpo].[ops_acc_process_queue] → wpo.ops_acc_process_queue
    if t in SCHEMA_MAP:
        t = t.replace('[','').replace(']','')
        return t
    raise KeyError(f"Table schema not found for '{table}'. Add it to SCHEMA_MAP.")

def _build_insert_sql(table: str) -> str:
    cols = SCHEMA_MAP[table]
    col_list = ", ".join([c for c, _ in cols])
    qmarks = ", ".join(["%s"] * len(cols))
    return f"INSERT INTO {table} ({col_list}) VALUES ({qmarks})"

def _debug_param_snapshot(table: str, params: List[Any]) -> None:
    cols = SCHEMA_MAP[table]
    print("🔎 Param snapshot:")
    for i, ((col, _), v) in enumerate(zip(cols, params), start=1):
        print(f"   {i:02d}. {col:22} → {repr(v)} ({type(v).__name__})")

def sanitize_sql_param(v: Any) -> Optional[str]:
    """
    Universal sanitizer for SQL parameters.
    Converts NaN, np.generic, np.datetime64, and invalid types into safe SQL types.
    Used automatically in all insert/update ops and can be imported directly elsewhere.
    """
    import math
    if v is None:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        try:
            return pd.to_datetime(v, errors="coerce").to_pydatetime()
        except Exception:
            return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, bool)):
        return v
    if isinstance(v, str):
        s = v.strip()
        return None if s.lower() in ("", "nan", "null", "none", "nat") else s
    # Fallback stringify for anything else
    return str(v).strip()

# ----------------------------------------------------------
# Public API
# ----------------------------------------------------------

def insert_records(table: str, records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """
    Bulk insert dict records into `table` using SCHEMA_MAP.
    - Normalizes values per column type
    - Prevents duplicates for queue tables (carrier_id + npn)
    - Returns dict with counts
    """
    table_key = _get_schema_key(table)

    # Accept DataFrame
    if isinstance(records, pd.DataFrame):
        records = records.to_dict(orient="records")

    records = list(records)
    if not records:
        print(f"⚠️ insert_records → no records for {table_key}")
        return {"inserted": 0, "failed": 0}

    # -------------------------------------------------------
    # 🧹 Deduplicate for ACC queue table only
    # -------------------------------------------------------
    if table_key.lower() == "wpo.ops_acc_process_queue":
        carrier_id = str(records[0].get("carrier_id") or "").strip()
        npns = [str(r.get("npn") or "").strip() for r in records if r.get("npn")]

        if carrier_id and npns:
            try:
                conn = get_postgres_connection()
                cur = conn.cursor()
                placeholders = ", ".join(["%s"] * len(npns))
                sql_check = f"""
                    SELECT npn
                    FROM wpo.ops_acc_process_queue
                    WHERE carrier_id = %s AND npn IN ({placeholders})
                      AND status IN ('Pending','Processing')
                """
                cur.execute(sql_check, [carrier_id] + npns)
                existing_npns = {row[0] for row in cur.fetchall()}
                conn.close()

                if existing_npns:
                    before = len(records)
                    records = [r for r in records if str(r.get("npn")) not in existing_npns]
                    skipped = before - len(records)
                    print(f"⚠️ Skipped {skipped} duplicates already pending/processing in queue.")
            except Exception as e:
                print(f"⚠️ Deduplication check failed: {e}")
                pass

        if not records:
            print("ℹ️ No new records to insert after deduplication.")
            return {"inserted": 0, "failed": 0}

    # -------------------------------------------------------
    # ✅ Proceed with normal insert
    # -------------------------------------------------------
    sql = _build_insert_sql(table_key)
    conn = get_postgres_connection()
    cur = conn.cursor()

    inserted = failed = 0
    for rec in records:
        try:
            params = _normalize_record(table_key, rec)
            cur.execute(sql, params)
            inserted += 1
        except Exception as e:
            failed += 1
            print(f"❌ INSERT failed on {table_key}: {e}")
            _debug_param_snapshot(table_key, params)
            continue

    conn.commit()
    conn.close()
    print(f"📦 insert_records[{table_key}] → {inserted} inserted, {failed} failed")
    return {"inserted": inserted, "failed": failed}



def update_records(
    table: str,
    updates: Dict[str, Any],
    where: Dict[str, Any],
) -> Dict[str, int]:
    """
    Generic UPDATE using SCHEMA_MAP types.
    Example:
        update_records(
            "wpo.ops_acc_process_queue",
            updates={"status": "Success", "updated_on": datetime.utcnow()},
            where={"carrier_id": "293...", "npn": "12345"}
        )
    """
    table_key = _get_schema_key(table)
    cols = dict(SCHEMA_MAP[table_key])

    if not updates or not where:
        print(f"⚠️ update_records → missing updates or where for {table_key}")
        return {"updated": 0, "failed": 0}

    # Normalize values
    set_items = []
    set_params = []
    for col, val in updates.items():
        if col not in cols:
            print(f"⚠️ Skipping unknown column in updates: {col}")
            continue
        set_items.append(f"{col} = %s")
        set_params.append(_normalize_value(val, cols[col]))

    # Always touch updated_on if the column exists and caller didn’t set it
    if "updated_on" in cols and "updated_on" not in updates:
        set_items.append("updated_on = %s")
        set_params.append(_normalize_value(datetime.utcnow(), "datetime2"))

    where_items = []
    where_params = []
    for col, val in where.items():
        if col not in cols:
            print(f"⚠️ Skipping unknown column in where: {col}")
            continue
        where_items.append(f"{col} = %s")
        where_params.append(_normalize_value(val, cols[col]))

    if not set_items or not where_items:
        print(f"⚠️ update_records → nothing to do for {table_key}")
        return {"updated": 0, "failed": 0}

    sql = f"UPDATE {table_key} SET {', '.join(set_items)} WHERE {' AND '.join(where_items)}"

    conn = get_postgres_connection()
    cur = conn.cursor()

    updated = failed = 0
    try:
        cur.execute(sql, set_params + where_params)
        updated = cur.rowcount or 0

        # ✅ Safe commit wrapper
        try:
            conn.commit()
        except Exception as e:
            if "No corresponding transaction" in str(e) or "111214" in str(e):
                print("⚠️ Skipping phantom transaction commit (already closed by SQL Server).")
            else:
                raise

    except Exception as e:
        failed = 1
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"❌ UPDATE failed on {table_key}: {e}")
        print("— SET params —")
        for i, v in enumerate(set_params, 1):
            print(f"   {i:02d}. {repr(v)} ({type(v).__name__})")
        print("— WHERE params —")
        for i, v in enumerate(where_params, 1):
            print(f"   {i:02d}. {repr(v)} ({type(v).__name__})")

    finally:
        try:
            conn.close()
        except Exception:
            pass

    print(f"📝 update_records[{table_key}] → {updated} updated, {failed} failed")
    return {"updated": updated, "failed": failed}


# ----------------------------------------------------------
# Convenience wrappers for the queue table
# ----------------------------------------------------------

QUEUE_TABLE = "wpo.ops_acc_process_queue"

def insert_queue_records(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """
    Convenience wrapper that also ensures:
      - txn_id and run_id are valid GUIDs (auto-generated if missing/invalid)
      - created_on/updated_on default to now() if not provided
    """
    prepped = []
    for r in (records.to_dict(orient="records") if isinstance(records, pd.DataFrame) else records):
        r = dict(r)  # shallow copy
        r["txn_id"] = _to_guid(r.get("txn_id"))
        r["run_id"] = _to_guid(r.get("run_id"))
        if "created_on" not in r:
            r["created_on"] = datetime.utcnow()
        if "updated_on" not in r:
            r["updated_on"] = datetime.utcnow()
        prepped.append(r)
    return insert_records(QUEUE_TABLE, prepped)

def update_queue_where(updates: Dict[str, Any], where: Dict[str, Any]) -> Dict[str, int]:
    from utils.db_utils import get_synapse_connection
    conn = get_postgres_connection()
    conn.autocommit = True  # ✅ one-line fix
    try:
        return update_records(QUEUE_TABLE, updates, where)
    finally:
        conn.close()
