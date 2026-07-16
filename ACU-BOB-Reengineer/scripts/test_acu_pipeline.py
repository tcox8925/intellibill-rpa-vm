"""
test_devoted_debug.py
---------------------
Diagnostic script for Devoted ACU processing.
Prints row counts + appointed_state samples after every step.

Usage: python test_devoted_debug.py
"""

import os, sys, io
import pandas as pd
import numpy as np
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.db_utils import get_postgres_connection
from utils.azure_blob_utils import authenticate_blob_storage, DEFAULT_CONTAINER

# ── CONFIG ──
CARRIER_NAME = "AMBETTER"
CARRIER_ID = "2931751000020024159"
PROCESS_TYPE = "ACU"
RULES_TABLE = "ops_srv.ops_acu_bob_rules_matrix"
MAPPING_TABLE = "ops_srv.ops_acu_bob_load_matrix"
CONTRACTS_TABLE = "wpo.lup_agents_contracts"

SCAN_DATE = date.today()


def peek(df, label, state_col="appointed_state"):
    """Print row count + appointed_state distribution."""
    print(f"\n  📊 {label}: {len(df)} rows")
    if df.empty:
        return
    if state_col in df.columns:
        non_blank = df[state_col].fillna("").astype(str).str.strip().replace("", pd.NA).dropna()
        blank = len(df) - len(non_blank)
        print(f"     appointed_state: {len(non_blank)} with values, {blank} blank")
        if not non_blank.empty:
            print(f"     sample values: {list(non_blank.head(10).values)}")
            print(f"     unique count: {non_blank.nunique()}")
    else:
        print(f"     ⚠️  '{state_col}' column NOT FOUND")
        print(f"     columns: {list(df.columns)}")


def main():
    print(f"\n{'='*60}")
    print(f"  DEVOTED ACU DEBUG — {SCAN_DATE}")
    print(f"{'='*60}")

    conn = get_postgres_connection()
    blob_client = authenticate_blob_storage()

    # ── STEP 1: Load rule ──
    print(f"\n── STEP 1: Load rule + mappings ──")
    rule = pd.read_sql(
        f"SELECT * FROM {RULES_TABLE} WHERE carrier_name=%s AND process_type=%s AND active_flag='Y'",
        conn, params=[CARRIER_NAME, PROCESS_TYPE]
    ).iloc[0].to_dict()

    print(f"  filter_rule_type: {rule.get('filter_rule_type')}")
    print(f"  filter_values: {rule.get('filter_values')}")
    print(f"  filter_column: {rule.get('filter_column')}")
    print(f"  filter_scope: {rule.get('filter_scope', 'ROW')}")
    print(f"  rts_filter: {rule.get('rts_filter')}")
    print(f"  appointed_state_applicable: {rule.get('appointed_state_applicable')}")
    print(f"  appointed_state_filter: {rule.get('appointed_state_filter')}")
    print(f"  appointment_type_value_map: {rule.get('appointment_type_value_map')}")
    print(f"  primary_identity_field: {rule.get('primary_identity_field')}")

    mappings = pd.read_sql(
        f"SELECT * FROM {MAPPING_TABLE} WHERE carrier_id=%s AND process_type=%s AND (end_date IS NULL OR end_date='' OR end_date='NA')",
        conn, params=[CARRIER_ID, PROCESS_TYPE]
    )
    print(f"\n  Column mappings ({len(mappings)} rows):")
    for _, m in mappings.iterrows():
        mp = m.get("mapping", "NA")
        print(f"    {m['database_column']:25s} <- {mp}")

    # ── STEP 2: Find + read file from blob ──
    print(f"\n── STEP 2: Read file from blob ──")
    month_folder = f"{SCAN_DATE.strftime('%Y')} {SCAN_DATE.strftime('%m')} {SCAN_DATE.strftime('%b')}"
    base_path = f"raw/agent_contract_update/acu_new_process/{month_folder}/"
    pattern = str(rule.get("file_naming_pattern", "")).strip().lower()

    container_client = blob_client.get_container_client(DEFAULT_CONTAINER)
    blob_name = None
    for blob in container_client.list_blobs(name_starts_with=base_path):
        fname = os.path.basename(blob.name).lower()
        if pattern in fname and "." in fname:
            blob_name = blob.name
            break

    if not blob_name:
        print(f"  ❌ No file matching '{pattern}' in {base_path}")
        conn.close()
        return

    print(f"  File: {os.path.basename(blob_name)}")
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()
    df = pd.read_csv(io.BytesIO(blob_data), dtype=str)
    df.columns = df.columns.str.lower().str.strip()

    peek(df, "RAW FILE (before column mapping)")
    print(f"     raw columns: {list(df.columns)}")

    # ── STEP 3: Column mapping ──
    print(f"\n── STEP 3: Column mapping ──")
    rename_map, duplicate_map = {}, {}
    for _, m in mappings.iterrows():
        carrier_col = str(m.get("mapping", "NA")).strip().lower()
        canonical_col = str(m.get("database_column", "")).strip().lower()
        if carrier_col and carrier_col != "na" and carrier_col in df.columns:
            if carrier_col in rename_map:
                duplicate_map[canonical_col] = carrier_col
            else:
                rename_map[carrier_col] = canonical_col

    for canonical_col, carrier_col in duplicate_map.items():
        df[canonical_col] = df[carrier_col]
    df = df.rename(columns=rename_map)

    print(f"  Renamed: {len(rename_map)} | Duplicates: {len(duplicate_map)}")
    peek(df, "AFTER COLUMN MAPPING")

    # ── STEP 4: Show contract_status distribution (BEFORE filter) ──
    print(f"\n── STEP 4: Status distribution (before filter) ──")
    if "contract_status" in df.columns:
        print(df["contract_status"].fillna("(blank)").value_counts().head(20).to_string())
    else:
        print("  ⚠️  No contract_status column")

    # Show which rows have appointed_state vs which have contract_status
    has_status = df["contract_status"].fillna("").str.strip() != "" if "contract_status" in df.columns else pd.Series(False, index=df.index)
    has_state = df["appointed_state"].fillna("").str.strip() != "" if "appointed_state" in df.columns else pd.Series(False, index=df.index)
    print(f"\n  Row breakdown:")
    print(f"    Both status AND state:    {(has_status & has_state).sum()}")
    print(f"    Status only (no state):   {(has_status & ~has_state).sum()}")
    print(f"    State only (no status):   {(~has_status & has_state).sum()}")
    print(f"    Neither:                  {(~has_status & ~has_state).sum()}")

    # ── STEP 5a: ROW-level STATUS filter ──
    print(f"\n── STEP 5a: ROW-level STATUS filter (what we've been doing) ──")
    filter_vals = [v.strip() for v in str(rule.get("filter_values", "")).split(",")]
    filter_col = str(rule.get("filter_column", "contract_status")).strip()
    if filter_col in df.columns:
        df_row = df[df[filter_col].astype(str).str.strip().isin(filter_vals)].copy()
        peek(df_row, "AFTER ROW-LEVEL STATUS FILTER")
    else:
        df_row = df.copy()
        print(f"  ⚠️  filter_col '{filter_col}' not found — no filter applied")

    # ── STEP 5b: AGENT-level STATUS filter ──
    print(f"\n── STEP 5b: AGENT-level STATUS filter (filter_scope=AGENT) ──")
    if filter_col in df.columns and "agent_npn" in df.columns:
        qualifying_npns = df.loc[
            df[filter_col].astype(str).str.strip().isin(filter_vals), "agent_npn"
        ].dropna().unique()
        df_agent = df[df["agent_npn"].isin(qualifying_npns)].copy()
        peek(df_agent, "AFTER AGENT-LEVEL STATUS FILTER")
    else:
        df_agent = df.copy()
        print(f"  ⚠️  Cannot apply agent-level filter")

    # ── STEP 5c: NO filter (like test_acu_pipeline.py) ──
    print(f"\n── STEP 5c: NO filter (like old test script) ──")
    df_none = df.copy()
    peek(df_none, "NO FILTER")

    # ── STEP 6: Continue with AGENT-level filter path ──
    print(f"\n── STEP 6: Continue with AGENT-level filtered data ──")
    df_work = df_agent.copy()
    df_work["contract_status"] = "Active"

    # Dedup
    before = len(df_work)
    df_work = df_work.drop_duplicates(subset=["appointed_state", "agent_npn"])
    df_work = df_work[df_work["agent_npn"].notna() & (df_work["agent_npn"].astype(str).str.strip() != "")].copy()
    peek(df_work, f"AFTER DEDUP (was {before})")

    # Show a sample agent's rows before dedup to understand the structure
    print(f"\n── STEP 7: Sample agent detail ──")
    sample_npn = df_agent["agent_npn"].dropna().iloc[0] if not df_agent.empty else None
    if sample_npn:
        sample = df_agent[df_agent["agent_npn"] == sample_npn]
        print(f"  Agent NPN: {sample_npn} — {len(sample)} rows in file")
        cols = ["agent_npn", "contract_status", "appointed_state", "current_rts", "current_rts_date", "appointment_type"]
        cols = [c for c in cols if c in sample.columns]
        print(sample[cols].to_string(index=False))

    # ── STEP 8: Identity resolution ──
    print(f"\n── STEP 8: Identity resolution ──")
    contracts = pd.read_sql(
        f"SELECT name, npn, writing_number, first_name, last_name, status, status_date, carrier, id FROM {CONTRACTS_TABLE} WHERE carrier = %s",
        conn, params=[CARRIER_ID]
    )
    contracts["npn"] = contracts["npn"].astype(str).str.strip().str.replace(".0", "", regex=False)
    print(f"  Contracts loaded: {len(contracts)}")

    df_work["_npn"] = df_work["agent_npn"].astype(str).str.strip()
    contracts["_npn"] = contracts["npn"].astype(str).str.strip()

    merged = df_work.merge(contracts, left_on="_npn", right_on="_npn", how="left", suffixes=("", "_ct"))
    matched = merged[merged["id"].notna()].copy()
    unmatched = merged[merged["id"].isna()].copy()
    peek(matched, f"MATCHED ({len(unmatched)} unmatched)")

    # ── STEP 9: Rollup ──
    print(f"\n── STEP 9: Rollup ──")
    if not matched.empty and "agent_npn" in matched.columns:
        def _agg(group):
            first = group.iloc[0]
            states = group["appointed_state"].fillna("").astype(str).str.strip()
            states = states[states != ""].unique()
            return pd.Series({
                "contract_status": "Active",
                "appointed_state": "; ".join(sorted(states)) if len(states) > 0 else "",
                "appointment_type": first.get("appointment_type", ""),
                "current_rts": "Yes" if "Yes" in group.get("current_rts", pd.Series([""])).fillna("").values else "",
                "next_rts": "Yes" if "Yes" in group.get("next_rts", pd.Series([""])).fillna("").values else "",
                "name": first.get("name", ""),
            })

        rolled = matched.groupby("agent_npn", sort=False).apply(_agg, include_groups=False).reset_index()
        peek(rolled, "AFTER ROLLUP")

        # Show sample agents with states
        has_states = rolled[rolled["appointed_state"].str.strip() != ""]
        no_states = rolled[rolled["appointed_state"].str.strip() == ""]
        print(f"\n  Agents WITH states: {len(has_states)}")
        print(f"  Agents WITHOUT states: {len(no_states)}")
        if not has_states.empty:
            print(f"\n  Sample agents with states:")
            print(has_states[["agent_npn", "appointed_state", "current_rts"]].head(5).to_string(index=False))
        if not no_states.empty:
            print(f"\n  Sample agents WITHOUT states:")
            print(no_states[["agent_npn", "appointed_state", "current_rts"]].head(5).to_string(index=False))
    else:
        print("  ⚠️  No matched data to roll up")

    conn.close()
    print(f"\n{'='*60}")
    print(f"  DEBUG COMPLETE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()