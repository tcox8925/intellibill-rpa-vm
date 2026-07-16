# pch_upload_utils.py  — Postgres, resolver-based (no per-call DB arg)

import uuid
from uuid import uuid4
from datetime import datetime, timezone
import json
import re
from typing import List, Dict, Any, Optional

from psycopg2.extras import execute_values
from rapidfuzz import fuzz

# =========================
# Global DB resolver
# =========================
_db_connection_resolver = None

def set_db_connection_resolver(func):
    """
    Call once at app startup (e.g., in runner):
        from utils.db_utils import get_postgres_connection
        from pch_upload_utils import set_db_connection_resolver
        set_db_connection_resolver(get_postgres_connection)
    """
    global _db_connection_resolver
    _db_connection_resolver = func

def _get_db_connection():
    if not _db_connection_resolver:
        raise RuntimeError("DB connection resolver not set. Call set_db_connection_resolver() first.")
    return _db_connection_resolver()

# =========================
# Helpers
# =========================
PAC_RE = re.compile(r"^\d{10}$")
PECOS_RE = PAC_RE

def _current_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def _as_guid_or_namespace(v: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except Exception:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(v)))

# =========================
# Normalizers
# =========================
def normalize_identifiers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    
    """
    - Ensures id_type, id_issuer, id_description are all filled
    - Accepts id_value or id_type_value
    - Trims strings; drops empty/None id_value rows
    - Validates PECOS format (10 digits)
    - De-duplicates by (id_type, id_value, id_state)
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for r in rows or []:
        r = dict(r)
        label = (r.get("id_type") or r.get("id_issuer") or r.get("id_description") or "").strip() or None
        r["id_type"] = (r.get("id_type") or label or "").strip() or "Unknown"
        r["id_issuer"] = (r.get("id_issuer") or label or "").strip() or r["id_type"]
        r["id_description"] = (r.get("id_description") or label or "").strip() or r["id_type"]

        id_value = r.get("id_value") or r.get("id_type_value")
        if isinstance(id_value, str):
            id_value = id_value.strip()
        r["id_value"] = id_value

        r["id_state"] = (r.get("id_state") or None) or None
        if isinstance(r["id_state"], str):
            r["id_state"] = r["id_state"].strip() or None

        if not r["id_value"]:
            continue

        if r["id_type"].strip().lower() == "pecos" and not PAC_RE.fullmatch(str(r["id_value"])):
            continue

        key = (r["id_type"].strip().lower(), str(r["id_value"]), r["id_state"])
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "id_type": r["id_type"],
            "id_issuer": r["id_issuer"],
            "id_description": r["id_description"],
            "id_value": str(r["id_value"]),
            "id_type_value": str(id_value),
            "id_state": r["id_state"],
            "source": r.get("source"),
        })
    return out


def normalize_care_locations(locs: list, txn_id_provider: str) -> list:
    """
    Normalize free-form Care.Healthline locations into structured rows.
    """
    out = []

    def parse_freeform(s: str):
        s = (s or "").strip()
        if not s:
            return None, None, None, None, None
        loc_type = None
        mtype = re.match(r"^\s*([A-Za-z &/+-]+)\s+(?=\d)", s)
        if mtype:
            loc_type = mtype.group(1).strip()
            rest = s[mtype.end():].strip()
        else:
            rest = s
        m = re.search(r"([^,]+),\s*([A-Z]{2})\s*(\d{5}(?:-\d{4})?)\s*$", rest)
        if not m:
            return loc_type, None, None, None, None
        city, state, zipc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        address_1 = rest[:m.start()].strip().rstrip(",")
        return loc_type, address_1, city, state, zipc

    for loc in locs or []:
        if not isinstance(loc, dict):
            continue
        src = loc.get("source") or "care_healthline"
        locname = loc.get("location_name") or loc.get("type") or None
        address_1, city, state, zipc = loc.get("address_1"), loc.get("city"), loc.get("state"), loc.get("zip")

        if not (address_1 and city and state and zipc):
            raw = loc.get("address") or loc.get("location_name") or ""
            typ, a1, c, st, zp = parse_freeform(raw)
            address_1 = address_1 or a1
            city = city or c
            state = state or st
            zipc = zipc or zp
            locname = locname or typ

        if not (address_1 and city and state and zipc):
            continue

        out.append({
            "txn_id": str(uuid4()),
            "source": src,
            "type": loc.get("type") or (locname or "Practice"),
            "location_name": locname or loc.get("type") or "Practice",
            "contact": loc.get("contact"),
            "fax": loc.get("fax"),
            "address_1": address_1,
            "address_2": loc.get("address_2"),
            "city": city,
            "state": state,
            "zip": zipc,
            "txn_id_provider": txn_id_provider,
            "updated_on": _current_ts()
        })
    return out

def load_column_types(conn):
    """
    Reads Postgres information_schema and returns:
    { "table_name": { "column_name": "data_type" } }
    """
    sql = """
        SELECT 
            table_name,
            column_name,
            data_type
        FROM information_schema.columns
        WHERE table_schema = 'wpo'
          AND table_name LIKE 'pch_caqh_%';
    """

    cur = conn.cursor()
    cur.execute(sql)

    type_map = {}

    for table, column, data_type in cur.fetchall():
        if table not in type_map:
            type_map[table] = {}
        type_map[table][column] = data_type.lower()

    cur.close()
    return type_map

def get_boolean_columns(type_map):
    boolean_columns = {}

    for table, cols in type_map.items():
        boolean_columns[table] = {
            col for col, dtype in cols.items()
            if dtype in ("boolean", "bit", "bit varying")
        }

    return boolean_columns


# =========================
# Uploaders (Postgres)
# =========================
def upload_provider_info(txn_id: str, npi: str, fields: dict, source: str = None):
    """
    UPDATE wpo.pch_provider_info for this provider.
    """
    if not (txn_id and npi and isinstance(fields, dict) and fields):
        return

    # Quote identifiers that collide with keywords
    colmap = {
        "type": '"type"',
        "primary_address_1": "primary_address_1",
        "primary_address_2": "primary_address_2",
        "city": "city",
        "state": "state",
        "zip": "zip",
        "gender": "gender",
        "primary_speciality": "primary_speciality",
        "secondary_speciality": "secondary_speciality",
        "professional_degree": "professional_degree",
        "rx_waiver_expiration_date": "rx_waiver_expiration_date",
        "awards": "awards",
        "language": '"language"',
        "board_cert": "board_cert",
        "board_cert_detail": "board_cert_detail",
        "race": "race",
        "source": '"source"',
    }

    sets, params = [], []
    for k, v in fields.items():
        if k not in colmap or v is None:
            continue
        sval = str(v).strip()
        if not sval:
            continue
        sets.append(f"{colmap[k]} = %s")
        params.append(sval)

    if source:
        sets.append('"source" = %s')
        params.append(source)

    if not sets:
        return

    sets.append("updated_on = %s")
    params.append(_current_ts())

    sql = f"""
    UPDATE wpo.pch_provider_info
       SET {', '.join(sets)}
     WHERE npi = %s AND txn_id = %s
    """

    params.extend([npi, txn_id])

    conn = _get_db_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    if cur.rowcount == 0:
        print(f"[WARN] provider_info not found for npi={npi}, txn_id={txn_id}")
    cur.close(); conn.close()


def upload_locations(txn_id_provider: str, locations: list):
    """
    Bulk insert provider locations.
    """
    rows = []
    for loc in locations or []:
        if not (loc.get("address_1") and loc.get("city") and loc.get("state") and loc.get("zip")):
            continue
        rows.append((
            str(uuid.uuid4()),
            txn_id_provider,
            loc.get("source"),
            loc.get("type"),
            loc.get("location_name"),
            loc.get("contact"),
            loc.get("fax"),
            loc.get("address_1"),
            loc.get("address_2"),
            loc.get("city"),
            loc.get("state"),
            loc.get("zip"),
            _current_ts()
        ))
    if not rows:
        return

    sql = """
    INSERT INTO wpo.pch_provider_location
      (txn_id, txn_id_provider, source, "type", location_name,
       contact, fax, address_1, address_2, city, state, zip, updated_on)
    VALUES %s
    """
    conn = _get_db_connection(); cur = conn.cursor()
    execute_values(cur, sql, rows)
    conn.commit(); cur.close(); conn.close()


def upload_identifiers(txn_id_provider: str, identifiers: List[Dict[str, Any]]):
    """
    Insert into wpo.pch_provider_identifiers (insert-only).
    """
    if not identifiers:
        return

    ts = _current_ts()
    rows, seen = [], set()

    for ident in identifiers:
        if not isinstance(ident, dict):
            continue
        desc = (ident.get("id_description") or ident.get("id_type") or ident.get("id_issuer") or "OTHER")
        desc = str(desc).strip()
        issuer = str(ident.get("id_issuer") or desc).strip() or desc
        raw_val = ident.get("id_type_value", ident.get("id_value"))
        val = (str(raw_val).strip() if raw_val is not None else "")
        if not val:
            continue

        id_state = ident.get("id_state")
        if isinstance(id_state, str):
            id_state = id_state.strip() or None

        if desc.lower() == "pecos" and not PECOS_RE.fullmatch(val):
            continue

        key = (desc.lower(), val, id_state)
        if key in seen:
            continue
        seen.add(key)

        rows.append((
            str(uuid.uuid4()),
            txn_id_provider,
            "Active",
            desc,          # id_type
            issuer,        # id_issuer
            desc,          # id_description
            val,           # id_type_value
            ident.get("id_issue_date"),
            id_state,
            (ident.get("source") or "NPI Registry/TMB").strip(),
            ts
        ))

    if not rows:
        return

    sql = """
    INSERT INTO wpo.pch_provider_identifiers
      (txn_id, txn_id_provider, status,
       id_type, id_issuer, id_description,
       id_type_value, id_issue_date, id_state,
       source, updated_on)
    VALUES %s
    """
    conn = _get_db_connection(); cur = conn.cursor()
    execute_values(cur, sql, rows)
    conn.commit(); cur.close(); conn.close()


def upload_regulatory_validation(txn_id_provider: str, validations):
    """
    Insert summary regulatory validation rows.
    Returns dict of { RAW_SOURCE: inserted_txn_id }
    """
    rows = []
    if isinstance(validations, dict):
        for k, v in validations.items():
            if v is None:
                continue
            rows.append({"source": k, "status": v})
    elif isinstance(validations, list):
        for v in validations:
            if isinstance(v, dict) and "source" in v and "status" in v:
                rows.append({"source": v["source"], "status": v["status"]})
    if not rows:
        return {}

    ts = _current_ts()

    def map_display(raw_source: str) -> str:
        us = (raw_source or "").upper()
        if us in {"BOARD", "CRIMINAL", "MALPRACTICE", "NON-TMB"}:
            return "Texas Medical Board"
        if us == "OIG":
            return "OIG"
        if us == "NPI_DEACTIVATION":
            return "NPI Registry"
        return raw_source

    data = []
    reg_txn_ids = {}
    for r in rows:
        raw_source = str(r.get("source", "")).upper()
        display_source = map_display(raw_source)
        inserted_txn = str(uuid4())
        data.append((
            str(uuid4()),
            inserted_txn,
            f"{raw_source}_{txn_id_provider}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            r["status"],
            display_source,
            ts,
            txn_id_provider
        ))
        reg_txn_ids[raw_source] = inserted_txn

    sql = """
    INSERT INTO wpo.pch_regulatory_validation
      (pk_id,txn_id, audit_id, status, source, date_time, txn_id_provider)
    VALUES %s
    """
    conn = _get_db_connection(); cur = conn.cursor()
    execute_values(cur, sql, data)
    conn.commit(); cur.close(); conn.close()
    return reg_txn_ids


def upload_regulatory_fail_details(txn_id_provider: str, rows: list):
    """
    Insert detailed regulatory action rows (insert-only).
    """
    if not rows:
        return
    ts = _current_ts()
    data = []
    for r in rows:
        if not r:
            continue
        txn_id_reg = r.get("txn_id_reg")
        if not txn_id_reg:
            continue
        data.append((
            str(uuid4()),
            txn_id_reg,
            txn_id_provider,
            (r.get("source") or "Texas Medical Board"),
            (r.get("check_type") or ""),
            r.get("action_date"),
            (r.get("description") or ""),
            ts
        ))
    if not data:
        return

    sql = """
    INSERT INTO wpo.pch_regulatory_fail_details
      (txn_id, txn_id_reg, txn_id_provider, source, check_type, action_date, description, created_on)
    VALUES %s
    """
    conn = _get_db_connection(); cur = conn.cursor()
    execute_values(cur, sql, data)
    conn.commit(); cur.close(); conn.close()


def upload_education(txn_id_provider: str, education: list, source: str = None):
    """
    Insert education rows (insert-only).
    """
    if not education:
        return
    ts = _current_ts()
    data = []
    for edu in education:
        if not (edu.get("program_name") and edu.get("grad_year")):
            continue
        data.append((
            str(uuid.uuid4()),
            txn_id_provider,
            edu.get("program_name"),
            edu.get("type"),
            edu.get("specialty") or edu.get("type"),
            edu.get("grad_year"),
            edu.get("location"),
            source or "EDU_CONSOLIDATED",
            ts
        ))
    if not data:
        return

    sql = """
    INSERT INTO wpo.pch_provider_education
      (txn_id, txn_id_provider, school_program_name, "type", specialty, grad_year, "location", "source", updated_on)
    VALUES %s
    """
    conn = _get_db_connection(); cur = conn.cursor()
    execute_values(cur, sql, data)
    conn.commit(); cur.close(); conn.close()


def upload_affiliations(txn_id_provider: str, affiliations: list, source: str = None):
    """
    Insert affiliations (insert-only) with simple de-dup across batch.
    """
    if not affiliations:
        print("[AFFILIATIONS] nothing to upload")
        return
    ts = _current_ts()
    inserted = skipped = 0
    seen = set()
    data = []

    for aff in affiliations:
        name = (aff.get("affiliate_name") or aff.get("name") or "").strip()
        loc = aff.get("location")
        src = source or aff.get("source") or "GPT_AFFILIATIONS"
        if not name:
            skipped += 1
            continue
        key = (name.lower(), (loc or "").lower())
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        data.append((str(uuid.uuid4()), txn_id_provider, name, loc, src, ts))
        inserted += 1

    if data:
        sql = """
        INSERT INTO wpo.pch_affiliations
          (txn_id, txn_id_provider, affiliate_name, "location", "source", updated_on)
        VALUES %s
        """
        conn = _get_db_connection(); cur = conn.cursor()
        execute_values(cur, sql, data)
        conn.commit(); cur.close(); conn.close()

    print(f"[AFFILIATIONS] inserted={inserted} skipped={skipped}")


def upload_carriers(txn_id_provider: str, carriers: list, source: str = None):
    """
    Insert carriers (insert-only). NAIC can be NULL if unknown.
    """
    if not carriers:
        return
    ts = _current_ts()
    data = []
    inserted = skipped = 0
    for c in carriers:
        name = (c.get("carrier_name") or c.get("name") or "").strip()
        naic = (c.get("carrier_naic_number") or c.get("carrier_niac_number") or c.get("naic_number") or "")
        naic = naic.strip() if isinstance(naic, str) else (str(naic).strip() if naic is not None else "")
        network = c.get("network_status")
        if not name:
            skipped += 1
            continue
        data.append((
            str(uuid.uuid4()),
            name,
            naic if naic else None,
            network,
            txn_id_provider,
            source or c.get("source") or "GPT_NAIC",
            ts
        ))
        inserted += 1

    sql = """
    INSERT INTO wpo.pch_carriers
      (txn_id, carrier_name, carrier_niac_number, network_status, txn_id_provider, "source", updated_on)
    VALUES %s
    """
    conn = _get_db_connection(); cur = conn.cursor()
    execute_values(cur, sql, data)
    conn.commit(); cur.close(); conn.close()
    print(f"[CARRIERS] inserted={inserted} skipped={skipped}")


def record_sources_used(txn_id_provider: str, sources: list):
    """
    Insert source tracking row (insert-only).
    """
    sources = sorted(set([s for s in (sources or []) if s]))
    sources_json = json.dumps(sources, ensure_ascii=False)
    sql = """
    INSERT INTO wpo.pch_source_tracking
      (txn_id, sources, txn_id_provider, updated_on)
    VALUES (%s, %s, %s, %s)
    """
    conn = _get_db_connection(); cur = conn.cursor()
    cur.execute(sql, (str(uuid.uuid4()), sources_json, txn_id_provider, _current_ts()))
    conn.commit(); cur.close(); conn.close()

# =========================
# Validators / Updates
# =========================
def validate_tmb_license_owner(txn_id_provider: str, npi: str, tmb_name: str) -> tuple[bool, float, Optional[str]]:
    """
    Compare TMB provider name with provider_info (txn_id or NPI).
    """
    if not (tmb_name and (txn_id_provider or npi)):
        return False, 0.0, None
    try:
        conn = _get_db_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT first_name, last_name
            FROM wpo.pch_provider_info
            WHERE txn_id = %s AND npi = %s
            LIMIT 1
        """, (txn_id_provider, npi))
        row = cur.fetchone()
        cur.close(); conn.close()

        if not row:
            print(f"[DEBUG] No provider_info row found for txn_id={txn_id_provider} or npi={npi}")
            return False, 0.0, None

        first_name, last_name = (row[0] or "").strip().upper(), (row[1] or "").strip().upper()
        db_name = f"{first_name} {last_name}".strip()

        tmb_clean = re.sub(r"[,.]", "", tmb_name.upper())
        for suffix in ["MD", "DO", "PA", "NP", "DDS", "DMD", "FNP", "JR", "SR"]:
            tmb_clean = tmb_clean.replace(f" {suffix}", "")
        tmb_clean = tmb_clean.strip()

        sim1 = fuzz.token_set_ratio(db_name, tmb_clean)
        sim2 = fuzz.token_set_ratio(f"{last_name} {first_name}", tmb_clean)
        similarity = max(sim1, sim2)
        return (similarity >= 95), similarity, db_name
    except Exception as e:
        print(f"[WARN] validate_tmb_license_owner failed: {e}")
        return False, 0.0, None


def update_license_status(txn_id_provider: str, license_number: str, license_status: str):
    """
    Update or insert license status in regulatory validation table.
    Audit ID format: LICENSE_NUMBER_<txn_id_provider>_<timestamp>
    """
    if not txn_id_provider or not license_number:
        return

    conn = _get_db_connection()
    cur = conn.cursor()

    status_clean = (license_status or "").strip().capitalize()
    if status_clean not in ["Active", "Inactive"]:
        status_clean = "Active"

    audit_id = f"LICENSE_NUMBER_{txn_id_provider}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # Check if exists
    sql_check = """
        SELECT COUNT(*)
        FROM wpo.pch_regulatory_validation
        WHERE txn_id_provider = %s
          AND audit_id ILIKE 'LICENSE_NUMBER%%'
          AND source = 'Texas Medical Board'
    """
    cur.execute(sql_check, (txn_id_provider,))
    exists = cur.fetchone()[0] > 0

    if exists:
        sql_update = """
            UPDATE wpo.pch_regulatory_validation
            SET status = %s, date_time = %s
            WHERE txn_id_provider = %s
              AND audit_id ILIKE 'LICENSE_NUMBER%%'
              AND source = 'Texas Medical Board'
        """
        cur.execute(sql_update, (status_clean, _current_ts(), txn_id_provider))
    else:
        sql_insert = """
            INSERT INTO wpo.pch_regulatory_validation
              (txn_id, audit_id, status, source, date_time, txn_id_provider)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cur.execute(sql_insert, (
            str(uuid4()),
            audit_id,
            status_clean,
            "Texas Medical Board",
            _current_ts(),
            txn_id_provider
        ))

    conn.commit()
    cur.close(); conn.close()
    print(f"[DEBUG] License status '{status_clean}' recorded for license {license_number}")

def upload_caqh_results(txn_id_provider: str, results: dict):

    conn = _get_db_connection()
    cur = conn.cursor()

    # Load datatype map ONCE
    type_map = load_column_types(conn)
    boolean_map = get_boolean_columns(type_map)

    inserted_any = False

    for table_name, rows in results.items():

        if not isinstance(rows, list) or not rows:
            continue

        print(f"[UPLOAD] Preparing to insert {len(rows)} rows into {table_name}")

        table_boolean_cols = boolean_map.get(table_name, set())

        # Normalize boolean values ONLY for boolean columns
        for r in rows:
            r["txn_id_provider"] = txn_id_provider

            for k, v in r.items():
                if k not in table_boolean_cols:
                    continue

                # Normalize allowed truthy/falsey values
                s = str(v).strip().lower()
                if s in ("1", "true", "yes"):
                    r[k] = True
                elif s in ("0", "false", "no"):
                    r[k] = False
                else:
                    r[k] = None

        cols = list(rows[0].keys())
        colnames = ",".join(f'"{c}"' for c in cols)

        sql = f"INSERT INTO wpo.{table_name} ({colnames}) VALUES %s"
        data = [tuple(row.get(c) for c in cols) for row in rows]

        try:
            execute_values(cur, sql, data)
            inserted_any = True
            print(f"[UPLOAD] {len(rows)} rows inserted into {table_name}")
        except Exception as e:
            print(f"[WARN] Failed bulk insert into {table_name}: {e}")

    if inserted_any:
        conn.commit()
        print("[UPLOAD] All CAQH tables committed successfully.")

    cur.close()
    conn.close()