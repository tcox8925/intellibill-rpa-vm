# ==========================================================
#  acu_runner.py
# ==========================================================
"""
Full ACU pipeline. Designed for cron.

Steps:
    1. Scan today's files in blob
    2. Classify: known (in matrix) vs unknown (new carrier)
    3a. Unknown → AI mapper → store suggestions → alert
    3b. Check ai_acu_bob_mapping for accepted/edited:
        - edited → train AI first → promote to matrices → set complete
        - accepted → promote to matrices → set complete
    4. Schema check → variance check → deactivate if bad
    5. Process valid carriers (threaded)
    6. Merge → upload → AI report → final notification

Usage:
    python acu_runner.py                # today, production
    python acu_runner.py --test         # test mode (_test suffix)
    python acu_runner.py --date 2026-03-01
"""

import os, re, io, json, hashlib, shutil, tempfile, random
import pandas as pd
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

# Dependency check for .xls support
try:
    import xlrd  # noqa: F401
except ImportError:
    print("⚠️  xlrd not installed — .xls files will fail. Run: pip install xlrd")

from utils.db_utils import get_postgres_connection
from utils.azure_blob_utils import authenticate_blob_storage, DEFAULT_CONTAINER
from utils.email_utils import send_teams_notification, DEFAULT_TEAMS_CHANNEL
from utils.notification import build_notification, build_notification_html
from utils.ai_utils import call_ai_model
from acu_processor import process_carrier, RESULT_COLUMNS, EXCEPTION_COLUMNS
from ai_carrier_mapper import detect_new_carriers, read_file_headers
from intelligence.ai_intelligence import generate_run_report
from job_tracking import job_start, job_finish, fetch_ready_inbound_jobs, start_inbound_job
from config import (FEATURES, MAX_THREADS, EXCEPTION_THRESHOLD_PCT,
                    ROW_VARIANCE_CRITICAL_PCT, ENTITY_ID, SUB_ENTITY_ID,
                    FILE_OVERRIDE, FILE_OVERRIDE_PATH)

RULES_TABLE = "ops_srv.ops_acu_bob_rules_matrix"
MAPPING_TABLE = "ops_srv.ops_acu_bob_load_matrix"
AI_MAPPING_TABLE = "ops_srv.ai_acu_bob_mapping"
OUTPUT_BASE = "results/agent_contract_update (acu)/acu_new_process/"

# ── LOCAL CONFIG (test mode) ──
# In test mode the rules + load matrices are read from local CSVs instead of the
# DB, so the pipeline can be exercised without touching ops_srv. The folder lives
# next to this file: <project>/config/  (matches the deployed layout).
# Override by setting the ACU_BOB_CONFIG_DIR environment variable.
LOCAL_CONFIG_DIR = os.environ.get(
    "ACU_BOB_CONFIG_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config"),
)
RULES_CSV = "ops_acu_bob_rules_matrix.csv"
LOAD_CSV = "ops_acu_bob_load_matrix.csv"


def _end_date_blank(series):
    """Mirror the SQL '<col> IS NULL OR LOWER(TRIM(col)) IN (...)' end-date filter."""
    s = series.fillna("").astype(str).str.strip().str.lower()
    return s.isin(["", "na", "nan", "none", "null"])


def load_rules_matrix(conn, process_type, active_only=True):
    """Load the rules matrix — from the DB in prod, from local CSV in test mode.

    Reproduces the SQL filters so downstream code gets an identical frame:
    process_type, optional active_flag='Y', and a blank/na rule_end_date.
    """
    pt = str(process_type).strip().upper()
    if FEATURES.get("use_local_matrix") or FEATURES.get("test_mode"):
        path = os.path.join(LOCAL_CONFIG_DIR, RULES_CSV)
        df = pd.read_csv(path, dtype=str).fillna("")
        df = df[df["process_type"].astype(str).str.strip().str.upper() == pt]
        if "rule_end_date" in df.columns:
            df = df[_end_date_blank(df["rule_end_date"])]
        if active_only:
            df = df[df["active_flag"].astype(str).str.strip().str.upper() == "Y"]
        print(f"  🧪 [test mode] loaded rules from {path} ({len(df)} rows, {pt}, active_only={active_only})")
        return df.reset_index(drop=True)
    end_clause = "(rule_end_date IS NULL OR LOWER(TRIM(rule_end_date)) IN ('', 'na', 'nan', 'none', 'null'))"
    active_clause = " AND active_flag='Y'" if active_only else ""
    return pd.read_sql(
        f"SELECT * FROM {RULES_TABLE} WHERE process_type='{pt}'{active_clause} AND {end_clause}", conn)


def load_prefixes(conn, process_type):
    """Distinct file_naming_pattern for a process — DB in prod, local CSV in test."""
    pt = str(process_type).strip().upper()
    if FEATURES.get("use_local_matrix") or FEATURES.get("test_mode"):
        df = pd.read_csv(os.path.join(LOCAL_CONFIG_DIR, RULES_CSV), dtype=str).fillna("")
        df = df[df["process_type"].astype(str).str.strip().str.upper() == pt]
        vals = df["file_naming_pattern"].dropna().unique()
        return pd.DataFrame({"file_naming_pattern": vals})
    return pd.read_sql(
        f"SELECT DISTINCT file_naming_pattern FROM {RULES_TABLE} WHERE process_type='{pt}'", conn)


def load_mapping_matrix(conn, process_type):
    """Load the load (column-mapping) matrix — DB in prod, local CSV in test mode.

    Reproduces the SQL filters: process_type and a blank/na end_date.
    """
    pt = str(process_type).strip().upper()
    if FEATURES.get("use_local_matrix") or FEATURES.get("test_mode"):
        path = os.path.join(LOCAL_CONFIG_DIR, LOAD_CSV)
        df = pd.read_csv(path, dtype=str).fillna("")
        df = df[df["process_type"].astype(str).str.strip().str.upper() == pt]
        if "end_date" in df.columns:
            df = df[_end_date_blank(df["end_date"])]
        print(f"  🧪 [test mode] loaded load-matrix from {path} ({len(df)} rows, {pt})")
        return df.reset_index(drop=True)
    return pd.read_sql(
        f"SELECT * FROM {MAPPING_TABLE} WHERE process_type='{pt}' "
        f"AND (end_date IS NULL OR LOWER(TRIM(end_date)) IN ('', 'na', 'nan', 'none', 'null'))", conn)


def _safe_carrier_id(val):
    """Convert carrier_id to clean string. Handles int64, float64, scientific notation."""
    if pd.isna(val):
        return ""
    if isinstance(val, float):
        return str(int(val))
    s = str(val).strip()
    if "E" in s or "e" in s:
        try:
            return str(int(float(s)))
        except (ValueError, OverflowError):
            return s
    return s.replace(".0", "")


# ── STEP 1: SCAN BLOB ──
def scan_blob_files(blob_client, scan_date, container_name=DEFAULT_CONTAINER):
    month_folder = f"{scan_date.strftime('%Y')} {scan_date.strftime('%m')} {scan_date.strftime('%b')}"
    base_path = f"raw/agent_contract_update/acu_new_process/{month_folder}/"
    container_client = blob_client.get_container_client(container_name)
    files = []
    try:
        for blob in container_client.list_blobs(name_starts_with=base_path):
            relative = blob.name[len(base_path):]
            if "/" in relative:
                continue
            fname = os.path.basename(blob.name)
            # Skip virtual directory markers (no extension, 0 bytes)
            if "." not in fname:
                continue
            files.append({"blob_path": blob.name, "file_name": fname, "size": blob.size})
    except Exception as e:
        print(f"⚠️  Error scanning: {e}")
    print(f"📂 Found {len(files)} file(s) in {base_path}")
    for f in files:
        print(f"    📄 {f['file_name']} ({f['size']:,} bytes)")
    return files


def filter_one_file(files, name):
    if not name:
        return files
    target = str(name).strip().lstrip("/")
    base = os.path.basename(target)
    return [
        f for f in files
        if f.get("file_name") in (target, base)
        or str(f.get("blob_path", "")).rstrip("/").endswith(target)
    ]


def scan_local_files(process_type="ACU"):
    """Scan local override directory for raw files."""
    local_dir = os.path.join(FILE_OVERRIDE_PATH, process_type)
    files = []
    if not os.path.isdir(local_dir):
        print(f"⚠️  Local override dir not found: {local_dir}")
        return files
    for fname in os.listdir(local_dir):
        fpath = os.path.join(local_dir, fname)
        if os.path.isfile(fpath) and "." in fname:
            files.append({
                "blob_path": fpath,  # local path used as identifier
                "file_name": fname,
                "size": os.path.getsize(fpath),
                "_local": True,
            })
    print(f"📂 Found {len(files)} local file(s) in {local_dir}")
    for f in files:
        print(f"    📄 {f['file_name']} ({f['size']:,} bytes)")
    return files


def read_local_file(file_path, rule):
    """Read a local file into a DataFrame based on rule config."""
    ext = os.path.splitext(file_path)[1].lower()
    skip = int(rule.get("ignore_header_rows", 0) or 0)
    sheet = str(rule.get("sheet_name", "")).strip()
    enc = str(rule.get("file_encoding", "utf-8") or "utf-8").strip()
    delim = "\t" if str(rule.get("file_delimiter", "")).strip() == "tab" else ","

    try:
        if ext in (".xlsx", ".xls"):
            kwargs = {"dtype": str, "skiprows": skip}
            if ext == ".xls":
                kwargs["engine"] = "xlrd"
            if sheet and sheet not in ("", "NA", "nan"):
                kwargs["sheet_name"] = sheet
            else:
                kwargs["sheet_name"] = 0
            return pd.read_excel(file_path, **kwargs)
        else:
            return pd.read_csv(file_path, dtype=str, sep=delim,
                               skiprows=skip, encoding=enc,
                               on_bad_lines="skip")
    except Exception as e:
        print(f"    ⚠️  Failed to read local file {os.path.basename(file_path)}: {e}")
        return None


# ── STEP 2: CLASSIFY ──
def classify_files(files, rules_df):
    known, unknown = [], []
    prefixes = {}
    _has_reader = lambda r: bool(str(r.get("custom_reader_name", "")).strip())
    for _, rule in rules_df.iterrows():
        pattern = str(rule.get("file_naming_pattern", "")).strip().lower()
        if not pattern or pattern == "na":
            continue
        # Several carriers can legitimately share ONE file pattern: a single
        # multi-sheet workbook (SMA, HCSC) is picked up once and the multi-carrier
        # reader fans it out to every carrier. So keep just one rule per pattern
        # ("use the first one"), but prefer whichever rule defines a
        # custom_reader_name, so the fan-out reader fires no matter the row order.
        existing = prefixes.get(pattern)
        if existing is None or (_has_reader(rule) and not _has_reader(existing)):
            prefixes[pattern] = rule
    for f in files:
        fname = f["file_name"].lower()
        matched = False
        for pattern, rule in prefixes.items():
            if pattern in fname:
                known.append({**f, "rule": rule.to_dict(), "pattern": pattern})
                matched = True
                break
        if not matched:
            unknown.append(f)
    print(f"  ✅ Known: {len(known)} | 🆕 Unknown: {len(unknown)}")
    for k in known:
        print(f"    ✅ {k['file_name']} → {k['rule']['carrier_name']}")
    for u in unknown:
        print(f"    🆕 {u['file_name']}")
    return known, unknown


def carrier_id_for_filename(conn, process_type, filename):
    """Resolve carrier_id from rules matrix (same pattern match as classify_files)."""
    rules_df = load_rules_matrix(conn, process_type, active_only=True)
    rules_df["carrier_id"] = rules_df["carrier_id"].apply(_safe_carrier_id)
    fname = os.path.basename(filename).lower()
    prefixes = {}
    _has_reader = lambda r: bool(str(r.get("custom_reader_name", "")).strip())
    for _, rule in rules_df.iterrows():
        pattern = str(rule.get("file_naming_pattern", "")).strip().lower()
        if not pattern or pattern == "na":
            continue
        existing = prefixes.get(pattern)
        if existing is None or (_has_reader(rule) and not _has_reader(existing)):
            prefixes[pattern] = rule
    for pattern, rule in prefixes.items():
        if pattern in fname:
            return _safe_carrier_id(rule.get("carrier_id", ""))
    return None


# ── STEP 3b: PROMOTE ACCEPTED/EDITED MAPPINGS ──
def promote_reviewed_mappings(conn):
    """
    Check ai_acu_bob_mapping for accepted/edited entries.
    - edited → feed corrections to AI FIRST → then promote
    - accepted → promote directly
    Both → write to rules_matrix + load_matrix → set status = complete
    """
    if FEATURES.get("test_mode"):
        print("  🧪 [test mode] skipping mapping promotion (no DB writes)")
        return 0
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Get distinct files that have been reviewed
    cur.execute(f"""
        SELECT DISTINCT carrier_name, process_type, file_name, suggested_rules
        FROM {AI_MAPPING_TABLE}
        WHERE status IN ('accepted', 'edited')
    """)
    reviewed_files = cur.fetchall()

    if not reviewed_files:
        return 0

    promoted_count = 0

    for carrier_name, process_type, file_name, rules_json in reviewed_files:
        print(f"\n  📤 Promoting reviewed carrier: {carrier_name} ({file_name})")

        # ── Train AI on edits FIRST ──
        # canonical_column = DB column (static), file_column = AI suggestion, accepted_column = human pick
        cur.execute(f"""
            SELECT canonical_column, file_column, accepted_column, ai_reasoning
            FROM {AI_MAPPING_TABLE}
            WHERE file_name = %s AND status = 'edited'
        """, (file_name,))
        edits = cur.fetchall()

        if edits:
            corrections = [
                {"database_column": e[0], "ai_suggested": e[1],
                 "human_corrected": e[2], "reasoning": e[3]}
                for e in edits
            ]
            print(f"    🧠 Training AI on {len(corrections)} correction(s)...")
            _feed_corrections_to_ai(corrections)

        # ── Promote to rules matrix ──
        rules = {}
        try:
            rules = json.loads(rules_json) if rules_json else {}
            rules.pop("file_headers", None)  # not a rule config field
        except:
            pass

        match = re.match(r"^(.*?_)(?=\d)", file_name)
        prefix = match.group(1) if match else file_name

        cur.execute(f"SELECT COUNT(*) FROM {RULES_TABLE} WHERE file_naming_pattern = %s AND process_type = %s", (prefix, process_type))
        if cur.fetchone()[0] == 0:
            # Helper: read from reviewed rules JSON, with sensible defaults
            def rv(key, default="NA"):
                v = rules.get(key, default)
                return default if v in (None, "", "nan", "None", "null") else str(v)

            cur.execute(f"""
                INSERT INTO {RULES_TABLE}
                (carrier_id, carrier_name, process_type, contract_type,
                 file_format, file_delimiter, file_encoding, sheet_name,
                 ignore_header_rows, password_secret_name, file_naming_pattern,
                 date_format, contract_count, multi_file_enabled, expected_file_count,
                 main_file_identifier, custom_logic_flag, custom_module_name,
                 exception_threshold_pct, rts_flag_applicable, rts_filter,
                 primary_identity_field, fallback_identity_field, combined_mdc_aca,
                 default_type_value, type_value_map,
                 parent_npn, parent_npn_scope,
                 filter_rule_type, filter_values, filter_column,
                 filter_scope, appointed_state_applicable, appointed_state_filter,
                 active_flag, rule_start_date, created_date, modified_date, modified_by)
                VALUES ('', %s, %s, %s, %s, %s, %s, %s, %s, 'NA', %s,
                        'MMDDYYYY', 'NA', 'N', 1, 'NA', 'N', 'NA', 10, %s, 'NA',
                        %s, %s, 'no', %s, %s, 'NA', 'NA',
                        %s, %s, %s, %s, 'N', 'NA',
                        'Y', %s, %s, %s, 'ai_promoted')
            """, (
                carrier_name, process_type,
                rv("contract_type", "ACA"),
                rv("file_format", "csv"), rv("file_delimiter", "comma"),
                rv("file_encoding", "utf-8"), rv("sheet_name", "NA"),
                int(rv("ignore_header_rows", "0")), prefix,
                rv("rts_flag_applicable", "N"),
                rv("primary_identity_field", "NPN"),
                rv("fallback_identity_field", "NAME"),
                rv("default_type_value", "NA"),
                rv("type_value_map", "NA"),
                rv("filter_rule_type", "ALL"),
                rv("filter_values", "NA"), rv("filter_column", "NA"),
                rv("filter_scope", "ROW"),
                now, now, now,
            ))
            print(f"    ✅ Rules row created (active=Y)")

        # ── Promote to load matrix ──
        # canonical_column = database_column, COALESCE(accepted_column, file_column) = mapping
        cur.execute(f"""
            SELECT canonical_column as db_col, COALESCE(accepted_column, file_column) as mapping_col
            FROM {AI_MAPPING_TABLE}
            WHERE file_name = %s AND status IN ('accepted', 'edited')
              AND COALESCE(accepted_column, file_column) != 'NA'
              AND COALESCE(accepted_column, file_column) IS NOT NULL
        """, (file_name,))

        mappings_inserted = 0
        for db_col, mapping_col in cur.fetchall():
            cur.execute(f"""
                SELECT COUNT(*) FROM {MAPPING_TABLE}
                WHERE carrier_name = %s AND process_type = %s AND database_column = %s AND mapping = %s
            """, (carrier_name, process_type, db_col, mapping_col))
            if cur.fetchone()[0] == 0:
                cur.execute(f"""
                    INSERT INTO {MAPPING_TABLE}
                    (carrier_id, carrier_name, process_type, database_column,
                     mapping, required_flag, start_date, end_date,
                     created_date, modified_date, modified_by)
                    VALUES ('', %s, %s, %s, %s, 'N', %s, NULL, %s, %s, 'ai_promoted')
                """, (carrier_name, process_type, db_col, mapping_col, now, now, now))
                mappings_inserted += 1

        # ── Set status = complete ──
        cur.execute(f"""
            UPDATE {AI_MAPPING_TABLE}
            SET status = 'complete', modified_date = %s
            WHERE file_name = %s AND status IN ('accepted', 'edited')
        """, (now, file_name))

        conn.commit()
        promoted_count += 1
        print(f"    ✅ {mappings_inserted} column mappings promoted → status = complete")

    cur.close()
    return promoted_count


def _feed_corrections_to_ai(corrections):
    text = "\n".join(f"  - DB column '{c['database_column']}': AI suggested file column '{c['ai_suggested']}' → human picked '{c['human_corrected']}'" for c in corrections)
    prompt = f"These column mappings were corrected:\n{text}\nExplain each in one sentence. Summarize the pattern (2 sentences)."
    response = call_ai_model(prompt, "Learn from corrections. Be concise.")
    if response:
        print(f"    📝 AI feedback: {response[:200]}")


# ── STEP 4: SCHEMA + VARIANCE CHECKS ──
def _load_dated_json(rule, column_name):
    """Load a dated JSON dict from a rules matrix column. Returns {} if empty/invalid."""
    raw = str(rule.get(column_name, "")).strip()
    if not raw or raw in ("", "NA", "nan", "None", "null", "{}"):
        return {}
    try:
        val = json.loads(raw)
        if isinstance(val, dict):
            return val
        # Migrate from old flat array format → dated dict
        if isinstance(val, list):
            return {"migrated": val}
    except Exception:
        pass
    return {}


def _save_dated_json(conn, rule, column_name, dated_dict):
    """Save a dated JSON dict back to the rules matrix column."""
    if FEATURES.get("test_mode"):
        return
    pt = str(rule.get("process_type", "ACU")).strip()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {RULES_TABLE} SET {column_name}=%s, modified_date=%s "
            f"WHERE carrier_id=%s AND process_type=%s AND file_naming_pattern=%s",
            (json.dumps(dated_dict, default=str),
             datetime.now().strftime("%Y-%m-%d"),
             str(rule["carrier_id"]), pt, str(rule["file_naming_pattern"])))
        conn.commit()
    except Exception as e:
        conn.rollback()
        if "column" in str(e).lower() and "does not exist" in str(e).lower():
            print(f"    ℹ️  Add column: ALTER TABLE {RULES_TABLE} ADD COLUMN {column_name} TEXT;")
        else:
            print(f"    ⚠️  Failed to save {column_name}: {e}")
    finally:
        cur.close()


def _get_latest_entry(dated_dict, exclude_date=None):
    """Get the most recent entry from a dated JSON dict, optionally excluding a date."""
    dates = sorted((k for k in dated_dict if k != "migrated" and k != exclude_date), reverse=True)
    if dates:
        return dates[0], dated_dict[dates[0]]
    if "migrated" in dated_dict:
        return "migrated", dated_dict["migrated"]
    return None, None


# ── COLUMN VALUE SIGNATURES ──
# Store per-column data fingerprints during scan; compare run-over-run
# to detect when column content changes character (mapping drift).

def _read_file_sample(blob_client, blob_path, container_name, rule=None, nrows=50):
    """Read a small sample (headers + N rows) from a blob file. Returns DataFrame or None."""
    try:
        container_client = blob_client.get_container_client(container_name)
        data = container_client.get_blob_client(blob_path).download_blob().readall()
        fname = os.path.basename(blob_path).lower()

        if fname.endswith(".csv"):
            try:
                return pd.read_csv(io.BytesIO(data), nrows=nrows, dtype=str)
            except UnicodeDecodeError:
                return pd.read_csv(io.BytesIO(data), nrows=nrows, dtype=str, encoding="latin-1")
        elif fname.endswith((".xlsx", ".xls")):
            try:
                return pd.read_excel(io.BytesIO(data), nrows=nrows, dtype=str, sheet_name=0)
            except Exception:
                return pd.read_excel(io.BytesIO(data), nrows=nrows, dtype=str, engine="xlrd", sheet_name=0)
        return None
    except Exception as e:
        print(f"    ⚠️  Could not sample file: {e}")
        return None


def _compute_column_signatures(df):
    """
    Compute per-column fingerprints: %numeric, %alpha, avg length, sample values.
    Stored as dated JSON — the comparison baseline for next run.
    """
    sigs = {}
    for col in df.columns:
        vals = df[col].dropna().astype(str).str.strip()
        vals = vals[(vals.str.len() > 0) & (~vals.str.lower().isin(["nan", "none"]))]
        n = len(vals)
        if n == 0:
            continue
        sigs[col.lower().strip()] = {
            "pct_numeric": round(vals.str.match(r'^\d+\.?\d*$').sum() / n, 2),
            "pct_alpha":   round(vals.str.match(r"^[A-Za-z \'\-\.]+$").sum() / n, 2),
            "avg_len":     round(vals.str.len().mean(), 1),
            "null_rate":   round(1 - n / max(len(df[col]), 1), 2),
            "samples":     vals.head(5).tolist(),
        }
    return sigs


def check_column_signatures(blob_client, file_info, rule, conn, container_name=DEFAULT_CONTAINER,
                             drift_threshold=0.30):
    """
    Read a sample of the file, compute per-column value fingerprints, store them,
    and compare against the previous run.

    Returns (warnings_list, current_signatures).
    A warning fires when a column's numeric% or alpha% shifts by more than
    drift_threshold (default 30pp) — strong signal of a column content swap.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    sample_df = _read_file_sample(blob_client, file_info["blob_path"], container_name, rule)
    if sample_df is None or sample_df.empty:
        return [], {}

    current_sigs = _compute_column_signatures(sample_df)

    # Load previous signatures from rule dict
    sig_history = _load_dated_json(rule, "column_signatures_json")
    prev_date, prev_sigs = _get_latest_entry(sig_history, exclude_date=today_str)

    # Store current snapshot
    sig_history[today_str] = current_sigs
    _save_dated_json(conn, rule, "column_signatures_json", sig_history)

    # First run — no baseline to compare
    if not prev_sigs:
        print(f"    📊 Value signatures stored ({len(current_sigs)} columns)")
        return [], current_sigs

    # Compare against previous run
    warnings = []
    for col, curr in current_sigs.items():
        prev = prev_sigs.get(col)
        if not prev:
            continue

        num_delta = abs(curr["pct_numeric"] - prev["pct_numeric"])
        alpha_delta = abs(curr["pct_alpha"] - prev["pct_alpha"])

        if num_delta > drift_threshold or alpha_delta > drift_threshold:
            detail = (f"numeric {prev['pct_numeric']:.0%}→{curr['pct_numeric']:.0%}, "
                      f"alpha {prev['pct_alpha']:.0%}→{curr['pct_alpha']:.0%}")
            warnings.append({
                "column": col, "detail": detail,
                "prev_samples": prev.get("samples", [])[:3],
                "curr_samples": curr.get("samples", [])[:3],
            })
            print(f"    ⚠️  VALUE DRIFT: '{col}' changed character vs {prev_date} — {detail}")
            print(f"         was: {prev.get('samples', [])[:3]}")
            print(f"         now: {curr.get('samples', [])[:3]}")

    if not warnings:
        print(f"    📊 Value signatures OK ({len(current_sigs)} columns, vs {prev_date})")

    return warnings, current_sigs


def check_schema(blob_client, file_info, rule, conn, container_name=DEFAULT_CONTAINER):
    """
    Compare current file headers against stored schema baseline (dated JSON).
    Saves current schema snapshot by date. Returns (ok, drift_info).
    """
    headers = read_file_headers(blob_client, file_info["blob_path"], container_name, rule=rule)
    if not headers:
        return True, None

    current_set = set(h.lower().strip() for h in headers)
    current_hash = hashlib.sha256(json.dumps(sorted(current_set)).encode()).hexdigest()
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Load dated schema history
    schema_history = _load_dated_json(rule, "schema_columns_json")

    # Find previous baseline (latest date before today)
    prev_date, prev_columns = _get_latest_entry(schema_history, exclude_date=today_str)

    # Always save today's snapshot
    schema_history[today_str] = sorted(current_set)
    _save_dated_json(conn, rule, "schema_columns_json", schema_history)

    # Also update hash (skipped in test mode — no DB writes)
    if not FEATURES.get("test_mode"):
        pt = str(rule.get("process_type", "ACU")).strip()
        cur = conn.cursor()
        try:
            cur.execute(
                f"UPDATE {RULES_TABLE} SET schema_signature_hash=%s "
                f"WHERE carrier_id=%s AND process_type=%s AND file_naming_pattern=%s",
                (current_hash, str(rule["carrier_id"]), pt, str(rule["file_naming_pattern"])))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()

    # First run — no previous baseline
    if prev_columns is None:
        print(f"    📐 Schema baseline stored ({len(headers)} columns)")
        return True, None

    stored_set = set(prev_columns) if isinstance(prev_columns, list) else set()
    if not stored_set:
        print(f"    📐 Schema baseline stored ({len(headers)} columns)")
        return True, None

    added = current_set - stored_set
    removed = stored_set - current_set

    if not added and not removed:
        return True, None

    print(f"    ⚠️  Schema drift vs {prev_date}: +{len(added)} / -{len(removed)}")
    return False, {
        "carrier_name": rule["carrier_name"],
        "carrier_id": str(rule["carrier_id"]),
        "added": list(added),
        "removed": list(removed),
        "current_headers": headers,
        "current_hash": current_hash,
        "previous_date": prev_date,
    }


def check_row_variance(blob_client, file_info, rule, conn, container_name=DEFAULT_CONTAINER):
    """
    Compare current file row count against previous run (dated JSON).
    Returns (ok, variance, details).
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    try:
        container_client = blob_client.get_container_client(container_name)
        data = container_client.get_blob_client(file_info["blob_path"]).download_blob().readall()
        fname = file_info["file_name"].lower()
        if fname.endswith(".csv"):
            try:
                df = pd.read_csv(io.BytesIO(data), dtype=str)
            except Exception:
                df = pd.read_csv(io.BytesIO(data), dtype=str, encoding="latin-1")
        elif fname.endswith((".xlsx", ".xls")):
            try:
                df = pd.read_excel(io.BytesIO(data), dtype=str)
            except Exception:
                try:
                    df = pd.read_excel(io.BytesIO(data), dtype=str, engine="openpyxl")
                except Exception:
                    df = pd.read_excel(io.BytesIO(data), dtype=str, engine="xlrd")
        else:
            return True, 0, None
        current = len(df)
    except Exception:
        return True, 0, None

    # Load dated row count history
    row_history = _load_dated_json(rule, "previous_row_count")

    # Handle migration from old integer format
    if not row_history:
        old_val = rule.get("previous_row_count")
        if old_val and str(old_val).strip() not in ("", "NA", "nan", "None", "null", "{}"):
            try:
                old_int = int(float(str(old_val)))
                row_history = {"migrated": old_int}
            except Exception:
                pass

    # Find previous row count
    prev_date, prev_count = _get_latest_entry(row_history, exclude_date=today_str)

    # Save today's count
    row_history[today_str] = current
    _save_dated_json(conn, rule, "previous_row_count", row_history)

    if prev_count is None:
        return True, 0, {"current": current, "previous": None, "variance_pct": 0}

    try:
        prev = int(prev_count)
    except Exception:
        return True, 0, {"current": current, "previous": None, "variance_pct": 0}
    if prev == 0:
        return True, 0, {"current": current, "previous": 0, "variance_pct": 0}

    variance = abs(current - prev) / prev
    direction = "increase" if current >= prev else "decrease"
    details = {"current": current, "previous": prev,
               "variance_pct": round(variance * 100, 1), "direction": direction}
    if variance >= ROW_VARIANCE_CRITICAL_PCT:
        if direction == "decrease":
            # A large DROP likely means a truncated / partial / broken file — deactivate.
            print(f"    🔴 Variance CRITICAL (drop): {prev:,} → {current:,} ({details['variance_pct']}%)")
            return False, variance, details
        # A large INCREASE is normal growth — notify only, keep the carrier active and processing.
        details["large_increase"] = True
        print(f"    🔵 Variance high (increase): {prev:,} → {current:,} (+{details['variance_pct']}%) — notify only, not deactivating")
    return True, variance, details


def _submit_to_ai_mapping(conn, rule, headers, reason):
    """Submit carrier to AI mapping table for re-mapping after critical schema drift."""
    if FEATURES.get("test_mode"):
        print(f"    🧪 [test mode] skipping AI mapping submission (reason: {reason})")
        return
    cur = conn.cursor()
    try:
        file_pattern = str(rule.get("file_naming_pattern", ""))
        carrier_name = str(rule.get("carrier_name", ""))
        process_type = str(rule.get("process_type", "ACU"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for col in headers:
            cur.execute(f"""
                INSERT INTO {AI_MAPPING_TABLE}
                    (file_name, carrier_name, process_type, file_column, canonical_column,
                     confidence, status, ai_reasoning, detected_date, created_date, modified_date)
                VALUES (%s, %s, %s, %s, '', 0, 'pending_review', %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                file_pattern, carrier_name, process_type,
                col.strip(),
                f"Re-submitted: {reason}",
                now, now, now
            ))
        conn.commit()
        print(f"    📋 Submitted {len(headers)} columns to AI mapping (reason: {reason})")
    except Exception as e:
        conn.rollback()
        print(f"    ⚠️  AI mapping submission failed: {e}")
    finally:
        cur.close()


def deactivate_carrier(conn, rule, reason):
    """Set active_flag='N' in the rules matrix."""
    if FEATURES.get("test_mode"):
        print(f"    🧪 [test mode] would deactivate carrier ({reason}) — no DB write")
        return
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {RULES_TABLE} SET active_flag='N', modified_date=%s, modified_by=%s "
            f"WHERE carrier_id=%s AND process_type='ACU' AND file_naming_pattern=%s",
            (datetime.now().strftime("%Y-%m-%d"), f"auto: {reason}",
             str(rule["carrier_id"]), str(rule["file_naming_pattern"])))
        conn.commit()
        print(f"    🚫 Deactivated: {reason}")
    except Exception:
        conn.rollback()
    finally:
        cur.close()


def get_pending_mappings(conn, process_type=None):
    """
    Query ai_acu_bob_mapping for carriers awaiting review.
    If process_type ('ACU'/'BOB') is given, only that type's entries are returned
    so an ACU run doesn't surface BOB files and vice-versa.
    Returns list of dicts: [{file_name, status, column_count, detected_date}]
    """
    try:
        cur = conn.cursor()
        # Only surface entries that genuinely still need a human:
        #   - drop 'complete' (promoted) and 'resolved_active_rule' (auto-resolved)
        #   - restrict to the running process_type when provided
        #   - also drop any file/carrier that already has a matching active rule,
        #     so a resolved detection never re-surfaces even in test mode
        #     (where the auto-resolve cleanup is skipped / no DB writes).
        pt_clause = "AND a.process_type = %s" if process_type else ""
        params = (process_type,) if process_type else ()
        cur.execute(f"""
            SELECT file_name, status, COUNT(*) as column_count,
                   MIN(created_date) as detected_date
            FROM {AI_MAPPING_TABLE} a
            WHERE a.status NOT IN ('complete', 'resolved_active_rule')
              {pt_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM {RULES_TABLE} r
                  WHERE r.process_type = a.process_type
                    AND r.active_flag = 'Y'
                    AND TRIM(r.file_naming_pattern) != ''
                    AND (
                        a.file_name ILIKE TRIM(r.file_naming_pattern) || '%'
                        OR a.carrier_name = TRIM(r.carrier_name)
                    )
              )
            GROUP BY file_name, status
            ORDER BY MIN(created_date)
        """, params)
        rows = cur.fetchall()
        cur.close()
        return [
            {"file_name": r[0], "status": r[1], "column_count": r[2],
             "detected_date": str(r[3])[:10] if r[3] else ""}
            for r in rows
        ]
    except Exception as e:
        print(f"  [warn] Could not query pending mappings: {e}")
        return []
    finally: cur.close()


def update_row_count(conn, rule, count):
    if FEATURES.get("test_mode"):
        return
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE {RULES_TABLE} SET previous_row_count=%s, modified_date=%s WHERE carrier_id=%s AND process_type='ACU' AND file_naming_pattern=%s",
                    (count, datetime.now().strftime("%Y-%m-%d"), str(rule["carrier_id"]), str(rule["file_naming_pattern"])))
        conn.commit()
    except: conn.rollback()
    finally: cur.close()


# ── MERGE + UPLOAD ──
def build_db_outputs(all_metrics, valid_tasks, all_rules, cr_path, ce_path,
                     run_date_str, scan_date, temp_dir, filenames, skip_inbound_log=False):
    """
    Build acu_contract_updates CSV (P + E rows) and ops_inbound_file_log CSV.
    Returns (contract_updates_path, file_log_path).
    skip_inbound_log: True for --ready runs (inbound row updated in job_finish).
    """
    import pytz

    # Read the combined results + exceptions
    frames = []
    for path, txn_status in [(cr_path, "P"), (ce_path, "E")]:
        if path and os.path.exists(path):
            df = pd.read_csv(path, dtype=str)
            if not df.empty:
                df["_txn_status"] = txn_status
                frames.append(df)

    if not frames:
        print("  ⚠️  No results or exceptions to write")
        return None, None

    combined = pd.concat(frames, ignore_index=True)

    # Market map from rules
    market_map = {}
    for rule_list in [all_rules]:
        if isinstance(rule_list, pd.DataFrame):
            market_map.update(dict(zip(rule_list["carrier_name"], rule_list["contract_type"])))
        elif isinstance(rule_list, list):
            for r in rule_list:
                if isinstance(r, dict):
                    market_map[r.get("carrier_name", "")] = r.get("contract_type", "")

    # Sub-carriers: infer from name
    for cn in combined["carrier_name"].unique():
        if pd.isna(cn) or not isinstance(cn, str):
            continue
        if cn not in market_map:
            if "MDC" in cn.upper():
                market_map[cn] = "MDC"
            elif "ACA" in cn.upper():
                market_map[cn] = "ACA"
            elif "SUP" in cn.upper():
                market_map[cn] = "SUP"

    # File map: carrier_name → raw_file_name from valid_tasks
    file_map = {}
    for task in valid_tasks:
        cn = task["rule"]["carrier_name"]
        if pd.isna(cn) or not isinstance(cn, str):
            continue
        if task.get("files"):
            file_map[cn] = os.path.basename(task["files"][0])
    # For sub-carriers, inherit parent file
    for cn in combined["carrier_name"].unique():
        if pd.isna(cn) or not isinstance(cn, str):
            continue
        if cn not in file_map:
            for task in valid_tasks:
                parent = task["rule"]["carrier_name"]
                if pd.isna(parent) or not isinstance(parent, str):
                    continue
                if parent in cn or cn.startswith(parent.split(" -")[0]):
                    file_map[cn] = os.path.basename(task["files"][0])
                    break

    now = datetime.now(pytz.timezone("US/Central")).strftime("%Y-%m-%d %H:%M:%S")

    # Report date is PER CARRIER — parsed from each carrier's own raw file name
    # (e.g. raw_acu_prominence_mdc_05122026.csv → 2026-05-12). Files are grabbed from
    # the current-month folder, but report_date follows the file. scan_date fallback.
    scan_date_str = scan_date.strftime("%Y-%m-%d")
    def _parse_file_date(fn):
        m = re.search(r"(\d{8})\.\w+$", str(fn))
        if m:
            try:
                return datetime.strptime(m.group(1), "%m%d%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        return scan_date_str
    file_date_map = {cn: _parse_file_date(fn) for cn, fn in file_map.items()}
    today = datetime.now().date()
    julian = today.strftime("%y%j")
    n = len(combined)

    # Read last inserted id from wpo.ops_uid_control to avoid conflicts
    try:
        conn_uid = get_postgres_connection()
        last_id_df = pd.read_sql("SELECT MAX(uid) as uid FROM wpo.ops_uid_control WHERE process_type = 'ACU'", conn_uid)
        last_id = last_id_df["uid"].iloc[0] if not last_id_df.empty and pd.notna(last_id_df["uid"].iloc[0]) else None

        if last_id:
            last_id_str = str(int(last_id))
            last_julian = last_id_str[:5]
            last_seq = int(last_id_str[5:])
            # If same day, continue sequence; otherwise reset to 1
            seq_start = last_seq + 1 if last_julian == julian else 1
        else:
            seq_start = 1

        txn_ids = [f"{julian}{str(seq_start + i).zfill(8)}" for i in range(n)]

        # Write new ids back to wpo.ops_uid_control (skipped in test mode)
        if not FEATURES.get("test_mode"):
            uid_df = pd.DataFrame({
                "uid": txn_ids,
                "process_type": "ACU",
                "table_name": "acu_contract_updates",
            })
            from io import StringIO
            buf = StringIO()
            uid_df.to_csv(buf, index=False, header=False)
            buf.seek(0)
            cur = conn_uid.cursor()
            cur.copy_expert("COPY wpo.ops_uid_control(uid, process_type, table_name) FROM STDIN WITH CSV", buf)
            conn_uid.commit()
            cur.close()
        conn_uid.close()
        _persist_note = " [test: not persisted]" if FEATURES.get("test_mode") else ""
        print(f"  🔑 txn_ids: {txn_ids[0]} → {txn_ids[-1]} ({n} ids, seq_start={seq_start}{_persist_note})")
    except Exception as e:
        print(f"  ⚠️  uid_control lookup failed ({e}), using fallback sequence")
        txn_ids = [f"{julian}{str(i + 1).zfill(8)}" for i in range(n)]

    cu = pd.DataFrame({
        "txn_status": combined["_txn_status"].values,
        "txn_id": txn_ids,
        "carrier_id": combined["carrier_id"].values,
        "carrier_name": combined["carrier_name"].values,
        "market": combined["carrier_name"].map(market_map).fillna("").values,
        "contract_status": combined.get("Status", pd.Series("", index=combined.index)).values,
        "contract_id": combined.get("Name", pd.Series("", index=combined.index)).values,
        "contract_date": combined.get("Status_Date", pd.Series("", index=combined.index)).values,
        "agent_npn": combined.get("NPN", pd.Series("", index=combined.index)).values,
        "agent_writing_num": combined.get("Writing_Number", pd.Series("", index=combined.index)).values,
        "appointment_type": combined.get("Appointment_Type", pd.Series("", index=combined.index)).values,
        "appointed_states": combined.get("Appointed_States", pd.Series("", index=combined.index)).values,
        "appointed_date": combined.get("Appointed_Date", pd.Series("", index=combined.index)).values,
        "parent_npn": combined.get("Parent_Contract", pd.Series("", index=combined.index)).values,
        "current_rts": combined.get("Current_Medicare_RTS", pd.Series("", index=combined.index)).values,
        "current_rts_date": combined.get("Current_Medicare_RTS_Date", pd.Series("", index=combined.index)).values,
        "next_rts": combined.get("Next_Medicare_RTS", pd.Series("", index=combined.index)).values,
        "next_rts_date": combined.get("Next_Medicare_RTS_Date", pd.Series("", index=combined.index)).values,
        "current_aca_rts": combined.get("ACA_RTS", pd.Series("", index=combined.index)).values,
        "next_aca_rts": "",
        "load_date": now,
        "report_date": combined["carrier_name"].map(file_date_map).fillna(scan_date_str).values,
        "raw_file_name": (
            combined["_source_file"].fillna("").astype(str).where(
                combined["_source_file"].fillna("").astype(str).str.strip() != "",
                combined["carrier_name"].map(file_map).fillna(""))
            if "_source_file" in combined.columns
            else combined["carrier_name"].map(file_map).fillna("")
        ).values,
        "entity_id": ENTITY_ID,
        "sub_entity_id": SUB_ENTITY_ID,
        "exception_id": combined.get("exception_id", pd.Series("", index=combined.index)).fillna("").values,
    })

    cu_path = os.path.join(temp_dir, filenames["contract_updates"])
    cu.to_csv(cu_path, index=False)
    print(f"  📝 acu_contract_updates: {len(cu):,} rows (P={len(cu[cu['txn_status']=='P']):,}, E={len(cu[cu['txn_status']=='E']):,})")

    # Delete existing rows for same report_date + raw_file_name + carrier_id to prevent duplicates on rerun
    if FEATURES.get("test_mode"):
        print("  🧪 [test mode] skipping delete-before-insert on acu_contract_updates (no DB writes)")
    else:
        try:
            conn_del = get_postgres_connection()
            cur_del = conn_del.cursor()
            combos = cu[["report_date", "raw_file_name", "carrier_id"]].drop_duplicates()
            total_deleted = 0
            for _, row in combos.iterrows():
                if pd.isna(row["carrier_id"]) or str(row["carrier_id"]).strip() in ("", "nan", "NaN"):
                    continue
                if pd.isna(row["raw_file_name"]) or str(row["raw_file_name"]).strip() in ("", "nan", "NaN"):
                    continue
                cur_del.execute(
                    "DELETE FROM wpo.acu_contract_updates WHERE report_date = %s AND raw_file_name = %s AND carrier_id = %s",
                    (row["report_date"], row["raw_file_name"], row["carrier_id"])
                )
                total_deleted += cur_del.rowcount
            conn_del.commit()
            cur_del.close()
            conn_del.close()
            if total_deleted > 0:
                print(f"  🗑️  Cleared {total_deleted:,} existing rows from acu_contract_updates (rerun safe)")
        except Exception as e:
            print(f"  ⚠️  Delete-before-insert failed: {e}")

    # ── Logs (skipped for --ready; that path updates the existing Ready row) ──
    if skip_inbound_log:
        return cu_path, None

    logs = []
    process_start = now
    for m in all_metrics:
        cn = m["carrier_name"]
        logs.append({
            "file_name": file_map.get(cn, ""),
            "destination_schema": "wpo",
            "destination_table": "acu_contract_updates",
            "process_type": "ACU",
            "process_date_start": process_start,
            "process_date_end": now,
            "load_status": "succeeded" if m["status"] != "error" else "failed",
            "txn_tot_cnt": m["results_count"] + m["exceptions_count"],
            "txn_process_cnt": m["results_count"],
            "txn_error_cnt": m["exceptions_count"],
            "status_message": "; ".join(m.get("errors", [])) or None,
            "file_report_month": None,
            "file_com_month": None,
            "product_name": None,
            "carrier_id": m.get("carrier_id", ""),
            "company_id": None,
            "sub_entity_id": SUB_ENTITY_ID,
            "pk_id": None,
        })

    log_df = pd.DataFrame(logs)
    log_path = os.path.join(temp_dir, filenames["file_log"])
    log_df.to_csv(log_path, index=False)
    print(f"  📝 ops_inbound_file_log: {len(log_df)} carrier entries")

    return cu_path, log_path


def upload_outputs_single(blob_client, local_path, scan_date, container_name=DEFAULT_CONTAINER, subfolder=""):
    """Upload a single file to the results folder."""
    folder = f"{OUTPUT_BASE}{scan_date.strftime('%Y')} {scan_date.strftime('%m')} {scan_date.strftime('%b')}/"
    bp = f"{folder}{subfolder}{os.path.basename(local_path)}"
    cc = blob_client.get_container_client(container_name)
    with open(local_path, "rb") as f:
        cc.get_blob_client(bp).upload_blob(f, overwrite=True)
    print(f"  ☁️  {os.path.basename(local_path)} → {bp}")


def write_contract_updates_to_db(cu_path):
    """Write acu_contract_updates CSV to wpo.acu_contract_updates in Postgres using COPY."""
    cu_df = pd.read_csv(cu_path, dtype=str).fillna("")
    if cu_df.empty:
        return

    conn = get_postgres_connection()
    cur = conn.cursor()

    cols = ["txn_status", "txn_id", "carrier_id", "carrier_name", "market",
            "contract_status", "contract_id", "contract_date", "agent_npn",
            "agent_writing_num", "appointment_type", "appointed_states",
            "appointed_date", "parent_npn", "current_rts", "current_rts_date",
            "next_rts", "next_rts_date", "current_aca_rts", "next_aca_rts",
            "load_date", "report_date", "raw_file_name",
            "entity_id", "sub_entity_id", "exception_id"]

    # Prepare: convert empty exception_id to None for integer column
    out = cu_df[cols].copy()
    out["exception_id"] = out["exception_id"].replace("", None)

    from io import StringIO
    buf = StringIO()
    out.to_csv(buf, index=False, header=False, sep='\t', na_rep='\\N')
    buf.seek(0)

    col_list = ", ".join(cols)
    copy_sql = f"COPY wpo.acu_contract_updates ({col_list}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '\\N')"
    cur.copy_expert(copy_sql, buf)
    conn.commit()
    cur.close()
    conn.close()
    print(f"  ✅ Wrote {len(out):,} rows to wpo.acu_contract_updates (Postgres, COPY)")


def write_logs_to_db(log_path):
    """Write ops_inbound_file_log CSV to Postgres (wpo)."""
    log_df = pd.read_csv(log_path, dtype=str).fillna("")
    if log_df.empty:
        return

    # Postgres has extra columns; pk_id is serial/auto-increment — omit it
    pg_cols = ["file_name", "destination_schema", "destination_table", "process_type",
               "process_date_start", "process_date_end", "load_status",
               "txn_tot_cnt", "txn_process_cnt", "txn_error_cnt", "status_message",
               "file_report_month", "file_com_month", "product_name",
               "carrier_id", "company_id", "sub_entity_id"]

    # ── Postgres: wpo.ops_inbound_file_log ──
    try:
        pg_rows = [tuple(row.get(c, "") or None for c in pg_cols) for _, row in log_df.iterrows()]
        pg_placeholders = ", ".join(["%s"] * len(pg_cols))
        pg_col_list = ", ".join(pg_cols)

        pg_conn = get_postgres_connection()
        pg_cur = pg_conn.cursor()
        pg_cur.executemany(
            f"INSERT INTO wpo.ops_inbound_file_log ({pg_col_list}) VALUES ({pg_placeholders})", pg_rows
        )
        pg_conn.commit()
        pg_cur.close()
        pg_conn.close()
        print(f"  ✅ Wrote {len(pg_rows)} log rows to wpo.ops_inbound_file_log (Postgres)")
    except Exception as e:
        print(f"  ⚠️  Postgres log write failed: {e}")


def _file_recency_key(fname):
    """Sortable recency from a raw file name ending in MMDDYYYY.ext. Higher = newer."""
    if not isinstance(fname, str) or not fname:
        return 0
    m = re.search(r"(\d{8})\.\w+$", fname) or re.search(r"(\d{8})", fname)
    if not m:
        return 0
    tok = m.group(1)
    try:
        return int(datetime.strptime(tok, "%m%d%Y").strftime("%Y%m%d"))
    except ValueError:
        try:
            return int(tok)
        except ValueError:
            return 0


_DUP_CONTRACT_RE = re.compile(r"\(contracts:\s*(.*?)\)", re.IGNORECASE)


def _dedup_multifile_acu(cr, ce):
    """Collapse multi-file (e.g. Sat/Sun/Mon) output within a single run.

    Results: per (carrier_id, agent identity), keep the record from the MOST
    RECENT file the agent appears in — so triplicates collapse but agents that
    fell off the latest file are preserved (kept from the newest file they're in).

    Exceptions: for the duplicate-contracts exception, union the contract set per
    (carrier, NPN) across files (identical repeats collapse, new contracts get
    added) while keeping v2's single-row-per-NPN display (contracts in the reason).
    Other exception types: recency dedup on the full signature.

    Both frames are expected to carry a `_source_file` column.
    """
    # Identity columns survive a CSV round-trip (write → read), where blank cells
    # come back as NaN and .astype(str) yields the literal "nan". Treat those (and
    # "none"/"<NA>") as empty so the NPN→WR→Name fallback actually triggers for
    # WR/Name-primary carriers; otherwise ident="nan" for every row and the dedup
    # collapses distinct agents by reason.
    def _idclean(s):
        s = s.fillna("").astype(str).str.strip()
        return s.mask(s.str.lower().isin(["nan", "none", "<na>", "null"]), "")

    # ----- results -----
    if cr is not None and not cr.empty and "_source_file" in cr.columns:
        cr = cr.copy()
        cr["_rk"] = cr["_source_file"].map(_file_recency_key)
        npn = _idclean(cr.get("NPN", pd.Series("", index=cr.index)))
        wr = _idclean(cr.get("Writing_Number", pd.Series("", index=cr.index)))
        name = _idclean(cr.get("Name", pd.Series("", index=cr.index)))
        cr["_ident"] = npn.where(npn != "", wr.where(wr != "", name))
        cid = cr.get("carrier_id", pd.Series("", index=cr.index)).astype(str)
        cr["_grp"] = cid + "||" + cr["_ident"]
        no_ident = cr["_ident"] == ""
        keep_noident = cr[no_ident]
        dedup = (cr[~no_ident].sort_values("_rk", ascending=False)
                 .drop_duplicates(subset=["_grp"], keep="first"))
        cr = pd.concat([dedup, keep_noident], ignore_index=True)
        cr = cr.drop(columns=["_rk", "_ident", "_grp"], errors="ignore")

    # ----- exceptions -----
    if ce is not None and not ce.empty and "_source_file" in ce.columns:
        ce = ce.copy()
        ce["_rk"] = ce["_source_file"].map(_file_recency_key)
        cid = ce.get("carrier_id", pd.Series("", index=ce.index)).astype(str)
        npn = _idclean(ce.get("NPN", pd.Series("", index=ce.index)))
        wr = _idclean(ce.get("Writing_Number", pd.Series("", index=ce.index)))
        name = _idclean(ce.get("Name", pd.Series("", index=ce.index)))
        # Identity fallback NPN → Writing_Number → Name (same as the results dedup).
        # Without this, WR/Name-primary carriers (blank NPN) collapse distinct agents
        # that merely share an exception reason — e.g. Manhattan, where many agents
        # carry the same multi-product status string, lost 277 rows on export.
        ident = npn.where(npn != "", wr.where(wr != "", name))
        exid = ce.get("exception_id", pd.Series("", index=ce.index)).astype(str).str.strip()
        reason = ce.get("exception_reason", pd.Series("", index=ce.index)).astype(str)
        is_dup = reason.str.contains(r"\(contracts:", case=False, regex=True)

        non_dup = ce[~is_dup].copy()
        if not non_dup.empty:
            non_dup["_sig"] = (cid[~is_dup] + "||" + ident[~is_dup] + "||"
                               + exid[~is_dup] + "||" + reason[~is_dup])
            non_dup = (non_dup.sort_values("_rk", ascending=False)
                       .drop_duplicates(subset=["_sig"], keep="first")
                       .drop(columns=["_sig"], errors="ignore"))

        dup = ce[is_dup].copy()
        union_rows = []
        if not dup.empty:
            dup["_grp"] = cid[is_dup] + "||" + ident[is_dup]
            for _grp, sub in dup.groupby("_grp"):
                base = sub.sort_values("_rk", ascending=False).iloc[0].copy()  # newest = metadata
                seen, merged = set(), []
                # collect contracts oldest-first so the union reads chronologically
                for r in sub.sort_values("_rk", ascending=True)["exception_reason"].astype(str):
                    mm = _DUP_CONTRACT_RE.search(r)
                    if not mm:
                        continue
                    for c in [x.strip() for x in mm.group(1).split(",") if x.strip()]:
                        if c not in seen:
                            seen.add(c)
                            merged.append(c)
                if merged:
                    npn_val = str(base.get("NPN", "")).strip()
                    base["exception_reason"] = (
                        f"Multiple contracts found for NPN {npn_val} "
                        f"(contracts: {', '.join(merged)})")
                union_rows.append(base)
        dup_union = pd.DataFrame(union_rows) if union_rows else pd.DataFrame(columns=ce.columns)

        ce = pd.concat([non_dup, dup_union], ignore_index=True)
        ce = ce.drop(columns=["_rk", "_grp"], errors="ignore")

    return cr, ce


def merge_outputs(all_metrics, filenames, temp_dir):
    r, e, m = [], [], []
    for met in all_metrics:
        src = met.get("raw_file_name", "") or ""
        for k, lst, tag in [("results_path", r, True), ("exceptions_path", e, True), ("missing_path", m, False)]:
            p = met.get(k)
            if p and os.path.exists(p):
                df = pd.read_csv(p, dtype=str, on_bad_lines='warn')
                if not df.empty:
                    if tag:
                        df["_source_file"] = src
                    lst.append(df)
    cr = pd.concat(r, ignore_index=True) if r else pd.DataFrame(columns=RESULT_COLUMNS)
    ce = pd.concat(e, ignore_index=True) if e else pd.DataFrame(columns=EXCEPTION_COLUMNS)
    cm = pd.concat(m, ignore_index=True) if m else pd.DataFrame()
    # Multi-file (e.g. Sat/Sun/Mon) recency dedup within the run — keep most recent
    # record per agent (results) and union duplicate-contract sets (exceptions).
    cr, ce = _dedup_multifile_acu(cr, ce)
    cr.to_csv(os.path.join(temp_dir, filenames["results"]), index=False)
    ce.to_csv(os.path.join(temp_dir, filenames["exceptions"]), index=False)
    if not cm.empty: cm.to_csv(os.path.join(temp_dir, filenames["missing"]), index=False)
    print(f"\n📊 Combined: {len(cr):,} results | {len(ce):,} exceptions | {len(cm):,} missing")
    return os.path.join(temp_dir, filenames["results"]), os.path.join(temp_dir, filenames["exceptions"]), os.path.join(temp_dir, filenames["missing"]) if not cm.empty else None


def upload_outputs(blob_client, cr_path, ce_path, cm_path, scan_date, container_name=DEFAULT_CONTAINER):
    folder = f"{OUTPUT_BASE}{scan_date.strftime('%Y')} {scan_date.strftime('%m')} {scan_date.strftime('%b')}/"
    exc = f"{folder}exceptions/"
    cc = blob_client.get_container_client(container_name)
    paths = {}
    for label, lp, bf in [("results", cr_path, folder), ("exceptions", ce_path, exc), ("missing", cm_path, exc)]:
        if lp and os.path.exists(lp):
            bp = f"{bf}{os.path.basename(lp)}"
            with open(lp, "rb") as f: cc.get_blob_client(bp).upload_blob(f, overwrite=True)
            paths[label] = bp
            print(f"  ☁️  {label} → {bp}")
    return paths


# ── ARCHIVE PROCESSED FILES ──
def archive_processed_files(blob_client, all_metrics, valid_tasks, container_name=DEFAULT_CONTAINER):
    """
    Move successfully processed raw files to archive/ subfolder.
    Creates archive/ if it doesn't exist (blob storage handles this automatically).
    Only archives carriers with status = success or threshold_exceeded.
    """
    successful_carriers = set(
        m["carrier_name"] for m in all_metrics
        if m["status"] in ("success", "threshold_exceeded")
    )

    if not successful_carriers:
        return 0

    container_client = blob_client.get_container_client(container_name)
    archived = 0

    for task in valid_tasks:
        carrier_name = task["rule"]["carrier_name"]
        if carrier_name not in successful_carriers:
            continue

        for blob_path in task["files"]:
            try:
                # Build archive path: same folder + archive/ + filename
                folder = blob_path.rsplit("/", 1)[0]
                file_name = blob_path.rsplit("/", 1)[1]
                archive_path = f"{folder}/archive/{file_name}"

                # Download → upload to archive → delete original
                source_blob = container_client.get_blob_client(blob_path)
                data = source_blob.download_blob().readall()

                archive_blob = container_client.get_blob_client(archive_path)
                archive_blob.upload_blob(data, overwrite=True)

                source_blob.delete_blob()

                archived += 1
                print(f"  📦 Archived: {file_name}")
            except Exception as e:
                print(f"  ⚠️  Archive failed for {blob_path}: {e}")

    print(f"  📦 Archived {archived} file(s)")
    return archived


# ── MAIN PIPELINE ──
def _parse_report_date(report_month_str, fallback=None):
    """Parse file_report_month from RPA log for blob folder scan."""
    if not report_month_str or str(report_month_str).strip().lower() in ("", "na", "none"):
        return fallback or date.today()
    s = str(report_month_str).strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return fallback or date.today()


def _pipeline_succeeded(result):
    if not result:
        return False
    metrics = result.get("metrics") or []
    if not metrics:
        return False
    return any(m.get("status") in ("success", "threshold_exceeded") for m in metrics)


def run_acu_pipeline(scan_date=None, container_name=DEFAULT_CONTAINER, test_mode=False,
                     file_filter=None, track_jobs=True, force_archive=False,
                     from_ready_queue=False):
    if scan_date is None: scan_date = date.today()
    test_mode = test_mode or FEATURES.get("test_mode", False)
    # Publish test_mode to FEATURES so the shared DB-mutation helpers
    # (mapping promotion, schema/signature/variance storage, carrier
    # deactivation, AI submission) — which are also imported by the BOB
    # runner — become no-ops. In test mode the pipeline still READS from the
    # DB (required for contract/rules/mapping matching) but performs NO
    # INSERT / UPDATE / DELETE / COPY anywhere.
    FEATURES["test_mode"] = test_mode
    run_date_str = scan_date.strftime("%m%d%Y")
    mode = " [TEST]" if test_mode else ""
    suffix = "_test" if test_mode else ""
    run_id = str(random.randint(1000, 9999))

    print(f"\n{'='*60}\n  ACU PIPELINE{mode} — {scan_date.strftime('%Y-%m-%d')}\n{'='*60}")

    conn = get_postgres_connection()
    blob_client = authenticate_blob_storage()

    # Step 1: Scan
    print(f"\n── STEP 1: Scan ──")
    file_override = FILE_OVERRIDE or FEATURES.get("file_override", False)
    if file_override:
        print(f"  📁 FILE OVERRIDE: reading from {FILE_OVERRIDE_PATH}/ACU")
        all_files = scan_local_files("ACU")
    else:
        all_files = scan_blob_files(blob_client, scan_date, container_name)
    if file_filter:
        all_files = filter_one_file(all_files, file_filter)
    if not all_files:
        print("⚠️  No files."); conn.close(); return {"ok": False, "metrics": []}

    # Step 2: Classify
    print(f"\n── STEP 2: Classify ──")
    # Load active + inactive (non-ended) rules for classification
    all_rules_for_classify = load_rules_matrix(conn, "ACU", active_only=False)
    all_rules_for_classify["carrier_id"] = all_rules_for_classify["carrier_id"].apply(_safe_carrier_id)
    rules_df = all_rules_for_classify[all_rules_for_classify["active_flag"] == "Y"]
    print(f"📋 {len(rules_df)} active ACU rules ({len(all_rules_for_classify)} total)")

    # Known prefixes = ALL prefixes ever registered (including ended/inactive)
    # so a deactivated or ended carrier is never re-detected as "new"
    all_prefixes_df = load_prefixes(conn, "ACU")
    known_prefixes = set(all_prefixes_df["file_naming_pattern"].str.strip().str.lower().dropna())
    all_known, unknown_files = classify_files(all_files, all_rules_for_classify)

    # Split known files into active (process) vs inactive (skip)
    known_files = []
    skipped_inactive = []
    for kf in all_known:
        if str(kf["rule"].get("active_flag", "Y")).strip().upper() == "Y":
            known_files.append(kf)
        else:
            skipped_inactive.append({"carrier_name": kf["rule"]["carrier_name"], "file_name": kf["file_name"]})
            print(f"    ⏸️  {kf['file_name']} → {kf['rule']['carrier_name']} (inactive — skipped)")

    # Cleanup: auto-resolve stale pending_review entries for carriers that
    # already have active rules — match by file prefix.
    if test_mode:
        print("  🧪 [test mode] skipping stale pending_review cleanup (no DB writes)")
    else:
        try:
            cur = conn.cursor()
            cur.execute(f"""
                UPDATE {AI_MAPPING_TABLE} a
                SET status = 'resolved_active_rule'
                WHERE a.status = 'pending_review'
                  AND a.process_type = 'ACU'
                  AND EXISTS (
                      SELECT 1 FROM {RULES_TABLE} r
                      WHERE r.process_type = 'ACU'
                        AND r.active_flag = 'Y'
                        AND TRIM(r.file_naming_pattern) != ''
                        AND (
                            a.file_name ILIKE TRIM(r.file_naming_pattern) || '%'
                            OR a.carrier_name = TRIM(r.carrier_name)
                        )
                  )
            """)
            resolved = cur.rowcount
            conn.commit()
            if resolved:
                print(f"  🧹 Auto-resolved {resolved} stale pending_review entries (carriers already have active rules)")
        except Exception as e:
            print(f"  ⚠️  Cleanup of stale ai_mapping entries failed: {e}")
            conn.rollback()

    # Step 3a: New carriers (only truly unknown files)
    new_carriers = []
    if unknown_files and FEATURES.get("ai_carrier_mapper", True):
        print(f"\n── STEP 3a: New carriers ──")
        new_carriers = detect_new_carriers(unknown_files, known_prefixes, blob_client, conn, "ACU", container_name)
    elif unknown_files:
        print(f"\n── STEP 3a: New carriers (DISABLED) — {len(unknown_files)} unknown file(s) skipped ──")

    # Step 3b: Promote accepted/edited
    print(f"\n── STEP 3b: Promote reviewed mappings ──")
    promoted = promote_reviewed_mappings(conn)
    if promoted:
        print(f"  ✅ Promoted {promoted} carrier(s) — reloading rules")
        rules_df = load_rules_matrix(conn, "ACU", active_only=True)
        rules_df["carrier_id"] = rules_df["carrier_id"].apply(_safe_carrier_id)
        known_files, _ = classify_files(all_files, rules_df)

    # Step 4: Schema + variance
    print(f"\n── STEP 4: Checks ──")
    all_mappings = load_mapping_matrix(conn, "ACU")
    all_mappings["carrier_id"] = all_mappings["carrier_id"].apply(_safe_carrier_id)
    valid_tasks, deactivated = [], []
    schema_drift_carriers = []
    row_variance_carriers = []
    row_increase_carriers = []
    value_drift_carriers = []

    for kf in known_files:
        rule = kf["rule"]
        print(f"\n  📋 {rule['carrier_name']}:")

        reader_name = str(rule.get("custom_reader_name", "")).strip()

        # Custom readers that return multiple sub-carriers (HCSC, SMA)
        # Skip schema/variance checks — the raw file contains data for many carriers
        if reader_name in ("read_hcsc", "read_sma", "read_quartz", "read_christus", "read_community_health", "read_molina", "read_allstate", "read_healthfirst", "read_physicians_mutual"):
            print(f"    📖 Multi-carrier reader: {reader_name} — expanding...")
            from acu_readers import get_custom_reader
            reader_fn = get_custom_reader(reader_name)
            # Multi-carrier readers apply column bindings themselves. Hand them the
            # matched carrier's ACU load-matrix mappings (readers that self-serve
            # from all_mappings — HCSC/SMA/Allstate/Physicians — simply ignore this).
            _parent_maps = all_mappings[
                (all_mappings["carrier_name"] == rule.get("carrier_name")) &
                (all_mappings["process_type"] == "ACU")
            ]
            sub_results = reader_fn(blob_client, kf["blob_path"], rule, _parent_maps,
                                    rules_df, all_mappings, container_name)
            for sub_rule, sub_df in sub_results:
                sub_cid = str(sub_rule.get("carrier_id", ""))
                sub_cm = all_mappings[all_mappings["carrier_id"].astype(str) == sub_cid]
                # For HCSC sub-carriers, use the parent HCSC mappings if no per-state mappings exist
                if sub_cm.empty and "HCSC" in str(sub_rule.get("carrier_name", "")):
                    sub_cm = all_mappings[
                        all_mappings["carrier_name"].str.contains("HCSC", case=False, na=False) &
                        (all_mappings["process_type"] == "ACU")
                    ]
                valid_tasks.append({
                    "rule": sub_rule, "mappings": sub_cm,
                    "files": [kf["blob_path"]], "pre_read_df": sub_df,
                    "row_count": len(sub_df),
                })
            continue

        if FEATURES.get("schema_check", True):
            schema_ok, drift = check_schema(blob_client, kf, rule, conn, container_name)
            if not schema_ok:
                added = set(drift.get("added", []))
                removed = set(drift.get("removed", []))

                # Columns that matter: mapped in load_matrix + referenced in rules
                cid = str(rule["carrier_id"])
                carrier_mappings = all_mappings[all_mappings["carrier_id"].astype(str) == cid]
                mapped_cols = set(carrier_mappings["mapping"].dropna().str.lower().str.strip())

                rule_cols = set()
                for field in ["filter_column", "sheet_name"]:
                    val = str(rule.get(field, "")).strip().lower()
                    if val and val not in ("", "na", "nan", "none"):
                        rule_cols.add(val)

                columns_that_matter = mapped_cols | rule_cols
                critical_removed = removed & columns_that_matter

                if critical_removed:
                    # Mapped columns removed — deactivate + submit to AI mapping
                    reason = f"Schema drift: lost mapped columns {critical_removed}"
                    if not test_mode:
                        deactivate_carrier(conn, rule, reason)
                        _submit_to_ai_mapping(conn, rule, drift["current_headers"], reason)
                    else:
                        print(f"    ⏭️  [TEST] Would deactivate + submit to AI mapping")
                    schema_drift_carriers.append({
                        "carrier_name": rule["carrier_name"],
                        "type": "critical",
                        "removed": sorted(critical_removed),
                        "added": sorted(added),
                        "action": "Deactivated + submitted to AI mapping"
                    })
                    deactivated.append({"carrier_name": rule["carrier_name"],
                                        "reason": f"Schema drift: lost {critical_removed}"})
                    continue
                else:
                    # Non-critical drift — schema already saved by check_schema, collect for notification
                    parts = []
                    if added: parts.append(f"+{len(added)} added")
                    if removed: parts.append(f"-{len(removed)} removed")
                    print(f"    ℹ️  Schema drift (soft): {', '.join(parts)} — none are mapped columns")
                    if added:
                        schema_drift_carriers.append({
                            "carrier_name": rule["carrier_name"],
                            "type": "soft",
                            "new_columns": sorted(added),
                            "action": "Auto-accepted (unmapped columns)"
                        })

        # Column value signature check — detect content drift run-over-run
        if FEATURES.get("value_signature_check", True):
            val_warnings, _ = check_column_signatures(blob_client, kf, rule, conn, container_name)
            if val_warnings:
                cid = str(rule["carrier_id"])
                carrier_mappings = all_mappings[all_mappings["carrier_id"].astype(str) == cid]
                mapped_raw_cols = set(carrier_mappings["mapping"].dropna().str.lower().str.strip())
                drifted_cols = set(w["column"] for w in val_warnings)
                critical_drifted = drifted_cols & mapped_raw_cols

                if critical_drifted:
                    reason = f"Value drift: mapped columns changed character — {critical_drifted}"
                    if not test_mode:
                        deactivate_carrier(conn, rule, reason)
                    else:
                        print(f"    ⏭️  [TEST] Would deactivate carrier")
                    value_drift_carriers.append({
                        "carrier_name": rule["carrier_name"], "type": "critical",
                        "columns": val_warnings, "action": "Deactivated"
                    })
                    deactivated.append({"carrier_name": rule["carrier_name"], "reason": reason})
                    continue
                else:
                    value_drift_carriers.append({
                        "carrier_name": rule["carrier_name"], "type": "soft",
                        "columns": val_warnings, "action": "Auto-accepted (unmapped columns)"
                    })

        var_details = None
        if FEATURES.get("variance_check", True):
            var_ok, _, var_details = check_row_variance(blob_client, kf, rule, conn, container_name)
            if not var_ok:
                reason = f"Row variance (drop): {var_details['variance_pct']}% ({var_details['previous']:,} → {var_details['current']:,})"
                if not test_mode:
                    deactivate_carrier(conn, rule, reason)
                else:
                    print(f"    ⏭️  [TEST] Would deactivate carrier")
                row_variance_carriers.append({
                    "carrier_name": rule["carrier_name"],
                    "previous": var_details["previous"],
                    "current": var_details["current"],
                    "variance_pct": var_details["variance_pct"],
                    "action": "Deactivated"
                })
                deactivated.append({"carrier_name": rule["carrier_name"], "reason": reason})
                continue
            elif var_details and var_details.get("large_increase"):
                # Large row increase — informational only; carrier stays active and processes.
                row_increase_carriers.append({
                    "carrier_name": rule["carrier_name"],
                    "previous": var_details["previous"],
                    "current": var_details["current"],
                    "variance_pct": var_details["variance_pct"],
                    "action": "Notify only (still processed)"
                })

        cid = str(rule["carrier_id"])
        cm = all_mappings[all_mappings["carrier_id"].astype(str) == cid]
        if cm.empty: print(f"    ⚠️  No mappings — skip"); continue
        task = {"rule": rule, "mappings": cm, "files": [kf["blob_path"]], "row_count": var_details["current"] if var_details else None,
                "previous_row_count": var_details.get("previous") if var_details else None,
                "variance_pct": var_details.get("variance_pct") if var_details else None}
        # File override: pre-read local file so processor skips blob download
        if file_override and kf.get("_local"):
            pre_df = read_local_file(kf["blob_path"], rule)
            if pre_df is not None:
                task["pre_read_df"] = pre_df
            else:
                print(f"    ⚠️  Failed to read local file — skip"); continue
        valid_tasks.append(task)

    # Step 5: Process
    all_metrics, uploaded, email_attachments = [], {}, []
    if valid_tasks:
        print(f"\n── STEP 5: Process {len(valid_tasks)} carrier(s) ──")
        conn.close()
        temp_dir = tempfile.mkdtemp(prefix="acu_")
        # Job tracking (per carrier per file). Test mode -> local CSV; prod -> DB.
        job_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"acu_job_history_{run_date_str}{suffix}_{run_id}.csv") if test_mode else None
        def _worker(task):
            tc = get_postgres_connection()
            _jcid = str(task["rule"]["carrier_id"])
            _jfile = os.path.basename(task["files"][0]) if task.get("files") else ""
            # report_month = this carrier's own file date (same source as its
            # wpo.acu_contract_updates.report_date), scan_date fallback.
            _jmonth = scan_date.strftime("%Y-%m-%d")
            _mm = re.search(r"(\d{8})\.\w+$", _jfile)
            if _mm:
                try:
                    _jmonth = datetime.strptime(_mm.group(1), "%m%d%Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass
            job_id = None
            if track_jobs:
                job_id = job_start(tc, "ACU", _jcid, _jfile, report_month=_jmonth,
                                   test_mode=test_mode, local_csv_path=job_csv)
            try:
                pre_df = task.get("pre_read_df")
                result = process_carrier(blob_client, tc, task["rule"], task["mappings"],
                                         task["files"], temp_dir, run_date_str, container_name,
                                         pre_read_df=pre_df)
                if result["status"] in ("success", "threshold_exceeded") and task["row_count"]:
                    if not test_mode:
                        update_row_count(tc, task["rule"], task["row_count"])
                _status = "FAILED" if result.get("status") == "error" else "SUCCESS"
                if track_jobs:
                    job_finish(tc, "ACU", _jcid, job_id, _status, file_name=_jfile,
                               report_month=_jmonth, note=result.get("status"),
                               test_mode=test_mode, local_csv_path=job_csv)
                return result
            except Exception:
                if track_jobs:
                    job_finish(tc, "ACU", _jcid, job_id, "FAILED", file_name=_jfile,
                               report_month=_jmonth, note="worker exception",
                               test_mode=test_mode, local_csv_path=job_csv)
                raise
            finally: tc.close()

        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = {executor.submit(_worker, t): t for t in valid_tasks}
            for future in as_completed(futures):
                task = futures[future]
                name = task["rule"]["carrier_name"]
                try:
                    result = future.result()
                    result["previous_row_count"] = task.get("previous_row_count")
                    result["variance_pct"] = task.get("variance_pct")
                    result["teams_channel_email"] = str(task["rule"].get("teams_channel_email", "")).strip()
                    all_metrics.append(result)
                except Exception as e:
                    import traceback
                    print(f"❌ {name}: {e}")
                    print(traceback.format_exc())
                    all_metrics.append({"carrier_name": name, "carrier_id": "", "total_rows": 0, "results_count": 0, "exceptions_count": 0, "exception_rate": 0, "missing_count": 0, "exception_categories": {"error": 1}, "status": "error", "errors": [str(e)], "results_path": None, "exceptions_path": None, "missing_path": None, "contracts_loaded": 0})

        filenames = {"results": f"acu_results_{run_date_str}{suffix}_{run_id}.csv", "exceptions": f"acu_exceptions_{run_date_str}{suffix}_{run_id}.csv", "missing": f"acu_missing_agents_{run_date_str}{suffix}_{run_id}.csv"}

        # Dedup metrics — same carrier from multiple file dates should appear once (keep latest by row count)
        seen = {}
        for m in all_metrics:
            cn = m["carrier_name"]
            if pd.isna(cn) if isinstance(cn, float) else False:
                continue
            if cn not in seen or m.get("total_rows", 0) >= seen[cn].get("total_rows", 0):
                seen[cn] = m
        if len(seen) < len(all_metrics):
            print(f"  📋 Deduped metrics: {len(all_metrics)} → {len(seen)} (multiple file dates)")
            all_metrics = list(seen.values())

        cr, ce, cm = merge_outputs(all_metrics, filenames, temp_dir)
        if file_override:
            # Copy results to local override directory
            import shutil
            local_out = os.path.join(FILE_OVERRIDE_PATH, "ACU")
            for src, label in [(cr, "results"), (ce, "exceptions"), (cm, "missing")]:
                if src and os.path.exists(src):
                    dst = os.path.join(local_out, os.path.basename(src))
                    shutil.copy2(src, dst)
                    print(f"  📁 {label} → {dst}")
            uploaded = {}
        else:
            uploaded = upload_outputs(blob_client, cr, ce, cm, scan_date, container_name)

        # Step 5a: Build acu_contract_updates + ops_inbound_file_log, write to DB + CSV
        db_write_ok = False
        try:
            db_filenames = {
                "contract_updates": f"acu_contract_updates_{run_date_str}{suffix}_{run_id}.csv",
                "file_log": f"ops_inbound_file_log_{run_date_str}{suffix}_{run_id}.csv",
            }
            cu_path, log_path = build_db_outputs(
                all_metrics, valid_tasks, rules_df,
                cr, ce, run_date_str, scan_date, temp_dir, db_filenames,
                skip_inbound_log=from_ready_queue,
            )
            # Upload CSVs to blob as backup (skip when file_override)
            if not file_override:
                if cu_path:
                    upload_outputs_single(blob_client, cu_path, scan_date, container_name)
                if log_path:
                    upload_outputs_single(blob_client, log_path, scan_date, container_name, subfolder="logs/")
            else:
                local_out = os.path.join(FILE_OVERRIDE_PATH, "ACU")
                for src in [cu_path, log_path]:
                    if src and os.path.exists(src):
                        import shutil
                        shutil.copy2(src, os.path.join(local_out, os.path.basename(src)))

            # Write to DB tables
            if not test_mode:
                if cu_path and os.path.exists(cu_path):
                    write_contract_updates_to_db(cu_path)
                if log_path and os.path.exists(log_path):
                    write_logs_to_db(log_path)
            else:
                print(f"    ⏭️  [TEST] DB writes skipped (CSVs still built + uploaded)")
            db_write_ok = True
        except Exception as e:
            import traceback
            print(f"  ⚠️  DB output step failed: {e}")
            print(traceback.format_exc())

        # Step 5b: Archive processed files — only if DB write succeeded
        if not test_mode and (FEATURES.get("file_archiving", True) or force_archive):
            if db_write_ok:
                print(f"\n── STEP 5b: Archive ──")
                archive_processed_files(blob_client, all_metrics, valid_tasks, container_name)
            else:
                print(f"\n── STEP 5b: Archive SKIPPED (DB write failed — files preserved for retry) ──")
        else:
            reason = "test mode" if test_mode else "DISABLED"
            print(f"\n── STEP 5b: Archive SKIPPED ({reason}) ──")

        # Build email attachments — zip exceptions and missing agents separately
        email_attachments = []
        if FEATURES.get("email_attachments", True):
            for path, label in [(ce, "exceptions"), (cm, "missing_agents")]:
                if not path or not os.path.exists(path) or os.path.getsize(path) <= 100:
                    continue
                try:
                    import base64, zipfile, io
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.write(path, os.path.basename(path))
                    zip_bytes = zip_buffer.getvalue()
                    zip_name = f"acu_{label}_{run_date_str}{suffix}_{run_id}.zip"
                    if len(zip_bytes) < 9_500_000:
                        email_attachments.append({
                            "name": zip_name,
                            "contentType": "application/zip",
                            "contentInBase64": base64.b64encode(zip_bytes).decode("utf-8"),
                        })
                        print(f"  Attachment: {zip_name} ({len(zip_bytes) / 1024 / 1024:.1f} MB)")
                    else:
                        print(f"  [warn] {label} zip too large ({len(zip_bytes) / 1024 / 1024:.1f} MB) — skipping, available in blob")
                except Exception as e:
                    print(f"  [warn] Could not build {label} zip: {e}")

        try: shutil.rmtree(temp_dir)
        except: pass
        conn = get_postgres_connection()

    # Step 6: AI report
    ai_text = ""
    total_rows = sum(m["total_rows"] for m in all_metrics)
    total_exc = sum(m["exceptions_count"] for m in all_metrics)
    if total_rows > 0 and FEATURES.get("ai_report", True):
        try:
            report = generate_run_report(all_metrics, scan_date.strftime("%Y-%m-%d"))
            ai_text = report.summary
            if report.success:
                print(f"  [ai] Analysis generated ({len(ai_text)} chars)")
            else:
                print(f"  [ai] Using rule-based fallback ({len(ai_text)} chars)")
        except Exception as e:
            print(f"  [ai] Report generation failed: {e}")
    elif total_rows > 0:
        print(f"\n-- STEP 6: AI report (DISABLED) --")

    # Step 7: Notify
    print(f"\n-- STEP 7: Notify --")
    pending_mappings = get_pending_mappings(conn, "ACU")
    if pending_mappings:
        print(f"  {len(pending_mappings)} carrier(s) awaiting mapping review")
    _has_att = bool(email_attachments)
    summary = build_notification(all_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att,
                                  schema_drift_carriers=schema_drift_carriers, row_variance_carriers=row_variance_carriers, value_drift_carriers=value_drift_carriers, row_increase_carriers=row_increase_carriers)
    print(f"\n{summary}")

    if FEATURES.get("notifications", True):
        summary_html = build_notification_html(all_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att,
                                                schema_drift_carriers=schema_drift_carriers, row_variance_carriers=row_variance_carriers, value_drift_carriers=value_drift_carriers, row_increase_carriers=row_increase_carriers)
        rate = round(total_exc / total_rows * 100, 1) if total_rows > 0 else 0
        has_errors = any(m["status"] == "error" for m in all_metrics)
        has_value_change = any(m["status"] == "value_change" for m in all_metrics)
        has_critical_drift = any(v.get("type") == "critical" for v in value_drift_carriers)
        if has_errors:
            subj = f"ACU{mode} - Errors - {run_date_str}"
        elif has_value_change:
            subj = f"ACU{mode} - Value Map Mismatch - {run_date_str}"
        elif has_critical_drift:
            subj = f"ACU{mode} - Value Drift (deactivated) - {run_date_str}"
        elif new_carriers:
            subj = f"ACU{mode} - New Carrier(s) - {run_date_str}"
        elif deactivated:
            subj = f"ACU{mode} - Deactivated - {run_date_str}"
        elif rate >= 20:
            subj = f"ACU{mode} - High Exceptions ({rate}%) - {run_date_str}"
        elif rate >= 10:
            subj = f"ACU{mode} - Elevated Exceptions ({rate}%) - {run_date_str}"
        else:
            subj = f"ACU{mode} Complete - {run_date_str}"
        if email_attachments:
            print(f"  {len(email_attachments)} file(s) attached ({', '.join(a['name'] for a in email_attachments)})")

        # Build per-carrier channel groups from rules matrix
        channel_groups = {}
        for m in all_metrics:
            ch = str(m.get("teams_channel_email", "")).strip()
            if ch and ch not in ("", "nan", "None", "none", "NA"):
                channel_groups.setdefault(ch, []).append(m)

        if channel_groups:
            # Send one email per unique channel with full summary
            for channel, channel_metrics in channel_groups.items():
                ch_summary = build_notification(channel_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att,
                                                schema_drift_carriers=schema_drift_carriers, row_variance_carriers=row_variance_carriers, value_drift_carriers=value_drift_carriers, row_increase_carriers=row_increase_carriers)
                ch_html = build_notification_html(channel_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att,
                                                  schema_drift_carriers=schema_drift_carriers, row_variance_carriers=row_variance_carriers, value_drift_carriers=value_drift_carriers, row_increase_carriers=row_increase_carriers)
                send_teams_notification(subject=subj, body=ch_summary, body_html=ch_html,
                                        channel_email=channel,
                                        attachments=email_attachments if email_attachments else None)
                print(f"  📨 Notification → {channel} ({len(channel_metrics)} carriers)")
        else:
            # No channels configured — fall back to default so we don't lose notifications
            print(f"  ⚠️  No teams_channel_email in rules matrix — using default channel")
            send_teams_notification(subject=subj, body=summary, body_html=summary_html,
                                    attachments=email_attachments if email_attachments else None)
    else:
        print(f"  Notifications DISABLED — email not sent")
    conn.close()
    print(f"\n{'='*60}\n  ACU PIPELINE COMPLETE{mode}\n{'='*60}\n")
    return {"metrics": all_metrics, "new_carriers": new_carriers, "deactivated": deactivated, "uploaded": uploaded, "ok": bool(all_metrics)}


def run_ready_acu_jobs(test_mode=False):
    """Process Ready rows from wpo.ops_inbound_file_log (cron entry point)."""
    if not FEATURES.get("job_tracking", True):
        print("job_tracking disabled — enable in config for --ready")
        return

    conn = get_postgres_connection()
    rows = fetch_ready_inbound_jobs(conn, "ACU")

    if not rows:
        print("No Ready ACU jobs.")
        conn.close()
        return

    print(f"Found {len(rows)} Ready ACU job(s)")

    for row in rows:
        filename = (row.get("file_name") or "").strip()
        filename = os.path.basename(filename) or filename
        carrier_id = str(row.get("carrier_id") or "")
        report_month = row.get("file_report_month") or ""
        source_inbound_pk_id = row.get("pk_id")
        scan_d = _parse_report_date(report_month)

        print(f"\n── Ready ACU: {filename} ({report_month}) ──")

        start_info = start_inbound_job(
            conn=conn,
            inbound_row=row,
            process_type="ACU",
            test_mode=test_mode,
            local_csv_path=None,
            job_type="ACU",
        )

        job_id = start_info.get("job_id")
        processing_inbound_pk_id = start_info.get("processing_inbound_pk_id")

        if not job_id or not processing_inbound_pk_id:
            print(f"  ⚠️  Could not initialize Ready ACU job for pk_id={source_inbound_pk_id}")
            continue

        try:
            result = run_acu_pipeline(
                scan_date=scan_d,
                test_mode=test_mode,
                file_filter=filename,
                track_jobs=False,
                force_archive=True,
                from_ready_queue=True,
            )

            ok = _pipeline_succeeded(result)

            job_finish(
                conn=conn,
                process_type="ACU",
                carrier_id=carrier_id,
                job_id=job_id,
                status="SUCCESS" if ok else "FAILED",
                file_name=filename,
                report_month=report_month,
                inbound_source_pk_id=source_inbound_pk_id,
                inbound_processing_pk_id=processing_inbound_pk_id,
                inbound_metrics=result,
                note="Processing Completed" if ok else "Processing Failed",
                test_mode=test_mode,
            )

        except Exception as e:
            print(f"  ⚠️  Ready ACU job failed: {e}")

            job_finish(
                conn=conn,
                process_type="ACU",
                carrier_id=carrier_id,
                job_id=job_id,
                status="FAILED",
                file_name=filename,
                report_month=report_month,
                inbound_source_pk_id=source_inbound_pk_id,
                inbound_processing_pk_id=processing_inbound_pk_id,
                note=str(e),
                test_mode=test_mode,
            )

    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ACU pipeline")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--date", type=str)
    parser.add_argument("--ready", action="store_true", help="Process Ready RPA queue")
    args = parser.parse_args()
    if args.ready:
        run_ready_acu_jobs(test_mode=args.test)
    else:
        scan = date.today()
        if args.date:
            scan = datetime.strptime(args.date, "%Y-%m-%d").date()
        run_acu_pipeline(scan_date=scan, test_mode=args.test)