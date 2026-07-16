import os
# ==========================================================
#  acu_processor.py
# ==========================================================
"""
Core ACU processing logic. Matrix-driven, vectorized.
Flow: Read file → map columns → matrix flags → handler → dedup →
      identity resolution → rollup → transitions → outputs
"""

import io, os, csv, re
import pandas as pd
import numpy as np
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from utils.azure_blob_utils import DEFAULT_CONTAINER
from acu_handlers import get_handler
from acu_readers import get_custom_reader
from config import (EXCLUDED_CONTRACT_STATUSES, EXCLUDED_AGENT_STATUSES,
                    BLOCKED_TRANSITION_FROM, BLOCKED_TRANSITION_TO, ALLOWED_FROM_ACTIVE,
                    PROCESS_COLUMN_MAP, EXCEPTION_THRESHOLD_PCT)

CONTRACTS_TABLE = "wpo.lup_agents_contracts"
AGENTS_TABLE = "wpo.lup_agents"

# ── EXCEPTION IDs (wpo.lup_exception_list) ──
EXC_IDENTITY_NOT_FOUND = 19       # E19-ACU-IdentityNotFound
EXC_IDENTITY_MULTIPLE_MATCH = 20  # E20-ACU-IdentityMultipleMatch
EXC_CONTRACT_STATUS_EXCLUDED = 21 # E21-ACU-ContractStatusExcluded
EXC_AGENT_STATUS_EXCLUDED = 22    # E22-ACU-AgentStatusExcluded
EXC_AGENT_NOT_IN_CRM = 23         # E23-ACU-AgentNotInCRM
EXC_BLOCKED_TRANSITION = 24       # E24-ACU-BlockedTransition
EXC_PARENT_NOT_RESOLVED = 25      # E25-ACU-ParentNotResolved

RESULT_COLUMNS = [
    "Name", "NPN", "Writing_Number", "Status_Date", "Status",
    "Appointment_Type", "Appointed_States", "Appointed_Date",
    "Parent_Contract", "Current_Medicare_RTS", "Current_Medicare_RTS_Date",
    "Next_Medicare_RTS", "Next_Medicare_RTS_Date", "ACA_RTS",
    "carrier_name", "carrier_id", "run_date", "note",
]

EXCEPTION_COLUMNS = [
    "Name", "NPN", "Writing_Number", "Status_Date", "Status",
    "Appointment_Type", "Appointed_States", "Appointed_Date",
    "Parent_Contract", "Current_Medicare_RTS", "Current_Medicare_RTS_Date",
    "Next_Medicare_RTS", "Next_Medicare_RTS_Date", "ACA_RTS",
    "carrier_name", "carrier_id", "run_date", "note",
    "exception_reason", "exception_id",
]


KEYVAULT_URL = os.getenv("KEYVAULT_URL", "")
_secret_cache = {}


def _get_secret(secret_name):
    """Fetch a secret from Key Vault (cached after first call per secret)."""
    if secret_name in _secret_cache:
        return _secret_cache[secret_name]
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KEYVAULT_URL, credential=credential)
        value = client.get_secret(secret_name).value
        _secret_cache[secret_name] = value
        print(f"    🔑 Secret loaded from Key Vault ({secret_name})")
        return value
    except Exception as e:
        print(f"    ⚠️  Key Vault error for '{secret_name}': {e}")
        return None


def _decrypt_excel(blob_data, password):
    """Decrypt a password-protected Excel file in memory."""
    import msoffcrypto
    encrypted = io.BytesIO(blob_data)
    decrypted = io.BytesIO()
    office_file = msoffcrypto.OfficeFile(encrypted)
    office_file.load_key(password=password)
    office_file.decrypt(decrypted)
    decrypted.seek(0)
    print(f"    🔓 File decrypted")
    return decrypted.read()


# ── COLUMN-MAPPING COERCION ──
def _coerce_column_mappings(column_mappings):
    """
    Defensive guard: callers are expected to pass a DataFrame with at least
    'mapping' and 'database_column' columns. Some paths hand in a plain dict
    (observed crashing Ameritas/Cigna with "'dict' object has no attribute ...").

    - DataFrame  → returned unchanged.
    - dict       → best-effort coercion:
        * flat {file_col: db_col}  → 2-col frame, mappings preserved.
        * records-style {col: {...}} or list-like → pd.DataFrame(dict).
        * anything unusable        → empty 2-col frame.
    - None/other → empty 2-col frame.

    Never raises; on any failure returns an empty frame so the rename loop is a no-op.
    """
    empty = pd.DataFrame(columns=["mapping", "database_column"])
    if isinstance(column_mappings, pd.DataFrame):
        return column_mappings
    if isinstance(column_mappings, dict):
        if not column_mappings:
            return empty
        try:
            # Flat {file_col: db_col} string mapping (the common shape).
            if all(not isinstance(v, (dict, list, tuple)) for v in column_mappings.values()):
                return pd.DataFrame(
                    [{"mapping": str(k), "database_column": str(v)}
                     for k, v in column_mappings.items()]
                )
            # Otherwise assume a column-oriented dict.
            df_cm = pd.DataFrame(column_mappings)
            return df_cm if {"mapping", "database_column"}.issubset(df_cm.columns) else empty
        except Exception as e:
            print(f"    ⚠️  Could not coerce dict column_mappings ({e}) — proceeding with no rename")
            return empty
    return empty


def _format_from_blob_name(blob_name):
    ext = os.path.splitext(blob_name.lower())[1]
    if ext == ".csv":
        return "csv"
    if ext in (".xlsx", ".xls"):
        return "xlsx"
    return None


def _normalize_file_format(file_format):
    if file_format in ("xlsx", "xls"):
        return "xlsx"
    if file_format == "csv":
        return "csv"
    return None


def _read_csv_bytes(blob_data, sep, encoding, skip_rows):
    # index_col=False: some carrier exports have a trailing delimiter, giving data
    # rows one more field than the header. Without this, pandas silently promotes
    # column 0 to the index and shifts every named column left by one (this was the
    # NEW ERA misread: PolicyStatus picked up PlanCode's values). It's a no-op for
    # well-formed files.
    try:
        return pd.read_csv(io.BytesIO(blob_data), sep=sep, encoding=encoding, skiprows=skip_rows, dtype=str, index_col=False, on_bad_lines='warn')
    except UnicodeDecodeError:
        return pd.read_csv(io.BytesIO(blob_data), sep=sep, encoding="latin-1", skiprows=skip_rows, dtype=str, index_col=False, on_bad_lines='warn')


def _read_excel_bytes(blob_data, sheet_name, skip_rows):
    sheet = 0 if sheet_name in ("NA", "", None) else sheet_name
    try:
        xls = pd.ExcelFile(io.BytesIO(blob_data))
        print(f"    📑 Excel sheets: {xls.sheet_names}")
        if sheet and sheet not in xls.sheet_names:
            matched_sheet = next((s for s in xls.sheet_names if s.strip().lower() == sheet.strip().lower()), None)
            if not matched_sheet:
                matched_sheet = next((s for s in xls.sheet_names
                                      if s.strip().lower().startswith(sheet.strip().lower())), None)
            if matched_sheet:
                print(f"    ⚠️  Sheet '{sheet}' not exact match — using '{matched_sheet}'")
                sheet = matched_sheet
        return pd.read_excel(xls, sheet_name=sheet, skiprows=skip_rows, dtype=str)
    except Exception as e1:
        for engine in ["openpyxl", "xlrd"]:
            try:
                df = pd.read_excel(io.BytesIO(blob_data), sheet_name=sheet, skiprows=skip_rows, dtype=str, engine=engine)
                print(f"    ⚠️  Opened with fallback engine: {engine}")
                return df
            except Exception:
                continue
        try:
            df = pd.read_html(io.BytesIO(blob_data))[0].astype(str)
            if skip_rows > 0:
                df = df.iloc[skip_rows:].reset_index(drop=True)
            print(f"    ⚠️  Opened as HTML table (exported as .xlsx)")
            return df
        except Exception:
            raise ValueError(f"Excel read error: {e1}") from e1


# ── READ FILE + MAP COLUMNS ──
def apply_column_mappings(df, column_mappings):
    """THE single place load-matrix column bindings are applied.

    Renames each mapped source column to its canonical database_column, and
    supports one source -> many targets (e.g. NPN -> agent_npn AND
    agent_writing_num) via a duplicate copy. Matching is case-insensitive on the
    source name, so this works whether the caller's columns are raw (custom
    readers) or already lower-cased (read_and_map_file).

    Custom readers call this AFTER doing format/reshape so that column bindings
    live in the matrix, never hardcoded in reader code. Columns not present in
    the mapping are left untouched (so a reader's raw helper columns survive).
    """
    column_mappings = _coerce_column_mappings(column_mappings)
    lc_to_actual = {str(c).strip().lower(): c for c in df.columns}
    rename_map, duplicate_map = {}, {}
    for _, m in column_mappings.iterrows():
        src = str(m.get("mapping", "NA")).strip().lower()
        tgt = str(m.get("database_column", "")).strip().lower()
        if not src or src in ("na", "nan") or not tgt:
            continue
        actual = lc_to_actual.get(src)
        if actual is None:
            continue
        if actual in rename_map:
            duplicate_map[tgt] = actual          # same source feeds another target
        else:
            rename_map[actual] = tgt
    for tgt, actual in duplicate_map.items():
        df[tgt] = df[actual]
    df = df.rename(columns=rename_map)
    print(f"    🔄 Applied {len(rename_map) + len(duplicate_map)} load-matrix column mappings")
    return df


def read_and_map_file(blob_service_client, blob_name, rule, column_mappings, container_name=DEFAULT_CONTAINER):
    column_mappings = _coerce_column_mappings(column_mappings)
    delimiter_map = {"comma": ",", "pipe": "|", "tab": "\t"}
    sep = delimiter_map.get(rule.get("file_delimiter", "comma"), ",")
    encoding = rule.get("file_encoding", "utf-8")
    skip_rows = int(rule.get("ignore_header_rows", 0) or 0)
    rule_format = _normalize_file_format(rule.get("file_format", "csv")) or "csv"
    blob_format = _format_from_blob_name(blob_name)
    sheet_name = rule.get("sheet_name", "NA")

    formats_to_try = []
    for fmt in (blob_format, rule_format, "csv", "xlsx"):
        if fmt and fmt not in formats_to_try:
            formats_to_try.append(fmt)

    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    password_secret = _rule_val(rule, "password_secret_name")
    if password_secret and "xlsx" in formats_to_try:
        password = _get_secret(password_secret)
        if password:
            blob_data = _decrypt_excel(blob_data, password)
        else:
            raise ValueError(f"Cannot read password-protected file — Key Vault secret '{password_secret}' not found")

    df = None
    last_error = None
    for fmt in formats_to_try:
        try:
            if fmt == "csv":
                df = _read_csv_bytes(blob_data, sep, encoding, skip_rows)
            else:
                df = _read_excel_bytes(blob_data, sheet_name, skip_rows)
            if fmt != rule_format:
                print(f"    ⚠️  Read as {fmt} (rule says {rule_format})")
            break
        except Exception as e:
            last_error = e
            continue
    if df is None:
        raise ValueError(f"Could not read {os.path.basename(blob_name)} as csv or xlsx: {last_error}")

    df.columns = df.columns.str.lower().str.strip()

    # Drop fully-blank rows before counting. Excel-exported CSVs frequently pad
    # the file out to the sheet's used range (~1,048,576 rows), writing every
    # empty row as a delimiter-only line (",,,,,,,,,"). Those read as all-NaN and
    # would otherwise inflate the row count massively (e.g. McLaren: 1,048,068 → 6).
    _pre_blank = len(df)
    df = df.dropna(how="all").reset_index(drop=True)
    _blank_dropped = _pre_blank - len(df)
    if _blank_dropped:
        print(f"    🧹 Dropped {_blank_dropped:,} fully-blank row(s)")

    print(f"    📄 Read {len(df)} rows from {os.path.basename(blob_name)}")

    df = apply_column_mappings(df, column_mappings)
    return df


# ── NORMALIZE VALUES ──
def normalize_values(df):
    """
    Clean all string columns:
      - Strip Excel formula wrappers: ="A" → A, ="Active" → Active
      - Trim leading/trailing whitespace
      - Remove non-printable characters
    Runs once after column mapping, before any matrix logic.
    """
    import re
    _excel_formula = re.compile(r'^="?(.*?)"?$')

    # Drop duplicate columns (multi-sheet carriers like HCSC can produce these)
    df = df.loc[:, ~df.columns.duplicated()]

    for col in df.columns:
        if df[col].dtype == object:
            # Strip whitespace
            df[col] = df[col].astype(str).str.strip()
            # Excel formula wrappers: ="A" → A, ="Active" → Active
            df[col] = df[col].apply(
                lambda x: _excel_formula.match(x).group(1) if isinstance(x, str) and x.startswith('=') and _excel_formula.match(x) else x
            )
            # Non-printable characters (keep newlines/tabs for multi-line fields)
            df[col] = df[col].str.replace(r'[^\x20-\x7E\n\t]', '', regex=True)
            # Restore true nulls
            df[col] = df[col].replace({"nan": None, "None": None, "": None})

    print(f"    🧹 Values normalized")
    return df


# ── MATRIX FLAGS ──
_BLANK_VALS = {"", "NA", "nan", "None", "none", "null", "NULL"}


def _rule_val(rule, key, default=""):
    """Get a rule value as a clean string. Returns default if null/blank/NA."""
    raw = rule.get(key)
    if raw is None:
        return default
    try:
        if pd.isna(raw):
            return default
    except (TypeError, ValueError):
        pass
    val = str(raw).strip()
    return default if val in _BLANK_VALS else val


def apply_matrix_flags(df, rule, process_type="ACU"):
    col_map = PROCESS_COLUMN_MAP.get(process_type, PROCESS_COLUMN_MAP["ACU"])
    filter_type = _rule_val(rule, "filter_rule_type", "ALL")
    _dropped_frames = []  # rows removed by STATUS/CONTAINS/DATE filters → tracked as exceptions

    if filter_type == "ALL":
        if "contract_status" not in df.columns:
            df["contract_status"] = "Active"
        else:
            df["contract_status"] = df["contract_status"].fillna("Active")
            df.loc[df["contract_status"].str.strip() == "", "contract_status"] = "Active"
    elif filter_type == "STATUS":
        filter_col = _rule_val(rule, "filter_column", "contract_status")
        filter_vals = _rule_val(rule, "filter_values")
        filter_scope = _rule_val(rule, "filter_scope", "ROW").upper()
        if filter_col in df.columns and filter_vals:
            # Pipe-delimited (consistent with value_maps) and case-insensitive.
            # Pipe (not comma) is the separator so status values that legitimately
            # contain a comma — e.g. "20 - Active, Premium Paying" — stay intact.
            vals_lc = [v.strip().lower() for v in filter_vals.split("|") if v.strip()]
            col_lc = df[filter_col].astype(str).str.strip().str.lower()
            before = len(df)
            if filter_scope == "AGENT" and "agent_npn" in df.columns:
                # Multi-row carriers: status on one row, state/RTS detail on separate rows.
                # Filter at AGENT level — keep ALL rows for agents who have at least one
                # qualifying status row.
                qualifying_npns = df.loc[col_lc.isin(vals_lc), "agent_npn"].dropna().unique()
                keep = df["agent_npn"].isin(qualifying_npns)
                _dropped_frames.append(df[~keep].copy())
                df = df[keep].copy().reset_index(drop=True)
                print(f"    🔧 STATUS filter (agent-level) on '{filter_col}': {before} → {len(df)} rows")
            else:
                keep = col_lc.isin(vals_lc)
                _dropped_frames.append(df[~keep].copy())
                df = df[keep].copy().reset_index(drop=True)
                print(f"    🔧 STATUS filter on '{filter_col}': {before} → {len(df)} rows")
            # Normalize contract_status to "Active" only when the filter is on
            # contract_status AND there is no status_value_map. When a
            # status_value_map exists it is authoritative (applied below) and
            # must see the raw surviving values — e.g. CHRISTUS keeps Suspended
            # rows and maps them to "Active - Recertification Needed".
            if filter_col == "contract_status" and not _rule_val(rule, "status_value_map"):
                df.loc[:, "contract_status"] = "Active"
    elif filter_type == "CONTAINS":
        # Substring match: normalize matching rows to Active, drop non-matching
        filter_col = _rule_val(rule, "filter_column", "contract_status")
        filter_vals = _rule_val(rule, "filter_values")
        if filter_col in df.columns and filter_vals:
            before = len(df)
            mask = df[filter_col].astype(str).str.contains(filter_vals, case=False, na=False)
            _dropped_frames.append(df[~mask].copy())
            df = df[mask].copy().reset_index(drop=True)
            df.loc[:, "contract_status"] = "Active"
            print(f"    🔧 CONTAINS filter on '{filter_col}' for '{filter_vals}': {before} → {len(df)} rows")
    elif filter_type == "DATE":
        # Active = coverage not yet past its term date (today <= term date), or no
        # term date at all. Used by carriers whose file has no status column
        # (e.g. several SMA tabs) and "active" must come from the term date.
        filter_col = _rule_val(rule, "filter_column", "mem_cov_end_date")
        if filter_col in df.columns:
            before = len(df)
            raw = df[filter_col].fillna("").astype(str).str.strip()
            end_dates = pd.to_datetime(raw, errors="coerce")
            # Retry values the default parse missed using the rule's declared
            # date_format (SMA term dates are compact MMDDYYYY, e.g. "12312026").
            miss = end_dates.isna() & (raw != "")
            if miss.any():
                _pyfmt = {"MMDDYYYY": "%m%d%Y", "MM/DD/YYYY": "%m/%d/%Y",
                          "YYYYMMDD": "%Y%m%d", "YYYY-MM-DD": "%Y-%m-%d",
                          "DDMMYYYY": "%d%m%Y"}.get(
                              _rule_val(rule, "date_format", "").strip().upper())
                if _pyfmt:
                    end_dates.loc[miss] = pd.to_datetime(raw[miss], format=_pyfmt, errors="coerce")
            keep = end_dates.isna() | (end_dates >= pd.Timestamp.today())
            _dropped_frames.append(df[~keep].copy())
            df = df[keep].copy().reset_index(drop=True)
            # Survivors are the active book; label them Active where the file
            # carried no status of its own (don't overwrite a real status value).
            _stcol = col_map.get("status_value_map_target", "contract_status")
            if _stcol not in df.columns:
                df[_stcol] = "Active"
            else:
                _blank = df[_stcol].fillna("").astype(str).str.strip() == ""
                df.loc[_blank, _stcol] = "Active"
            print(f"    🔧 DATE filter on '{filter_col}': {before} → {len(df)} rows (active = no/future term date)")

    # Stash rows the filter removed so process_carrier can surface them as tracked
    # exceptions (file → results + exceptions must reconcile; no silent drops).
    _dropped = [f for f in _dropped_frames if f is not None and not f.empty]
    if _dropped:
        try:
            df.attrs["_status_filtered"] = pd.concat(_dropped, ignore_index=True)
        except Exception:
            pass

    default_val = _rule_val(rule, "default_type_value")
    default_target = col_map.get("default_type_target", "appointment_type")
    if default_val:
        if default_target not in df.columns:
            df[default_target] = default_val
        else:
            df[default_target] = df[default_target].fillna(default_val)
            df.loc[df[default_target].str.strip() == "", default_target] = default_val

    # type_value_map MUST run before parent_npn assignment (ACU)
    # so that parent_npn_scope (e.g. "Subproducer") matches the transformed values
    type_map_str = _rule_val(rule, "type_value_map")
    type_source = col_map.get("type_value_map_source", "appointment_type")
    type_target = col_map.get("type_value_map_target", "appointment_type")
    if type_map_str and type_source in df.columns:
        df[type_target] = df[type_source].apply(lambda x: _apply_value_map(x, type_map_str))
        print(f"    🗺️  type_value_map applied ({type_source} → {type_target})")

    # product_value_map: decode product_type in place. Exact by default; prefix the
    # map with "contains|" when the code is embedded in the value (e.g. Pivot's
    # policy-id prefixes STM/DTL/DTV). Keeps these decodes matrix-driven, not in code.
    prod_map_str = _rule_val(rule, "product_value_map")
    if prod_map_str and "product_type" in df.columns:
        if prod_map_str.strip().lower().startswith("contains|"):
            _pairs = [(k.strip(), v.strip())
                      for k, v in (e.split(":", 1) for e in prod_map_str.split("|")[1:] if ":" in e)]
            def _contains_map(val, _pairs=_pairs):
                s = str(val).upper()
                for k, v in _pairs:
                    if k.upper() in s:
                        return v
                return val
            df["product_type"] = df["product_type"].apply(_contains_map)
        else:
            df["product_type"] = df["product_type"].apply(lambda x: _apply_value_map(x, prod_map_str))
        print(f"    🗺️  product_value_map applied → product_type")

    # status_value_map (e.g. A:Active|T:Terminated, Paid Activated:Active)
    status_target = col_map.get("status_value_map_target", "contract_status")
    status_map_str = _rule_val(rule, "status_value_map")
    if status_map_str and status_target in df.columns:
        df[status_target] = df[status_target].apply(lambda x: _apply_value_map(x, status_map_str))
        print(f"    🗺️  status_value_map applied → {status_target}")

    # subscriber_value_map: normalize a relation/subscriber indicator into the Y/N
    # form the downstream vault expects (contract_count = 0 when is_subscriber == 'N',
    # else 1). Carrier-scoped — only carriers whose rule sets this field run it (e.g.
    # UHC-SUP 'Relation to Primary' → 'Primary:Y|*:N'), so carriers whose is_subscriber
    # is already Y/N (e.g. HCSC 'Subscriber Ind') are left untouched.
    sub_map_str = _rule_val(rule, "subscriber_value_map")
    if sub_map_str and "is_subscriber" in df.columns:
        df["is_subscriber"] = df["is_subscriber"].apply(lambda x: _apply_value_map(x, sub_map_str))
        print(f"    🗺️  subscriber_value_map applied → is_subscriber")

    parent_npn = _rule_val(rule, "parent_npn")
    parent_scope = _rule_val(rule, "parent_npn_scope")
    if parent_npn:
        if "parent_npn" not in df.columns:
            df["parent_npn"] = ""
        if parent_scope == "all":
            df["parent_npn"] = parent_npn
        elif parent_scope and "appointment_type" in df.columns:
            df.loc[df["appointment_type"] == parent_scope, "parent_npn"] = parent_npn

    state_filter = _rule_val(rule, "appointed_state_filter")
    if state_filter:
        if ":" in state_filter:
            # Column:Value filter — e.g. "_state_status:Active/Certified"
            col_part, val_part = state_filter.split(":", 1)
            col_part = col_part.strip()
            vals = [v.strip() for v in val_part.split("|")]
            if col_part in df.columns:
                before = len(df)
                df = df[df[col_part].astype(str).str.strip().isin(vals)].copy().reset_index(drop=True)
                print(f"    🔧 State filter on '{col_part}': {before} → {len(df)} rows")
        else:
            # Simple static value — e.g. "FL", "Michigan"
            df["appointed_state"] = state_filter

    if "appointed_date" not in df.columns or df["appointed_date"].isna().all():
        df["appointed_date"] = datetime.today().strftime("%Y-%m-%d")

    # Ensure all canonical columns exist before RTS/date logic touches them
    for col in ["agent_npn", "agent_writing_num", "agent_full_name", "contract_status",
                 "appointed_state", "appointment_type", "contract_date", "appointed_date",
                 "parent_npn", "current_rts", "current_rts_date", "next_rts", "next_rts_date", "aca_rts"]:
        if col not in df.columns:
            df[col] = ""

    # RTS logic — routed by rts_filter
    rts_flag = _rule_val(rule, "rts_flag_applicable", "N")
    rts_filter = _rule_val(rule, "rts_filter")
    if rts_flag == "Y":
        df = _apply_rts_logic(df, rts_filter)
        print(f"    📊 RTS logic applied (filter={rts_filter or 'default'})")

    # RTS date override — e.g. rts_date_rule=TODAY sets dates to today where flag is Yes
    rts_date_rule = _rule_val(rule, "rts_date_rule")
    if rts_date_rule == "TODAY":
        today = datetime.today().strftime("%Y-%m-%d")
        yes_vals = {"YES", "Y", "TRUE", "1", "Yes"}
        for rts_col, date_col in [("current_rts", "current_rts_date"), ("next_rts", "next_rts_date")]:
            if rts_col in df.columns and date_col in df.columns:
                has_rts = df[rts_col].fillna("").astype(str).str.strip().isin(yes_vals)
                df.loc[has_rts, date_col] = today
                df.loc[~has_rts, date_col] = ""
        print(f"    📊 RTS dates set to today where flag=Yes")

    # Dates — only parse contract_date and appointed_date; rts dates handled by _apply_rts_logic
    for col in ["contract_date", "appointed_date"]:
        if col in df.columns:
            df[col] = df[col].replace("########", None).replace("", None)
            df[col] = pd.to_datetime(df[col].astype(str), errors="coerce").dt.strftime("%Y-%m-%d")

    for col in ["agent_npn", "agent_writing_num", "parent_npn"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(".0", "", regex=False)
            df[col] = df[col].replace({"None": None, "nan": None, "": None})

    return df


# ── VALUE MAP ──
def _apply_value_map(val, map_str):
    """
    Apply pipe-delimited value map to a single value.
    Format: FILE_VALUE:MappedValue|...|*:CatchAll
    Special keys: NULL (blank/null input), * (any unmatched value)
    """
    # Keys are matched case-insensitively (the row filter that selects which
    # values reach here is itself case-insensitive, so the map must agree).
    mapping, wildcard, null_val = {}, None, None
    for entry in map_str.split("|"):
        if ":" not in entry:
            continue
        k, v = entry.split(":", 1)
        k, v = k.strip(), v.strip()
        if k == "*":
            wildcard = v
        elif k.upper() == "NULL":
            null_val = v
        else:
            mapping[k.lower()] = v
    if pd.isna(val) or str(val).strip() == "":
        return null_val if null_val is not None else val
    val_str = str(val).strip()
    return mapping.get(val_str.lower(), wildcard if wildcard is not None else val)


def _known_map_keys(map_str):
    """Return set of explicit (non-wildcard) keys from a value map string."""
    keys = set()
    for entry in map_str.split("|"):
        if ":" not in entry:
            continue
        k = entry.split(":", 1)[0].strip()
        if k not in ("*", "NULL"):
            keys.add(k)
    return keys


# ── RTS LOGIC ──
def _apply_rts_logic(df, rts_filter):
    """Route RTS population based on rts_filter value from rules matrix."""
    cy, ny = str(datetime.today().year), str(datetime.today().year + 1)
    yes_vals = {"YES", "Y", "TRUE", "1"}

    if rts_filter == "PLAN_YEAR":
        # Multi-row per agent: current_rts_date IS the plan year; current_rts is Yes/No per row
        if "current_rts_date" not in df.columns:
            return df
        plan_year = df["current_rts_date"].astype(str).str.strip()
        curr_mask = plan_year == cy
        next_mask = plan_year == ny
        other_mask = ~curr_mask & ~next_mask
        # Next-year rows → move to next_rts fields, clear current
        df.loc[next_mask, "next_rts"] = df.loc[next_mask, "current_rts"].apply(
            lambda x: "Yes" if str(x).strip().upper() in yes_vals else "")
        df.loc[next_mask, "next_rts_date"] = df.loc[next_mask, "current_rts_date"]
        df.loc[next_mask, "current_rts"] = ""
        df.loc[next_mask, "current_rts_date"] = ""
        # Current-year rows → normalize current_rts, clear next
        df.loc[curr_mask, "current_rts"] = df.loc[curr_mask, "current_rts"].apply(
            lambda x: "Yes" if str(x).strip().upper() in yes_vals else "")
        df.loc[curr_mask, "next_rts"] = ""
        df.loc[curr_mask, "next_rts_date"] = ""
        # Other rows: clear all
        for col in ["current_rts", "current_rts_date", "next_rts", "next_rts_date"]:
            if col in df.columns:
                df.loc[other_mask, col] = ""

    elif rts_filter == "DATE":
        # current_rts is a date string or year — parse year, route to current or next bucket
        def _parse(row):
            try:
                year = datetime.strptime(str(row["current_rts"]), "%Y-%m-%d").year
                rts_val = str(year)
            except (ValueError, TypeError):
                rts_val = str(row.get("current_rts", "")).strip()
            if rts_val == cy or rts_val.upper() in yes_vals:
                return "Yes", ""
            elif rts_val == ny:
                return "", "Yes"
            return "", ""
        df[["current_rts", "next_rts"]] = df.apply(lambda r: pd.Series(_parse(r)), axis=1)

    elif rts_filter == "NOTNULL":
        if "current_rts_date" in df.columns:
            df["current_rts"] = df["current_rts_date"].apply(
                lambda x: "Yes" if pd.notna(x) and str(x).strip() not in ("", "NA", "nan") else "")
        if "next_rts_date" in df.columns:
            df["next_rts"] = df["next_rts_date"].apply(
                lambda x: "Yes" if pd.notna(x) and str(x).strip() not in ("", "NA", "nan") else "")

    elif rts_filter == "ACA_RTS":
        # AmeriHealth ACA: appointed_date = next_rts_date; aca_rts from year check; clear rts fields
        if "next_rts_date" in df.columns:
            df["appointed_date"] = df["next_rts_date"]
        if "current_rts" in df.columns:
            df["aca_rts"] = df["current_rts"].apply(
                lambda x: "Yes" if str(x).strip() == cy or str(x).strip().upper() in yes_vals else "")
        for col in ["current_rts", "current_rts_date", "next_rts", "next_rts_date"]:
            if col in df.columns:
                df[col] = ""

    else:
        # Default: normalize Yes/blank; dates only if rts=Yes
        for rts_col, date_col in [("current_rts", "current_rts_date"), ("next_rts", "next_rts_date")]:
            if rts_col in df.columns:
                df[rts_col] = df[rts_col].apply(
                    lambda x: "Yes" if str(x).strip().upper() in yes_vals else "")
            if date_col in df.columns and rts_col in df.columns:
                df[date_col] = df.apply(
                    lambda r, rc=rts_col, dc=date_col: r[dc] if r[rc] == "Yes" else "", axis=1)

    if "aca_rts" not in df.columns:
        df["aca_rts"] = ""

    # Final cleanup: dates must be blank where flag is not "Yes"
    # This prevents stale dates leaking across year buckets
    for rts_col, date_col in [("current_rts", "current_rts_date"), ("next_rts", "next_rts_date")]:
        if rts_col in df.columns and date_col in df.columns:
            no_flag = df[rts_col].fillna("").astype(str).str.strip() != "Yes"
            df.loc[no_flag, date_col] = ""

    return df


# ── ROW FILTER (read-only) ──
def _rows_surviving_filter(df, rule):
    """
    Return the subset of rows that will survive the matrix row filter
    (STATUS / CONTAINS / DATE), without mutating values or printing.

    Used by check_value_maps so the value-map integrity check only inspects
    values that actually reach processing — rows the filter drops (e.g. an
    inactive 'I' status removed by a STATUS filter) never need a mapping.

    Mirrors the row-selection logic in apply_matrix_flags; keep them in sync.
    """
    filter_type = _rule_val(rule, "filter_rule_type", "ALL")

    if filter_type == "STATUS":
        filter_col = _rule_val(rule, "filter_column", "contract_status")
        filter_vals = _rule_val(rule, "filter_values")
        filter_scope = _rule_val(rule, "filter_scope", "ROW").upper()
        if filter_col in df.columns and filter_vals:
            vals_lc = [v.strip().lower() for v in filter_vals.split("|") if v.strip()]
            col_lc = df[filter_col].astype(str).str.strip().str.lower()
            if filter_scope == "AGENT" and "agent_npn" in df.columns:
                qualifying = df.loc[col_lc.isin(vals_lc), "agent_npn"].dropna().unique()
                return df[df["agent_npn"].isin(qualifying)]
            return df[col_lc.isin(vals_lc)]
    elif filter_type == "CONTAINS":
        filter_col = _rule_val(rule, "filter_column", "contract_status")
        filter_vals = _rule_val(rule, "filter_values")
        if filter_col in df.columns and filter_vals:
            mask = df[filter_col].astype(str).str.contains(filter_vals, case=False, na=False)
            return df[mask]
    elif filter_type == "DATE":
        filter_col = _rule_val(rule, "filter_column", "mem_cov_end_date")
        if filter_col in df.columns:
            end_dates = pd.to_datetime(df[filter_col], errors="coerce")
            return df[end_dates.isna() | (end_dates >= pd.Timestamp.today())]

    return df


# ── VALUE MAP INTEGRITY CHECK ──
def check_value_maps(df, rule, process_type="ACU"):
    """
    For columns with a configured value map, check that all file values are known.
    If unknown values exist AND no wildcard (*) is in the map → block processing.
    Returns (ok: bool, unknown_values: dict {column: [unknown vals]})

    Only rows that survive the matrix row filter are validated — values that the
    filter drops never need a mapping. Matching is case-insensitive, consistent
    with both the filter and _apply_value_map.
    """
    col_map = PROCESS_COLUMN_MAP.get(process_type, PROCESS_COLUMN_MAP["ACU"])
    unknown_found = {}

    # Validate only the values that survive filtering.
    check_df = _rows_surviving_filter(df, rule)

    def _unknown_values(map_str, column):
        """Return sorted unknown values in `column` not covered by `map_str`."""
        if "*:" in map_str or column not in check_df.columns:
            return []
        known_keys_lc = {k.lower() for k in _known_map_keys(map_str)}
        seen = {}  # lowercased value -> original (for a readable message)
        for v in check_df[column].dropna().astype(str).str.strip().unique():
            if v in ("", "nan"):
                continue
            seen.setdefault(v.lower(), v)
        unknown_lc = set(seen) - known_keys_lc
        return sorted(seen[lc] for lc in unknown_lc)

    # Check type_value_map
    type_map_str = _rule_val(rule, "type_value_map")
    type_source = col_map.get("type_value_map_source", "appointment_type")
    if type_map_str:
        unknown = _unknown_values(type_map_str, type_source)
        if unknown:
            unknown_found[type_source] = unknown

    # Check status_value_map
    status_map_str = _rule_val(rule, "status_value_map")
    status_target = col_map.get("status_value_map_target", "contract_status")
    if status_map_str:
        unknown = _unknown_values(status_map_str, status_target)
        if unknown:
            unknown_found[status_target] = unknown

    return len(unknown_found) == 0, unknown_found


# ── DEDUP ──
def dedup_carrier_data(df, rule):
    primary = str(rule.get("primary_identity_field", "NPN")).strip()
    if primary == "NPN" and "agent_npn" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["appointed_state", "agent_npn"])
        df = df[df["agent_npn"].notna() & (df["agent_npn"].astype(str).str.strip() != "")].copy().reset_index(drop=True)
        print(f"    🧹 Dedup (NPN): {before} → {len(df)} rows")
    elif primary == "WR" and "agent_writing_num" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["appointed_state", "agent_writing_num"])
        df = df[df["agent_writing_num"].notna() & (df["agent_writing_num"].astype(str).str.strip() != "")].copy().reset_index(drop=True)
        print(f"    🧹 Dedup (WR): {before} → {len(df)} rows")
    return df


# ── LOAD CONTRACTS ──
def load_contracts(conn, carrier_id):
    # Load ALL statuses (not active-only). The matched contract's true prior
    # status is required so the transition engine can evaluate moves FROM a
    # non-active state (e.g. terminated/inactive -> active reactivation). Status
    # routing is handled downstream by the transition rules and the exclusion
    # lists, not by hiding rows here.
    #
    # NOTE: consumers that must stay active-scoped (find_missing_agents,
    # resolve_parent_npn) receive an active-only view built in process_carrier;
    # resolve_identity intentionally sees all statuses so that an agent who has
    # both an active and a non-active contract surfaces as a multi-match
    # reconciliation rather than being silently auto-resolved.
    carrier_id = str(carrier_id).replace('"', '').replace("'", '').strip()
    query = (
        f"SELECT name, npn, writing_number, first_name, last_name, status, status_date, carrier, id "
        f"FROM {CONTRACTS_TABLE} WHERE carrier = %s"
    )
    df = pd.read_sql(query, conn, params=[carrier_id])
    for col in ["npn", "writing_number"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.replace(".0", "", regex=False)
    print(f"    📋 Loaded {len(df)} contracts (all statuses) for carrier {carrier_id}")
    return df


def _active_contracts(contracts_df):
    """Active-only view of a contracts frame (status starts with 'active',
    case-insensitive — matches Active / Active/Certified / Active: Certified
    while excluding Inactive). Used by consumers that must stay active-scoped."""
    if contracts_df is None or contracts_df.empty or "status" not in contracts_df.columns:
        return contracts_df
    active_mask = contracts_df["status"].fillna("").astype(str).str.strip().str.lower().str.startswith("active")
    return contracts_df[active_mask].copy()


def load_agent_registry(conn, npns):
    """
    Look up agent-level status from lup_agents for a list of NPNs.
    Returns DataFrame with npn, status, full_name.
    """
    if not npns:
        return pd.DataFrame(columns=["npn", "status", "full_name"])
    placeholders = ", ".join(["%s"] * len(npns))
    query = f"SELECT npn, status, full_name FROM {AGENTS_TABLE} WHERE npn IN ({placeholders})"
    df = pd.read_sql(query, conn, params=list(npns))
    if "npn" in df.columns:
        df["npn"] = df["npn"].astype(str).str.strip().str.replace(".0", "", regex=False)
    return df


# ── IDENTITY RESOLUTION ──
def _drop_ct_columns(frame):
    """Drop merge-artifact columns (suffixed _ct) to prevent concat column clashes."""
    ct_cols = [c for c in frame.columns if c.endswith("_ct")]
    if ct_cols:
        frame = frame.drop(columns=ct_cols, errors="ignore")
    return frame


def resolve_identity(df, contracts_df, rule):
    primary = str(rule.get("primary_identity_field", "NPN")).strip().upper()
    if primary == "WR":
        return _resolve_by_writing_number(df, contracts_df)
    if primary == "NAME":
        return _resolve_by_name(df, contracts_df)

    # Multi-carrier-id mode: join on (carrier_id, npn) for carriers like HCSC
    multi_cid = "_carrier_id" in contracts_df.columns and "carrier_id" in df.columns

    df["_npn"] = df["agent_npn"].astype(str).str.strip()
    contracts_df["_npn"] = contracts_df["npn"].astype(str).str.strip()

    if multi_cid:
        # HCSC path: join on (carrier_id, npn) — same NPN can exist under different state entities
        contracts_df["_cid"] = contracts_df["_carrier_id"].astype(str).str.strip()
        df["_cid"] = df["carrier_id"].astype(str).str.strip()

        merge_keys_l = ["_cid", "_npn"]
        merge_keys_r = ["_cid", "_npn"]

        npn_counts = contracts_df.groupby(merge_keys_r).size().reset_index(name="_cnt")
        ct = contracts_df.merge(npn_counts, on=merge_keys_r, how="left")
        unique_ct = ct[ct["_cnt"] == 1].drop(columns=["_cnt"])
        dup_npns = set(ct[ct["_cnt"] > 1]["_npn"].unique())

        merged = df.merge(unique_ct, left_on=merge_keys_l, right_on=merge_keys_r, how="left", suffixes=("", "_ct"))

        # Cleanup temp cols
        for col in ["_cid"]:
            for frame in [df, contracts_df]:
                if col in frame.columns:
                    frame.drop(columns=[col], inplace=True, errors="ignore")

        print(f"    🔍 Multi-carrier-id join (carrier_id + NPN)")
    else:
        # Standard path: join on NPN only
        npn_counts = contracts_df.groupby("_npn").size().reset_index(name="_cnt")
        ct = contracts_df.merge(npn_counts, on="_npn", how="left")
        unique_ct = ct[ct["_cnt"] == 1].drop(columns=["_cnt"])
        dup_npns = set(ct[ct["_cnt"] > 1]["_npn"].unique())

        merged = df.merge(unique_ct, left_on="_npn", right_on="_npn", how="left", suffixes=("", "_ct"))

    matched_mask = merged["id"].notna()
    multi_mask = merged["_npn"].isin(dup_npns) & ~matched_mask

    matched_df = _drop_ct_columns(merged[matched_mask].copy())
    unmatched_df = merged[~matched_mask].copy()
    if "exception_reason" not in unmatched_df.columns:
        unmatched_df["exception_reason"] = pd.NA
    if "exception_id" not in unmatched_df.columns:
        unmatched_df["exception_id"] = pd.NA

    # Build NPN → contract IDs lookup for multi-match exceptions
    if dup_npns:
        dup_contracts = ct[ct["_npn"].isin(dup_npns)].groupby("_npn")["name"].apply(
            lambda names: ", ".join(str(n) for n in names if pd.notna(n))
        ).to_dict()
        multi_rows = multi_mask[~matched_mask]
        for idx in unmatched_df.loc[multi_rows].index:
            npn = unmatched_df.at[idx, "_npn"]
            contract_ids = dup_contracts.get(npn, "")
            unmatched_df.at[idx, "exception_reason"] = f"Multiple contracts found for NPN {npn} (contracts: {contract_ids})"
            unmatched_df.at[idx, "exception_id"] = EXC_IDENTITY_MULTIPLE_MATCH

    # Fallback: NAME (ilike / case-insensitive substring match)
    # Only for rows where the primary field is BLANK in the file data —
    # rows that have an NPN but didn't match should stay as exceptions.
    fallback = str(rule.get("fallback_identity_field", "NAME")).strip().upper()
    if fallback == "NAME":
        no_reason = unmatched_df[unmatched_df["exception_reason"].isna()].copy()
        has_reason = unmatched_df[unmatched_df["exception_reason"].notna()].copy()
        if not no_reason.empty:
            # Only try name match for rows where NPN is blank/missing
            npn_blank_mask = no_reason["_npn"].isin(["", "nan", "None", "none"]) | no_reason["_npn"].isna()
            needs_name = no_reason[npn_blank_mask].copy()
            has_npn_no_match = no_reason[~npn_blank_mask].copy()
            has_npn_no_match["exception_reason"] = "NPN not found in contracts: " + has_npn_no_match["_npn"]
            has_npn_no_match["exception_id"] = EXC_IDENTITY_NOT_FOUND

            if not needs_name.empty and "agent_full_name" in needs_name.columns:
                nm_matched, nm_unmatched = _resolve_by_name(needs_name, contracts_df)
                matched_df = pd.concat([matched_df, nm_matched], ignore_index=True)
                unmatched_df = pd.concat([has_reason, has_npn_no_match, nm_unmatched], ignore_index=True)
            else:
                unmatched_df = pd.concat([has_reason, has_npn_no_match, needs_name], ignore_index=True)
        else:
            unmatched_df = pd.concat([no_reason, has_reason], ignore_index=True)

    unmatched_df = _drop_ct_columns(unmatched_df)
    # Ensure every exception has a clear reason — never "Unknown"
    if "exception_reason" not in unmatched_df.columns:
        unmatched_df["exception_reason"] = ""
    no_reason_mask = unmatched_df["exception_reason"].isna() | (unmatched_df["exception_reason"].astype(str).str.strip() == "")
    if no_reason_mask.any():
        for idx in unmatched_df[no_reason_mask].index:
            npn = str(unmatched_df.at[idx, "_npn"]).strip() if "_npn" in unmatched_df.columns else ""
            if npn and npn not in ("", "nan", "None"):
                unmatched_df.at[idx, "exception_reason"] = f"NPN not found in contracts: {npn}"
                unmatched_df.at[idx, "exception_id"] = EXC_IDENTITY_NOT_FOUND
            else:
                name = str(unmatched_df.at[idx, "agent_full_name"]).strip() if "agent_full_name" in unmatched_df.columns else ""
                if name and name not in ("", "nan", "None"):
                    unmatched_df.at[idx, "exception_reason"] = f"Agent not found - Name: {name}"
                    unmatched_df.at[idx, "exception_id"] = EXC_IDENTITY_NOT_FOUND
                else:
                    unmatched_df.at[idx, "exception_reason"] = "No NPN, WR, or name available for matching"
                    unmatched_df.at[idx, "exception_id"] = EXC_IDENTITY_NOT_FOUND
    for col in ["_npn", "_name"]:
        for f in [matched_df, unmatched_df, contracts_df]:
            if col in f.columns:
                f.drop(columns=[col], inplace=True, errors="ignore")

    print(f"    🔍 Identity: {len(matched_df)} matched, {len(unmatched_df)} exceptions")
    return matched_df.reset_index(drop=True), unmatched_df.reset_index(drop=True)


def _normalize_person_name(series):
    """Normalize person names to a cleaned "first last" form for matching.

    Carriers send names in wildly inconsistent shapes: "Last, First M",
    "First Middle Last", "FIRST LAST", with hyphens/apostrophes/periods/suffixes.
    We can't control either side, so reduce every name to the same canonical
    shape:
      1. Split into first + last (comma form -> last is before the comma,
         first is the first token after; otherwise first token + last token).
      2. Drop middle names/initials.
      3. Strip everything that isn't a letter or space (removes . , ' - etc.),
         casefold, and collapse whitespace.
    Both the carrier file and the contract side (first_name/last_name) run
    through this, so "Matthews, Thomas A", "Thomas A. Matthews", and
    "THOMAS MATTHEWS" all normalize to "thomas matthews" and match.
    """
    def _one(s):
        s = str(s or "").strip()
        if not s or s.lower() in ("nan", "none"):
            return ""
        if "," in s:
            last, rest = s.split(",", 1)
            rest_toks = rest.strip().split()
            first = rest_toks[0] if rest_toks else ""
        else:
            toks = s.split()
            first = toks[0] if toks else ""
            last = toks[-1] if len(toks) > 1 else ""
        # Strip every non-letter WITHIN each token (so O'Brien -> obrien,
        # Smith-Jones -> smithjones) rather than splitting on punctuation.
        clean = lambda x: re.sub(r"[^a-z]", "", str(x).lower())
        first, last = clean(first), clean(last)
        # Order-independent key: sort the two tokens so "First Last" and
        # "Last First" collapse to the same value. The carrier file and the
        # contracts table disagree on ordering (contracts often store the
        # free-text `name` as "Last First" with no comma, or have
        # first_name/last_name swapped), which otherwise breaks every match.
        return " ".join(sorted(t for t in (first, last) if t))
    return series.map(_one)


def _resolve_by_name(df, contracts_df, exc_not_found=None, exc_multiple=None, name_col="agent_full_name"):
    """
    Resolve agents by name using case-insensitive substring match (ilike).
    Match target: contracts.name if populated, else first_name + ' ' + last_name.
    Direction: contract_name CONTAINS file name (asymmetric).
      - 1 contract match  → matched
      - 2+ contract matches → exception ("multiple name matches, need NPN/WR from carrier")
      - 0 matches          → exception ("Agent not found — Name: …")
      - blank file name    → exception ("No NPN and no name available")

    exc_not_found/exc_multiple default to ACU codes; BOB passes its own from lup_exception_list.
    name_col: column in df containing the agent name. Default "agent_full_name";
              BOB carriers may use "carrier_agent_name".
    """
    if exc_not_found is None:
        exc_not_found = EXC_IDENTITY_NOT_FOUND
    if exc_multiple is None:
        exc_multiple = EXC_IDENTITY_MULTIPLE_MATCH
    df = df.copy()
    contracts_df = contracts_df.copy()

    # Build the contract-side lookup name: prefer the structured first_name +
    # last_name (clean, per-token), fall back to the free-text `name` column.
    # Both go through _normalize_person_name so special chars / middle names /
    # ordering don't matter.
    concat_name = (
        contracts_df.get("first_name", pd.Series([""] * len(contracts_df), index=contracts_df.index)).fillna("").astype(str).str.strip()
        + " "
        + contracts_df.get("last_name", pd.Series([""] * len(contracts_df), index=contracts_df.index)).fillna("").astype(str).str.strip()
    ).str.strip()
    if "name" in contracts_df.columns:
        ct_name = contracts_df["name"].fillna("").astype(str).str.strip()
    else:
        ct_name = pd.Series([""] * len(contracts_df), index=contracts_df.index)
    contracts_df["_lookup_name"] = concat_name.where(concat_name != "", ct_name)
    contracts_df["_lookup_name_lc"] = _normalize_person_name(contracts_df["_lookup_name"])

    df["_file_name"] = df.get(name_col, pd.Series([""] * len(df), index=df.index)).fillna("").astype(str).str.strip()
    df["_file_name_lc"] = _normalize_person_name(df["_file_name"])

    matched_rows = []
    unmatched_rows = []

    # Index non-blank contract names for scanning.
    ct_nonblank = contracts_df[contracts_df["_lookup_name_lc"] != ""].copy()

    # Collapse multiple contract rows for the SAME agent (same normalized name +
    # npn) so an agent with a status history (active + terminated, etc.) is not
    # mis-flagged as a "multiple name match". Distinct agents who happen to share
    # a name (different npn) are kept separate so genuine ambiguity still fires.
    if "npn" in ct_nonblank.columns:
        ct_nonblank = ct_nonblank.drop_duplicates(subset=["_lookup_name_lc", "npn"])
    else:
        ct_nonblank = ct_nonblank.drop_duplicates(subset=["_lookup_name_lc"])

    for idx, row in df.iterrows():
        fname = row["_file_name"]
        fname_lc = row["_file_name_lc"]

        if not fname_lc:
            out = row.to_dict()
            out["exception_reason"] = "No NPN and no name available"
            out["exception_id"] = exc_not_found
            unmatched_rows.append(out)
            continue

        # Order-independent exact match on the normalized first+last key
        # (both sides are sorted-token keys, so ordering / swapped columns
        # no longer matter, and substring false-positives are avoided).
        hits = ct_nonblank[ct_nonblank["_lookup_name_lc"] == fname_lc]

        if len(hits) == 1:
            merged = {**row.to_dict(), **hits.iloc[0].to_dict()}
            matched_rows.append(merged)
        elif len(hits) >= 2:
            out = row.to_dict()
            out["exception_reason"] = f"Multiple name matches found for '{fname}' - need NPN/WR from carrier"
            out["exception_id"] = exc_multiple
            unmatched_rows.append(out)
        else:
            out = row.to_dict()
            out["exception_reason"] = f"Agent not found - Name: {fname}"
            out["exception_id"] = exc_not_found
            unmatched_rows.append(out)

    matched_df = pd.DataFrame(matched_rows) if matched_rows else df.iloc[0:0].copy()
    unmatched_df = pd.DataFrame(unmatched_rows) if unmatched_rows else df.iloc[0:0].copy()

    # Pull NPN and writing_number from matched contracts into agent fields
    if not matched_df.empty:
        if "npn" in matched_df.columns:
            matched_df["agent_npn"] = matched_df["npn"]
        if "writing_number" in matched_df.columns and (matched_df.get("agent_writing_num", "").fillna("").astype(str).str.strip() == "").all():
            matched_df["agent_writing_num"] = matched_df["writing_number"]

    # Clean helper cols.
    for col in ["_file_name", "_file_name_lc", "_lookup_name", "_lookup_name_lc"]:
        for f in [matched_df, unmatched_df, contracts_df, df]:
            if col in f.columns:
                f.drop(columns=[col], inplace=True, errors="ignore")

    matched_df = _drop_ct_columns(matched_df)
    unmatched_df = _drop_ct_columns(unmatched_df)
    print(f"    🔍 Identity (NAME ilike): {len(matched_df)} matched, {len(unmatched_df)} exceptions")
    return matched_df.reset_index(drop=True), unmatched_df.reset_index(drop=True)


def _resolve_by_writing_number(df, contracts_df):
    df["_wr"] = df["agent_writing_num"].astype(str).str.strip()
    contracts_df["_wr"] = contracts_df["writing_number"].astype(str).str.strip()

    # Detect duplicate WRs in contracts (same pattern as NPN resolution)
    wr_counts = contracts_df.groupby("_wr").size().reset_index(name="_cnt")
    ct = contracts_df.merge(wr_counts, on="_wr", how="left")
    unique_ct = ct[ct["_cnt"] == 1].drop(columns=["_cnt"])
    dup_wrs = set(ct[ct["_cnt"] > 1]["_wr"].unique())

    merged = df.merge(unique_ct, left_on="_wr", right_on="_wr", how="left", suffixes=("", "_ct"))

    matched_mask = merged["id"].notna()
    multi_mask = merged["_wr"].isin(dup_wrs) & ~matched_mask

    matched = _drop_ct_columns(merged[matched_mask].copy())
    unmatched = merged[~matched_mask].copy()
    if "exception_reason" not in unmatched.columns:
        unmatched["exception_reason"] = pd.NA
    if "exception_id" not in unmatched.columns:
        unmatched["exception_id"] = pd.NA

    # Build WR → contract names lookup for multi-match exceptions
    if dup_wrs:
        dup_contracts = ct[ct["_wr"].isin(dup_wrs)].groupby("_wr")["name"].apply(
            lambda names: ", ".join(str(n) for n in names if pd.notna(n))
        ).to_dict()
        multi_rows = multi_mask[~matched_mask]
        for idx in unmatched.loc[multi_rows].index:
            wr = unmatched.at[idx, "_wr"]
            contract_ids = dup_contracts.get(wr, "")
            unmatched.at[idx, "exception_reason"] = f"Multiple contracts found for WR {wr} (contracts: {contract_ids})"
            unmatched.at[idx, "exception_id"] = EXC_IDENTITY_MULTIPLE_MATCH

    # Set reason for non-multi unmatched
    no_reason = unmatched["exception_reason"].isna() | (unmatched["exception_reason"].astype(str).str.strip() == "")

    # Retry unmatched with base WR (strip suffix after last '-')
    # Handles carriers like Ameritas where DB stores both 'AG00136498-01' and 'AG00136498'
    retry_mask = no_reason & unmatched["agent_writing_num"].astype(str).str.contains("-", na=False)
    if retry_mask.any():
        retry_df = unmatched[retry_mask].copy()
        retry_df["_wr"] = retry_df["agent_writing_num"].astype(str).str.rsplit("-", n=1).str[0].str.strip()
        retry_merged = retry_df.merge(unique_ct, left_on="_wr", right_on="_wr", how="left", suffixes=("", "_ct"))
        retry_matched_mask = retry_merged["id"].notna()
        if retry_matched_mask.any():
            retry_matched = _drop_ct_columns(retry_merged[retry_matched_mask].copy())
            if "npn" in retry_matched.columns:
                retry_matched["agent_npn"] = retry_matched["npn"]
            matched = pd.concat([matched, retry_matched], ignore_index=True)
            # Remove retried-and-matched from unmatched
            matched_indices = unmatched[retry_mask].index[retry_matched_mask.values]
            unmatched = unmatched.drop(index=matched_indices).reset_index(drop=True)
            print(f"    🔄 WR base retry: {retry_matched_mask.sum()} additional matches")
            # Recalculate no_reason after dropping
            no_reason = unmatched["exception_reason"].isna() | (unmatched["exception_reason"].astype(str).str.strip() == "")

    unmatched.loc[no_reason, "exception_reason"] = unmatched.loc[no_reason].apply(
        lambda r: f"Agent not found - WR: {r.get('agent_writing_num', 'N/A')}", axis=1)
    unmatched.loc[no_reason, "exception_id"] = EXC_IDENTITY_NOT_FOUND

    matched = _drop_ct_columns(matched)
    unmatched = _drop_ct_columns(unmatched)

    if not matched.empty and "npn" in matched.columns:
        matched["agent_npn"] = matched["npn"]
    for f in [matched, unmatched, contracts_df]:
        if "_wr" in f.columns:
            f.drop(columns=["_wr"], inplace=True, errors="ignore")
    print(f"    🔍 Identity (WR): {len(matched)} matched, {len(unmatched)} exceptions ({len(dup_wrs)} duplicate WRs)")
    return matched.reset_index(drop=True), unmatched.reset_index(drop=True)


# ── PARENT NPN RESOLUTION ──
# Legal-entity / suffix tokens stripped when matching agency parent names. The
# carrier file decorates names ("…, Inc. - FMO", "…, LLC.") that wpo.lup_agents
# stores plainly ("PANDORA INSURANCE INC", "AGILITY INSURANCE SERVICES, LLC"), so
# we normalize BOTH sides to the distinctive words before comparing.
_PARENT_ENTITY_TOKENS = {
    "inc", "incorporated", "llc", "ltd", "corp", "corporation", "co", "company",
    "fmo", "imo", "gmo", "lp", "llp", "pllc", "pc", "dba",
}


def _norm_org_name(s):
    s = re.sub(r"[^a-z0-9 ]+", " ", str(s).lower())
    return " ".join(t for t in s.split() if t and t not in _PARENT_ENTITY_TOKENS)


def _resolve_parent_name_via_agents(conn, name, cache):
    """
    Resolve a parent NPN by NAME against the lup_agents master (port of legacy
    get_parent_npn). Agencies live in wpo.lup_agents (status 'Active'/'Key Agency');
    the per-carrier contracts table does not contain them, which is why the old
    contracts-based lookup resolved 0.

    Matching is normalization-based because the file and the master spell the same
    org differently — file "Pandora Insurance, Inc. - FMO" vs stored "PANDORA
    INSURANCE INC", file "Agility Insurance Services, LLC." vs stored "…, LLC". We
    strip punctuation + legal-entity tokens from both sides, then:
      1. pull active-family candidates whose name contains the most distinctive
         (first) token,
      2. prefer an exact normalized-name match, else a containment match.
    Results cached per name.
    """
    raw = str(name).strip()
    ck = raw.lower()
    if ck in cache:
        return cache[ck]
    norm_key = _norm_org_name(raw)
    if not norm_key:
        cache[ck] = ""
        return ""
    first_tok = norm_key.split()[0]
    npn = ""
    try:
        q = (f"SELECT npn, full_name FROM {AGENTS_TABLE} "
             f"WHERE LOWER(full_name) LIKE %s AND (status ILIKE %s OR status = %s) LIMIT 300")
        cand = pd.read_sql(q, conn, params=[f"%{first_tok}%", "active%", "Key Agency"])
        contain_hit = None
        for _, row in cand.iterrows():
            nc = _norm_org_name(row["full_name"])
            if not nc:
                continue
            if nc == norm_key:                       # exact normalized match wins
                npn = str(row["npn"]).strip()
                break
            if contain_hit is None and (norm_key in nc or nc in norm_key):
                contain_hit = str(row["npn"]).strip()
        if not npn and contain_hit:
            npn = contain_hit
    except Exception as ex:
        print(f"    ⚠️  parent-name lookup failed for {name!r}: {ex}")
    npn = npn.replace(".0", "")
    if npn in ("", "nan", "None", "0"):
        npn = ""
    cache[ck] = npn
    return npn


def resolve_parent_npn(df, contracts_df, rule, conn=None):
    """
    Resolve parent_npn from raw identifier (WR, NPN, or NAME) to actual NPN.
    Same logic as agent identity resolution, applied to the parent column.

    - parent_identity_field = NPN → already an NPN, just validate it exists
    - parent_identity_field = WR  → look up writing_number in contracts → get NPN
    - parent_identity_field = NAME → look up name in contracts → one match = NPN, multiple = exception

    Returns (df_resolved, exceptions_df). Agents with unresolvable parents go to exceptions.
    """
    parent_id_field = _rule_val(rule, "parent_identity_field").upper()
    parent_scope = _rule_val(rule, "parent_npn_scope")

    # Determine which rows need resolution
    if parent_scope == "Subproducer" and "appointment_type" in df.columns:
        needs_resolve = df["appointment_type"].astype(str).str.strip() == "Subproducer"
    elif parent_scope == "all":
        needs_resolve = pd.Series([True] * len(df), index=df.index)
    else:
        needs_resolve = df["parent_npn"].fillna("").astype(str).str.strip() != ""

    # Clear parent_npn for rows NOT in scope (Producers don't have parents)
    df.loc[~needs_resolve, "parent_npn"] = ""

    raw_parents = df.loc[needs_resolve, "parent_npn"].fillna("").astype(str).str.strip()
    has_value = raw_parents != ""

    if not has_value.any():
        return df, pd.DataFrame()

    resolved = df.copy()
    exception_rows = []

    if parent_id_field == "NPN":
        # Already NPN — just validate it's not blank
        pass  # nothing to resolve

    elif parent_id_field == "WR":
        # Build WR → NPN lookup from contracts
        ct = contracts_df.copy()
        ct["_wr"] = ct["writing_number"].astype(str).str.strip()
        ct["_npn"] = ct["npn"].astype(str).str.strip()
        wr_to_npn = ct.drop_duplicates(subset=["_wr"]).set_index("_wr")["_npn"].to_dict()

        resolve_idx = has_value[has_value].index
        exc_indices = []
        for idx in resolve_idx:
            raw_wr = raw_parents[idx]
            npn = wr_to_npn.get(raw_wr)
            if npn and npn not in ("", "nan", "None"):
                resolved.at[idx, "parent_npn"] = npn
            else:
                row = resolved.loc[idx].copy()
                row["exception_reason"] = f"Parent WR {raw_wr} not found in contracts"
                row["exception_id"] = EXC_PARENT_NOT_RESOLVED
                exception_rows.append(row)
                exc_indices.append(idx)

        if exc_indices:
            resolved = resolved.drop(index=exc_indices).reset_index(drop=True)

        found = len(resolve_idx) - len(exc_indices)
        print(f"    🔗 Parent NPN resolved (WR): {found}/{len(resolve_idx)} resolved, {len(exc_indices)} exceptions")

    elif parent_id_field == "NAME":
        resolve_idx = has_value[has_value].index
        exc_indices = []

        # Preferred path: resolve against the lup_agents master (legacy behavior).
        # Falls back to the per-carrier contracts only when no DB connection is
        # available (e.g. the offline harness).
        use_master = conn is not None
        name_cache = {}
        if not use_master:
            ct = contracts_df.copy()
            ct["_name"] = ct["name"].fillna("").astype(str).str.strip().str.lower()
            ct["_npn"] = ct["npn"].astype(str).str.strip()

        for idx in resolve_idx:
            raw_name = raw_parents[idx].strip()
            npn = ""
            multiple = False

            if use_master:
                npn = _resolve_parent_name_via_agents(conn, raw_name, name_cache)
            else:
                matches = ct[ct["_name"].str.contains(raw_name.lower(), regex=False, na=False)]
                unique_npns = matches["_npn"].unique()
                if len(unique_npns) == 1:
                    npn = unique_npns[0]
                elif len(unique_npns) > 1:
                    multiple = True

            if npn:
                resolved.at[idx, "parent_npn"] = npn
            elif multiple:
                row = resolved.loc[idx].copy()
                row["exception_reason"] = f"Multiple parent matches for name '{raw_parents[idx]}'"
                row["exception_id"] = EXC_PARENT_NOT_RESOLVED
                exception_rows.append(row)
                exc_indices.append(idx)
            else:
                row = resolved.loc[idx].copy()
                row["exception_reason"] = f"Parent name '{raw_parents[idx]}' not found in contracts"
                row["exception_id"] = EXC_PARENT_NOT_RESOLVED
                exception_rows.append(row)
                exc_indices.append(idx)

        if exc_indices:
            resolved = resolved.drop(index=exc_indices).reset_index(drop=True)

        found = len(resolve_idx) - len(exc_indices)
        src = "lup_agents master" if use_master else "contracts (no-DB fallback)"
        print(f"    🔗 Parent NPN resolved (NAME via {src}): {found}/{len(resolve_idx)} resolved, {len(exc_indices)} exceptions")

    exceptions_df = pd.DataFrame(exception_rows) if exception_rows else pd.DataFrame()
    return resolved, exceptions_df


# ── ROLLUP ──
def rollup_appointments(matched_df, rule):
    primary = str(rule.get("primary_identity_field", "NPN")).strip()
    group_col = "agent_npn" if primary != "WR" else "agent_writing_num"
    other_col = "agent_writing_num" if primary != "WR" else "agent_npn"
    if matched_df.empty or group_col not in matched_df.columns:
        return matched_df

    def _agg(group):
        first = group.iloc[0]
        statuses = group["contract_status"].fillna("").astype(str).str.strip().str.lower()
        final_status = "Active" if "active" in set(statuses) else group["contract_status"].iloc[0]
        active_mask = statuses.str.contains("active", na=False)
        active_states = group.loc[active_mask, "appointed_state"].fillna("").astype(str).str.strip()
        states = active_states[active_states != ""].unique()
        def _rts_agg(col):
            vals = group[col].fillna("").astype(str) if col in group.columns else pd.Series([""])
            return "Yes" if "Yes" in vals.values else ""
        def _rts_date_agg(col):
            if col not in group.columns: return ""
            return next((v for v in group[col].fillna("") if v and str(v).strip()), "")
        result = {
            "agent_full_name": first.get("agent_full_name", ""), "contract_status": final_status,
            "contract_date": group["contract_date"].min() if "contract_date" in group.columns else "",
            "appointed_state": "; ".join(sorted(states)) if len(states) > 0 else "",
            "appointed_date": first.get("appointed_date", ""), "appointment_type": first.get("appointment_type", ""),
            "parent_npn": first.get("parent_npn", ""),
            other_col: first.get(other_col, ""),
            "current_rts": _rts_agg("current_rts"), "current_rts_date": _rts_date_agg("current_rts_date"),
            "next_rts": _rts_agg("next_rts"), "next_rts_date": _rts_date_agg("next_rts_date"),
            "aca_rts": _rts_agg("aca_rts"),
            "name": first.get("name", ""), "writing_number": first.get("writing_number", ""),
            "status": first.get("status", ""), "status_date": first.get("status_date", ""),
            "id": first.get("id", ""),
            "note": "; ".join(group["note"].fillna("").astype(str).str.strip().unique().tolist()) if "note" in group.columns else "",
        }
        return pd.Series(result)

    # reset_index() restores the group key column. _agg includes the OTHER identity field.
    rolled = matched_df.groupby(group_col, sort=False).apply(_agg, include_groups=False).reset_index()
    print(f"    📦 Rolled up: {len(matched_df)} -> {len(rolled)} agents")
    return rolled


# ── TRANSITIONS ──
def check_transitions(rolled_df):
    """
    Compare old status (from contracts DB) with new status (from carrier file).
    - Blocked transitions (e.g., terminated → active): mark _blocked = True
    - Normal transitions: add note
    - Collects transition_summary dict for AI analysis
    """
    if rolled_df.empty:
        rolled_df["note"] = ""
        rolled_df["_blocked"] = False
        rolled_df["_noop"] = False
        return rolled_df

    existing_notes = rolled_df["note"].fillna("").astype(str) if "note" in rolled_df.columns else pd.Series([""] * len(rolled_df))
    old = rolled_df["status"].fillna("").astype(str).str.strip().str.lower()
    new = rolled_df["contract_status"].fillna("").astype(str).str.strip().str.lower()

    transition_notes = []
    blocked_flags = []
    noop_flags = []

    for o, n in zip(old, new):
        o_active = "active" in o
        n_active = "active" in n

        # No-op ONLY when the status is genuinely unchanged: CRM (old) and file
        # (new) are BOTH non-active AND identical. A *different* non-active status
        # from the file is a real, guide-mapped update — e.g. SCAN/Wellcare map a
        # Status Reason to "Request Sent to Carrier" / "Pending - Certification
        # Required" / "Pending" — and must flow through to results, not be dropped.
        # (terminated->terminated etc. stay no-op so we don't churn stale records.)
        if o and n and not o_active and not n_active and o == n:
            transition_notes.append(f"No update: CRM '{o}' retained (file '{n}')")
            blocked_flags.append(False)
            noop_flags.append(True)
            continue

        if o == n or not o or not n:
            transition_notes.append("")
            blocked_flags.append(False)
            noop_flags.append(False)
            continue

        # Rule 1: reverse reactivation (terminated/inactive/cancelled → active)
        from_match = any(pat in o for pat in BLOCKED_TRANSITION_FROM)
        to_match = any(pat in n for pat in BLOCKED_TRANSITION_TO)

        # Exemption: a move INTO an explicit "...Recertification Needed" state is a
        # guide-defined, reviewed reactivation (e.g. CHC maps Inactive → "Active -
        # Recertification Needed"), NOT a silent flip to plain active — so it is
        # allowed even from inactive/terminated/cancelled.
        is_recert_dest = "recertification" in n

        # Rule 2: unusual deactivation from active (active → anything not in allowed list).
        # Exact "pending" is a guide-defined destination (e.g. Zing maps "Pending
        # Contract" → "Pending") and must post; this stays distinct from
        # "pending contract"/"pending - …" which remain blocked (substring match).
        is_from_active = o_active
        to_allowed = (any(pat in n for pat in ALLOWED_FROM_ACTIVE) or n.strip() == "pending") if is_from_active else True

        if from_match and to_match and not is_recert_dest:
            transition_notes.append(f"Blocked transition: {o} -> {n}")
            blocked_flags.append(True)
            noop_flags.append(False)
        elif is_from_active and not to_allowed:
            transition_notes.append(f"Blocked transition: {o} -> {n}")
            blocked_flags.append(True)
            noop_flags.append(False)
        else:
            transition_notes.append(f"Status: {o} -> {n}")
            blocked_flags.append(False)
            noop_flags.append(False)

    rolled_df["note"] = [
        "; ".join(filter(None, [e.strip(), t.strip()]))
        for e, t in zip(existing_notes, transition_notes)
    ]
    rolled_df["_blocked"] = blocked_flags
    rolled_df["_noop"] = noop_flags

    blocked_count = sum(blocked_flags)
    if blocked_count:
        print(f"    🚫 {blocked_count} agent(s) blocked by status transition")
    noop_count = sum(noop_flags)
    if noop_count:
        print(f"    ⏸️  {noop_count} agent(s) no-op (CRM non-active retained, no update)")

    return rolled_df


# ── BUILD OUTPUTS ──
def rollup_exceptions(unmatched_df, rule):
    """
    Roll up exception rows to one row per agent.
    Same pattern as rollup_appointments — combine states, keep first exception_reason.
    Prevents duplicate exception rows when an agent has multiple state rows.
    """
    primary = str(rule.get("primary_identity_field", "NPN")).strip().upper()
    if primary == "WR":
        group_col = "agent_writing_num"
    elif primary == "NAME":
        group_col = "agent_full_name"
    else:
        group_col = "agent_npn"

    if unmatched_df.empty or group_col not in unmatched_df.columns:
        return unmatched_df

    # Give blank/NaN ids a UNIQUE sentinel per row so the groupby keeps distinct
    # agents separate (the old single "_blank_" key collapsed every id-less agent
    # into one row, silently dropping the rest). Restored to "" after grouping.
    _ids = unmatched_df[group_col].fillna("").astype(str).str.strip()
    _blank = _ids == ""
    if _blank.any():
        unmatched_df = unmatched_df.copy()
        unmatched_df.loc[~_blank, group_col] = _ids[~_blank]
        unmatched_df.loc[_blank, group_col] = [f"_blank_{i}_" for i in range(int(_blank.sum()))]
    else:
        unmatched_df[group_col] = _ids

    def _agg(group):
        first = group.iloc[0]
        states = group["appointed_state"].fillna("").astype(str).str.strip() if "appointed_state" in group.columns else pd.Series([""])
        states = states[states != ""].unique()
        return pd.Series({
            "contract_status": first.get("contract_status", ""),
            "contract_date": first.get("contract_date", ""),
            "appointed_state": "; ".join(sorted(states)) if len(states) > 0 else "",
            "appointed_date": first.get("appointed_date", ""),
            "appointment_type": first.get("appointment_type", ""),
            "parent_npn": first.get("parent_npn", ""),
            "current_rts": first.get("current_rts", ""),
            "current_rts_date": first.get("current_rts_date", ""),
            "next_rts": first.get("next_rts", ""),
            "next_rts_date": first.get("next_rts_date", ""),
            "aca_rts": first.get("aca_rts", ""),
            "name": first.get("name", ""),
            "writing_number": first.get("writing_number", ""),
            "status": first.get("status", ""),
            "status_date": first.get("status_date", ""),
            "note": first.get("note", ""),
            "exception_reason": first.get("exception_reason", ""),
            "exception_id": first.get("exception_id", pd.NA),
        })

    before = len(unmatched_df)
    rolled = unmatched_df.groupby(group_col, sort=False).apply(_agg, include_groups=False).reset_index()

    # Restore blanks (clear the per-row sentinels)
    rolled[group_col] = rolled[group_col].astype(str).str.replace(r"^_blank_\d+_$", "", regex=True)

    if len(rolled) < before:
        print(f"    📦 Exception rollup: {before} -> {len(rolled)} rows")
    return rolled


def build_results(rolled_df, carrier_name, carrier_id, run_date):
    if rolled_df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    rolled_df = rolled_df.reset_index(drop=True)
    r = pd.DataFrame({
        "Name": rolled_df.get("name", ""), "NPN": rolled_df.get("agent_npn", ""),
        "Writing_Number": rolled_df.get("agent_writing_num", pd.Series(dtype=str)).fillna("").astype(str).str.strip().where(
            lambda x: x != "", rolled_df.get("writing_number", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
        ),
        "Status_Date": rolled_df.get("contract_date", ""),
        "Status": rolled_df.get("contract_status", ""), "Appointment_Type": rolled_df.get("appointment_type", ""),
        "Appointed_States": rolled_df.get("appointed_state", ""), "Appointed_Date": rolled_df.get("appointed_date", ""),
        "Parent_Contract": rolled_df.get("parent_npn", ""),
        "Current_Medicare_RTS": rolled_df.get("current_rts", ""),
        "Current_Medicare_RTS_Date": rolled_df.get("current_rts_date", ""),
        "Next_Medicare_RTS": rolled_df.get("next_rts", ""),
        "Next_Medicare_RTS_Date": rolled_df.get("next_rts_date", ""),
        "ACA_RTS": rolled_df.get("aca_rts", ""),
        "carrier_name": carrier_name, "carrier_id": carrier_id, "run_date": run_date,
        "note": rolled_df.get("note", ""),
    })
    return r.reindex(columns=RESULT_COLUMNS).reset_index(drop=True)

def build_exceptions(unmatched_df, carrier_name, carrier_id, run_date):
    if unmatched_df.empty:
        return pd.DataFrame(columns=EXCEPTION_COLUMNS)
    unmatched_df = unmatched_df.reset_index(drop=True)
    e = pd.DataFrame({
        "Name": unmatched_df.get("name", pd.Series("", index=unmatched_df.index)).fillna(""),
        "NPN": unmatched_df.get("agent_npn", ""), "Writing_Number": unmatched_df.get("agent_writing_num", ""),
        "Status_Date": unmatched_df.get("contract_date", ""), "Status": unmatched_df.get("contract_status", ""),
        "Appointment_Type": unmatched_df.get("appointment_type", ""),
        "Appointed_States": unmatched_df.get("appointed_state", ""),
        "Appointed_Date": unmatched_df.get("appointed_date", ""),
        "Parent_Contract": unmatched_df.get("parent_npn", ""),
        "Current_Medicare_RTS": unmatched_df.get("current_rts", ""),
        "Current_Medicare_RTS_Date": unmatched_df.get("current_rts_date", ""),
        "Next_Medicare_RTS": unmatched_df.get("next_rts", ""),
        "Next_Medicare_RTS_Date": unmatched_df.get("next_rts_date", ""),
        "ACA_RTS": unmatched_df.get("aca_rts", ""),
        "carrier_name": carrier_name, "carrier_id": carrier_id, "run_date": run_date,
        "note": unmatched_df.get("note", ""),
        "exception_reason": unmatched_df.get("exception_reason", "Reason not set"),
        "exception_id": unmatched_df.get("exception_id", pd.NA),
    })
    return e.reindex(columns=EXCEPTION_COLUMNS).reset_index(drop=True)

def find_missing_agents(df, contracts_df, carrier_name, carrier_id, run_date):
    file_npns = set(df["agent_npn"].astype(str).str.strip().unique())
    contracts_df["_npn"] = contracts_df["npn"].astype(str).str.strip()
    missing = contracts_df[~contracts_df["_npn"].isin(file_npns)].copy()
    contracts_df.drop(columns=["_npn"], inplace=True, errors="ignore")
    if missing.empty:
        print(f"    ✅ No missing agents")
        return pd.DataFrame()
    m = pd.DataFrame({
        "Name": missing.get("name", ""), "NPN": missing.get("npn", ""),
        "Writing_Number": missing.get("writing_number", ""), "Status": missing.get("status", ""),
        "Status_Date": missing.get("status_date", ""), "First_Name": missing.get("first_name", ""),
        "Last_Name": missing.get("last_name", ""), "carrier_name": carrier_name,
        "carrier_id": carrier_id, "run_date": run_date, "note": "In contracts but not in carrier file",
    })
    print(f"    👻 Missing agents: {len(m)}")
    return m.reset_index(drop=True)


# ── PROCESS SINGLE CARRIER ──
def process_carrier(blob_service_client, conn, rule, column_mappings, blob_names, temp_dir, run_date,
                    container_name=DEFAULT_CONTAINER, pre_read_df=None):
    carrier_name = rule["carrier_name"]
    # Robust carrier_id conversion: int → str preserves digits, float → int → str avoids sci notation
    _cid = rule["carrier_id"]
    if isinstance(_cid, float):
        carrier_id = str(int(_cid))
    else:
        carrier_id = str(_cid).strip().replace(".0", "")
    print(f"\n  🔄 {carrier_name} ACU ({len(blob_names) if blob_names else 0} file(s))")
    raw_file_name = os.path.basename(blob_names[0]) if blob_names else ""

    handler = get_handler(rule)
    print(f"    🔧 Handler: {handler.__name__}")

    # Check for custom reader
    reader_name = str(rule.get("custom_reader_name", "")).strip()
    if reader_name and reader_name not in ("", "NA", "nan", "None"):
        reader_name = reader_name  # keep it
    else:
        reader_name = None

    if pre_read_df is not None:
        # SMA sub-carriers: data already read by SMA reader.
        # .copy() so downstream in-place edits don't trip SettingWithCopyWarning
        # on a slice handed in by the reader/groupby.
        df = pre_read_df.copy()
        print(f"    📄 Pre-read: {len(df)} rows")
    elif reader_name and reader_name != "read_sma":
        # Custom reader (HCSC etc) — replaces read_and_map_file
        custom_reader = get_custom_reader(reader_name)
        if custom_reader:
            print(f"    📖 Custom reader: {reader_name}")
            df = custom_reader(blob_service_client, blob_names[0], rule, column_mappings, container_name)
        else:
            print(f"    ⚠️  Custom reader '{reader_name}' not found — falling back to standard")
            frames = []
            for blob_name in blob_names:
                frames.append(read_and_map_file(blob_service_client, blob_name, rule, column_mappings, container_name))
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    else:
        # Standard reader
        frames = []
        for blob_name in blob_names:
            frames.append(read_and_map_file(blob_service_client, blob_name, rule, column_mappings, container_name))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if df.empty:
        return _empty_metrics(carrier_name, carrier_id, "no_data", raw_file_name=raw_file_name)

    total_rows = len(df)

    df = normalize_values(df)

    # Value map integrity check — skip for custom readers and pre-read data (they set values directly)
    if not reader_name and pre_read_df is None and _rule_val(rule, "custom_logic_flag") != "Y":
        maps_ok, unknown_vals = check_value_maps(df, rule)
        if not maps_ok:
            msg = "; ".join(f"{col}: {vals}" for col, vals in unknown_vals.items())
            print(f"    🚨 Value map mismatch — unknown values: {msg}")
            return _empty_metrics(carrier_name, carrier_id, "value_change",
                                  errors=[f"Unknown mapped values: {msg}"],
                                  raw_file_name=raw_file_name)

    df = apply_matrix_flags(df, rule)
    _status_filtered = getattr(df, "attrs", {}).pop("_status_filtered", None)
    df = handler(df, rule)
    df = dedup_carrier_data(df, rule)

    # Load contracts — for multi-carrier-id files (HCSC), load all relevant carrier_ids
    if "carrier_id" in df.columns and df["carrier_id"].nunique() > 1:
        # Multi-carrier-id: HCSC has different carrier_id per state
        unique_cids = [str(c) for c in df["carrier_id"].dropna().unique() if str(c).strip()]
        contracts_frames = []
        for cid in unique_cids:
            ct = load_contracts(conn, cid)
            if not ct.empty:
                ct["_carrier_id"] = cid
                contracts_frames.append(ct)
        contracts_df = pd.concat(contracts_frames, ignore_index=True) if contracts_frames else pd.DataFrame()
        if not contracts_df.empty:
            # Join on (carrier_id, npn) instead of just npn
            print(f"    📋 Multi-carrier contracts: {len(contracts_df)} across {len(unique_cids)} carrier IDs")
    else:
        contracts_df = load_contracts(conn, carrier_id)

    if contracts_df.empty:
        df["exception_reason"] = f"No contracts for carrier {carrier_id}"
        df["exception_id"] = EXC_IDENTITY_NOT_FOUND
        exc_df = build_exceptions(df, carrier_name, carrier_id, run_date)
        return _build_metrics(carrier_name, carrier_id, total_rows, pd.DataFrame(columns=RESULT_COLUMNS), exc_df, pd.DataFrame(), temp_dir, "no_contracts", raw_file_name=raw_file_name)

    # contracts_df holds ALL statuses (needed for transition detection and to
    # surface active+non-active duplicates as multi-match reconciliations).
    # Consumers that must stay active-scoped use this active-only view.
    active_contracts_df = _active_contracts(contracts_df)

    matched_df, unmatched_df = resolve_identity(df, contracts_df, rule)

    # Global rule 1: route matched agents with excluded CONTRACT statuses to exceptions
    if not matched_df.empty and EXCLUDED_CONTRACT_STATUSES and "status" in matched_df.columns:
        excluded_lower = {s.lower().strip() for s in EXCLUDED_CONTRACT_STATUSES}
        contract_status = matched_df["status"].fillna("").astype(str).str.strip().str.lower()
        excluded_mask = contract_status.isin(excluded_lower)
        if excluded_mask.any():
            excluded_agents = matched_df[excluded_mask].copy()
            excluded_agents["exception_reason"] = "Contract Status - " + matched_df.loc[excluded_mask, "status"].fillna("").astype(str).str.strip()
            excluded_agents["exception_id"] = EXC_CONTRACT_STATUS_EXCLUDED
            unmatched_df = pd.concat([unmatched_df, excluded_agents], ignore_index=True)
            matched_df = matched_df[~excluded_mask].copy().reset_index(drop=True)
            print(f"    🚫 {len(excluded_agents)} agent(s) excluded by contract status ({', '.join(excluded_agents['status'].unique())})")

    # Global rule 2: check agent registry (lup_agents) for agent-level status
    if not matched_df.empty and "agent_npn" in matched_df.columns:
        matched_npns = matched_df["agent_npn"].dropna().astype(str).str.strip()
        matched_npns = set(matched_npns[matched_npns != ""])
        if matched_npns:
            agent_registry = load_agent_registry(conn, matched_npns)
            if not agent_registry.empty:
                registry_lookup = dict(zip(agent_registry["npn"], agent_registry["status"]))
                registry_npns = set(agent_registry["npn"].values)

                # Agents not in registry at all
                file_npn = matched_df["agent_npn"].astype(str).str.strip()
                not_in_registry = ~file_npn.isin(registry_npns) & (file_npn != "")
                if not_in_registry.any():
                    missing_agents = matched_df[not_in_registry].copy()
                    missing_agents["exception_reason"] = "Agent not in CRM"
                    missing_agents["exception_id"] = EXC_AGENT_NOT_IN_CRM
                    unmatched_df = pd.concat([unmatched_df, missing_agents], ignore_index=True)
                    matched_df = matched_df[~not_in_registry].copy().reset_index(drop=True)
                    print(f"    🚫 {len(missing_agents)} agent(s) not found in CRM")

                # Agents with excluded agent-level status
                if EXCLUDED_AGENT_STATUSES and not matched_df.empty:
                    excluded_lower = {s.lower().strip() for s in EXCLUDED_AGENT_STATUSES}
                    agent_status = matched_df["agent_npn"].astype(str).str.strip().map(registry_lookup).fillna("").astype(str).str.strip().str.lower()
                    excluded_mask = agent_status.isin(excluded_lower)
                    if excluded_mask.any():
                        excluded_agents = matched_df[excluded_mask].copy()
                        actual_status = matched_df.loc[excluded_mask, "agent_npn"].astype(str).str.strip().map(registry_lookup)
                        excluded_agents["exception_reason"] = "Agent Status - " + actual_status.fillna("").astype(str).str.strip()
                        excluded_agents["exception_id"] = EXC_AGENT_STATUS_EXCLUDED
                        unmatched_df = pd.concat([unmatched_df, excluded_agents], ignore_index=True)
                        matched_df = matched_df[~excluded_mask].copy().reset_index(drop=True)
                        print(f"    🚫 {len(excluded_agents)} agent(s) excluded by agent status ({', '.join(actual_status.unique())})")
            else:
                # No agents found in registry — all are exceptions
                matched_df_copy = matched_df.copy()
                matched_df_copy["exception_reason"] = "Agent not in CRM"
                matched_df_copy["exception_id"] = EXC_AGENT_NOT_IN_CRM
                unmatched_df = pd.concat([unmatched_df, matched_df_copy], ignore_index=True)
                matched_df = matched_df.iloc[0:0].copy()
                print(f"    🚫 No matched agents found in CRM")

    # Resolve parent NPN if parent_identity_field is configured
    parent_id_field = _rule_val(rule, "parent_identity_field")
    if parent_id_field and not matched_df.empty and "parent_npn" in matched_df.columns:
        matched_df, parent_exc = resolve_parent_npn(matched_df, active_contracts_df, rule, conn=conn)
        if not parent_exc.empty:
            unmatched_df = pd.concat([unmatched_df, parent_exc], ignore_index=True)

    rolled_df = rollup_appointments(matched_df, rule)
    rolled_df = check_transitions(rolled_df)

    # No-op rows (CRM non-active + file non-active, identical → CRM wins, no update):
    # exclude from BOTH results and exceptions. Capture their IDs so the
    # reconciliation guard below can account for them (intentional, not silent).
    _noop_ids = set()
    _primary = str(_rule_val(rule, "primary_identity_field", "NPN")).upper()
    _srccol = {"WR": "agent_writing_num", "NAME": "agent_full_name"}.get(_primary, "agent_npn")
    if not rolled_df.empty and "_noop" in rolled_df.columns:
        if _srccol in rolled_df.columns:
            _noop_ids = set(rolled_df.loc[rolled_df["_noop"], _srccol]
                            .astype(str).str.strip().replace("", pd.NA).dropna())
        rolled_df = rolled_df[~rolled_df["_noop"]].copy().reset_index(drop=True)
        rolled_df = rolled_df.drop(columns=["_noop"], errors="ignore")

    # Split: blocked transitions → exceptions only, rest → results
    if not rolled_df.empty and "_blocked" in rolled_df.columns:
        blocked_df = rolled_df[rolled_df["_blocked"]].copy()
        rolled_df = rolled_df[~rolled_df["_blocked"]].copy().reset_index(drop=True)
        if not blocked_df.empty:
            blocked_df["exception_reason"] = blocked_df["note"].fillna("Blocked transition")
            blocked_df["exception_id"] = EXC_BLOCKED_TRANSITION
            unmatched_df = pd.concat([unmatched_df, blocked_df], ignore_index=True)
        rolled_df = rolled_df.drop(columns=["_blocked"], errors="ignore")

    results_df = build_results(rolled_df, carrier_name, carrier_id, run_date)
    unmatched_df = rollup_exceptions(unmatched_df, rule)
    exceptions_df = build_exceptions(unmatched_df, carrier_name, carrier_id, run_date)

    # Surface rows the STATUS/CONTAINS filter removed (pre-identity) as tracked
    # exceptions, deduped to agent level, so file → results + exceptions reconciles.
    if isinstance(_status_filtered, pd.DataFrame) and not _status_filtered.empty:
        sf = _status_filtered.copy()
        idk = "agent_npn" if ("agent_npn" in sf.columns and sf["agent_npn"].astype(str).str.strip().ne("").any()) \
              else ("agent_writing_num" if "agent_writing_num" in sf.columns else None)
        if idk:
            sf = sf[sf[idk].astype(str).str.strip() != ""].drop_duplicates(subset=[idk])
        if not sf.empty:
            fcol = _rule_val(rule, "filter_column", "contract_status")
            raw_status = (sf[fcol].astype(str).str.strip() if fcol in sf.columns
                          else pd.Series(["unknown"] * len(sf), index=sf.index))
            sf["exception_reason"] = "Filtered out by status filter: '" + raw_status + "'"
            sf["exception_id"] = EXC_IDENTITY_NOT_FOUND
            exc_extra = build_exceptions(sf, carrier_name, carrier_id, run_date)
            exceptions_df = pd.concat([exceptions_df, exc_extra], ignore_index=True)
            print(f"    🔧 Status-filtered rows surfaced as exceptions: {len(exc_extra)}")

    missing_df = find_missing_agents(df, active_contracts_df, carrier_name, carrier_id, run_date)

    # --- Reconciliation guard: every file agent must land in results, exceptions,
    # or the intentional no-op set. Anything else is a silent drop — log it (with
    # sample IDs) so it never hides again. (Logs only; does not alter output.)
    try:
        _idcol = {"WR": "Writing_Number", "NAME": "Name"}.get(_primary, "NPN")
        def _ids(frame, col):
            if frame is None or getattr(frame, "empty", True) or col not in frame.columns:
                return set()
            return set(frame[col].astype(str).str.strip().replace("", pd.NA).dropna())
        expected = _ids(df, _srccol) | _ids(_status_filtered, _srccol)
        accounted = _ids(results_df, _idcol) | _ids(exceptions_df, _idcol) | _noop_ids
        gap = expected - accounted
        if gap:
            print(f"    ⚠️  RECONCILE {carrier_name}: {len(gap)} file agent(s) in NEITHER "
                  f"results nor exceptions nor no-op — e.g. {sorted(gap)[:10]}")
        elif expected:
            print(f"    ✅ RECONCILE {carrier_name}: all {len(expected)} file agents accounted "
                  f"({len(_ids(results_df,_idcol))} results / {len(_ids(exceptions_df,_idcol))} exc / {len(_noop_ids)} no-op)")
    except Exception as _ex:
        print(f"    (reconcile check skipped: {_ex})")

    return _build_metrics(carrier_name, carrier_id, total_rows, results_df, exceptions_df, missing_df, temp_dir, "success", contracts_loaded=len(contracts_df), raw_file_name=raw_file_name, threshold_pct=_rule_val(rule, "exception_threshold_pct"))


def _empty_metrics(cn, ci, st, errors=None, raw_file_name=""):
    return {"carrier_name": cn, "carrier_id": ci, "total_rows": 0, "results_count": 0,
            "exceptions_count": 0, "exception_rate": 0, "missing_count": 0,
            "exception_categories": {}, "status": st, "errors": errors or [],
            "results_path": None, "exceptions_path": None, "missing_path": None,
            "raw_file_name": raw_file_name,
            "contracts_loaded": 0}

def _build_metrics(cn, ci, total, results_df, exceptions_df, missing_df, temp_dir, status, contracts_loaded=0, raw_file_name="", threshold_pct=None):
    safe = cn.replace(" ", "_").lower()
    os.makedirs(temp_dir, exist_ok=True)
    rp, ep, mp = os.path.join(temp_dir, f"temp_r_{safe}.csv"), os.path.join(temp_dir, f"temp_e_{safe}.csv"), os.path.join(temp_dir, f"temp_m_{safe}.csv")
    # Force carrier_id to string to prevent scientific notation in CSV output
    for _df in [results_df, exceptions_df, missing_df]:
        if "carrier_id" in _df.columns:
            _df["carrier_id"] = _df["carrier_id"].astype(str).str.replace(".0", "", regex=False)
    results_df.to_csv(rp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    exceptions_df.to_csv(ep, index=False, quoting=csv.QUOTE_NONNUMERIC)
    if not missing_df.empty:
        missing_df.to_csv(mp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    ec = len(exceptions_df)
    er = round(ec / total * 100, 1) if total > 0 else 0
    cats = {}
    if not exceptions_df.empty:
        cats = exceptions_df["exception_reason"].apply(
            lambda r: "contract_status_excluded" if "contract status" in str(r).lower()
            else "agent_status_excluded" if "agent status" in str(r).lower()
            else "agent_not_in_crm" if "not in crm" in str(r).lower()
            else "blocked_transition" if "blocked transition" in str(r).lower()
            else "parent_not_resolved" if "parent" in str(r).lower()
            else "identity_not_found" if "not found" in str(r).lower()
            else "identity_multiple_match" if "multiple" in str(r).lower()
            else "other"
        ).value_counts().to_dict()
    # Per-carrier threshold from the rules matrix (exception_threshold_pct),
    # falling back to the global default. Replaces the previously hardcoded 10.
    try:
        threshold = float(threshold_pct) if threshold_pct not in (None, "", "NA", "nan") else float(EXCEPTION_THRESHOLD_PCT)
    except (TypeError, ValueError):
        threshold = float(EXCEPTION_THRESHOLD_PCT)
    if er > threshold:
        status = "threshold_exceeded"
    print(f"    ✅ {cn}: {len(results_df):,} agents | {ec:,} exceptions ({er}%) | {len(missing_df):,} missing")
    return {"carrier_name": cn, "carrier_id": ci, "total_rows": total, "results_count": len(results_df), "exceptions_count": ec, "exception_rate": er, "missing_count": len(missing_df), "exception_categories": cats, "status": status, "errors": [], "results_path": rp, "exceptions_path": ep, "missing_path": mp if not missing_df.empty else None, "raw_file_name": raw_file_name, "contracts_loaded": contracts_loaded}