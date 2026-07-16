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

from utils.db_utils import get_postgres_connection, get_synapse_connection
from utils.azure_blob_utils import authenticate_blob_storage, DEFAULT_CONTAINER
from utils.email_utils import send_teams_notification, DEFAULT_TEAMS_CHANNEL
from utils.notification import build_notification, build_notification_html
from utils.ai_utils import call_ai_model
from acu_processor import process_carrier, RESULT_COLUMNS, EXCEPTION_COLUMNS
from ai_carrier_mapper import detect_new_carriers, read_file_headers
from intelligence.ai_intelligence import generate_run_report
from config import FEATURES, MAX_THREADS, EXCEPTION_THRESHOLD_PCT, ROW_VARIANCE_CRITICAL_PCT

RULES_TABLE = "ops_srv.ops_acu_bob_rules_matrix"
MAPPING_TABLE = "ops_srv.ops_acu_bob_load_matrix"
AI_MAPPING_TABLE = "ops_srv.ai_acu_bob_mapping"
OUTPUT_BASE = "results/agent_contract_update (acu)/acu_new_process/"


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


# ── STEP 2: CLASSIFY ──
def classify_files(files, rules_df):
    known, unknown = [], []
    prefixes = {}
    for _, rule in rules_df.iterrows():
        pattern = str(rule.get("file_naming_pattern", "")).strip().lower()
        if pattern and pattern != "na":
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


# ── STEP 3b: PROMOTE ACCEPTED/EDITED MAPPINGS ──
def promote_reviewed_mappings(conn):
    """
    Check ai_acu_bob_mapping for accepted/edited entries.
    - edited → feed corrections to AI FIRST → then promote
    - accepted → promote directly
    Both → write to rules_matrix + load_matrix → set status = complete
    """
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
                 default_appointment_type, appointment_type_value_map,
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
                rv("default_appointment_type", "NA"),
                rv("appointment_type_value_map", "NA"),
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
def check_schema(blob_client, file_info, rule, conn, container_name=DEFAULT_CONTAINER, test_mode=False):
    stored_json = str(rule.get("schema_columns_json", "")).strip()
    headers = read_file_headers(blob_client, file_info["blob_path"], container_name, rule=rule)
    if not headers:
        return True, None
    current_set = set(h.lower().strip() for h in headers)
    current_hash = hashlib.sha256(json.dumps(sorted(current_set)).encode()).hexdigest()

    if not stored_json or stored_json in ("", "NA", "nan"):
        if not test_mode:
            _update_schema(conn, rule, headers, current_hash)
        print(f"    📐 Schema baseline stored ({len(headers)} columns)")
        return True, None

    try:
        stored_columns = set(json.loads(stored_json))
    except:
        if not test_mode:
            _update_schema(conn, rule, headers, current_hash)
        return True, None

    added = current_set - stored_columns
    removed = stored_columns - current_set
    if not added and not removed:
        return True, None

    print(f"    ⚠️  Schema drift: +{len(added)} / -{len(removed)}")
    return False, {"carrier_name": rule["carrier_name"], "added": list(added), "removed": list(removed),
                   "current_headers": headers, "current_hash": current_hash}


def _update_schema(conn, rule, headers, h):
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE {RULES_TABLE} SET schema_columns_json=%s, schema_signature_hash=%s, modified_date=%s WHERE carrier_id=%s AND process_type='ACU' AND file_naming_pattern=%s",
                    (json.dumps(sorted(x.lower().strip() for x in headers)), h, datetime.now().strftime("%Y-%m-%d"), str(rule["carrier_id"]), str(rule["file_naming_pattern"])))
        conn.commit()
    except: conn.rollback()
    finally: cur.close()


def check_row_variance(blob_client, file_info, rule, container_name=DEFAULT_CONTAINER):
    previous = rule.get("previous_row_count")
    try:
        container_client = blob_client.get_container_client(container_name)
        data = container_client.get_blob_client(file_info["blob_path"]).download_blob().readall()
        fname = file_info["file_name"].lower()
        if fname.endswith(".csv"):
            try: df = pd.read_csv(io.BytesIO(data), dtype=str)
            except: df = pd.read_csv(io.BytesIO(data), dtype=str, encoding="latin-1")
        elif fname.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(data), dtype=str)
        else: return True, 0, None
        current = len(df)
    except:
        return True, 0, None

    if not previous or str(previous).strip() in ("", "NA", "nan", "None"):
        return True, 0, {"current": current, "previous": None}
    try: prev = int(float(previous))
    except: return True, 0, {"current": current, "previous": None}
    if prev == 0: return True, 0, {"current": current, "previous": 0}

    variance = abs(current - prev) / prev
    details = {"current": current, "previous": prev, "variance_pct": round(variance*100, 1)}
    if variance >= ROW_VARIANCE_CRITICAL_PCT:
        print(f"    🔴 Variance CRITICAL: {prev:,} → {current:,} ({details['variance_pct']}%)")
        return False, variance, details
    return True, variance, details


def deactivate_carrier(conn, rule, reason):
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE {RULES_TABLE} SET active_flag='N', modified_date=%s, modified_by=%s WHERE carrier_id=%s AND process_type='ACU' AND file_naming_pattern=%s",
                    (datetime.now().strftime("%Y-%m-%d"), f"auto: {reason}", str(rule["carrier_id"]), str(rule["file_naming_pattern"])))
        conn.commit()
        print(f"    🚫 Deactivated: {reason}")
    except: conn.rollback()


def get_pending_mappings(conn):
    """
    Query ai_acu_bob_mapping for carriers awaiting review.
    Returns list of dicts: [{file_name, status, column_count, detected_date}]
    """
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT file_name, status, COUNT(*) as column_count,
                   MIN(created_date) as detected_date
            FROM {AI_MAPPING_TABLE}
            WHERE status NOT IN ('complete')
            GROUP BY file_name, status
            ORDER BY MIN(created_date)
        """)
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
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE {RULES_TABLE} SET previous_row_count=%s, modified_date=%s WHERE carrier_id=%s AND process_type='ACU' AND file_naming_pattern=%s",
                    (count, datetime.now().strftime("%Y-%m-%d"), str(rule["carrier_id"]), str(rule["file_naming_pattern"])))
        conn.commit()
    except: conn.rollback()
    finally: cur.close()


# ── MERGE + UPLOAD ──
def build_db_outputs(all_metrics, valid_tasks, all_rules, cr_path, ce_path,
                     run_date_str, scan_date, temp_dir, filenames, test_mode=False):
    """
    Build acu_contract_updates CSV (P + E rows) and ops_inbound_file_log CSV.
    Returns (contract_updates_path, file_log_path).
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

    # Report date: parse from raw file name (e.g. raw_acu_prominence_mdc_05122026.csv → 2026-05-12)
    # Fall back to scan_date if parsing fails
    report_date = scan_date.strftime("%Y-%m-%d")
    for fn in file_map.values():
        m = re.search(r"(\d{8})\.\w+$", str(fn))
        if m:
            try:
                report_date = datetime.strptime(m.group(1), "%m%d%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
            break
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
        if not test_mode:
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
        print(f"  🔑 txn_ids: {txn_ids[0]} → {txn_ids[-1]} ({n} ids, seq_start={seq_start})")
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
        "report_date": report_date,
        "raw_file_name": combined["carrier_name"].map(file_map).fillna("").values,
        "entity_id": "",
        "sub_entity_id": "",
        "exception_id": combined.get("exception_id", pd.Series("", index=combined.index)).fillna("").values,
    })

    cu_path = os.path.join(temp_dir, filenames["contract_updates"])
    cu.to_csv(cu_path, index=False)
    print(f"  📝 acu_contract_updates: {len(cu):,} rows (P={len(cu[cu['txn_status']=='P']):,}, E={len(cu[cu['txn_status']=='E']):,})")

    # Delete existing rows for same report_date + raw_file_name + carrier_id to prevent duplicates on rerun
    # Skipped in test mode — no DB mutations.
    if not test_mode:
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

    # ── Logs ──
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
            "sub_entity_id": None,
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
    """Write ops_inbound_file_log CSV to both Postgres (wpo) and Synapse (raw)."""
    log_df = pd.read_csv(log_path, dtype=str).fillna("")
    if log_df.empty:
        return

    # Postgres has extra columns; pk_id is serial/auto-increment — omit it
    pg_cols = ["file_name", "destination_schema", "destination_table", "process_type",
               "process_date_start", "process_date_end", "load_status",
               "txn_tot_cnt", "txn_process_cnt", "txn_error_cnt", "status_message",
               "file_report_month", "file_com_month", "product_name",
               "carrier_id", "company_id", "sub_entity_id"]

    # Synapse only has the core columns
    syn_cols = ["file_name", "destination_schema", "destination_table", "process_type",
                "process_date_start", "process_date_end", "load_status",
                "txn_tot_cnt", "txn_process_cnt", "txn_error_cnt", "status_message"]

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

    # ── Synapse: raw.ops_inbound_file_log ──
    try:
        syn_rows = [tuple(row.get(c, "") or None for c in syn_cols) for _, row in log_df.iterrows()]
        syn_placeholders = ", ".join(["?"] * len(syn_cols))
        syn_col_list = ", ".join(syn_cols)

        syn_conn = get_synapse_connection()
        syn_cur = syn_conn.cursor()
        syn_cur.executemany(
            f"INSERT INTO raw.ops_inbound_file_log ({syn_col_list}) VALUES ({syn_placeholders})", syn_rows
        )
        syn_conn.commit()
        syn_cur.close()
        syn_conn.close()
        print(f"  ✅ Wrote {len(syn_rows)} log rows to raw.ops_inbound_file_log (Synapse)")
    except Exception as e:
        print(f"  ⚠️  Synapse log write failed: {e}")


def merge_outputs(all_metrics, filenames, temp_dir):
    r, e, m = [], [], []
    for met in all_metrics:
        for k, lst in [("results_path", r), ("exceptions_path", e), ("missing_path", m)]:
            p = met.get(k)
            if p and os.path.exists(p):
                df = pd.read_csv(p, dtype=str, on_bad_lines='warn')
                if not df.empty: lst.append(df)
    cr = pd.concat(r, ignore_index=True) if r else pd.DataFrame(columns=RESULT_COLUMNS)
    ce = pd.concat(e, ignore_index=True) if e else pd.DataFrame(columns=EXCEPTION_COLUMNS)
    cm = pd.concat(m, ignore_index=True) if m else pd.DataFrame()
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
def run_acu_pipeline(scan_date=None, container_name=DEFAULT_CONTAINER, test_mode=False):
    if scan_date is None: scan_date = date.today()
    run_date_str = scan_date.strftime("%m%d%Y")
    mode = " [TEST]" if test_mode else ""
    suffix = "_test" if test_mode else ""
    run_id = str(random.randint(1000, 9999))

    print(f"\n{'='*60}\n  ACU PIPELINE{mode} — {scan_date.strftime('%Y-%m-%d')}\n{'='*60}")

    conn = get_postgres_connection()
    blob_client = authenticate_blob_storage()

    # Step 1: Scan
    print(f"\n── STEP 1: Scan ──")
    all_files = scan_blob_files(blob_client, scan_date, container_name)
    if not all_files:
        print("⚠️  No files."); conn.close(); return

    # Step 2: Classify
    print(f"\n── STEP 2: Classify ──")
    # Load active + inactive (non-ended) rules for classification
    all_rules_for_classify = pd.read_sql(f"SELECT * FROM {RULES_TABLE} WHERE process_type='ACU' AND (rule_end_date IS NULL OR LOWER(TRIM(rule_end_date)) IN ('', 'na', 'nan', 'none', 'null'))", conn)
    all_rules_for_classify["carrier_id"] = all_rules_for_classify["carrier_id"].apply(_safe_carrier_id)
    rules_df = all_rules_for_classify[all_rules_for_classify["active_flag"] == "Y"]
    print(f"📋 {len(rules_df)} active ACU rules ({len(all_rules_for_classify)} total)")

    # Known prefixes = ALL prefixes ever registered (including ended/inactive)
    # so a deactivated or ended carrier is never re-detected as "new"
    all_prefixes_df = pd.read_sql(f"SELECT DISTINCT file_naming_pattern FROM {RULES_TABLE} WHERE process_type='ACU'", conn)
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

    # Step 3a: New carriers (only truly unknown files)
    new_carriers = []
    if unknown_files and FEATURES.get("ai_carrier_mapper", True):
        if not test_mode:
            print(f"\n── STEP 3a: New carriers ──")
            new_carriers = detect_new_carriers(unknown_files, known_prefixes, blob_client, conn, "ACU", container_name)
        else:
            print(f"\n── STEP 3a: New carriers SKIPPED (test mode) — {len(unknown_files)} unknown file(s) ──")
    elif unknown_files:
        print(f"\n── STEP 3a: New carriers (DISABLED) — {len(unknown_files)} unknown file(s) skipped ──")

    # Step 3b: Promote accepted/edited
    promoted = 0
    if not test_mode:
        print(f"\n── STEP 3b: Promote reviewed mappings ──")
        promoted = promote_reviewed_mappings(conn)
    else:
        print(f"\n── STEP 3b: Promote reviewed mappings SKIPPED (test mode) ──")
    if promoted:
        print(f"  ✅ Promoted {promoted} carrier(s) — reloading rules")
        rules_df = pd.read_sql(f"SELECT * FROM {RULES_TABLE} WHERE process_type='ACU' AND active_flag='Y' AND (rule_end_date IS NULL OR LOWER(TRIM(rule_end_date)) IN ('', 'na', 'nan', 'none', 'null'))", conn)
        rules_df["carrier_id"] = rules_df["carrier_id"].apply(_safe_carrier_id)
        known_files, _ = classify_files(all_files, rules_df)

    # Step 4: Schema + variance
    print(f"\n── STEP 4: Checks ──")
    all_mappings = pd.read_sql(f"SELECT * FROM {MAPPING_TABLE} WHERE process_type='ACU' AND (end_date IS NULL OR LOWER(TRIM(end_date)) IN ('', 'na', 'nan', 'none', 'null'))", conn)
    all_mappings["carrier_id"] = all_mappings["carrier_id"].apply(_safe_carrier_id)
    valid_tasks, deactivated = [], []

    for kf in known_files:
        rule = kf["rule"]
        print(f"\n  📋 {rule['carrier_name']}:")

        reader_name = str(rule.get("custom_reader_name", "")).strip()

        # Custom readers that return multiple sub-carriers (HCSC, SMA)
        # Skip schema/variance checks — the raw file contains data for many carriers
        if reader_name in ("read_hcsc", "read_sma", "read_quartz", "read_christus", "read_community_health", "read_molina", "read_allstate", "read_healthfirst"):
            print(f"    📖 Multi-carrier reader: {reader_name} — expanding...")
            from acu_readers import get_custom_reader
            reader_fn = get_custom_reader(reader_name)
            sub_results = reader_fn(blob_client, kf["blob_path"], rule, None,
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
            schema_ok, drift = check_schema(blob_client, kf, rule, conn, container_name, test_mode=test_mode)
            if not schema_ok:
                added = set(drift.get("added", []))
                removed = set(drift.get("removed", []))

                # Columns that matter: mapped in load_matrix + referenced in rules (filter, sheet, etc.)
                cid = str(rule["carrier_id"])
                carrier_mappings = all_mappings[all_mappings["carrier_id"].astype(str) == cid]
                mapped_cols = set(carrier_mappings["mapping"].dropna().str.lower().str.strip())

                # Rule-referenced columns (filter_column after mapping is canonical, but raw file columns
                # are what's in the schema — so we check the raw mapping values)
                rule_cols = set()
                for field in ["filter_column", "sheet_name"]:
                    val = str(rule.get(field, "")).strip().lower()
                    if val and val not in ("", "na", "nan", "none"):
                        rule_cols.add(val)

                columns_that_matter = mapped_cols | rule_cols
                critical_removed = removed & columns_that_matter
                critical_added = added & columns_that_matter  # renamed column = removed old + added new

                if critical_removed:
                    # Mapped/rule columns were removed — real break
                    if not test_mode:
                        deactivate_carrier(conn, rule, f"Schema drift: lost mapped columns {critical_removed}")
                    deactivated.append({"carrier_name": rule["carrier_name"],
                                        "reason": f"Schema drift: lost {critical_removed}"}); continue
                else:
                    # Drift only in columns we don't use — soft warning, auto-accept
                    if not test_mode:
                        _update_schema(conn, rule, drift["current_headers"], drift["current_hash"])
                    parts = []
                    if added: parts.append(f"+{len(added)} added")
                    if removed: parts.append(f"-{len(removed)} removed")
                    print(f"    ℹ️  Schema updated (soft): {', '.join(parts)} — none are mapped columns")

        var_details = None
        if FEATURES.get("variance_check", True):
            var_ok, _, var_details = check_row_variance(blob_client, kf, rule, container_name)
            if not var_ok:
                if not test_mode:
                    deactivate_carrier(conn, rule, f"Row variance: {var_details['variance_pct']}%")
                deactivated.append({"carrier_name": rule["carrier_name"], "reason": f"Variance {var_details['variance_pct']}%"}); continue

        cid = str(rule["carrier_id"])
        cm = all_mappings[all_mappings["carrier_id"].astype(str) == cid]
        if cm.empty: print(f"    ⚠️  No mappings — skip"); continue
        valid_tasks.append({"rule": rule, "mappings": cm, "files": [kf["blob_path"]], "row_count": var_details["current"] if var_details else None,
                            "previous_row_count": var_details.get("previous") if var_details else None,
                            "variance_pct": var_details.get("variance_pct") if var_details else None})

    # Step 5: Process
    all_metrics, uploaded, email_attachments = [], {}, []
    if valid_tasks:
        print(f"\n── STEP 5: Process {len(valid_tasks)} carrier(s) ──")
        conn.close()
        temp_dir = tempfile.mkdtemp(prefix="acu_")
        def _worker(task):
            tc = get_postgres_connection()
            try:
                pre_df = task.get("pre_read_df")
                result = process_carrier(blob_client, tc, task["rule"], task["mappings"],
                                         task["files"], temp_dir, run_date_str, container_name,
                                         pre_read_df=pre_df)
                if result["status"] in ("success", "threshold_exceeded") and task["row_count"]:
                    if not test_mode:
                        update_row_count(tc, task["rule"], task["row_count"])
                return result
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
        cr, ce, cm = merge_outputs(all_metrics, filenames, temp_dir)
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
                test_mode=test_mode
            )
            # Upload CSVs to blob as backup
            if cu_path:
                upload_outputs_single(blob_client, cu_path, scan_date, container_name)
            if log_path:
                upload_outputs_single(blob_client, log_path, scan_date, container_name, subfolder="logs/")

            # Write to DB tables (skipped in test mode)
            if not test_mode:
                if cu_path and os.path.exists(cu_path):
                    write_contract_updates_to_db(cu_path)
                if log_path and os.path.exists(log_path):
                    write_logs_to_db(log_path)
            db_write_ok = True
        except Exception as e:
            import traceback
            print(f"  ⚠️  DB output step failed: {e}")
            print(traceback.format_exc())

        # Step 5b: Archive processed files — only if DB write succeeded
        if not test_mode and FEATURES.get("file_archiving", True):
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

        # Dedup metrics — same carrier from multiple file dates should appear once (keep latest)
        seen = {}
        for m in all_metrics:
            cn = m["carrier_name"]
            if cn not in seen or m.get("total_rows", 0) >= seen[cn].get("total_rows", 0):
                seen[cn] = m
        if len(seen) < len(all_metrics):
            print(f"  📋 Deduped metrics: {len(all_metrics)} → {len(seen)} (multiple file dates)")
            all_metrics = list(seen.values())

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
    pending_mappings = get_pending_mappings(conn)
    if pending_mappings:
        print(f"  {len(pending_mappings)} carrier(s) awaiting mapping review")
    _has_att = bool(email_attachments)
    summary = build_notification(all_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att)
    print(f"\n{summary}")

    if FEATURES.get("notifications", True):
        summary_html = build_notification_html(all_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att)
        rate = round(total_exc / total_rows * 100, 1) if total_rows > 0 else 0
        has_errors = any(m["status"] == "error" for m in all_metrics)
        has_value_change = any(m["status"] == "value_change" for m in all_metrics)
        if has_errors:
            subj = f"ACU{mode} - Errors - {run_date_str}"
        elif has_value_change:
            subj = f"ACU{mode} - Value Map Mismatch - {run_date_str}"
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
                ch_summary = build_notification(channel_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att)
                ch_html = build_notification_html(channel_metrics, run_date_str, uploaded, new_carriers, deactivated, ai_text, test_mode, pending_mappings, skipped_inactive, _has_att)
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
    return {"metrics": all_metrics, "new_carriers": new_carriers, "deactivated": deactivated, "uploaded": uploaded}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ACU pipeline")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--date", type=str)
    args = parser.parse_args()
    scan = date.today()
    if args.date: scan = datetime.strptime(args.date, "%Y-%m-%d").date()
    run_acu_pipeline(scan_date=scan, test_mode=args.test)