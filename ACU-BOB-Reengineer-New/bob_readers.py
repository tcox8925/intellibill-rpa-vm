# ==========================================================
#  bob_readers.py
# ==========================================================
"""
Custom BOB file readers for carriers whose file structure
can't be handled by the standard read_and_map_file.

DESIGN PRINCIPLE: Readers get ALL configuration from the matrices.
  - carrier_id    → rules matrix
  - sheet_name    → rules matrix
  - contract_type → rules matrix
  - column maps   → load matrix
  - skip rows     → rules matrix (ignore_header_rows)

No hardcoded carrier IDs, sheet names, or state mappings.

HCSC reader:
  - Multi-sheet workbook (MAPD/PDP, MedSup, Retail Health ×N, Retail Dental)
  - skiprows from ignore_header_rows
  - Plan State → carrier_id built from rules matrix at runtime
  - Column fallback: slash notation in load matrix

SMA reader:
  - Multi-sheet workbook, one carrier per sheet
  - sheet_name field in rules matrix drives sheet→carrier matching
  - Carriers with empty sheet_name are skipped (e.g. standalone files)
"""

import io, os
import pandas as pd
import numpy as np

from utils.azure_blob_utils import DEFAULT_CONTAINER


_BLANK_VALS = {"", "nan", "None", "NA", "none", "na"}


def _open_excel(blob_data, **kwargs):
    """Open Excel with engine fallback (auto → openpyxl → xlrd)."""
    for engine in [None, "openpyxl", "xlrd"]:
        try:
            kw = {**kwargs}
            if engine:
                kw["engine"] = engine
            return pd.ExcelFile(io.BytesIO(blob_data), **kw)
        except Exception:
            continue
    raise ValueError("Failed to open Excel file with any engine")


def _build_hcsc_state_map(rules_df):
    """
    Build state → {contract_type: carrier_id} map from HCSC rules.

    Parses carrier names like:
      'HCSC - TX ACA'  → state='TX', contract_type='ACA'
      'HCSC - TX MDC'  → state='TX', contract_type='MDC'
      'HCSC - IL'      → state='IL', contract_type from rule

    Returns:
      state_map: dict  e.g. {'TX': {'ACA': '...cid...', 'MDC': '...cid...'},
                              'IL': {'ACA': '...cid...'}, ...}
    """
    if rules_df is None:
        return {}

    hcsc_rules = rules_df[
        rules_df["carrier_name"].str.contains("HCSC", case=False, na=False) &
        (rules_df["process_type"] == "BOB") &
        (rules_df["active_flag"] == "Y")
    ]

    state_map = {}
    for _, r in hcsc_rules.iterrows():
        name = str(r.get("carrier_name", "")).strip()
        cid = str(r.get("carrier_id", "")).strip().replace("'", "")
        ct = str(r.get("contract_type", "")).strip().upper()

        # Parse state from carrier name: "HCSC - TX ACA" → "TX"
        suffix = name.replace("HCSC", "").strip().lstrip("-").strip()
        parts = suffix.split()
        if not parts:
            continue
        state = parts[0].upper()

        # If contract_type is in the name (e.g. "TX ACA"), use it;
        # otherwise fall back to rule's contract_type field
        if len(parts) > 1 and parts[1].upper() in ("ACA", "MDC", "SUP"):
            ct = parts[1].upper()
        elif not ct or ct in _BLANK_VALS:
            ct = "ACA"  # default

        if state not in state_map:
            state_map[state] = {}
        state_map[state][ct] = cid

    return state_map


# ── Sheet type classification for HCSC ──
# This maps Excel sheet name patterns to contract_type.
# It's file-format knowledge (how HCSC structures their workbook),
# not carrier configuration. Stable across runs.
_HCSC_MDC_SHEET_PATTERNS = ["MAPD", "PDP", "MEDSUP"]


def _sheet_contract_type(sheet_name):
    """Determine MDC vs ACA from HCSC sheet name pattern."""
    upper = sheet_name.upper()
    if any(p in upper for p in _HCSC_MDC_SHEET_PATTERNS):
        return "MDC"
    return "ACA"  # Retail Health, Retail Dental, etc.


def read_hcsc_bob(blob_service_client, blob_name, rule, column_mappings,
                  container_name=DEFAULT_CONTAINER,
                  rules_df=None, all_mappings=None):
    """
    HCSC BOB reader.

    Reads multi-sheet workbook, maps columns from load matrix,
    splits by Plan State into per-state carrier_ids using rules matrix.

    Returns list of (sub_rule, sub_df) tuples when rules_df is provided,
    or a single combined DataFrame otherwise.
    """
    skip_rows = int(rule.get("ignore_header_rows", 0) or 0)

    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    xl = _open_excel(blob_data)
    print(f"    📑 HCSC BOB sheets: {xl.sheet_names}")

    # Build state→{contract_type: carrier_id} from rules matrix
    state_map = _build_hcsc_state_map(rules_df)
    if not state_map:
        print(f"    ⚠️  No HCSC carrier rules found in rules matrix")
        return []

    # Load column mappings for HCSC from the load matrix
    hcsc_mappings = None
    if all_mappings is not None:
        hcsc_mappings = all_mappings[
            all_mappings["carrier_name"].str.contains("HCSC", case=False, na=False) &
            (all_mappings["process_type"] == "BOB")
        ]
    elif column_mappings is not None:
        hcsc_mappings = column_mappings

    dataframes = []

    for sheet_name in xl.sheet_names:
        try:
            sheet_df = xl.parse(sheet_name, skiprows=skip_rows, dtype=str)
        except Exception as e:
            print(f"    ⚠️  Failed to read sheet '{sheet_name}': {e}")
            continue

        if sheet_df.empty:
            continue

        sheet_df.columns = sheet_df.columns.str.lower().str.strip()
        sheet_df = sheet_df.loc[:, ~sheet_df.columns.duplicated()]

        # Determine contract_type from sheet name pattern
        contract_type = _sheet_contract_type(sheet_name)

        # Apply column fallback (slash notation: "Sub Producer NPN/Producer NPN")
        if hcsc_mappings is not None and not hcsc_mappings.empty:
            for _, m in hcsc_mappings.iterrows():
                mapping = str(m.get("mapping", "")).strip()
                if "/" in mapping and mapping.lower() != "na":
                    parts = [p.strip().lower() for p in mapping.split("/")]
                    primary, fallback = parts[0], parts[1] if len(parts) > 1 else None
                    if primary in sheet_df.columns and fallback and fallback in sheet_df.columns:
                        sheet_df[primary] = sheet_df[primary].fillna(sheet_df[fallback])

            # Apply column rename from mappings.
            # Slash notation lists the column-name variants used across sheets
            # (e.g. Retail Health uses 'coverage effective date' while MAPD/PDP/
            # MedSup use 'membership coverage effective date'). The load matrix
            # has no sheet_name scoping, so map whichever variant is actually
            # present in THIS sheet — not just the first part.
            rename_map = {}
            for _, m in hcsc_mappings.iterrows():
                raw = str(m.get("mapping", "")).strip().lower()
                db_col = str(m.get("database_column", "")).strip().lower()
                if not raw or raw == "na":
                    continue
                candidates = [p.strip() for p in raw.split("/")] if "/" in raw else [raw]
                chosen = next((c for c in candidates if c in sheet_df.columns), None)
                if chosen:
                    rename_map[chosen] = db_col

            sheet_df = sheet_df.rename(columns=rename_map)

        # Ensure Plan State column exists
        plan_state_col = None
        for candidate in ["plan state", "planstate", "state"]:
            if candidate in sheet_df.columns:
                plan_state_col = candidate
                break

        if plan_state_col is None:
            print(f"    ⚠️  Sheet '{sheet_name}': no Plan State column found")
            continue

        # Drop rows where all values are blank (except Plan State)
        data_cols = [c for c in sheet_df.columns if c != plan_state_col]
        sheet_df = sheet_df.dropna(how="all", subset=data_cols)

        # Set mem_market and mem_count
        sheet_df["mem_market"] = contract_type
        sheet_df["mem_count"] = 1

        # Assign carrier_id from rules-driven state map
        def _resolve_cid(row_state):
            st = str(row_state).strip().upper()
            state_entry = state_map.get(st, {})
            # Look up by sheet's contract_type; fall back to any available
            cid = state_entry.get(contract_type)
            if cid is None and state_entry:
                cid = next(iter(state_entry.values()))
            return cid

        sheet_df["carrier_id"] = sheet_df[plan_state_col].apply(_resolve_cid)

        # Drop Plan State column (no longer needed)
        sheet_df.drop(columns=[plan_state_col], inplace=True, errors="ignore")

        sheet_df["_source_sheet"] = sheet_name
        sheet_df = sheet_df.loc[:, ~sheet_df.columns.duplicated()]
        dataframes.append(sheet_df)
        print(f"    📄 Sheet '{sheet_name}': {len(sheet_df)} rows ({contract_type})")

    if not dataframes:
        return pd.DataFrame()

    combined = pd.concat(dataframes, ignore_index=True)
    combined.drop(columns=["_source_sheet"], inplace=True, errors="ignore")

    # Drop rows where carrier_id couldn't be resolved (unknown state)
    unknown_mask = combined["carrier_id"].isna()
    if unknown_mask.any():
        unknown_states = combined.loc[unknown_mask, "carrier_id"].unique()
        print(f"    ⚠️  {unknown_mask.sum()} rows with unresolved state (no matching HCSC rule) — dropped")
        combined = combined[~unknown_mask].reset_index(drop=True)

    print(f"    📊 HCSC BOB total: {len(combined)} rows across {len(dataframes)} sheets")
    print(f"       carrier_id distribution: {combined['carrier_id'].value_counts().to_dict()}")

    # If called from runner expand pattern, return (sub_rule, sub_df) tuples
    if rules_df is not None:
        results = []
        for cid, group_df in combined.groupby("carrier_id"):
            cid_str = str(cid).lstrip("'")
            matching_rules = rules_df[
                (rules_df["carrier_id"].astype(str).str.lstrip("'") == cid_str) &
                (rules_df["process_type"] == "BOB")
            ]
            if matching_rules.empty:
                # Fall back to parent HCSC rule
                sub_rule = rule.copy()
                sub_rule["carrier_id"] = cid_str
            else:
                sub_rule = matching_rules.iloc[0].to_dict()
            results.append((sub_rule, group_df.copy()))
        return results

    return combined


def read_sma_bob(blob_service_client, blob_name, rule, column_mappings,
                 container_name=DEFAULT_CONTAINER,
                 rules_df=None, all_mappings=None):
    """
    SMA BOB reader. Multi-sheet workbook, one carrier per sheet.

    Sheet→carrier matching is driven by the sheet_name field in the
    rules matrix. Carriers with empty/blank sheet_name are skipped
    (they have standalone files processed separately).

    Column mappings come from the load matrix per carrier.
    contract_type comes from the rules matrix per carrier.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    xl = _open_excel(blob_data)
    print(f"    📑 SMA BOB sheets: {xl.sheet_names}")

    # Build sheet_name → rule mapping from rules matrix
    # Key = sheet_name from the rule (source of truth), Value = rule dict
    sma_rules = {}
    if rules_df is not None:
        sma_carriers = rules_df[
            (rules_df['carrier_name'].str.contains('SMA', case=False, na=False)) &
            (rules_df['process_type'] == 'BOB') &
            (rules_df['active_flag'].isin(['Y', 'y']))
        ]
        for _, r in sma_carriers.iterrows():
            sheet_key = str(r.get('sheet_name', '')).strip()
            if not sheet_key or sheet_key in _BLANK_VALS:
                continue  # No sheet_name = standalone file, skip
            sma_rules[sheet_key.upper()] = r.to_dict() if hasattr(r, 'to_dict') else dict(r)

    results = []
    for sheet_name in xl.sheet_names:
        sheet_upper = sheet_name.strip().upper()

        # Match to carrier rule via sheet_name from the matrix
        sub_rule = sma_rules.get(sheet_upper)
        if sub_rule is None:
            # Try substring match (e.g. sheet "UHC_MS" matches rule sheet_name "UHC")
            sub_rule = next(
                (v for k, v in sma_rules.items()
                 if k in sheet_upper or sheet_upper in k),
                None
            )

        if sub_rule is None:
            print(f"    ⚠️  Sheet '{sheet_name}' has no matching SMA rule — skipping")
            continue

        try:
            df = xl.parse(sheet_name, dtype=str)
        except Exception as e:
            print(f"    ⚠️  Failed to read sheet '{sheet_name}': {e}")
            continue

        if df.empty:
            continue

        df.columns = df.columns.str.lower().str.strip()
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.dropna(how='all')

        if df.empty:
            continue

        # Get carrier metadata from the rule (source of truth)
        cid = str(sub_rule.get('carrier_id', '')).replace("'", "")
        carrier_name = sub_rule.get('carrier_name', '')
        contract_type = sub_rule.get('contract_type', 'MDC')

        # Get column mappings from load matrix for this carrier
        if all_mappings is not None:
            carrier_mappings = all_mappings[
                (all_mappings['carrier_name'].str.strip() == carrier_name) &
                (all_mappings['process_type'] == 'BOB')
            ]
        else:
            carrier_mappings = pd.DataFrame()

        # Apply column mapping
        if not carrier_mappings.empty:
            mapped = carrier_mappings[
                carrier_mappings['mapping'].notna() &
                ~carrier_mappings['mapping'].isin(['', 'NA', 'nan', 'None'])
            ]
            rename_map = {}
            for _, m in mapped.iterrows():
                file_col = str(m['mapping']).strip().lower()
                db_col = str(m['database_column']).strip()
                if file_col in df.columns:
                    rename_map[file_col] = db_col

            df = df.rename(columns=rename_map)

        # Set carrier metadata from rules matrix
        df['carrier_id'] = cid
        df['carrier_name'] = carrier_name
        df['mem_market'] = contract_type
        df['mem_count'] = 1

        results.append((sub_rule, df.copy()))
        print(f"    📄 Sheet '{sheet_name}' → {carrier_name}: {len(df)} rows")

    print(f"    📊 SMA BOB total: {len(results)} carriers, "
          f"{sum(len(r[1]) for r in results)} rows")

    return results


# ==========================================================
#  READER REGISTRY
# ==========================================================

bob_reader_map = {
    "read_hcsc_bob": read_hcsc_bob,
    "read_sma_bob": read_sma_bob,
}


def get_bob_reader(name):
    """Get a custom BOB reader by name. Returns None if not found."""
    name = str(name).strip()
    if name in ("", "NA", "nan", "None"):
        return None
    reader = bob_reader_map.get(name)
    if reader is None:
        print(f"    ⚠️  Unknown BOB reader: {name}")
    return reader
