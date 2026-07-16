# ==========================================================
#  bob_runner.py
# ==========================================================
"""
BOB pipeline orchestrator. Same patterns as acu_runner.py:
  Step 1: Scan blob storage for files
  Step 2: Classify files against rules matrix
  Step 3: Schema drift + row variance checks
  Step 4: Process carriers (parallel)
  Step 5: Write results to bob_carrier_memberships (COPY)
  Step 6: Archive processed files
  Step 7: Notifications + AI report

Key differences from ACU:
  - Blob path: raw/production_report/{YYYY MM Mon}/
  - Target table: wpo.bob_carrier_memberships
  - Report level routing: SUMMARY skips agent matching
  - Simpler exception model: E13/E14 only
  - No rollup, transitions, or missing agents
  - HCSC reader returns sub-carriers (expand pattern)
"""

import io, os, sys, json, csv, hashlib, tempfile, uuid
import pandas as pd
import numpy as np
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (FEATURES, MAX_THREADS, EXCEPTION_THRESHOLD_PCT,
                    ROW_VARIANCE_CRITICAL_PCT, load_bob_exception_codes,
                    ENTITY_ID, SUB_ENTITY_ID, FILE_OVERRIDE, FILE_OVERRIDE_PATH)
from bob_processor import process_bob_carrier
from bob_readers import get_bob_reader
from acu_runner import (
    # Reuse shared utilities from ACU runner
    scan_blob_files, classify_files, filter_one_file,
    check_schema, check_row_variance, deactivate_carrier,
    check_column_signatures,
    _submit_to_ai_mapping, _safe_carrier_id, _load_dated_json,
    _save_dated_json, _get_latest_entry,
    load_rules_matrix, load_mapping_matrix,
    AI_MAPPING_TABLE,
    _parse_report_date,
    get_pending_mappings,
)
from ai_carrier_mapper import read_file_headers
from utils.azure_blob_utils import authenticate_blob_storage, DEFAULT_CONTAINER
from utils.db_utils import get_postgres_connection
from utils.notification import build_notification, build_notification_html
from utils.email_utils import send_teams_notification, DEFAULT_TEAMS_CHANNEL
from job_tracking import job_start, job_finish, fetch_ready_inbound_jobs, start_inbound_job

RULES_TABLE = "ops_srv.ops_acu_bob_rules_matrix"
MAPPING_TABLE = "ops_srv.ops_acu_bob_load_matrix"
BOB_TABLE = "wpo.bob_carrier_memberships"
PROCESS_TYPE = "BOB"

# Blob paths (all on agilityops container)
BOB_BLOB_PREFIX = "raw/production_report/"
BOB_RESULTS_PREFIX = "raw/production_report/results/"


# ── CANONICAL OUTPUT SCHEMA ──
# Ordered column list of wpo.bob_carrier_memberships (the live table).
# Used to align output in BOTH the DB COPY and the test-mode CSVs, so the CSV is a
# faithful mirror of what lands in the table. The DB path still re-queries
# information_schema at runtime (authoritative); this constant is the offline
# source of truth for test mode, where there is no DB connection.
# NOTE: keep this in sync if the table schema changes.
BOB_TABLE_COLUMNS = [
    "aptc_subsidy", "mem_status_uhc", "mem_zip_plus4", "mem_rating_zip", "solicitor_npn_10",
    "sol_status10", "report_agent", "renew_type", "renew_plan", "renew_flag", "renew_date",
    "policy_premium_renew", "policy_premium", "policy_due_date", "plan_status", "payment_status",
    "payee", "parent_agent", "nma_status80", "nma_status70", "nma_status60", "nma_npn_80",
    "nma_npn_70", "nma_npn_60", "mga_status40", "mga_npn_40", "mem_ssn", "mem_second_zip",
    "mem_second_state", "mem_second_phone_num", "mem_second_county", "mem_second_city",
    "mem_second_address2", "mem_second_address1", "mem_premium", "mem_policy_premium",
    "mem_phone_num", "mem_gender", "mem_enroll_type", "mem_enroll_date", "market_type", "mar_num",
    "link_start_date", "ga_status30", "ga_npn_30", "fmo_status50", "fmo_npn_50", "ffm_id",
    "ffm_app_num", "exch_on_off", "exch_mem_id", "emp_subsidy_type", "emp_subsidy_amt",
    "emp_grp_name", "coverage_status", "cov_type", "cov_grp", "cov_date", "comm_eligible",
    "comm_eff_date", "comm_assign_payee", "bill_status", "aptc_subsidy_start_date",
    "aptc_subsidy_renew", "aptc_subsidy_toggle", "aptc_mar_num", "app_recvd_date", "app_num",
    "amt_due", "aging_status", "agent_wid20", "agent_status20", "agent_start_date", "agent_npn_20",
    "agent_name20", "agent_end_date", "agent_email", "account_creation_status", "raw_file_name",
    "process_date", "load_date", "report_date", "mem_paid_thru_date", "mem_app_date",
    "mem_cov_end_date", "mem_effective_date", "mem_count", "contract_count", "mem_plan_year",
    "product_type", "mem_email", "mem_zip", "mem_city", "mem_address2", "mem_address1",
    "mem_status", "mem_county", "mem_state", "mem_age", "mem_dob", "mem_lname", "mem_fname",
    "mem_full_name", "mem_id", "mem_policy_num", "agent_recruiter", "agent_top_upline_npn",
    "agent_direct_upline_npn", "agent_contract_id", "agent_fullname", "agent_fname", "agent_lname",
    "carrier_agent_name", "agent_writing_num", "agent_npn", "mem_market", "carrier_name",
    "carrier_id", "txn_id", "txn_status", "pk_id",
    # Added via ALTER TABLE wpo.bob_carrier_memberships (appended after pk_id):
    #   is_subscriber  <- HCSC "Subscriber Ind"
    #   mem_plan_name  <- Health First plan name
    # DB write picks these up automatically from information_schema once the DDL
    # runs; they're listed here so the file/CSV output includes them too.
    "is_subscriber", "mem_plan_name",
]

# Load-matrix targets whose names differ from the real table columns. The matrix
# intentionally keeps its own spelling; THIS DICT IS THE SINGLE PLACE that
# translation to the live column names happens. Applied coalesce-safe (see
# _apply_column_alias) so a frame carrying both the source and target names merges
# rather than colliding.
COLUMN_ALIAS = {
    "agent_full_name":   "agent_fullname",
    "mem_top_upline":    "agent_top_upline_npn",
    "mem_direct_upline": "agent_direct_upline_npn",
}

# Columns the database generates itself — never written by the pipeline.
# pk_id is uuid DEFAULT gen_random_uuid(); excluding it from the COPY lets the
# default fire (otherwise we would insert NULL and override the default).
DB_MANAGED_COLUMNS = {"pk_id"}


def _apply_column_alias(df):
    """Rename load-matrix target names to the real table columns, coalesce-safe.

    If both the alias source and target exist on the frame, fill the target's
    blank cells from the source, then drop the source — rather than creating a
    duplicate column.
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    for src, dst in COLUMN_ALIAS.items():
        if src not in df.columns:
            continue
        if dst in df.columns:
            dst_blank = df[dst].isna() | (df[dst].astype(str).str.strip() == "")
            df.loc[dst_blank, dst] = df.loc[dst_blank, src]
            df.drop(columns=[src], inplace=True)
        else:
            df.rename(columns={src: dst}, inplace=True)
    return df


def prepare_output_frame(df, table_columns):
    """Shape a results DataFrame for writing to bob_carrier_memberships.

    Single source of truth used by BOTH the DB COPY and the test-mode CSVs so the
    CSV mirrors the table exactly:
      1. Alias load-matrix target names to real table columns (coalesce-safe).
      2. Drop DB-managed columns (pk_id) so the table default fires.
      3. Align to the table's column set/order — add missing as empty, drop extras.
      4. Replace NaN/None with empty string.

    `table_columns` is the live information_schema list (DB path) or
    BOB_TABLE_COLUMNS (test mode / offline).
    """
    out_cols = [c for c in table_columns if c not in DB_MANAGED_COLUMNS]
    if df is None or df.empty:
        return pd.DataFrame(columns=out_cols)
    df = _apply_column_alias(df)
    for col in out_cols:
        if col not in df.columns:
            df[col] = ""
    return df[out_cols].copy().fillna("")


# ── BLOB SCANNING ──

def get_report_month_path(run_date=None):
    """
    Build the blob prefix for the report month.
    Format: raw/production_report/{YYYY MM Mon}/
    Same date logic as ACU.
    """
    if run_date:
        dt = datetime.strptime(run_date, "%Y-%m-%d") if isinstance(run_date, str) else run_date
    else:
        dt = datetime.today()
    return f"{BOB_BLOB_PREFIX}{dt.strftime('%Y %m %b')}/"


def scan_bob_files(blob_service_client, run_date=None, container_name=DEFAULT_CONTAINER):
    """Scan blob storage for BOB files in the report month folder."""
    prefix = get_report_month_path(run_date)
    print(f"  📁 Scanning: {container_name}/{prefix}")

    container_client = blob_service_client.get_container_client(container_name)
    files = []
    for blob in container_client.list_blobs(name_starts_with=prefix):
        if blob.name.endswith(("/", ".zip", ".log")):
            continue
        # Only top-level files — skip archive/, test/, any subfolder
        relative = blob.name[len(prefix):]
        if "/" in relative:
            continue
        fname = os.path.basename(blob.name)
        if "." not in fname:
            continue
        files.append({
            "blob_path": blob.name,
            "file_name": fname,
            "size": blob.size,
            "last_modified": blob.last_modified,
        })

    print(f"  📄 Found {len(files)} files")
    return files


def scan_local_bob_files():
    """Scan local override directory for BOB files."""
    local_dir = os.path.join(FILE_OVERRIDE_PATH, "BOB")
    files = []
    if not os.path.isdir(local_dir):
        print(f"  ⚠️  Local override dir not found: {local_dir}")
        return files
    for fname in os.listdir(local_dir):
        fpath = os.path.join(local_dir, fname)
        if os.path.isfile(fpath) and "." in fname:
            files.append({
                "blob_path": fpath,
                "file_name": fname,
                "size": os.path.getsize(fpath),
                "_local": True,
            })
    print(f"  📁 Found {len(files)} local file(s) in {local_dir}")
    return files


def read_local_bob_file(file_path, rule):
    """Read a local BOB file into a DataFrame."""
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


# ── DB WRITE (COPY bulk) ──

def write_to_db(conn, df, table_name=BOB_TABLE):
    """
    Bulk write DataFrame to bob_carrier_memberships using COPY.
    Same pattern as ACU — avoids row-by-row executemany hanging.
    """
    if df.empty:
        return 0

    import io as _io
    cur = conn.cursor()

    # Get table columns (live schema is authoritative for the DB write)
    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema='wpo' AND table_name='bob_carrier_memberships' ORDER BY ordinal_position")
    db_columns = [row[0] for row in cur.fetchall()]

    # Alias load-matrix names → real columns, drop pk_id (so gen_random_uuid()
    # fires), and align/order to the live table — shared with the test-mode CSV.
    df_write = prepare_output_frame(df, db_columns)
    if df_write.empty or len(df_write.columns) == 0:
        cur.close()
        return 0

    # Write via COPY
    buffer = _io.StringIO()
    df_write.to_csv(buffer, index=False, header=False, sep="\t", quoting=csv.QUOTE_MINIMAL)
    buffer.seek(0)

    cols_str = ", ".join(df_write.columns)
    copy_sql = f"COPY {table_name} ({cols_str}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')"

    try:
        cur.copy_expert(copy_sql, buffer)
        conn.commit()
        print(f"    💾 Wrote {len(df_write)} rows to {table_name}")
    except Exception as e:
        conn.rollback()
        print(f"    🚨 COPY failed: {e}")
        raise
    finally:
        cur.close()

    return len(df_write)


def write_to_file(df, output_dir, carrier_name):
    """Test mode: write results to CSV instead of DB.

    Mirrors the table exactly (alias applied, pk_id excluded, aligned/ordered to
    BOB_TABLE_COLUMNS) so the CSV is a faithful preview of the real DB write.
    """
    if df.empty:
        return

    safe_name = carrier_name.replace(" ", "_").replace("/", "_")
    path = os.path.join(output_dir, f"bob_{safe_name}_output.csv")
    df_out = prepare_output_frame(df, BOB_TABLE_COLUMNS)
    df_out.to_csv(path, index=False)
    print(f"    📄 Test mode: wrote {len(df_out)} rows to {path}")
    return path


# ── PURGE OLD DATA ──

def purge_report_date(conn, report_date, carrier_id=None):
    """Delete existing data for a report_date (and optionally carrier_id) before reload."""
    cur = conn.cursor()
    try:
        if carrier_id:
            cur.execute(f"DELETE FROM {BOB_TABLE} WHERE report_date = %s AND carrier_id = %s",
                        (report_date, carrier_id))
        else:
            cur.execute(f"DELETE FROM {BOB_TABLE} WHERE report_date = %s", (report_date,))
        deleted = cur.rowcount
        conn.commit()
        print(f"    🗑️  Purged {deleted} existing rows for report_date={report_date}" +
              (f", carrier_id={carrier_id}" if carrier_id else ""))
        return deleted
    except Exception as e:
        conn.rollback()
        print(f"    ⚠️  Purge failed: {e}")
        return 0
    finally:
        cur.close()


# ── MERGE OUTPUTS ──

def merge_bob_outputs(all_results, filenames, temp_dir):
    """Merge all carrier results and exceptions into combined CSVs."""
    results_list, exceptions_list = [], []

    for result in all_results:
        if result["status"] in ("error", "no_data"):
            continue
        rdf = result.get("results_df", pd.DataFrame())
        edf = result.get("exceptions_df", pd.DataFrame())
        if not rdf.empty:
            results_list.append(rdf)
        if not edf.empty:
            exceptions_list.append(edf)

    combined_results = pd.concat(results_list, ignore_index=True) if results_list else pd.DataFrame()
    combined_exceptions = pd.concat(exceptions_list, ignore_index=True) if exceptions_list else pd.DataFrame()

    results_path = os.path.join(temp_dir, filenames["results"])
    exceptions_path = os.path.join(temp_dir, filenames["exceptions"])

    if not combined_results.empty:
        # Mirror the live table: alias names, drop pk_id, align/order to schema.
        # (Exceptions use a different schema, so they are written as-is.)
        combined_results = prepare_output_frame(combined_results, BOB_TABLE_COLUMNS)
        combined_results.to_csv(results_path, index=False)
    if not combined_exceptions.empty:
        combined_exceptions.to_csv(exceptions_path, index=False)

    print(f"\n  📊 Combined: {len(combined_results):,} results | {len(combined_exceptions):,} exceptions")
    return results_path, exceptions_path


# ── UPLOAD OUTPUTS TO BLOB ──

def upload_bob_outputs(blob_client, results_path, exceptions_path, run_date,
                       container_name=DEFAULT_CONTAINER):
    """
    Upload combined results and exceptions CSVs to blob storage.
    Path: raw/production_report/results/{YYYY MM Mon}/
    Exceptions: raw/production_report/results/{YYYY MM Mon}/exceptions/
    """
    dt = datetime.strptime(run_date, "%Y-%m-%d") if isinstance(run_date, str) else run_date
    month_folder = dt.strftime("%Y %m %b")
    results_folder = f"{BOB_RESULTS_PREFIX}{month_folder}/"
    exceptions_folder = f"{results_folder}exceptions/"

    container_client = blob_client.get_container_client(container_name)
    paths = {}

    for label, local_path, blob_folder in [
        ("results", results_path, results_folder),
        ("exceptions", exceptions_path, exceptions_folder),
    ]:
        if local_path and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            blob_path = f"{blob_folder}{os.path.basename(local_path)}"
            with open(local_path, "rb") as f:
                container_client.get_blob_client(blob_path).upload_blob(f, overwrite=True)
            paths[label] = blob_path
            print(f"    ☁️  Uploaded {label}: {blob_path}")

    return paths


# ── ARCHIVE PROCESSED FILES ──

def archive_bob_files(blob_client, all_results, valid_tasks, container_name=DEFAULT_CONTAINER):
    """
    Move successfully processed raw files to archive/ subfolder.
    Path: raw/production_report/{YYYY MM Mon}/archive/
    """
    container_client = blob_client.get_container_client(container_name)
    success_carriers = {r["carrier_name"] for r in all_results if r["status"] == "success"}
    archived = 0

    for task in valid_tasks:
        rule = task["rule"]
        carrier_name = rule["carrier_name"] if isinstance(rule, dict) else rule.get("carrier_name", "")
        if carrier_name not in success_carriers:
            continue

        for blob_path in task.get("files", []):
            file_name = os.path.basename(blob_path)
            folder = os.path.dirname(blob_path)
            archive_path = f"{folder}/archive/{file_name}"

            try:
                source_blob = container_client.get_blob_client(blob_path)
                data = source_blob.download_blob().readall()

                archive_blob = container_client.get_blob_client(archive_path)
                archive_blob.upload_blob(data, overwrite=True)

                source_blob.delete_blob()
                archived += 1
            except Exception as e:
                print(f"    ⚠️  Archive failed for {file_name}: {e}")

    print(f"    📦 Archived {archived} files")
    return archived


# ── MAIN PIPELINE ──

def report_date_from_files(files, fallback):
    """Parse report_date from a raw file name (e.g. raw_bob_devoted_mdc_05272026.csv
    -> 2026-05-27). Mirrors the ACU runner. Files are grabbed from the current-month
    folder, but report_date comes from the file itself — NOT the run/scan date.
    Falls back to `fallback` (the run_date) only if no date token is found."""
    for f in (files or []):
        m = re.search(r"(\d{8})\.\w+$", os.path.basename(str(f)))
        if m:
            try:
                return datetime.strptime(m.group(1), "%m%d%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
    return fallback


def run_bob_pipeline(run_date=None, test_mode=False, container_name=DEFAULT_CONTAINER,
                     carrier_filter=None, skip_archive=False, file_filter=None,
                     track_jobs=True, force_archive=False):
    """
    Main BOB pipeline entry point.

    Args:
        run_date: Report date (YYYY-MM-DD). Defaults to today.
        test_mode: If True, write to files instead of DB.
        container_name: Azure blob container.
        carrier_filter: Optional list of carrier names to process (for testing).
        skip_archive: Skip file archiving after processing.
    """
    start = datetime.now()
    run_date = run_date or datetime.today().strftime("%Y-%m-%d")
    test_mode = test_mode or FEATURES.get("test_mode", False)
    # Publish test_mode to FEATURES so shared DB-mutation helpers (imported
    # from acu_runner) become no-ops. Test mode = reads only, zero writes.
    FEATURES["test_mode"] = test_mode
    print(f"{'='*60}")
    print(f"  BOB Pipeline — {run_date} {'[TEST MODE]' if test_mode else ''}")
    print(f"{'='*60}")

    # ── Setup ──
    blob_client = authenticate_blob_storage()
    conn = get_postgres_connection()
    load_bob_exception_codes(conn)
    temp_dir = tempfile.mkdtemp(prefix="bob_")
    run_id = uuid.uuid4().hex[:8]

    # ── Step 1: Load rules ──
    print(f"\n── STEP 1: Load rules ──")
    # Load the FULL rule set (active + inactive). Classification and processing
    # use only the active subset, but we keep the inactive rows so a
    # deliberately deactivated carrier's file (active_flag='N') can be
    # recognized as known-but-off and skipped — never mis-flagged as an
    # unknown/new file and submitted to the AI mapper as a spurious
    # pending_review (e.g. Prominence, which we set inactive).
    all_rules_df = load_rules_matrix(conn, PROCESS_TYPE, active_only=False)
    all_rules_df["carrier_id"] = all_rules_df["carrier_id"].apply(_safe_carrier_id)
    rules_df = all_rules_df[
        all_rules_df["active_flag"].astype(str).str.strip().str.upper() == "Y"
    ].copy()
    print(f"  📋 Loaded {len(rules_df)} active BOB rules ({len(all_rules_df)} total incl. inactive)")

    if carrier_filter:
        rules_df = rules_df[rules_df["carrier_name"].isin(carrier_filter)]
        print(f"  🔍 Filtered to {len(rules_df)} carriers: {carrier_filter}")

    # ── Step 2: Scan files ──
    print(f"\n── STEP 2: Scan ──")
    file_override = FILE_OVERRIDE or FEATURES.get("file_override", False)
    if file_override:
        print(f"  📁 FILE OVERRIDE: reading from {FILE_OVERRIDE_PATH}/BOB")
        all_files = scan_local_bob_files()
    else:
        all_files = scan_bob_files(blob_client, run_date, container_name)

    if file_filter:
        all_files = filter_one_file(all_files, file_filter)

    if not all_files:
        print("  ⚠️  No files found — exiting")
        conn.close()
        return []

    # ── Step 3: Classify files ──
    print(f"\n── STEP 3: Classify files ──")
    known_files, unknown_files = classify_files(all_files, rules_df)
    print(f"  ✅ Known: {len(known_files)}, ❓ Unknown: {len(unknown_files)}")

    # Triage "unknown" files: some belong to deliberately-deactivated carriers
    # (active_flag='N'). They don't match any ACTIVE rule, so they land here —
    # but they are NOT new carriers and must not be submitted to the AI mapper.
    # Recognize them via the inactive rules' file patterns and skip quietly.
    # Note: shared-pattern workbooks (BCBS MI, Humana, SMA) already match an
    # active rule above and never reach this branch, so they're unaffected.
    inactive_rules = all_rules_df[
        all_rules_df["active_flag"].astype(str).str.strip().str.upper() != "Y"
    ]
    inactive_patterns = [
        (str(r["file_naming_pattern"]).strip().lower(), r["carrier_name"])
        for _, r in inactive_rules.iterrows()
        if str(r.get("file_naming_pattern", "")).strip().lower() not in ("", "na")
    ]
    if unknown_files and inactive_patterns:
        still_unknown, skipped_inactive = [], []
        for uf in unknown_files:
            fname = uf["file_name"].lower()
            hit = next(((p, c) for p, c in inactive_patterns if p in fname), None)
            if hit:
                skipped_inactive.append(uf)
                print(f"    ⏸️  {uf['file_name']} → {hit[1]} (inactive carrier — skipped, not flagged as unknown)")
            else:
                still_unknown.append(uf)
        unknown_files = still_unknown
        if skipped_inactive:
            print(f"  ⏸️  {len(skipped_inactive)} file(s) belong to inactive carriers — skipped (not sent to AI mapper)")

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
                  AND a.process_type = 'BOB'
                  AND EXISTS (
                      SELECT 1 FROM {RULES_TABLE} r
                      WHERE r.process_type = 'BOB'
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

    # Submit unknown files to AI carrier mapper
    if unknown_files and FEATURES.get("ai_carrier_mapper", True):
        for uf in unknown_files:
            print(f"    🤖 Submitting to AI mapper: {uf['file_name']}")
            try:
                headers = read_file_headers(blob_client, uf["blob_path"], container_name)
                _submit_to_ai_mapping(conn, {"carrier_name": uf["file_name"],
                                              "file_naming_pattern": uf["file_name"]},
                                       headers, "Unknown file — no matching rule")
            except Exception as e:
                print(f"    ⚠️  AI mapper submission failed: {e}")

    # Load mappings
    all_mappings = load_mapping_matrix(conn, PROCESS_TYPE)
    all_mappings["carrier_id"] = all_mappings["carrier_id"].apply(_safe_carrier_id)

    # ── Step 4: Schema + variance checks + expand multi-carrier readers ──
    print(f"\n── STEP 4: Pre-processing checks ──")
    valid_tasks = []
    schema_drift_carriers = []
    row_variance_carriers = []
    row_increase_carriers = []
    value_drift_carriers = []

    for kf in known_files:
        rule = kf["rule"]
        carrier_name = rule["carrier_name"]
        print(f"\n  📋 {carrier_name}:")

        reader_name = str(rule.get("custom_reader_name", "")).strip()

        # Multi-carrier readers (HCSC) — expand into sub-carriers
        if reader_name in ("read_hcsc_bob", "read_sma_bob"):
            print(f"    📖 Multi-carrier reader: {reader_name} — expanding...")
            reader_fn = get_bob_reader(reader_name)
            if reader_fn:
                sub_results = reader_fn(blob_client, kf["blob_path"], rule, None,
                                        container_name, rules_df, all_mappings)
                if isinstance(sub_results, list):
                    for sub_rule, sub_df in sub_results:
                        sub_cid = str(sub_rule.get("carrier_id", ""))
                        sub_cm = all_mappings[all_mappings["carrier_id"].astype(str) == sub_cid]
                        if sub_cm.empty:
                            sub_cm = all_mappings[
                                all_mappings["carrier_name"].str.contains("HCSC", case=False, na=False) &
                                (all_mappings["process_type"] == PROCESS_TYPE)
                            ]
                        valid_tasks.append({
                            "rule": sub_rule if isinstance(sub_rule, dict) else sub_rule.to_dict() if hasattr(sub_rule, 'to_dict') else sub_rule,
                            "mappings": sub_cm,
                            "files": [kf["blob_path"]],
                            "pre_read_df": sub_df,
                        })
                else:
                    # Single DataFrame returned
                    cid = str(rule["carrier_id"])
                    valid_tasks.append({
                        "rule": rule, "mappings": all_mappings[all_mappings["carrier_id"].astype(str) == cid],
                        "files": [kf["blob_path"]], "pre_read_df": sub_results,
                    })
            continue

        # Schema check
        if FEATURES.get("schema_check", True):
            schema_ok, drift = check_schema(blob_client, kf, rule, conn, container_name)
            if not schema_ok and drift:
                added = set(drift.get("added", []))
                removed = set(drift.get("removed", []))

                cid = str(rule["carrier_id"])
                carrier_mappings = all_mappings[all_mappings["carrier_id"].astype(str) == cid]
                mapped_cols = set(carrier_mappings["mapping"].dropna().str.lower().str.strip())

                critical_removed = removed & mapped_cols

                if critical_removed:
                    reason = f"Schema drift: lost mapped columns {critical_removed}"
                    deactivate_carrier(conn, rule, reason)
                    _submit_to_ai_mapping(conn, rule, drift["current_headers"], reason)
                    schema_drift_carriers.append({
                        "carrier_name": carrier_name, "type": "critical",
                        "removed": sorted(critical_removed), "added": sorted(added),
                        "action": "Deactivated + submitted to AI mapping"
                    })
                    continue
                else:
                    if added:
                        schema_drift_carriers.append({
                            "carrier_name": carrier_name, "type": "soft",
                            "new_columns": sorted(added), "action": "Auto-accepted"
                        })

        # Column value signature check — detect content drift run-over-run
        if FEATURES.get("value_signature_check", True):
            val_warnings, _ = check_column_signatures(blob_client, kf, rule, conn, container_name)
            if val_warnings:
                # Check if any drifted columns are mapped (used by the pipeline)
                cid = str(rule["carrier_id"])
                carrier_mappings = all_mappings[all_mappings["carrier_id"].astype(str) == cid]
                mapped_raw_cols = set(carrier_mappings["mapping"].dropna().str.lower().str.strip())
                drifted_cols = set(w["column"] for w in val_warnings)
                critical_drifted = drifted_cols & mapped_raw_cols

                if critical_drifted:
                    reason = f"Value drift: mapped columns changed character — {critical_drifted}"
                    deactivate_carrier(conn, rule, reason)
                    value_drift_carriers.append({
                        "carrier_name": carrier_name, "type": "critical",
                        "columns": val_warnings, "action": "Deactivated"
                    })
                    continue
                else:
                    value_drift_carriers.append({
                        "carrier_name": carrier_name, "type": "soft",
                        "columns": val_warnings, "action": "Auto-accepted (unmapped columns)"
                    })

        # Row variance check
        if FEATURES.get("variance_check", True):
            var_ok, _, var_details = check_row_variance(blob_client, kf, rule, conn, container_name)
            if not var_ok and var_details:
                reason = f"Row variance (drop): {var_details['variance_pct']}% ({var_details['previous']:,} → {var_details['current']:,})"
                deactivate_carrier(conn, rule, reason)
                row_variance_carriers.append({
                    "carrier_name": carrier_name, **var_details, "action": "Deactivated"
                })
                continue
            elif var_details and var_details.get("large_increase"):
                # Large row increase — informational only; carrier stays active and processes.
                row_increase_carriers.append({
                    "carrier_name": carrier_name, **var_details, "action": "Notify only (still processed)"
                })

        # Build task
        cid = str(rule["carrier_id"])
        carrier_mappings = all_mappings[all_mappings["carrier_id"].astype(str) == cid]

        # Group multiple files for the same carrier (multi-file)
        existing_task = next((t for t in valid_tasks
                              if str(t["rule"].get("carrier_id", "")) == cid
                              and t.get("pre_read_df") is None), None)
        if existing_task:
            existing_task["files"].append(kf["blob_path"])
        else:
            valid_tasks.append({
                "rule": rule if isinstance(rule, dict) else rule.to_dict(),
                "mappings": carrier_mappings,
                "files": [kf["blob_path"]],
            })

    print(f"\n  ✅ {len(valid_tasks)} carriers ready for processing")

    # ── Step 5: Process carriers ──
    print(f"\n── STEP 5: Process carriers ──")
    all_results = []

    def _process_task(task, task_conn=None):
        # Parallel workers pass their OWN connection (task_conn); the sequential
        # path passes none and uses the main `conn` (single-threaded, safe).
        tc = task_conn if task_conn is not None else conn
        rule = task["rule"]
        if not isinstance(rule, dict):
            rule = rule.to_dict() if hasattr(rule, 'to_dict') else dict(rule)
        # report_date comes from the FILE name (current-month folder, file's own date),
        # not the run/scan date. mem_age ref_date follows the same date.
        task_report_date = report_date_from_files(task["files"], run_date)
        return process_bob_carrier(
            blob_client, tc, rule, task["mappings"],
            task["files"], temp_dir, task_report_date,
            container_name=container_name,
            pre_read_df=task.get("pre_read_df"),
        )

    # Job tracking (per carrier per file). Test mode -> local CSV; prod -> DB.
    job_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"bob_job_history_{run_date}_{run_id}.csv") if test_mode else None

    def _job_meta(task):
        rule = task["rule"]
        rd = rule if isinstance(rule, dict) else (rule.to_dict() if hasattr(rule, "to_dict") else dict(rule))
        cid = _safe_carrier_id(rd.get("carrier_id", ""))
        files = task.get("files") or []
        fname = ", ".join(os.path.basename(f) for f in files) if files else ""
        # report_month is sourced from the same report_date written to
        # wpo.bob_carrier_memberships.report_date (the file's date), not run_date.
        rmonth = report_date_from_files(files, run_date)
        return cid, fname, rmonth

    if test_mode or len(valid_tasks) <= 2:
        # Sequential for test/debug
        for task in valid_tasks:
            _jcid, _jfile, _jmonth = _job_meta(task)
            job_id = None
            if track_jobs:
                job_id = job_start(conn, "BOB", _jcid, _jfile, report_month=_jmonth,
                                   test_mode=test_mode, local_csv_path=job_csv)
            try:
                result = _process_task(task)
                all_results.append(result)
                _status = "FAILED" if result.get("status") == "error" else "SUCCESS"
                if track_jobs:
                    job_finish(conn, "BOB", _jcid, job_id, _status, file_name=_jfile,
                               report_month=_jmonth, note=result.get("status"),
                               test_mode=test_mode, local_csv_path=job_csv)
            except Exception as e:
                cn = task["rule"].get("carrier_name", "?") if isinstance(task["rule"], dict) else "?"
                print(f"    🚨 {cn} failed: {e}")
                if track_jobs:
                    job_finish(conn, "BOB", _jcid, job_id, "FAILED", file_name=_jfile,
                               report_month=_jmonth, note=str(e),
                               test_mode=test_mode, local_csv_path=job_csv)
                all_results.append({
                    "carrier_name": cn, "carrier_id": "",
                    "total_rows": 0, "results_count": 0, "exceptions_count": 0,
                    "exception_rate": 0, "status": "error", "errors": [str(e)],
                    "results_df": pd.DataFrame(), "exceptions_df": pd.DataFrame(),
                })
    else:
        # Parallel (prod, >2 carriers). Each worker opens its OWN DB connection
        # and does start → process → finish on that one connection, entirely
        # within its own thread (mirrors the ACU runner). No psycopg2 connection
        # is ever shared across threads — which is what left carriers stuck in
        # 'processing' before, when job_finish's UPDATE raced worker queries on a
        # shared connection. Status now commits per carrier the moment it finishes.
        def _worker(task):
            wc = get_postgres_connection()
            _jcid, _jfile, _jmonth = _job_meta(task)
            job_id = None
            if track_jobs:
                job_id = job_start(wc, "BOB", _jcid, _jfile, report_month=_jmonth,
                                   test_mode=test_mode, local_csv_path=job_csv)
            try:
                result = _process_task(task, task_conn=wc)
                _status = "FAILED" if result.get("status") == "error" else "SUCCESS"
                if track_jobs:
                    job_finish(wc, "BOB", _jcid, job_id, _status, file_name=_jfile,
                               report_month=_jmonth, note=result.get("status"),
                               test_mode=test_mode, local_csv_path=job_csv)
                return result
            except Exception as e:
                if track_jobs:
                    job_finish(wc, "BOB", _jcid, job_id, "FAILED", file_name=_jfile,
                               report_month=_jmonth, note=str(e),
                               test_mode=test_mode, local_csv_path=job_csv)
                raise
            finally:
                try:
                    wc.close()
                except Exception:
                    pass

        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = {executor.submit(_worker, t): t for t in valid_tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    all_results.append(future.result())
                except Exception as e:
                    cn = task["rule"].get("carrier_name", "?") if isinstance(task["rule"], dict) else "?"
                    print(f"    🚨 {cn} failed: {e}")
                    all_results.append({
                        "carrier_name": cn, "carrier_id": "",
                        "total_rows": 0, "results_count": 0, "exceptions_count": 0,
                        "exception_rate": 0, "status": "error", "errors": [str(e)],
                        "results_df": pd.DataFrame(), "exceptions_df": pd.DataFrame(),
                    })

    # ── Step 6: Merge + Write results ──
    print(f"\n── STEP 6: Merge + Write results ──")
    total_written = 0

    # Merge all carrier results into combined files
    filenames = {
        "results": f"bob_results_{run_date}_{run_id}.csv",
        "exceptions": f"bob_exceptions_{run_date}_{run_id}.csv",
    }
    results_path, exceptions_path = merge_bob_outputs(all_results, filenames, temp_dir)

    # Write to DB or file
    for result in all_results:
        if result["status"] in ("error", "no_data", "no_contracts"):
            continue

        results_df = result.get("results_df", pd.DataFrame())
        if results_df.empty:
            continue

        carrier_id = result["carrier_id"]

        if not test_mode:
            # Stamp entity IDs (config-driven, same for all carriers)
            results_df["entity_id"] = ENTITY_ID
            results_df["sub_entity_id"] = SUB_ENTITY_ID

            # Purge existing data for this report_date + carrier.
            # report_date is the file's date (stamped during processing), NOT run_date.
            rd = results_df["report_date"].iloc[0] if "report_date" in results_df.columns and not results_df.empty else run_date
            purge_report_date(conn, rd, carrier_id)
            written = write_to_db(conn, results_df)
            total_written += written

    # ── Step 7: Upload + Archive ──
    print(f"\n── STEP 7: Upload + Archive ──")

    # Upload combined results + exceptions to blob (always — even in test mode)
    uploaded_paths = upload_bob_outputs(
        blob_client, results_path, exceptions_path,
        run_date, container_name
    )

    # Archive processed raw files (skip in test mode)
    if not test_mode and (FEATURES.get("file_archiving", True) or force_archive) and not skip_archive:
        archive_bob_files(blob_client, all_results, valid_tasks, container_name)
    else:
        reason = "test mode" if test_mode else ("skip_archive flag" if skip_archive else "DISABLED in config")
        print(f"    ⏭️  Archive skipped ({reason})")

    if test_mode:
        print(f"    📂 Test mode — local files in: {temp_dir}")

    # ── Step 8: AI Report + Notifications ──
    print(f"\n── STEP 8: AI Report + Notifications ──")
    ai_text = ""
    total_rows = sum(r.get("total_rows", 0) for r in all_results)

    if total_rows > 0 and FEATURES.get("ai_report", True):
        try:
            from intelligence.ai_intelligence import generate_run_report
            ai_result = generate_run_report(all_results, run_date, PROCESS_TYPE)
            ai_text = ai_result.summary if hasattr(ai_result, 'summary') else str(ai_result)
            print(f"    🤖 AI report generated ({len(ai_text)} chars)")
        except Exception as e:
            print(f"    ⚠️  AI report failed: {e}")
    else:
        print(f"    ⏭️  AI report skipped" + (" (no data)" if total_rows == 0 else " (DISABLED)"))

    if FEATURES.get("notifications", True):
        try:
            mode = " [TEST]" if test_mode else ""
            run_date_str = run_date if isinstance(run_date, str) else run_date.strftime("%Y-%m-%d")
            total_exc = sum(r.get("exceptions_count", 0) for r in all_results)
            rate = round(total_exc / total_rows * 100, 1) if total_rows > 0 else 0
            has_errors = any(r["status"] == "error" for r in all_results)

            bob_pending = get_pending_mappings(conn, "BOB")

            summary = build_notification(
                all_results, run_date_str, uploaded_paths, [], [],
                ai_text, test_mode, bob_pending, process_type="BOB",
                schema_drift_carriers=schema_drift_carriers,
                row_variance_carriers=row_variance_carriers,
                row_increase_carriers=row_increase_carriers,
                value_drift_carriers=value_drift_carriers,
            )
            print(f"\n{summary}")

            summary_html = build_notification_html(
                all_results, run_date_str, uploaded_paths, [], [],
                ai_text, test_mode, bob_pending, process_type="BOB",
                schema_drift_carriers=schema_drift_carriers,
                row_variance_carriers=row_variance_carriers,
                row_increase_carriers=row_increase_carriers,
                value_drift_carriers=value_drift_carriers,
            )

            has_critical_drift = any(v.get("type") == "critical" for v in value_drift_carriers)
            if has_errors:
                subj = f"BOB{mode} - Errors - {run_date_str}"
            elif has_critical_drift:
                subj = f"BOB{mode} - Value Drift (deactivated) - {run_date_str}"
            elif rate >= 20:
                subj = f"BOB{mode} - High Exceptions ({rate}%) - {run_date_str}"
            elif rate >= 10:
                subj = f"BOB{mode} - Elevated Exceptions ({rate}%) - {run_date_str}"
            else:
                subj = f"BOB{mode} Complete - {run_date_str}"

            send_teams_notification(subject=subj, body=summary, body_html=summary_html)
            print(f"    📧 Notification sent{'  [TEST]' if test_mode else ''}")
        except Exception as e:
            print(f"    ⚠️  Notification failed: {e}")
    else:
        print(f"    ⏭️  Notifications skipped (DISABLED in config)")
        # Per-carrier files also in temp_dir

    # ── Step 9: Summary ──
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'='*60}")
    print(f"  BOB Pipeline Complete — {elapsed:.0f}s")
    print(f"  Carriers processed: {len(all_results)}")
    print(f"  Total rows written to DB: {total_written}")
    print(f"  Schema drift: {len(schema_drift_carriers)}")
    print(f"  Value drift: {len(value_drift_carriers)}")
    print(f"  Row variance: {len(row_variance_carriers)}")

    success = [r for r in all_results if r["status"] == "success"]
    errors = [r for r in all_results if r["status"] == "error"]
    print(f"  Success: {len(success)}, Errors: {len(errors)}")

    for r in all_results:
        status_icon = "✅" if r["status"] == "success" else "⚠️" if r["status"] in ("no_data", "no_contracts") else "🚨"
        print(f"    {status_icon} {r['carrier_name']:35s} | rows={r['total_rows']:>6,} | "
              f"results={r.get('results_count', 0):>6,} | exc={r.get('exceptions_count', 0):>4,} "
              f"({r.get('exception_rate', 0):.1f}%) | {r['status']}")

    if uploaded_paths:
        print(f"\n  Blob outputs:")
        for label, path in uploaded_paths.items():
            print(f"    {label}: {path}")

    # Clean up temp dir — combined results are already on blob
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"{'='*60}")

    conn.close()
    return all_results


def _bob_pipeline_succeeded(results):
    if not results:
        return False
    return any(r.get("status") == "success" for r in results)


def run_ready_bob_jobs(test_mode=False, container_name=DEFAULT_CONTAINER):
    """Process Ready rows from wpo.ops_inbound_file_log (cron entry point)."""
    if not FEATURES.get("job_tracking", True):
        print("job_tracking disabled — enable in config for --ready")
        return

    conn = get_postgres_connection()
    rows = fetch_ready_inbound_jobs(conn, "BOB")

    if not rows:
        print("No Ready BOB jobs.")
        conn.close()
        return

    print(f"Found {len(rows)} Ready BOB job(s)")

    for row in rows:
        filename = (row.get("file_name") or "").strip()
        filename = os.path.basename(filename) or filename
        carrier_id = str(row.get("carrier_id") or "")
        report_month = row.get("file_report_month") or ""
        source_inbound_pk_id = row.get("pk_id")
        scan_d = _parse_report_date(report_month)
        run_date = scan_d.strftime("%Y-%m-%d")

        print(f"\n── Ready BOB: {filename} ({report_month}) ──")

        start_info = start_inbound_job(
            conn=conn,
            inbound_row=row,
            process_type="BOB",
            test_mode=test_mode,
            local_csv_path=None,
            job_type="BOB",
        )

        job_id = start_info.get("job_id")
        processing_inbound_pk_id = start_info.get("processing_inbound_pk_id")

        if not job_id or not processing_inbound_pk_id:
            print(f"  ⚠️  Could not initialize Ready BOB job for pk_id={source_inbound_pk_id}")
            continue

        try:
            results = run_bob_pipeline(
                run_date=run_date,
                test_mode=test_mode,
                container_name=container_name,
                file_filter=filename,
                track_jobs=False,
                force_archive=True,
            )

            ok = _bob_pipeline_succeeded(results)

            job_finish(
                conn=conn,
                process_type="BOB",
                carrier_id=carrier_id,
                job_id=job_id,
                status="SUCCESS" if ok else "FAILED",
                file_name=filename,
                report_month=report_month,
                inbound_source_pk_id=source_inbound_pk_id,
                inbound_processing_pk_id=processing_inbound_pk_id,
                inbound_metrics=results,
                note="Processing Completed" if ok else "Processing Failed",
                test_mode=test_mode,
            )

        except Exception as e:
            print(f"  ⚠️  Ready BOB job failed: {e}")

            job_finish(
                conn=conn,
                process_type="BOB",
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


# ── CLI ENTRY POINT ──

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BOB Pipeline Runner")
    parser.add_argument("--date", help="Report date (YYYY-MM-DD)", default=None)
    parser.add_argument("--test", action="store_true", help="Test mode (write to files)")
    parser.add_argument("--carrier", nargs="*", help="Filter to specific carrier(s)")
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="Blob container name")
    parser.add_argument("--ready", action="store_true", help="Process Ready RPA queue")
    args = parser.parse_args()

    if args.ready:
        run_ready_bob_jobs(test_mode=args.test, container_name=args.container)
    else:
        run_bob_pipeline(
            run_date=args.date,
            test_mode=args.test,
            container_name=args.container,
            carrier_filter=args.carrier,
        )
