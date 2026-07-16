# ==========================================================
#  ai_carrier_mapper.py
# ==========================================================
"""
Detects new carrier files not in matrix. Uses AI to suggest
column mappings. For each DATABASE column, AI suggests which
FILE column maps to it (same as load_matrix pattern).

Stores in ops_srv.ai_acu_bob_mapping. Sends Teams alert.
Does NOT promote — runner handles that.
"""

import io
import json
import os
import re
import pandas as pd
from datetime import datetime, date

from utils.db_utils import get_postgres_connection
from utils.azure_blob_utils import authenticate_blob_storage, DEFAULT_CONTAINER
from utils.ai_utils import call_ai_model
from utils.email_utils import send_teams_notification

RULES_TABLE = "ops_srv.ops_acu_bob_rules_matrix"
MAPPING_TABLE = "ops_srv.ops_acu_bob_load_matrix"
AI_MAPPING_TABLE = "ops_srv.ai_acu_bob_mapping"

ACU_CANONICAL_COLUMNS = [
    "agent_npn", "agent_writing_num", "agent_full_name", "agent_fname", "agent_lname",
    "contract_status", "contract_date", "appointment_type", "appointed_state",
    "appointed_date", "parent_npn", "current_rts", "current_rts_date",
    "next_rts", "next_rts_date", "market",
]

BOB_CANONICAL_COLUMNS = [
    "agent_npn", "agent_writing_num", "agent_full_name", "agent_fname", "agent_lname",
    "mem_policy_num", "mem_id", "mem_full_name", "mem_fname", "mem_lname",
    "mem_dob", "mem_state", "mem_county", "mem_status", "mem_market",
    "mem_address1", "mem_address2", "mem_city", "mem_zip", "mem_email",
    "mem_plan_year", "is_subscriber", "mem_count", "product_type",
    "mem_direct_upline", "mem_top_upline", "mem_effective_date",
    "mem_cov_end_date", "mem_app_date", "mem_paid_thru_date",
]


def read_file_headers(blob_client, blob_path, container_name, rule=None):
    """Read just the header row from a blob file."""
    try:
        container_client = blob_client.get_container_client(container_name)
        blob = container_client.get_blob_client(blob_path)
        data = blob.download_blob().readall()
        fname = os.path.basename(blob_path).lower()

        # Decrypt password-protected Excel files
        if rule and fname.endswith((".xlsx", ".xls")):
            password_secret = str(rule.get("password_secret_name", "")).strip()
            if password_secret and password_secret not in ("", "nan", "NA", "None"):
                try:
                    from utils.db_utils import get_postgres_db_secrets
                    from azure.identity import DefaultAzureCredential
                    from azure.keyvault.secrets import SecretClient
                    credential = DefaultAzureCredential()
                    kv = SecretClient(vault_url=os.getenv("KEYVAULT_URL", ""), credential=credential)
                    password = kv.get_secret(password_secret).value
                    if password:
                        import msoffcrypto
                        decrypted = io.BytesIO()
                        f = msoffcrypto.OfficeFile(io.BytesIO(data))
                        f.load_key(password=password)
                        f.decrypt(decrypted)
                        data = decrypted.getvalue()
                except Exception as e:
                    print(f"    ⚠️  Header decrypt failed: {e} — skipping header check")
                    return []

        if fname.endswith(".csv"):
            try:
                df = pd.read_csv(io.BytesIO(data), nrows=0, dtype=str)
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(data), nrows=0, dtype=str, encoding="latin-1")
        elif fname.endswith((".xlsx", ".xls")):
            try:
                df = pd.read_excel(io.BytesIO(data), nrows=0, dtype=str)
            except Exception:
                try:
                    df = pd.read_excel(io.BytesIO(data), nrows=0, dtype=str, engine="xlrd")
                except Exception:
                    df = pd.read_excel(io.BytesIO(data), nrows=0, dtype=str, engine="openpyxl")
        else:
            return []
        return [c.strip() for c in df.columns.tolist()]
    except Exception as e:
        print(f"    ❌ Error reading headers: {e}")
        return []


def load_existing_mappings(conn):
    """Load existing mappings: {database_column: [list of file column names from other carriers]}."""
    query = f"""
        SELECT database_column, mapping FROM {MAPPING_TABLE}
        WHERE mapping IS NOT NULL AND mapping != 'NA'
          AND (end_date IS NULL OR LOWER(TRIM(end_date)) IN ('', 'na', 'nan', 'none', 'null'))
    """
    df = pd.read_sql(query, conn)
    examples = {}
    for _, row in df.iterrows():
        db_col = row["database_column"]
        file_col = row["mapping"]
        if db_col not in examples:
            examples[db_col] = set()
        examples[db_col].add(file_col)
    return {k: sorted(v) for k, v in examples.items()}


# ==========================================================
#  AI: For each DB column, suggest which FILE column maps to it
# ==========================================================
def ai_suggest_mappings(file_info, headers, canonical_columns, existing_mappings):
    """For each database column, suggest which file column maps to it."""
    examples_str = ""
    for db_col, file_cols in existing_mappings.items():
        if db_col in canonical_columns:
            examples_str += f"  {db_col} ← {', '.join(file_cols[:5])}\n"

    prompt = f"""For each DATABASE COLUMN, suggest which FILE COLUMN from the new carrier maps to it.

FILE: {file_info['file_name']} ({file_info['process_type']})

FILE COLUMNS AVAILABLE:
{chr(10).join(f'  - {h}' for h in headers)}

DATABASE COLUMNS TO MAP:
{chr(10).join(f'  - {c}' for c in canonical_columns)}

EXISTING MAPPINGS FROM OTHER CARRIERS (database_col ← file columns that mapped to it):
{examples_str}

For each DATABASE COLUMN, suggest the best FILE COLUMN or "NA" if no match.
Respond ONLY as JSON array:
[{{"database_column": "agent_npn", "file_column": "NPN", "confidence": "high", "reasoning": "exact match"}}]"""

    response = call_ai_model(prompt, "Return ONLY valid JSON. No markdown. No explanation.")

    if response:
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(cleaned)
        except (json.JSONDecodeError, Exception):
            pass

    return _fallback_mapping(headers, canonical_columns, existing_mappings)


def _fallback_mapping(headers, canonical_columns, existing_mappings):
    """For each DB column, try to find a matching file column."""
    headers_lower = {h.lower().strip(): h for h in headers}

    # Reverse lookup: for each DB column, what file column names have mapped to it before?
    reverse = {}
    for db_col, file_cols in existing_mappings.items():
        for fc in file_cols:
            if db_col not in reverse:
                reverse[db_col] = set()
            reverse[db_col].add(fc.lower().strip())

    suggestions = []
    for db_col in canonical_columns:
        db_lower = db_col.lower().strip()

        # Check if any file column exactly matches DB column name
        if db_lower in headers_lower:
            suggestions.append({
                "database_column": db_col, "file_column": headers_lower[db_lower],
                "confidence": "high", "reasoning": "exact name match",
            })
            continue

        # Check if any file column matches what other carriers have mapped to this DB column
        matched = False
        if db_col in reverse:
            for known_fc in reverse[db_col]:
                if known_fc in headers_lower:
                    suggestions.append({
                        "database_column": db_col, "file_column": headers_lower[known_fc],
                        "confidence": "high", "reasoning": "matched from existing carriers",
                    })
                    matched = True
                    break

        if not matched:
            suggestions.append({
                "database_column": db_col, "file_column": "NA",
                "confidence": "low", "reasoning": "no match found",
            })

    return suggestions


# ==========================================================
#  RULES: Load existing rules as learning examples
# ==========================================================
RULES_FIELDS = [
    "contract_type", "file_format", "file_delimiter", "file_encoding",
    "sheet_name", "ignore_header_rows", "filter_rule_type", "filter_values",
    "filter_column", "filter_scope", "primary_identity_field",
    "fallback_identity_field", "default_appointment_type",
    "appointment_type_value_map", "rts_flag_applicable",
]


def load_existing_rules(conn):
    """Load existing rules_matrix rows as learning examples.
    Returns a list of dicts, one per carrier rule (active only)."""
    query = f"""
        SELECT carrier_name, process_type, contract_type, file_format,
               file_delimiter, file_encoding, sheet_name, ignore_header_rows,
               filter_rule_type, filter_values, filter_column, filter_scope,
               primary_identity_field, fallback_identity_field,
               default_appointment_type, appointment_type_value_map,
               rts_flag_applicable
        FROM {RULES_TABLE}
        WHERE active_flag = 'Y'
          AND (rule_end_date IS NULL OR LOWER(TRIM(rule_end_date)) IN ('', 'na', 'nan', 'none', 'null'))
        ORDER BY carrier_name
    """
    df = pd.read_sql(query, conn)
    return df.to_dict("records")


def read_file_sample(blob_client, blob_path, container_name):
    """Read sheet names, headers, and sample rows from a blob file.
    Returns dict with 'sheets', 'sheet_details' (per-sheet header+sample+row_count)."""
    try:
        container_client = blob_client.get_container_client(container_name)
        data = container_client.get_blob_client(blob_path).download_blob().readall()
        fname = os.path.basename(blob_path).lower()

        result = {"sheets": [], "sheet_details": {}}

        if fname.endswith((".xlsx", ".xls")):
            xls = pd.ExcelFile(io.BytesIO(data))
            result["sheets"] = xls.sheet_names
            for sheet in xls.sheet_names:
                try:
                    df = pd.read_excel(xls, sheet_name=sheet, dtype=str, nrows=20)
                    headers = [c.strip() for c in df.columns.tolist()]
                    # Detect secondary header row (row 0 looks like column names)
                    has_sub_header = False
                    if len(df) > 0:
                        first_row = df.iloc[0].tolist()
                        # If most values in row 0 look like labels (no digits, short),
                        # it's likely a secondary header
                        label_count = sum(1 for v in first_row
                                          if isinstance(v, str) and len(v) < 40
                                          and not any(c.isdigit() for c in str(v)))
                        has_sub_header = label_count >= len(first_row) * 0.7

                    # Sample unique values per column (for filter detection)
                    col_samples = {}
                    for col in df.columns:
                        start = 1 if has_sub_header else 0
                        vals = df[col].iloc[start:].dropna().unique().tolist()[:10]
                        col_samples[col] = vals

                    result["sheet_details"][sheet] = {
                        "headers": headers,
                        "row_count": len(df),
                        "has_sub_header": has_sub_header,
                        "sub_header_row": df.iloc[0].tolist() if has_sub_header and len(df) > 0 else [],
                        "col_samples": col_samples,
                    }
                except Exception as e:
                    result["sheet_details"][sheet] = {"headers": [], "row_count": 0, "error": str(e)}
        else:
            # CSV — single "sheet"
            try:
                df = pd.read_csv(io.BytesIO(data), dtype=str, nrows=20)
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(data), dtype=str, nrows=20, encoding="latin-1")
            headers = [c.strip() for c in df.columns.tolist()]
            col_samples = {}
            for col in df.columns:
                vals = df[col].dropna().unique().tolist()[:10]
                col_samples[col] = vals
            result["sheets"] = ["(csv)"]
            result["sheet_details"]["(csv)"] = {
                "headers": headers, "row_count": len(df),
                "has_sub_header": False, "sub_header_row": [],
                "col_samples": col_samples,
            }
        return result
    except Exception as e:
        print(f"    ❌ Error reading file sample: {e}")
        return {"sheets": [], "sheet_details": {}}


# ==========================================================
#  AI: Suggest rules_matrix config from existing patterns
# ==========================================================
def ai_suggest_rules(file_info, headers, existing_rules=None, sample_data=None):
    """AI-backed rules suggestion using existing rules as learning examples.

    Parameters
    ----------
    file_info : dict      — file_name, process_type, prefix, etc.
    headers : list         — column headers from the data sheet
    existing_rules : list  — list of dicts from load_existing_rules()
    sample_data : dict     — output of read_file_sample()
    """
    # Build enhanced fallback first (always available)
    fallback = _fallback_rules(file_info, headers, sample_data, existing_rules)

    if not existing_rules:
        return fallback

    # Build examples string from existing rules (limit to same process_type)
    proc = file_info.get("process_type", "ACU")
    same_type = [r for r in existing_rules if r.get("process_type") == proc][:15]
    examples_str = ""
    for r in same_type:
        line_parts = []
        for field in RULES_FIELDS:
            val = r.get(field, "")
            if val and str(val).strip() not in ("", "nan", "NA", "None"):
                line_parts.append(f"{field}={val}")
        if line_parts:
            examples_str += f"  {r['carrier_name']}: {', '.join(line_parts)}\n"

    # Build sheet/sample context
    sheet_context = ""
    if sample_data and sample_data.get("sheets"):
        sheet_context += f"\nSHEETS IN FILE: {sample_data['sheets']}\n"
        for sheet_name, detail in sample_data.get("sheet_details", {}).items():
            if detail.get("headers"):
                sheet_context += f"\n  Sheet '{sheet_name}': {detail['row_count']} rows"
                sheet_context += f"\n    Headers: {detail['headers']}"
                if detail.get("has_sub_header"):
                    sheet_context += f"\n    Sub-header row detected: {detail['sub_header_row']}"
                # Show columns with few unique values (likely filter/status candidates)
                for col, vals in detail.get("col_samples", {}).items():
                    if 1 < len(vals) <= 6:
                        sheet_context += f"\n    Column '{col}' unique values: {vals}"

    prompt = f"""Suggest rules_matrix configuration for a new carrier file.

FILE: {file_info['file_name']} (process_type: {proc})

FILE COLUMNS:
{chr(10).join(f'  - {h}' for h in headers)}
{sheet_context}

EXISTING CARRIER RULES (same process_type — learn from these patterns):
{examples_str}

RULES FIELDS TO SUGGEST (provide a value for each):
  contract_type — typically ACA, MDC, or SUP
  file_format — csv or xlsx
  file_delimiter — comma, pipe, or tab
  file_encoding — utf-8 or latin-1
  sheet_name — which sheet contains the data (NA for CSV)
  ignore_header_rows — 0 or 1 (1 if file has a secondary header row)
  filter_rule_type — ALL (no filter), STATUS (filter by column value), CONTAINS, or DATE
  filter_column — which column to filter on (after column mapping to canonical names). NA if filter_rule_type is ALL
  filter_values — comma-separated values to keep. NA if ALL
  filter_scope — ROW (filter individual rows) or AGENT (keep all rows for qualifying agents)
  primary_identity_field — NPN, WR (writing number), or NAME
  fallback_identity_field — NAME or WR (backup if primary doesn't match)
  default_appointment_type — Producer, Subproducer, or NA
  rts_flag_applicable — Y or N (does this carrier have Ready-To-Sell data?)

IMPORTANT RULES:
- If a status-like column exists with values like Active/Inactive/A/C/Terminated, use STATUS filter
- filter_column should reference the CANONICAL (database) column name after mapping, not the raw file column
- If the file has multiple sheets, pick the one with actual data rows (not a Guide/instructions sheet)
- If a sub-header row is detected, set ignore_header_rows to 1
- Look at existing patterns: similar carrier types tend to have similar rules

Respond ONLY as JSON object with the field names as keys:
{{"contract_type": "ACA", "file_format": "xlsx", ...}}"""

    response = call_ai_model(prompt, "Return ONLY valid JSON. No markdown. No explanation.")

    if response:
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
            ai_rules = json.loads(cleaned)
            # Merge AI suggestions with fallback (AI overrides where it has values)
            merged = {**fallback}
            for k, v in ai_rules.items():
                if k in RULES_FIELDS and v and str(v).strip() not in ("", "null", "None"):
                    merged[k] = str(v).strip()
            print(f"    🤖 AI rules suggestion applied ({len(ai_rules)} fields)")
            return merged
        except (json.JSONDecodeError, Exception) as e:
            print(f"    ⚠️  AI rules parse failed ({e}) — using fallback")

    return fallback


def _fallback_rules(file_info, headers, sample_data=None, existing_rules=None):
    """Enhanced heuristic fallback for rules suggestion."""
    headers_lower = [h.lower() for h in headers]
    fname = file_info.get("file_name", "")

    rules = {
        "contract_type": "ACA",
        "file_format": "xlsx" if fname.endswith((".xlsx", ".xls")) else "csv",
        "file_delimiter": "comma",
        "file_encoding": "utf-8",
        "sheet_name": "NA",
        "ignore_header_rows": "0",
        "filter_rule_type": "ALL",
        "filter_values": "NA",
        "filter_column": "NA",
        "filter_scope": "ROW",
        "primary_identity_field": "NPN",
        "fallback_identity_field": "NAME",
        "default_appointment_type": "NA",
        "appointment_type_value_map": "NA",
        "rts_flag_applicable": "N",
    }

    # Contract type from filename
    if "_mdc_" in fname.lower() or "medicare" in fname.lower():
        rules["contract_type"] = "MDC"
    elif "_sup_" in fname.lower() or "supplement" in fname.lower():
        rules["contract_type"] = "SUP"

    # Sheet detection
    if sample_data and sample_data.get("sheets"):
        sheets = sample_data["sheets"]
        if len(sheets) > 1:
            # Pick the sheet with the most rows (skip guide/instructions sheets)
            best_sheet, best_rows = None, 0
            for s in sheets:
                detail = sample_data.get("sheet_details", {}).get(s, {})
                skip_names = ["guide", "instructions", "readme", "notes", "info"]
                if any(sk in s.lower() for sk in skip_names):
                    continue
                rows = detail.get("row_count", 0)
                if rows > best_rows:
                    best_rows = rows
                    best_sheet = s
            if best_sheet:
                rules["sheet_name"] = best_sheet
                detail = sample_data["sheet_details"].get(best_sheet, {})
                if detail.get("has_sub_header"):
                    rules["ignore_header_rows"] = "1"
        elif len(sheets) == 1 and sheets[0] != "(csv)":
            rules["sheet_name"] = sheets[0]
            detail = sample_data["sheet_details"].get(sheets[0], {})
            if detail.get("has_sub_header"):
                rules["ignore_header_rows"] = "1"

    # Status/filter detection from column samples
    status_keywords = ["status", "active", "agtstatuscode", "appointment status"]
    for h in headers_lower:
        if any(kw in h for kw in status_keywords):
            # Check sample values if available
            if sample_data:
                for sheet_detail in sample_data.get("sheet_details", {}).values():
                    for col, vals in sheet_detail.get("col_samples", {}).items():
                        if col.lower().strip() == h:
                            # If column has a small set of values, suggest STATUS filter
                            str_vals = [str(v).strip() for v in vals if str(v).strip()]
                            if 1 <= len(str_vals) <= 8:
                                rules["filter_rule_type"] = "STATUS"
                                rules["filter_column"] = h  # will be refined by AI
                                # Pick values that look "active"
                                active_vals = [v for v in str_vals
                                               if v.upper() in ("A", "ACTIVE", "Y", "YES",
                                                                  "APPROVED", "LICENSED")]
                                rules["filter_values"] = ",".join(active_vals) if active_vals else str_vals[0]
                            break
            else:
                rules["filter_rule_type"] = "STATUS"
                rules["filter_column"] = h
                rules["filter_values"] = "Active"
            break

    # Identity field detection
    if any("npn" in h for h in headers_lower):
        rules["primary_identity_field"] = "NPN"
        rules["fallback_identity_field"] = "NAME"
    elif any("writing" in h or "awn" in h for h in headers_lower):
        rules["primary_identity_field"] = "WR"
        rules["fallback_identity_field"] = "NAME"
    else:
        rules["primary_identity_field"] = "NAME"
        rules["fallback_identity_field"] = "WR"

    # RTS detection
    if any("rts" in h or "ready to sell" in h or "certification" in h for h in headers_lower):
        rules["rts_flag_applicable"] = "Y"

    # Appointment type from existing patterns
    if existing_rules:
        proc = file_info.get("process_type", "ACU")
        appt_types = [r.get("default_appointment_type", "") for r in existing_rules
                      if r.get("process_type") == proc
                      and r.get("default_appointment_type")
                      and str(r["default_appointment_type"]).strip() not in ("", "nan", "NA", "None")]
        if appt_types:
            from collections import Counter
            most_common = Counter(appt_types).most_common(1)[0][0]
            rules["default_appointment_type"] = most_common

    return rules


def store_suggestions(conn, file_info, suggestions, rule_suggestion):
    """Insert mapping suggestions — one row per DATABASE column."""
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Include file headers in rules JSON so UI can build dropdowns
    rules_with_headers = {**rule_suggestion, "file_headers": file_info.get("headers", [])}

    try:
        # Guard: skip if this file already has suggestions (prevents re-run duplicates)
        cur.execute(f"SELECT COUNT(*) FROM {AI_MAPPING_TABLE} WHERE file_name = %s",
                    (file_info["file_name"],))
        if cur.fetchone()[0] > 0:
            print(f"    ℹ️  Suggestions already exist for {file_info['file_name']} — skipping insert")
            cur.close()
            return

        for s in suggestions:
            cur.execute(f"""
                INSERT INTO {AI_MAPPING_TABLE}
                (carrier_name, process_type, file_name, detected_date, status,
                 file_column, canonical_column, confidence, ai_reasoning,
                 suggested_rules, created_date, modified_date)
                VALUES (%s, %s, %s, %s, 'pending_review', %s, %s, %s, %s, %s, %s, %s)
            """, (
                file_info.get("prefix", ""), file_info["process_type"],
                file_info["file_name"], file_info["detected_date"],
                s.get("file_column", "NA"),          # AI-suggested file column
                s.get("database_column", ""),          # static DB column
                s.get("confidence", "low"), s.get("reasoning", ""),
                json.dumps(rules_with_headers), now, now,
            ))
        conn.commit()
        print(f"    💾 Stored {len(suggestions)} mapping suggestions")
    except Exception as e:
        conn.rollback()
        print(f"    ❌ Failed to store suggestions: {e}")
    finally:
        cur.close()


def send_new_carrier_alert(new_files, all_suggestions):
    """Send Teams alert about new carrier files needing review."""
    file_list = "\n".join(f"  • {f['file_name']} ({f['process_type']})" for f in new_files)
    mapping_summary = ""
    for s in all_suggestions:
        info = s["file_info"]
        suggs = s["suggestions"]
        high = sum(1 for x in suggs if x.get("confidence") == "high")
        med = sum(1 for x in suggs if x.get("confidence") == "medium")
        low = sum(1 for x in suggs if x.get("confidence") == "low")
        mapping_summary += f"\n  {info['file_name']}: {len(suggs)} DB columns | High: {high} | Med: {med} | Low: {low}"

    body = f"""🆕 New Carrier File(s) Detected — Mapping Review Needed
{'='*55}

{file_list}

AI Mapping Suggestions:{mapping_summary}

Action Required:
  Review and accept/edit mappings in MyOps (or run mapping_server.py locally).
  Pending mappings will NOT be processed until reviewed.
"""
    send_teams_notification(
        subject=f"🆕 New Carrier Detected — Review Needed ({len(new_files)} file(s))",
        body=body,
    )
    print(f"📨 Alert sent for {len(new_files)} new carrier file(s)")


def detect_new_carriers(files, known_prefixes, blob_client, conn,
                         process_type="ACU", container_name=DEFAULT_CONTAINER):
    """
    Detect unknown files, AI suggest mappings, store in DB, send alert.
    Called from the runner.
    """
    unknown = []
    for f in files:
        match = re.match(r"^(.*?_)(?=\d)", f["file_name"])
        if not match:
            print(f"  ⚠️  '{f['file_name']}' doesn't match naming convention (prefix_MMDDYYYY) — skipping")
            continue
        prefix = match.group(1).lower()
        if prefix not in known_prefixes:
            unknown.append({**f, "prefix": prefix, "process_type": process_type,
                            "detected_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

    if not unknown:
        return []

    # Skip files that already have entries in ai_acu_bob_mapping (pending/accepted/edited)
    cur = conn.cursor()
    cur.execute(f"SELECT DISTINCT file_name FROM {AI_MAPPING_TABLE} WHERE status != 'complete'")
    already_tracked = set(row[0] for row in cur.fetchall())
    cur.close()

    truly_new = [f for f in unknown if f["file_name"] not in already_tracked]
    skipped = len(unknown) - len(truly_new)
    if skipped:
        print(f"  ℹ️  {skipped} file(s) already have pending mappings — skipping")

    if not truly_new:
        return []

    print(f"\n🆕 {len(truly_new)} new carrier file(s) detected")
    existing_mappings = load_existing_mappings(conn)
    existing_rules = load_existing_rules(conn)
    canonical = ACU_CANONICAL_COLUMNS if process_type == "ACU" else BOB_CANONICAL_COLUMNS

    all_suggestions = []
    for file_info in truly_new:
        # Read headers (for column mapping)
        headers = read_file_headers(blob_client, file_info["blob_path"], container_name)
        if not headers:
            print(f"    ⚠️  Cannot read headers for {file_info['file_name']}")
            continue

        # Read sample data (for rules: sheets, sub-headers, value patterns)
        sample_data = read_file_sample(blob_client, file_info["blob_path"], container_name)

        file_info["headers"] = headers
        file_info["sample_data"] = sample_data
        print(f"    📊 {file_info['file_name']}: {len(headers)} file columns → mapping to {len(canonical)} DB columns")
        if sample_data.get("sheets"):
            print(f"    📑 Sheets: {sample_data['sheets']}")

        suggestions = ai_suggest_mappings(file_info, headers, canonical, existing_mappings)
        rule_suggestions = ai_suggest_rules(file_info, headers, existing_rules, sample_data)
        store_suggestions(conn, file_info, suggestions, rule_suggestions)

        all_suggestions.append({"file_info": file_info, "suggestions": suggestions, "rules": rule_suggestions})

    if all_suggestions:
        # Alert only includes files that were successfully stored
        stored_files = [s["file_info"] for s in all_suggestions]
        send_new_carrier_alert(stored_files, all_suggestions)

    return all_suggestions