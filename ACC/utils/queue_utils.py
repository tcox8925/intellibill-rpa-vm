# ==========================================================
# utils/queue_utils.py — Final (insert-only, refactored)
# ==========================================================
"""
Queue Utilities for ACC RPA
---------------------------
Responsibilities:
    • Pre-validate queue records using global_rules
    • Insert only via db_insertor.insert_queue_records()
    • Safe updates via db_insertor.update_queue_where()
    • Fetch / cleanup / deduplicate helpers
"""

import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Union
from utils.db_utils import get_postgres_connection
from utils.db_insertor import insert_queue_records, update_queue_where
from utils.global_rules import apply_global_rules
from utils.logger_utils import safe_log


# ==========================================================
# INSERT WRAPPER
# ==========================================================
def insert_to_queue(records: List[Dict], carrier_row: Dict) -> Dict[str, int]:
    """
    Insert queue records for a carrier after pre-validation.
    Invalid ones (missing NPN/email) are inserted with fail status.
    """
    if not records:
        print("⚠️ insert_to_queue → empty input.")
        return {"inserted": 0, "failed": 0, "invalid": 0}

    valid, invalid = apply_global_rules(records, carrier_row)
    summary = {"inserted": 0, "failed": 0, "invalid": len(invalid)}

    # Insert invalids (soft-fail)
    if invalid:
        for rec in invalid:
            rec.setdefault("status", "Success")
            rec.setdefault("contract_status", carrier_row.get("crm_fail_status", "Needs Attention"))
        try:
            insert_queue_records(invalid)
            print(f"⚠️ {len(invalid)} invalid record(s) inserted as Needs Attention.")
        except Exception as e:
            safe_log("QUEUE_INSERT_FAIL", f"Invalid insert failed: {e}")

    # Insert valid ones
    if valid:
        try:
            result = insert_queue_records(valid)
            summary.update(result)
        except Exception as e:
            safe_log("QUEUE_INSERT_FAIL", f"Valid insert failed: {e}")
            summary["failed"] = len(valid)

    print(f"📦 Queue insert summary → {summary}")
    return summary


# ==========================================================
# SAFE UPDATE WRAPPERS
# ==========================================================
def update_queue_status(carrier_id: str, npn: str, new_status: str, reason: Optional[str] = None):
    """Update queue record’s status + reason."""
    try:
        update_queue_where(
            {"status": new_status, "error_reason": reason},
            {"carrier_id": carrier_id, "npn": npn},
        )
    except Exception as e:
        safe_log("QUEUE_UPDATE_FAIL", f"update_queue_status failed: {e}")


def bulk_update_queue(carrier_id: str, npn_list: List[str], fields: Dict[str, any]):
    """Bulk update by npn list."""
    if not npn_list:
        print(f"⚠️ bulk_update_queue skipped for {carrier_id}")
        return
    for npn in npn_list:
        try:
            update_queue_where(fields, {"carrier_id": carrier_id, "npn": npn})
        except Exception as e:
            safe_log("QUEUE_BULK_UPDATE_FAIL", f"{carrier_id}:{npn}: {e}")


# ==========================================================
# FETCH / DEDUP / CLEANUP
# ==========================================================
def fetch_queue(
    carrier_id: Optional[str] = None,
    status_filter: Optional[Union[str, List[str]]] = None,
    npn_list: Optional[List[str]] = None
) -> pd.DataFrame:
    """Fetch queue data by filters."""
    try:
        conn = get_postgres_connection()
        query = "SELECT * FROM wpo.ops_acc_process_queue WHERE 1=1"
        params = []
        if carrier_id:
            query += " AND carrier_id=%s"
            params.append(carrier_id)
        if status_filter:
            if isinstance(status_filter, str):
                query += " AND status=%s"
                params.append(status_filter)
            else:
                placeholders = ",".join(["%s"] * len(status_filter))
                query += f" AND status IN ({placeholders})"
                params.extend(status_filter)
        if npn_list:
            placeholders = ",".join(["%s"] * len(npn_list))
            query += f" AND npn IN ({placeholders})"
            params.extend(npn_list)
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        print(f"📥 fetch_queue({carrier_id}) → {len(df)} row(s)")
        return df
    except Exception as e:
        safe_log("QUEUE_FETCH_FAIL", f"{carrier_id}: {e}")
        return pd.DataFrame()

# Postgres inbound
"""def deduplicate_contracts(df: pd.DataFrame,
                          carrier_field="carrier",
                          npn_field="npn",
                          date_field="status_date",
                          product_field="product_type") -> pd.DataFrame:
    ""Flag duplicates.""
    if df.empty or npn_field not in df.columns or carrier_field not in df.columns:
        return df
    df[date_field] = pd.to_datetime(df[date_field], errors="coerce")
    df = df.sort_values([carrier_field, npn_field, product_field, date_field], ascending=[True, True, True, False])
    df["is_duplicate"] = df.duplicated(subset=[carrier_field, npn_field, product_field], keep="first")
    df.loc[df["is_duplicate"], "contract_status"] = "Possible Duplicate"
    return df"""

# Zoho inbound
def deduplicate_contracts(df: pd.DataFrame,
                          carrier_field="carrier",
                          npn_field="npn",
                          date_field="status_date") -> pd.DataFrame:
    """Flag duplicates."""
    if df.empty or npn_field not in df.columns or carrier_field not in df.columns:
        return df
    df[date_field] = pd.to_datetime(df[date_field], errors="coerce")
    df = df.sort_values([carrier_field, npn_field, date_field], ascending=[True, True, True])
    df["is_duplicate"] = df.duplicated(subset=[carrier_field, npn_field], keep="first")
    df.loc[df["is_duplicate"], "contract_status"] = "Possible Duplicate"
    return df


def clear_queue_for_carrier(carrier_id: str, statuses: Optional[List[str]] = None) -> int:
    """Remove completed rows."""
    statuses = statuses or ["Success", "Fail"]
    try:
        conn = get_postgres_connection()
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(statuses))
        sql = f"""
            DELETE FROM wpo.ops_acc_process_queue
             WHERE carrier_id=%s AND status IN ({placeholders})
        """
        cur.execute(sql, [carrier_id, *statuses])
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        print(f"🧹 Cleared {deleted} record(s) for {carrier_id}")
        return deleted
    except Exception as e:
        safe_log("QUEUE_CLEAR_FAIL", f"{carrier_id}: {e}")
        return 0


def clean_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce numeric columns safely."""
    if df is None or df.empty:
        return df
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            try:
                df[col] = pd.to_numeric(df[col], errors="ignore")
            except Exception:
                pass
    return df

