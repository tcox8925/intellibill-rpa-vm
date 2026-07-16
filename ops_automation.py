import os
import pytz
import pandas as pd
from datetime import datetime, timedelta, date
from psycopg2.extras import execute_batch, execute_values
from collections import defaultdict

# Azure Authentication
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient

# DB Drivers
import pyodbc
import psycopg2

#for json
import json

# ===========================================================
# 🔐 Manual Override Maps
# ===========================================================
ACR_NO_ACTION = {
    2931751000020024151, 2931751000020024196,
    2931751000337238821, 2931751000020024153
}

ACR_FORCE_ACTIVE = {
    2931751000113881001, 2931751000159101001, 2931751000347318116,
    2931751000020024158, 2931751000060862001, 2931751000461056007,
    2931751000368291166, 2931751000020024164, 2931751000020024163,
    2931751000368291177, 2931751000339907203, 2931751000020024197,
    2931751000382772986, 2931751000347318787, 2931751000463023052,
    2931751000382772962, 2931751000481551992, 2931751000368291161,
    2931751000368200015, 2931751000487341104, 2931751000020024152,
    2931751000039320019, 2931751000299987001, 2931751000481362918,
    2931751000361213696, 2931751000487341081, 2931751000358392001,
    2931751000361927203, 2931751000487521076, 2931751000514170443,
    2931751000571257066, 2931751000104828062, 2931751000560092589
}

ACU_HCSC_FORCE_ACTIVE = {
    2931751000020024204, 2931751000035531281,
    2931751000119585861, 2931751000034092012,
    2931751000020024151
}

BOB_HCSC_FORCE_ACTIVE = {
    2931751000020024204, 2931751000035531281,
    2931751000119585861, 2931751000034092012,
    2931751000020024151, 2931751000481362918
}

ACU_SMA_FORCE_ACTIVE = {
    2931751000020024174, 2931751000020024161,
    2931751000020024178, 2931751000337238821,
    2931751000358392001, 2931751000284358302,
    2931751000020024182,
    2931751000048354001, 2931751000020024190,
    2931751000020024195, 2931751000020024195,
    2931751000492608306, 2931751000020024154
}

BOB_SMA_FORCE_ACTIVE = {
    2931751000337238821, 2931751000020024178,
    2931751000492608306, 2931751000020024161,
    2931751000020024174, 2931751000358392001,
    2931751000048354001, 2931751000060862001,
    2931751000020024190, 2931751000020024195,
    2931751000020024182,
}

#These 2 carriers are not present in the com_process matrix but are there in the logs -  so treating them as automated
COM_FORCE_ACTIVE = { 
    2931751000020024189
}
# Special carriers
UHC = 2931751000147793570
AMBETTER = 2931751000020024159

EXC_REQUIRED_PREFIXES = (
    "raw_exc_employee_",
    "raw_exc_sam_",
    "raw_exc_oig_",
)

EXC_PROCESS_NAME = "EXC"
EXC_AUTOMATION_TYPE = "Manual - Portal"
EXC_CADENCE = "Monthly - 3rd Week"

# ===========================================================
# 🔐 Key Vault & DB configuration
# ===========================================================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

SYNAPSE_DB = {
    "server": "834analyticsynapse.sql.azuresynapse.net",
    "database": "834_analytics_dev",
    "driver": "{ODBC Driver 17 for SQL Server}"
}

POSTGRES_DB = {
    "server": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "username": os.getenv("DEFAULT834_DB_USER", "")
}


def get_secret(name):
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=DefaultAzureCredential())
    return client.get_secret(name).value


# ===========================================================
# 🔌 Synapse DB connection
# ===========================================================
def get_synapse_connection():
    client_id = get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", ""))
    client_secret = get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", ""))
    tenant_id = get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", ""))

    conn_str = (
        f"DRIVER={SYNAPSE_DB['driver']};"
        f"SERVER={SYNAPSE_DB['server']};"
        f"DATABASE={SYNAPSE_DB['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};PWD={client_secret};Authority Id={tenant_id};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str)

# ===========================================================
# 🏛 PostgreSQL connection
# ===========================================================
def get_postgres_connection():
    tenant = get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", ""))
    client = get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", ""))
    secret = get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", ""))

    token = ClientSecretCredential(
        tenant, client, secret
    ).get_token("https://ossrdbms-aad.database.windows.net/.default").token

    return psycopg2.connect(
        host=POSTGRES_DB["server"],
        dbname=POSTGRES_DB["database"],
        user=POSTGRES_DB["username"],
        password=token,
        sslmode="require"
    )

def to_db_null(v):
    """Convert pandas NaN / NA to real None so Postgres stores NULL."""
    if pd.isna(v):
        return None
    return v

ACC_DEV = set()
ACR_DEV = set()

PROCESS_ID_MAP = {}

entity_id = "990980340"
sub_entity_id = "990980340001"

SHARED_FILE_KEYWORDS = {
    ("ACU", "HCSC"): "raw_acu_hcsc_",
    ("ACU", "SMA"): "raw_acu_sma_",
    ("BOB", "HCSC"): "raw_bob_hcsc_",
    ("BOB", "SMA"): "raw_bob_sma_",
}

SHARED_GROUPS = {
    ("ACU", "HCSC"): ACU_HCSC_FORCE_ACTIVE,
    ("ACU", "SMA"): ACU_SMA_FORCE_ACTIVE,
    ("BOB", "HCSC"): BOB_HCSC_FORCE_ACTIVE,
    ("BOB", "SMA"): BOB_SMA_FORCE_ACTIVE,
}

FORCE_BASELINE_TODAY = True
MANUAL_DATE_STR = None #"2026-03-18"
# ===========================================================
# 🧩 Generic helpers
# ===========================================================
def _std_cadence_word(value):
    s = (value or "").strip()
    if s.lower() == "daily":
        return "Daily"
    if s.lower() == "weekly":
        return "Weekly"
    if s.lower() == "monthly":
        return "Monthly"
    return s


def _is_varies(cadence):
    s = (cadence or "").strip().lower()
    return s in ("", "cadence varies")

def _normalize_notes_json(value):
    if value is None:
        return {}

    if isinstance(value, dict):
        return value if value else {}

    if isinstance(value, str):
        text = value.strip()
        if text in ("", "{}", "null", "None"):
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}

def _normalize_interruptions_json(value):
    if value is None:
        return {}

    if isinstance(value, dict):
        return value if value else {}

    if isinstance(value, str):
        text = value.strip()
        if text in ("", "{}", "null", "None"):
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}

def _merge_interruptions_json(prev_value, today_value):
    prev_json = _normalize_interruptions_json(prev_value)
    today_json = _normalize_interruptions_json(today_value)

    merged = dict(prev_json)

    for key, today_val in today_json.items():
        prev_val = merged.get(key)

        prev_text = "" if prev_val is None else str(prev_val).strip()
        today_text = "" if today_val is None else str(today_val).strip()

        if prev_text and today_text:
            if prev_text == today_text:
                merged[key] = today_text
            else:
                merged[key] = f"{prev_text},{today_text}"
        elif today_text:
            merged[key] = today_text
        elif prev_text:
            merged[key] = prev_text
        else:
            merged[key] = ""

    return {k: v for k, v in merged.items() if str(v).strip()}

def _normalize_log_filename(file_name):
    name = str(file_name or "").strip().lower()
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def _shared_keyword(proc, grp):
    return SHARED_FILE_KEYWORDS[(proc.upper().strip(), grp.upper().strip())]


def _filename_contains_shared_keyword(file_name, keyword):
    return keyword in _normalize_log_filename(file_name)


def _is_truthy_flag(value):
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "t", "y", "yes")

def _is_active_automated(value):
    if value is None:
        return False
    return str(value).strip().lower() == "active"

def _to_date(x):
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return datetime.fromisoformat(str(x)).date()
    except Exception:
        return None


def _extract_mmddyyyy_suffix(name, fallback_date):
    base = _normalize_log_filename(name)
    parts = base.split("_")
    if parts:
        token = parts[-1]
        if len(token) == 8 and token.isdigit():
            try:
                mm, dd, yyyy = int(token[:2]), int(token[2:4]), int(token[4:])
                return date(yyyy, mm, dd)
            except Exception:
                pass
    return fallback_date


def _status_to_file_value(status):
    success_terms = {"success", "succeeded", "successful", "successfully", "completed", "1"}
    fail_terms = {"fail", "failed", "error", "errored", "timeout", "not processed", "0"}
    status_l = str(status or "").strip().lower()
    if any(term in status_l for term in success_terms):
        return 1
    if any(term in status_l for term in fail_terms):
        return 0
    return None


def _notes_to_text(value):
    if value is None:
        return ""

    if isinstance(value, dict):
        if not value:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value).strip()

    if isinstance(value, list):
        if not value:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value).strip()

    if isinstance(value, str):
        text = value.strip()
        if text in ("", "{}", "[]", "null", "None"):
            return ""
        return text

    text = str(value).strip()
    if text in ("", "{}", "[]", "null", "None"):
        return ""
    return text


def _merge_note_values(*values):
    parts = []
    seen = set()
    for value in values:
        text = _notes_to_text(value)
        if not text:
            continue
        key = text.strip().lower()
        if key not in seen:
            seen.add(key)
            parts.append(text)
    return ", ".join(parts)


def _empty_last_run_date():
    return {"acu": "", "bob": "", "com": "", "acc": "", "acr": ""}

def _exc_month_key(d):
    return d.replace(day=1)

def _load_previous_day_process_map(ref_date):
    prev_date = ref_date - timedelta(days=1)

    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            carrier_id,
            acu_process,
            bob_process,
            acc_process,
            acr_process
        FROM ops_srv.ops_automation_dashboard
        WHERE record_date = %s
        """,
        (prev_date,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    prev_map = {}
    for carrier_id, acu_process, bob_process, acc_process, acr_process in rows:
        prev_map[str(carrier_id).strip()] = {
            "acu_process": acu_process,
            "bob_process": bob_process,
            "acc_process": acc_process,
            "acr_process": acr_process,
        }

    return prev_map

# ===========================================================
# 📥 Matrix / baseline loaders
# ===========================================================
def load_matrices():
    global ACC_DEV, ACR_DEV

    conn = get_postgres_connection()
    cur = conn.cursor()

    # Base carriers
    cur.execute("SELECT id, vendor_name, status FROM wpo.lup_carriers")
    carrier_rows = cur.fetchall()
    carriers = pd.DataFrame(
        [(r[0], r[1] or f"Carrier_{r[0]}", r[2] or "Active") for r in carrier_rows],
        columns=["carrier_id", "carrier_name", "carrier_status"],
    )
    carriers["carrier_id"] = carriers["carrier_id"].astype("int64")

    # RPA matrix for ACU/BOB/COM automation types + cadence
    cur.execute(
        """
        SELECT carrier_id::BIGINT AS carrier_id,
               upper(btrim(process_name)) AS process_name,
               pickup_method,
               cadence,
               target_dates,
               automated
        FROM wpo.ops_rpa_matrix
        WHERE process_name IN ('ACU','BOB','COM')
        """
    )
    rpa_rows = cur.fetchall()

    auto_maps = {"ACU": {}, "BOB": {}, "COM": {}}
    cadence_maps = {"ACU": {}, "BOB": {}, "COM": {}}
    varies_sets = {"ACU": set(), "BOB": set(), "COM": set()}
    automated_sets = {"ACU": set(), "BOB": set(), "COM": set()}
    download_sets = {"ACU": set(), "BOB": set(), "COM": set()}

    for cid, pname, pickup, cadence, target_dates, automated in rpa_rows:
        if not cid or not pname:
            continue
        pname = pname.upper().strip()

        # Only ACU/BOB should now respect automated column here.
        if pname in ("ACU", "BOB"):
            if _is_active_automated(automated):
                automated_sets[pname].add(cid)
        else:
            automated_sets[pname].add(cid)

        if pickup and cid not in auto_maps[pname]:
            auto_maps[pname][cid] = pickup

        # Only ACU/BOB download flags should respect automated column here.
        if pname in ("ACU", "BOB"):
            if pickup and "RPA" in str(pickup).upper() and _is_active_automated(automated):
                download_sets[pname].add(cid)
        else:
            if pickup and "RPA" in str(pickup).upper():
                download_sets[pname].add(cid)

        if cid not in cadence_maps[pname]:
            cadence_maps[pname][cid] = _std_cadence_word(cadence) or "Daily"

        if _is_varies(cadence):
            varies_sets[pname].add(cid)

    # Process matrices
    cur.execute(
        """
        SELECT DISTINCT carrier_id::BIGINT AS carrier_id,
               automated
        FROM wpo.ops_load_matrix_acu
        WHERE process_type = 'ACU'
        """
    )
    acu_process_rows = cur.fetchall()
    acu_process = {cid for cid, automated in acu_process_rows if cid and _is_active_automated(automated)}

    cur.execute(
        """
        SELECT DISTINCT carrier_id::BIGINT AS carrier_id,
               automated
        FROM wpo.ops_process_matrix
        WHERE process_type = 'BOB'
        """
    )
    bob_process_rows = cur.fetchall()
    bob_process = {cid for cid, automated in bob_process_rows if cid and _is_active_automated(automated)}

    # COM untouched
    cur.execute(
        """
        SELECT DISTINCT carrier_id::BIGINT AS carrier_id,
                automated
        FROM wpo.ops_process_matrix_com
        WHERE process_type = 'COM'
        """
    )
    com_process_rows = cur.fetchall()
    com_process = {cid for cid, automated in com_process_rows if cid and _is_active_automated(automated)}

    # ACC
    cur.execute(
        """
        SELECT DISTINCT carrier_id::BIGINT AS carrier_id,
               COALESCE(in_development::INT, 0) AS in_development,
               active_flag,
               automated
        FROM wpo.ops_acc_process_matrix
        """
    )
    acc_rows = cur.fetchall()
    acc_all_set = {cid for cid, _dev, _af, automated in acc_rows if cid and _is_active_automated(automated)}
    acc = {cid for cid, dev, _af, automated in acc_rows if cid and dev == 0 and _is_active_automated(automated)}
    ACC_DEV = {cid for cid, dev, _af, _automated in acc_rows if cid and dev == 1}
    acc_rpa_on = {
        cid for cid, dev, af, automated in acc_rows
        if cid and dev == 0 and _is_active_automated(automated) and _is_truthy_flag(af)
    }
    acc_cad_map = {cid: "Daily" for cid, _dev, _af, _automated in acc_rows if cid}

    # ACR
    cur.execute(
        """
        SELECT DISTINCT carrier_id::BIGINT AS carrier_id,
               COALESCE(in_development::INT, 0) AS in_development,
               active_flag,
               schedule,
               automated
        FROM wpo.ops_acr_process_matrix
        """
    )
    acr_rows = cur.fetchall()
    acr_all_set = {cid for cid, _dev, _af, _sched, automated in acr_rows if cid and _is_active_automated(automated)}
    acr = {cid for cid, dev, _af, _sched, automated in acr_rows if cid and dev == 0 and _is_active_automated(automated)}
    ACR_DEV = {cid for cid, dev, _af, _sched, _automated in acr_rows if cid and dev == 1}
    acr_rpa_on = {
        cid for cid, dev, af, _sched, automated in acr_rows
        if cid and dev == 0 and _is_active_automated(automated) and _is_truthy_flag(af)
    }
    acr_cad_map = {}
    for cid, _dev, _af, sched, _automated in acr_rows:
        if not cid:
            continue
        val = _std_cadence_word(sched)
        if val:
            acr_cad_map[cid] = val
    for cid in ACR_FORCE_ACTIVE:
        acr_cad_map[cid] = "Daily"

    cur.close()
    conn.close()

    carriers["acu_automation_type"] = carriers["carrier_id"].map(auto_maps["ACU"])
    carriers["bob_automation_type"] = carriers["carrier_id"].map(auto_maps["BOB"])
    carriers["com_automation_type"] = carriers["carrier_id"].map(auto_maps["COM"])
    carriers["acc_automation_type"] = carriers["carrier_id"].apply(
        lambda cid: "RPA - Portal" if cid in acc_all_set else None
    )

    def _acr_auto_type(cid):
        if cid in ACR_DEV:
            return "RPA - Portal"
        if cid in ACR_NO_ACTION:
            return None
        if cid in ACR_FORCE_ACTIVE:
            return "RPA - Email"
        if cid in acr:
            return "RPA - Portal"
        return None

    carriers["acr_automation_type"] = carriers["carrier_id"].apply(_acr_auto_type)
    carriers["acu_cadence"] = carriers["carrier_id"].map(cadence_maps["ACU"])
    carriers["bob_cadence"] = carriers["carrier_id"].map(cadence_maps["BOB"])
    carriers["com_cadence"] = carriers["carrier_id"].map(cadence_maps["COM"])
    carriers["acc_cadence"] = carriers["carrier_id"].map(acc_cad_map)
    carriers["acr_cadence"] = carriers["carrier_id"].map(acr_cad_map)

    print(
        f"[BASELINE] carriers={len(carriers)} ACU_DL={len(download_sets['ACU'])} "
        f"BOB_DL={len(download_sets['BOB'])} COM_DL={len(download_sets['COM'])} "
        f"ACU_P={len(acu_process)} BOB_P={len(bob_process)} COM_P={len(com_process)} "
        f"ACC={len(acc)} ACR={len(acr)}"
    )

    return (
        carriers,
        download_sets["ACU"],
        download_sets["BOB"],
        download_sets["COM"],
        acu_process,
        bob_process,
        com_process,
        acc,
        acr,
        acc_rpa_on,
        acr_rpa_on,
        automated_sets["ACU"],
        automated_sets["BOB"],
        automated_sets["COM"],
        acc_all_set,
        acr_all_set,
        varies_sets["ACU"],
        varies_sets["BOB"],
        varies_sets["COM"],
    )


# ===========================================================
# 🧠 Stage logic
# ===========================================================
def derive_stage(download, process):
    if process == 1:
        return 1
    if download == 2 and process == 2:
        return 2
    if download == 1 and process != 1:
        return 0
    if download == 0 or process == 0:
        return 0
    return 0


def baseline_row(
    row,
    acu_dl,
    bob_dl,
    com_dl,
    acu_ps,
    bob_ps,
    com_ps,
    acc,
    acr,
    acc_rpa_on,
    acr_rpa_on,
    acu_auto_set,
    bob_auto_set,
    com_auto_set,
    acc_all_set,
    acr_all_set,
    acu_varies_set,
    bob_varies_set,
    com_varies_set,
):
    cid = row.carrier_id

    acu_automated = 1 if cid in acu_auto_set else 0
    bob_automated = 1 if cid in bob_auto_set else 0
    com_automated = 1 if cid in com_auto_set else 0
    acc_automated = 1 if cid in acc_all_set else 0
    acr_automated = 1 if (cid in acr_all_set or cid in ACR_FORCE_ACTIVE) else 0

    acu_proc_automated = 1 if (cid in acu_ps or cid in ACU_HCSC_FORCE_ACTIVE) else 0
    bob_proc_automated = 1 if cid in bob_ps else 0
    com_proc_automated = 1 if (cid in com_ps or cid in COM_FORCE_ACTIVE) else 0

    if str(row.carrier_status).lower() == "inactive":
        return dict(
            acu_download=2, acu_process=2, acu_status=2,
            bob_download=2, bob_process=2, bob_status=2,
            com_download=2, com_process=2, com_status=2,
            acc_rpa=2, acc_process=2, acc_status=2,
            acr_rpa=2, acr_process=2, acr_status=2,
            acu_automated=2, bob_automated=2, com_automated=2,
            acc_automated=2, acr_automated=2,
            acu_proc_automated=2, bob_proc_automated=2, com_proc_automated=2,
        )

    # ACC
    if cid in ACC_DEV:
        acc_rpa = 3
        acc_process = 3
        acc_status = 3
        acc_automated = 3
    else:
        acc_rpa = 1 if cid in acc_rpa_on else 0
        acc_process = 0
        acc_status = derive_stage(acc_rpa, acc_process)

    # ACR
    if cid in ACR_DEV:
        acr_rpa = 3
        acr_process = 3
        acr_status = 3
        acr_automated = 3
    else:
        acr_hard_no_action = (cid in ACR_NO_ACTION and cid not in ACR_FORCE_ACTIVE) or (cid in {UHC, AMBETTER})
        if acr_hard_no_action:
            acr_rpa = 2
            acr_process = 2
            acr_status = 2
            acr_automated = 2
        else:
            acr_rpa = 1 if cid in acr_rpa_on else 0
            acr_process = 0
            acr_status = derive_stage(acr_rpa, acr_process)
            if cid in ACR_FORCE_ACTIVE:
                acr_status = 1
                acr_automated = 1

    # ACU / BOB / COM defaults
    if cid in acu_varies_set:
        acu_download = 1
        acu_process = 1
    else:
        acu_download = 0
        acu_process = 1 if cid in acu_ps else 0
    acu_status = derive_stage(acu_download, acu_process)

    if cid in bob_varies_set:
        bob_download = 1
        bob_process = 1
    else:
        bob_download = 0
        bob_process = 1 if cid in bob_ps else 0
    bob_status = derive_stage(bob_download, bob_process)

    if cid in com_varies_set:
        com_download = 1
        com_process = 1
    else:
        com_download = 0
        com_process = 1 if cid in com_ps else 0
    com_status = derive_stage(com_download, com_process)

    return dict(
        acu_download=acu_download, acu_process=acu_process, acu_status=acu_status,
        bob_download=bob_download, bob_process=bob_process, bob_status=bob_status,
        com_download=com_download, com_process=com_process, com_status=com_status,
        acc_rpa=acc_rpa, acc_process=acc_process, acc_status=acc_status,
        acr_rpa=acr_rpa, acr_process=acr_process, acr_status=acr_status,
        acu_automated=acu_automated, bob_automated=bob_automated, com_automated=com_automated,
        acc_automated=acc_automated, acr_automated=acr_automated,
        acu_proc_automated=acu_proc_automated, bob_proc_automated=bob_proc_automated, com_proc_automated=com_proc_automated
    )

def get_previous_exc_state(conn, today):
    exc_month_key = _exc_month_key(today)

    cur = conn.cursor()
    cur.execute(
        """
        SELECT notes, interruptions, last_run_date
        FROM ops_srv.ops_automation_dashboard_exc
        WHERE record_date < %s
        ORDER BY record_date DESC
        LIMIT 1
        """,
        (exc_month_key,),
    )
    row = cur.fetchone()
    cur.close()

    if not row:
        return None, None, None
    return row[0], row[1], row[2]


def get_existing_today_exc_state(conn, today):
    exc_month_key = _exc_month_key(today)

    cur = conn.cursor()
    cur.execute(
        """
        SELECT notes, interruptions, last_run_date
        FROM ops_srv.ops_automation_dashboard_exc
        WHERE record_date = %s
          AND process_name = %s
        LIMIT 1
        """,
        (exc_month_key, "Exclusion Report"),
    )
    row = cur.fetchone()
    cur.close()

    if not row:
        return None, None, None
    return row[0], row[1], row[2]


def build_exc_dashboard_baseline(conn_prev, target_date):
    exc_month_key = _exc_month_key(target_date)

    prev_notes, prev_interruptions, prev_last_run = get_previous_exc_state(conn_prev, target_date)
    today_notes, today_interruptions, today_last_run = get_existing_today_exc_state(conn_prev, target_date)

    notes = _merge_note_values(today_notes if today_notes is not None else prev_notes)

    if today_interruptions is not None and str(today_interruptions).strip() not in ("", "null", "None"):
        interruptions = str(today_interruptions).strip()
    elif prev_interruptions is not None and str(prev_interruptions).strip() not in ("", "null", "None"):
        interruptions = str(prev_interruptions).strip()
    else:
        interruptions = ""

    if today_last_run is not None and str(today_last_run).strip() not in ("", "null", "None"):
        last_run_date = str(today_last_run).strip()
    elif prev_last_run is not None and str(prev_last_run).strip() not in ("", "null", "None"):
        last_run_date = str(prev_last_run).strip()
    else:
        last_run_date = ""

    return {
        "record_date": exc_month_key,
        "process_name": "Exclusion Report",
        "exc_automation_type": EXC_AUTOMATION_TYPE,
        "interruptions": interruptions,
        "notes": notes,
        "entity_id": entity_id,
        "sub_entity_id": sub_entity_id,
        "exc_process": 0,
        "exc_cadence": EXC_CADENCE,
        "last_run_date": last_run_date,
    }


def insert_or_update_exc_snapshot(row, today, run_ts):
    conn = get_postgres_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO ops_srv.ops_automation_dashboard_exc (
            record_date,
            process_name,
            last_updated,
            exc_automation_type,
            interruptions,
            notes,
            entity_id,
            sub_entity_id,
            exc_process,
            exc_cadence,
            last_run_date
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (record_date, process_name)
        DO UPDATE SET
            last_updated = EXCLUDED.last_updated,
            exc_automation_type = EXCLUDED.exc_automation_type,
            interruptions = EXCLUDED.interruptions,
            notes = EXCLUDED.notes,
            entity_id = EXCLUDED.entity_id,
            sub_entity_id = EXCLUDED.sub_entity_id,
            exc_process = EXCLUDED.exc_process,
            exc_cadence = EXCLUDED.exc_cadence,
            last_run_date = EXCLUDED.last_run_date
        """,
        (
            row["record_date"],
            row["process_name"],
            run_ts,
            row["exc_automation_type"],
            row["interruptions"],
            row["notes"],
            row["entity_id"],
            row["sub_entity_id"],
            row["exc_process"],
            row["exc_cadence"],
            row["last_run_date"],
        ),
    )

    conn.commit()
    cur.close()
    conn.close()


def update_exc_dashboard(today, run_ts):
    exc_month_key = _exc_month_key(today)
    exc_state = _build_exc_month_state(today)
    exc_last_success_all_time = _build_exc_all_time_last_success()

    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE ops_srv.ops_automation_dashboard_exc
        SET exc_process = %s,
            exc_automation_type = %s,
            exc_cadence = %s,
            last_run_date = %s,
            last_updated = %s
        WHERE record_date = %s
          AND process_name = %s
        """,
        (
            exc_state["process_value"],
            EXC_AUTOMATION_TYPE,
            EXC_CADENCE,
            "" if exc_last_success_all_time is None else str(exc_last_success_all_time),
            run_ts,
            exc_month_key,
            "Exclusion Report",
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def sync_exc_dashboard_interruptions(today, run_ts):
    exc_month_key = _exc_month_key(today)

    conn = get_postgres_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT interruption_id
        FROM ops_srv.service_interruption
        WHERE issue_status = 'Open'
          AND interruption_id IS NOT NULL
          AND issue_date IS NOT NULL
          AND CAST(issue_date AS DATE) <= %s
          AND upper(btrim(process_name)) = 'EXC'
          AND carrier_id IS NULL
        ORDER BY interruption_id
        """,
        (today,),
    )
    rows = cur.fetchall()

    interruption_ids = []
    seen = set()
    for (iid,) in rows:
        text = str(iid).strip()
        if text and text not in seen:
            seen.add(text)
            interruption_ids.append(text)

    payload = ",".join(interruption_ids)

    cur.execute(
        """
        UPDATE ops_srv.ops_automation_dashboard_exc
        SET interruptions = %s,
            last_updated = %s
        WHERE record_date = %s
          AND process_name = %s
        """,
        (payload, run_ts, exc_month_key, "Exclusion Report"),
    )

    conn.commit()
    cur.close()
    conn.close()

# ===========================================================
# 📝 Carry-forward state helpers
# ===========================================================
def get_previous_record_state(conn, carrier_id, today):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT notes, interruptions, carry_over_flag
        FROM ops_srv.ops_automation_dashboard
        WHERE carrier_id = %s
          AND record_date < %s
        ORDER BY record_date DESC
        LIMIT 1
        """,
        (carrier_id, today),
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        return None, None, None
    return row[0], row[1], row[2]


def get_existing_today_state(conn, carrier_id, today):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT notes, interruptions, carry_over_flag, last_run_date
        FROM ops_srv.ops_automation_dashboard
        WHERE carrier_id = %s
          AND record_date = %s
        LIMIT 1
        """,
        (carrier_id, today),
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        return None, None, None, None
    return row[0], row[1], row[2], row[3]


# ===========================================================
# 📤 Snapshot upsert
# ===========================================================
def insert_snapshot(df, today, run_ts):
    conn = get_postgres_connection()
    cur = conn.cursor()

    sql = """
    INSERT INTO ops_srv.ops_automation_dashboard (
        record_date, entity_id, sub_entity_id, carrier_id, carrier_name, carrier_status,
        acu_cadence, bob_cadence, com_cadence, acc_cadence, acr_cadence,
        acu_download, acu_process, acu_status,
        bob_download, bob_process, bob_status,
        com_download, com_process, com_status,
        acc_rpa, acc_process, acc_status,
        acr_rpa, acr_process, acr_status,
        acu_automated, bob_automated, com_automated, acc_automated, acr_automated,
        acu_automation_type, bob_automation_type, com_automation_type,
        acc_automation_type, acr_automation_type,
        acu_proc_automated, bob_proc_automated, com_proc_automated,
        last_run_date,
        notes,
        interruptions,
        carry_over_flag,
        last_updated
    ) VALUES %s
    ON CONFLICT (record_date, carrier_id)
    DO UPDATE SET
        carrier_name = EXCLUDED.carrier_name,
        carrier_status = EXCLUDED.carrier_status,
        acu_cadence = EXCLUDED.acu_cadence,
        bob_cadence = EXCLUDED.bob_cadence,
        com_cadence = EXCLUDED.com_cadence,
        acc_cadence = EXCLUDED.acc_cadence,
        acr_cadence = EXCLUDED.acr_cadence,
        acu_download = EXCLUDED.acu_download,
        acu_process = EXCLUDED.acu_process,
        acu_status = EXCLUDED.acu_status,
        bob_download = EXCLUDED.bob_download,
        bob_process = EXCLUDED.bob_process,
        bob_status = EXCLUDED.bob_status,
        com_download = EXCLUDED.com_download,
        com_process = EXCLUDED.com_process,
        com_status = EXCLUDED.com_status,
        acc_rpa = EXCLUDED.acc_rpa,
        acc_process = EXCLUDED.acc_process,
        acc_status = EXCLUDED.acc_status,
        acr_rpa = EXCLUDED.acr_rpa,
        acr_process = EXCLUDED.acr_process,
        acr_status = EXCLUDED.acr_status,
        acu_automated = EXCLUDED.acu_automated,
        bob_automated = EXCLUDED.bob_automated,
        com_automated = EXCLUDED.com_automated,
        acc_automated = EXCLUDED.acc_automated,
        acr_automated = EXCLUDED.acr_automated,
        acu_automation_type = EXCLUDED.acu_automation_type,
        bob_automation_type = EXCLUDED.bob_automation_type,
        com_automation_type = EXCLUDED.com_automation_type,
        acc_automation_type = EXCLUDED.acc_automation_type,
        acr_automation_type = EXCLUDED.acr_automation_type,
        acu_proc_automated = EXCLUDED.acu_proc_automated,
        bob_proc_automated = EXCLUDED.bob_proc_automated,
        com_proc_automated = EXCLUDED.com_proc_automated,
        entity_id = EXCLUDED.entity_id,
        sub_entity_id = EXCLUDED.sub_entity_id,
        notes = EXCLUDED.notes,
        interruptions = EXCLUDED.interruptions,
        carry_over_flag = EXCLUDED.carry_over_flag,
        last_updated = EXCLUDED.last_updated
    """

    values = []
    for _, r in df.iterrows():
        values.append(
            (
                today,
                entity_id,
                sub_entity_id,
                str(r.carrier_id),
                r.carrier_name,
                r.carrier_status,
                to_db_null(getattr(r, "acu_cadence", None)),
                to_db_null(getattr(r, "bob_cadence", None)),
                to_db_null(getattr(r, "com_cadence", None)),
                to_db_null(getattr(r, "acc_cadence", None)),
                to_db_null(getattr(r, "acr_cadence", None)),
                r.acu_download,
                r.acu_process,
                r.acu_status,
                r.bob_download,
                r.bob_process,
                r.bob_status,
                r.com_download,
                r.com_process,
                r.com_status,
                r.acc_rpa,
                r.acc_process,
                r.acc_status,
                r.acr_rpa,
                r.acr_process,
                r.acr_status,
                r.acu_automated,
                r.bob_automated,
                r.com_automated,
                r.acc_automated,
                r.acr_automated,
                to_db_null(getattr(r, "acu_automation_type", None)),
                to_db_null(getattr(r, "bob_automation_type", None)),
                to_db_null(getattr(r, "com_automation_type", None)),
                to_db_null(getattr(r, "acc_automation_type", None)),
                to_db_null(getattr(r, "acr_automation_type", None)),
                to_db_null(getattr(r, "acu_proc_automated", None)),
                to_db_null(getattr(r, "bob_proc_automated", None)),
                to_db_null(getattr(r, "com_proc_automated", None)),
                json.dumps(getattr(r, "last_run_date", _empty_last_run_date())),
                json.dumps(getattr(r, "notes", {})),
                json.dumps(getattr(r, "interruptions", {})),
                json.dumps(getattr(r, "carry_over_flag", {})),
                run_ts,
            )
        )

    execute_values(cur, sql, values)
    conn.commit()
    cur.close()
    conn.close()
    print(f"[BASELINE] Inserted/updated {len(df)} rows for {today}")


# ===========================================================
# 🔎 Baseline existence
# ===========================================================
def baseline_exists(day):
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM ops_srv.ops_automation_dashboard WHERE record_date = %s LIMIT 1",
        (day,),
    )
    exists = cur.fetchone() is not None
    cur.close()
    conn.close()
    return exists


# ===========================================================
# 📅 Due / schedule helpers
# ===========================================================
def load_rpa_schedule_for_today(today):
    conn = get_postgres_connection()
    cur = conn.cursor()

    expected_today = {}
    cur.execute(
        """
        SELECT carrier_id::BIGINT AS carrier_id,
               upper(btrim(process_name)) AS process_name,
               cadence,
               target_dates,
               pickup_method,
               automated
        FROM wpo.ops_rpa_matrix
        WHERE process_name IN ('ACU','BOB','COM')
        """
    )
    rows = cur.fetchall()

    dow = today.weekday() + 1
    dom = today.day
    for cid, pname, cadence, target_dates, pickup, automated in rows:
        if not cid or not pname:
            continue

        # Only ACU/BOB should respect automated here. COM untouched.
        if pname in ("ACU", "BOB") and not _is_active_automated(automated):
            continue

        td_list = []
        if target_dates:
            for part in str(target_dates).split(","):
                part = part.strip()
                if part:
                    try:
                        td_list.append(int(part))
                    except ValueError:
                        pass

        cad_raw = (cadence or "").strip().lower()
        if cad_raw in ("", "cadence varies"):
            expected = True
        elif cad_raw == "daily":
            expected = True
        elif cad_raw == "weekly":
            expected = bool(td_list) and dow in td_list
        elif cad_raw == "monthly":
            expected = bool(td_list) and dom in td_list
        else:
            expected = True

        if expected:
            expected_today[(cid, pname)] = True

    cur.execute(
        """
        SELECT DISTINCT carrier_id::BIGINT AS carrier_id,
               COALESCE(in_development::INT, 0) AS in_development,
               active_flag,
               automated
        FROM wpo.ops_acc_process_matrix
        """
    )
    acc_rows = cur.fetchall()
    acc_carriers = {
        cid for cid, dev, af, automated in acc_rows
        if cid and dev == 0 and _is_active_automated(automated)
    }

    cur.execute(
        """
        SELECT DISTINCT carrier_id::BIGINT AS carrier_id,
               COALESCE(in_development::INT, 0) AS in_development,
               active_flag,
               automated
        FROM wpo.ops_acr_process_matrix
        """
    )
    acr_rows = cur.fetchall()
    acr_carriers = {
        cid for cid, dev, af, automated in acr_rows
        if cid and dev == 0 and _is_active_automated(automated)
    }

    cur.close()
    conn.close()
    return expected_today, acc_carriers, acr_carriers

def _build_acc_acr_log_state_maps(today):
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT script_name, start_datetime, error, success,
               upper(btrim(process_type)) AS process_type,
               carrier_id::BIGINT AS carrier_id
        FROM wpo.ops_rpa_script_logs
        WHERE process_type IN ('ACC','ACR')
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    today_flags = {}
    latest_success = {"ACC": {}, "ACR": {}}

    for _script, start_dt, err, ok, ptype, cid in rows:
        if not start_dt or not cid:
            continue

        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt)

        log_date = start_dt.date()
        ptype = (ptype or "").upper().strip()

        proc = "ACC" if (cid in {UHC, AMBETTER} and ptype == "ACR") else ptype
        if proc not in ("ACC", "ACR"):
            continue

        has_success = False
        has_error = False

        if proc == "ACC":
            if ok is not None:
                s = str(ok).strip().lower()
                if s in ("0", "false", "f", "n", "no", "failed", "fail", "error"):
                    has_error = True
                elif s != "":
                    has_success = True
            if not has_success and not has_error and err is not None:
                has_error = bool(str(err).strip())
        else:
            has_success = bool(ok)
            has_error = bool(err) and not has_success

        cid_key = str(cid).strip()

        if log_date == today and (has_success or has_error):
            key = (proc, cid_key)
            today_flags.setdefault(key, {"success": False, "error": False})
            if has_success:
                today_flags[key]["success"] = True
            if has_error:
                today_flags[key]["error"] = True

        if has_success:
            prev_dt = latest_success[proc].get(cid_key)
            if prev_dt is None or log_date > prev_dt:
                latest_success[proc][cid_key] = log_date

    today_outcomes = {}
    for key, flags in today_flags.items():
        today_outcomes[key] = "success" if flags["success"] else "error"

    return today_outcomes, latest_success

def load_due_map_for_today(today):
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT carrier_id::BIGINT AS carrier_id,
               upper(btrim(process_name)) AS process_name,
               cadence,
               target_dates,
               automated
        FROM wpo.ops_rpa_matrix
        WHERE process_name IN ('ACU','BOB','COM')
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    dow = today.weekday() + 1
    dom = today.day
    due_today = {}
    for cid, pname, cadence, target_dates, automated in rows:
        if not cid or not pname:
            continue

        # Only ACU/BOB respect automated here. COM untouched.
        if pname in ("ACU", "BOB") and not _is_active_automated(automated):
            continue

        cad = (cadence or "").strip().lower()
        if cad in ("", "cadence varies", "monthly"):
            continue

        td_list = []
        if target_dates:
            for part in str(target_dates).split(","):
                part = part.strip()
                if part:
                    try:
                        td_list.append(int(part))
                    except ValueError:
                        pass

        if cad == "daily":
            due = True
        elif cad == "weekly":
            due = bool(td_list) and dow in td_list
        else:
            due = True

        due_today[(cid, pname)] = bool(due)

    return due_today

def load_hcsc_shared_schedule():
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            upper(btrim(process_name)) AS process_name,
            cadence,
            target_dates,
            automated
        FROM wpo.ops_rpa_matrix
        WHERE upper(btrim(carrier_name)) = 'HCSC'
          AND upper(btrim(process_name)) IN ('ACU', 'BOB')
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    rules = {}

    for proc, cadence, target_dates, automated in rows:
        if not _is_active_automated(automated):
            continue

        td_list = []
        if target_dates:
            for part in str(target_dates).split(","):
                part = part.strip()
                if part:
                    try:
                        td_list.append(int(part))
                    except ValueError:
                        pass

        rules[str(proc).upper().strip()] = {
            "cadence": (cadence or "").strip().lower(),
            "target_dates": td_list,
        }

    return rules

# ===========================================================
# 📥 Prefix maps
# ===========================================================
def load_inbound_prefix_map():
    conn = get_postgres_connection()
    cur = conn.cursor()
    prefix_map = {"ACU": [], "BOB": [], "COM": []}

    cur.execute(
        """
        SELECT DISTINCT raw_file_name_prefix, carrier_id::BIGINT AS cid, automated
        FROM wpo.ops_load_matrix_acu
        WHERE process_type = 'ACU'
        """
    )
    prefix_map["ACU"] = [
        (str(prefix).strip().lower(), cid)
        for prefix, cid, automated in cur.fetchall()
        if cid and prefix and _is_active_automated(automated)
    ]

    cur.execute(
        """
        SELECT DISTINCT raw_file_name_prefix, carrier_id::BIGINT AS cid, automated
        FROM wpo.ops_process_matrix
        WHERE process_type = 'BOB'
        """
    )
    prefix_map["BOB"] = [
        (str(prefix).strip().lower(), cid)
        for prefix, cid, automated in cur.fetchall()
        if cid and prefix and _is_active_automated(automated)
    ]

    # COM untouched
    cur.execute(
        """
        SELECT DISTINCT raw_file_name_prefix, carrier_id::BIGINT AS cid, automated
        FROM wpo.ops_process_matrix_com
        WHERE process_type = 'COM'
        """
    )
    prefix_map["COM"] = [
        (str(prefix).strip().lower(), cid)
        for prefix, cid, automated in cur.fetchall()
        if cid and prefix and _is_active_automated(automated)
    ]

    cur.close()
    conn.close()
    return prefix_map


# ===========================================================
# 🔁 RPA log updates
# ===========================================================
def update_from_rpa_logs(today, run_ts, acu_varies_set, bob_varies_set, com_varies_set):
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT script_name, start_datetime, error, success,
               upper(btrim(process_type)) AS process_type,
               carrier_id::BIGINT AS carrier_id
        FROM wpo.ops_rpa_script_logs
        WHERE CAST(start_datetime AS DATE) = %s
        """,
        (today,),
    )
    rows_today = cur.fetchall()

    cur.execute(
        """
        SELECT script_name, start_datetime, error, success,
               upper(btrim(process_type)) AS process_type,
               carrier_id::BIGINT AS carrier_id
        FROM wpo.ops_rpa_script_logs
            WHERE 1 = 1
            AND process_type IN ('ACC','ACR')
        """
    )
    rows_all_acc_acr = cur.fetchall()
    cur.close()
    conn.close()

    agg = {}

    def reg(log_date, proc, carrier_ids, success, error):
        if not carrier_ids or not proc:
            return
        proc = proc.upper().strip()
        if proc == "ACC":
            has_success = False
            has_error = False
            if success is not None:
                s = str(success).strip().lower()
                if s in ("0", "false", "f", "n", "no", "failed", "fail", "error"):
                    has_error = True
                elif s != "":
                    has_success = True
            if not has_success and not has_error and error is not None:
                has_error = bool(str(error).strip())
        else:
            has_success = bool(success)
            has_error = bool(error) and not has_success

        if not has_success and not has_error:
            return

        for cid in carrier_ids:
            key = (log_date, proc, str(cid).strip())
            agg.setdefault(key, {"success": False, "error": False})
            if has_success:
                agg[key]["success"] = True
            if has_error:
                agg[key]["error"] = True

    for script, start_dt, err, ok, ptype, cid in rows_today:
        if not start_dt:
            continue
        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt)
        log_date = start_dt.date()
        ptype = (ptype or "").upper().strip()

        if script == "ACU_HCSC_RPA":
            reg(log_date, "ACU", list(ACU_HCSC_FORCE_ACTIVE), ok, err)
            continue
        if script == "BOB_HCSC_RPA":
            reg(log_date, "BOB", list(BOB_HCSC_FORCE_ACTIVE), ok, err)
            continue
        if script == "ACU_SMA_RPA":
            reg(log_date, "ACU", list(ACU_SMA_FORCE_ACTIVE), ok, err)
            continue
        if script == "BOB_SMA_RPA":
            reg(log_date, "BOB", list(BOB_SMA_FORCE_ACTIVE), ok, err)
            continue

        if not cid:
            continue
        if cid in {UHC, AMBETTER} and ptype == "ACR":
            reg(log_date, "ACC", [cid], ok, err)
        elif cid in {UHC, AMBETTER} and ptype == "ACC":
            continue
        else:
            reg(log_date, ptype, [cid], ok, err)

    decisions = {key: ("success" if flags["success"] else "error") for key, flags in agg.items()}

    last_proc_acc = {}
    last_proc_acr = {}
    for _script, start_dt, _err, ok, ptype, cid in rows_all_acc_acr:
        if not start_dt or not cid:
            continue
        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt)
        dt = start_dt.date()
        proc_for_last = "ACC" if (cid in {UHC, AMBETTER} and ptype == "ACR") else ptype
        has_success = False
        if proc_for_last == "ACC":
            if ok is not None:
                s = str(ok).strip().lower()
                has_success = s not in ("0", "false", "f", "n", "no", "failed", "fail", "error") and s != ""
        else:
            has_success = bool(ok)
        if not has_success:
            continue
        cid_key = str(cid).strip()
        if proc_for_last == "ACC":
            if cid_key not in last_proc_acc or dt > last_proc_acc[cid_key]:
                last_proc_acc[cid_key] = dt
        elif proc_for_last == "ACR":
            if cid_key not in last_proc_acr or dt > last_proc_acr[cid_key]:
                last_proc_acr[cid_key] = dt

    expected_today, acc_carriers, acr_carriers = load_rpa_schedule_for_today(today)
    ACC_DEV_KEYS = {str(x).strip() for x in ACC_DEV}
    ACR_DEV_KEYS = {str(x).strip() for x in ACR_DEV}
    ACR_FORCE_ACTIVE_KEYS = {str(x).strip() for x in ACR_FORCE_ACTIVE}
    acu_varies_keys = {str(x).strip() for x in acu_varies_set}
    bob_varies_keys = {str(x).strip() for x in bob_varies_set}
    com_varies_keys = {str(x).strip() for x in com_varies_set}

    prev_day_process_map = _load_previous_day_process_map(today)
    is_weekend = today.weekday() >= 5

    updates_dl = {"ACU": [], "BOB": [], "COM": []}
    updates_proc = {"ACC": [], "ACR": []}
    touched = set()

    for (dt, proc, cid_key), outcome in decisions.items():
        v = 1 if outcome == "success" else 0

        if proc in ("ACU", "BOB", "COM"):
            updates_dl[proc].append((v, run_ts, dt, cid_key, v))
            touched.add(dt)

        elif proc == "ACC":
            if cid_key not in ACC_DEV_KEYS:
                updates_proc["ACC"].append((v, run_ts, dt, cid_key, v))
                touched.add(dt)

        elif proc == "ACR":
            if (
                cid_key not in ACR_DEV_KEYS
                and cid_key not in {str(UHC), str(AMBETTER)}
                and int(cid_key) not in ACR_NO_ACTION
                and cid_key not in ACR_FORCE_ACTIVE_KEYS
            ):
                updates_proc["ACR"].append((v, run_ts, dt, cid_key, v))
                touched.add(dt)

    for cid, proc_name in expected_today.keys():
        cid_key = str(cid).strip()
        if proc_name == "ACU" and cid_key in acu_varies_keys:
            continue
        if proc_name == "BOB" and cid_key in bob_varies_keys:
            continue
        if proc_name == "COM" and cid_key in com_varies_keys:
            continue
        if (today, proc_name, cid_key) not in decisions and proc_name in ("ACU", "BOB", "COM"):
            updates_dl[proc_name].append((0, run_ts, today, cid_key, 0))
            touched.add(today)

    for cid in {str(x).strip() for x in acc_carriers if x}:
        if cid in ACC_DEV_KEYS:
            continue
        if (today, "ACC", cid) in decisions:
            continue

        if is_weekend:
            prev_vals = prev_day_process_map.get(cid, {})
            v = prev_vals.get("acc_process", 1)
            if v is None:
                v = 1
        else:
            v = 0

        updates_proc["ACC"].append((v, run_ts, today, cid, v))
        touched.add(today)

    for cid in {str(x).strip() for x in acr_carriers if x}:
        if cid in ACR_DEV_KEYS:
            continue
        if cid in {str(UHC), str(AMBETTER)}:
            continue
        if int(cid) in ACR_NO_ACTION and cid not in ACR_FORCE_ACTIVE_KEYS:
            continue
        if cid in ACR_FORCE_ACTIVE_KEYS:
            continue
        if (today, "ACR", cid) in decisions:
            continue

        if is_weekend:
            prev_vals = prev_day_process_map.get(cid, {})
            v = prev_vals.get("acr_process", 1)
            if v is None:
                v = 1
        else:
            v = 0

        updates_proc["ACR"].append((v, run_ts, today, cid, v))
        touched.add(today)

    conn = get_postgres_connection()
    cur = conn.cursor()

    for proc in ("ACU", "BOB", "COM"):
        col = proc.lower() + "_download"
        if updates_dl[proc]:
            execute_batch(
                cur,
                f"""
                UPDATE ops_srv.ops_automation_dashboard
                SET {col} = %s,
                    last_updated = %s
                WHERE record_date = %s
                  AND carrier_id = %s
                  AND {col} IS DISTINCT FROM %s
                  AND {col} NOT IN (2,3)
                """,
                updates_dl[proc],
            )

    for proc in ("ACC", "ACR"):
        col = proc.lower() + "_process"

        if updates_proc[proc]:
            execute_batch(
                cur,
                f"""
                UPDATE ops_srv.ops_automation_dashboard
                SET {col} = %s,
                    last_updated = %s
                WHERE record_date = %s
                AND carrier_id = %s
                AND {col} IS DISTINCT FROM %s
                AND {col} NOT IN (2,3)
                """,
                updates_proc[proc],
            )

    force_active_updates = [
        (1, run_ts, today, str(cid), 1)
        for cid in ACR_FORCE_ACTIVE
    ]
    if force_active_updates:
        execute_batch(
            cur,
            """
            UPDATE ops_srv.ops_automation_dashboard
            SET acr_process = %s,
                last_updated = %s
            WHERE record_date = %s
            AND carrier_id = %s
            AND acr_process IS DISTINCT FROM %s
            AND acr_process NOT IN (2,3)
            """,
            force_active_updates,
        )

    json_updates = []
    for cid_key, dt in last_proc_acc.items():
        json_updates.append(("acc", str(dt), run_ts, today, cid_key))
    for cid_key, dt in last_proc_acr.items():
        json_updates.append(("acr", str(dt), run_ts, today, cid_key))
    for cid in ACR_FORCE_ACTIVE:
        json_updates.append(("acr", str(today), run_ts, today, str(cid)))
    if json_updates:
        execute_batch(
            cur,
            """
            UPDATE ops_srv.ops_automation_dashboard
            SET last_run_date =
                '{"acu":"","bob":"","com":"","acc":"","acr":""}'::jsonb
                || COALESCE(last_run_date, '{}'::jsonb)
                || jsonb_build_object(%s, %s),
                last_updated = %s
            WHERE record_date = %s
              AND carrier_id = %s
            """,
            json_updates,
        )

    conn.commit()
    cur.close()
    conn.close()
    return touched


# ===========================================================
# 📥 Inbound logs loaders
# ===========================================================
def _load_inbound_log_rows(today, lookback_days=30):
    start_day = today - timedelta(days=lookback_days)
    conn = get_synapse_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT file_name,
               upper(ltrim(rtrim(process_type))) AS process_type,
               load_status,
               process_date_start,
               destination_table
        FROM raw.ops_inbound_file_log
        WHERE CAST(process_date_start AS DATE) BETWEEN ? AND ?
        """,
        (start_day, today),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _load_all_inbound_log_rows():
    conn = get_synapse_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT file_name,
               upper(ltrim(rtrim(process_type))) AS process_type,
               load_status,
               process_date_start,
               destination_table
        FROM raw.ops_inbound_file_log
        WHERE upper(ltrim(rtrim(process_type))) IN ('ACU','BOB','COM')
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _build_seen_from_inbound_rows(rows, today, prefix_map):
    seen = {}

    def store(key, val, filename, dt):
        prev = seen.get(key)
        if prev is None:
            seen[key] = {"val": val, "file_name": filename, "log_dt": dt}
            return
        prev_dt = prev.get("log_dt")
        if prev_dt is None or (dt is not None and dt > prev_dt):
            seen[key] = {"val": val, "file_name": filename, "log_dt": dt}

    for filename, ptype, status, dt, dest_table in rows:
        if not filename or not ptype or status is None:
            continue
        proc = (ptype or "").upper().strip()
        if proc not in ("ACU", "BOB", "COM"):
            continue

        dest_lower = str(dest_table or "").strip().lower()
        if proc == "ACU" and dest_lower not in ("acu_carrier_updates", "acu_contract_updates"):
            continue
        if proc == "BOB" and dest_lower != "bob_carrier_memberships":
            continue
        if proc == "COM" and dest_lower != "com_header_stg":
            continue

        val = _status_to_file_value(status)
        if val is None:
            continue

        name = _normalize_log_filename(filename)
        fallback_date = today if dt is None else dt.date()

        matched_shared = False
        for grp in ("HCSC", "SMA"):
            keyword = _shared_keyword(proc, grp) if (proc, grp) in SHARED_GROUPS else None
            if keyword and _filename_contains_shared_keyword(name, keyword):
                # For shared files, use the actual log/process date, not the MMDDYYYY in the file name.
                file_date = fallback_date
                for cid in SHARED_GROUPS[(proc, grp)]:
                    store((file_date, proc, str(cid).strip()), val, filename, dt)
                matched_shared = True
                break
        if matched_shared:
            continue
        
        for prefix, cid in prefix_map[proc]:
            if not name.startswith(prefix):
                continue
            if proc in ("ACU", "BOB"):
                file_date = _extract_mmddyyyy_suffix(name, fallback_date)
            else:
                file_date = fallback_date
            store((file_date, proc, str(cid).strip()), val, filename, dt)

    return seen

def _has_success_in_window(seen, cid_key, proc, ref_date, days):
    cid_key = str(cid_key).strip()
    proc = str(proc).strip().upper()
    window_start = ref_date - timedelta(days=days - 1)

    return any(
        p == proc
        and str(c).strip() == cid_key
        and rec["val"] == 1
        and window_start <= file_date <= ref_date
        for (file_date, p, c), rec in seen.items()
    )


def _latest_record_in_window(seen, cid_key, proc, ref_date, days):
    cid_key = str(cid_key).strip()
    proc = str(proc).strip().upper()
    window_start = ref_date - timedelta(days=days - 1)

    latest = None
    for (file_date, p, c), rec in seen.items():
        if p != proc or str(c).strip() != cid_key:
            continue
        if not (window_start <= file_date <= ref_date):
            continue

        if latest is None or file_date > latest[0]:
            latest = (file_date, rec)

    return latest

def _load_com_process_history_rows(today, lookback_days=30):
    start_day = today - timedelta(days=lookback_days)

    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            carrier_id::text AS carrier_id,
            job_status,
            commission_status,
            job_end_datetime,
            pk_id
        FROM wpo.com_process_history
        WHERE carrier_id IS NOT NULL
          AND job_end_datetime IS NOT NULL
          AND carrier_id::text ~ '^[0-9]+$'
          AND CAST(job_end_datetime AS DATE) BETWEEN %s AND %s
        ORDER BY carrier_id::text, job_end_datetime DESC, pk_id DESC
        """,
        (start_day, today),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _classify_com_process_row(job_status, commission_status):
    js = str(job_status or "").strip()
    cs = str(commission_status or "").strip()

    if js == "Completed" and cs == "Completed":
        return 1
    if js == "Processing" and cs == "Calcs":
        return 1
    if js == "Processing" and cs == "Calcs (Exc)":
        return 1

    if js == "Failed" and cs == "Failed":
        return 0

    if js == "Processing" and cs == "Processing":
        return None

    return None


def _build_com_30day_state_map(today, lookback_days=30):
    rows = _load_com_process_history_rows(today, lookback_days=lookback_days)

    state_map = {}
    last_success_by_carrier = {}

    for carrier_id, job_status, commission_status, job_end_datetime, pk_id in rows:
        if not carrier_id:
            continue

        cid_key = str(carrier_id).strip()
        result = _classify_com_process_row(job_status, commission_status)

        end_dt = job_end_datetime
        end_date = None

        if isinstance(end_dt, str):
            try:
                end_dt = datetime.fromisoformat(end_dt)
            except Exception:
                end_dt = None
                end_date = _to_date(job_end_datetime)

        if end_date is None and isinstance(end_dt, datetime):
            end_date = end_dt.date()

        if cid_key not in state_map:
            state_map[cid_key] = {
                "has_any_run": False,
                "has_success": False,
                "latest_result": None,
                "latest_job_end_datetime": None,
                "job_status": None,
                "commission_status": None,
            }

        rec = state_map[cid_key]
        rec["has_any_run"] = True

        if result == 1:
            rec["has_success"] = True
            if end_date is not None:
                prev_success = last_success_by_carrier.get(cid_key)
                if prev_success is None or end_date > prev_success:
                    last_success_by_carrier[cid_key] = end_date

        if end_dt is not None:
            prev_dt = rec["latest_job_end_datetime"]
            if prev_dt is None or end_dt > prev_dt:
                rec["latest_job_end_datetime"] = end_dt
                rec["latest_result"] = result
                rec["job_status"] = job_status
                rec["commission_status"] = commission_status

    com_process_map = {
        cid_key: 1 if rec["has_success"] else 0
        for cid_key, rec in state_map.items()
    }

    return state_map, com_process_map, last_success_by_carrier

def _build_com_all_time_last_success_map(today):
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            carrier_id::text AS carrier_id,
            job_status,
            commission_status,
            job_end_datetime,
            pk_id
        FROM wpo.com_process_history
        WHERE carrier_id IS NOT NULL
          AND job_end_datetime IS NOT NULL
          AND carrier_id::text ~ '^[0-9]+$'
        ORDER BY carrier_id::text, job_end_datetime DESC, pk_id DESC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    last_success_by_carrier = {}

    for carrier_id, job_status, commission_status, job_end_datetime, pk_id in rows:
        if not carrier_id:
            continue

        cid_key = str(carrier_id).strip()
        result = _classify_com_process_row(job_status, commission_status)

        if result != 1:
            continue

        end_dt = job_end_datetime
        end_date = None

        if isinstance(end_dt, str):
            try:
                end_dt = datetime.fromisoformat(end_dt)
            except Exception:
                end_dt = None
                end_date = _to_date(job_end_datetime)

        if end_date is None and isinstance(end_dt, datetime):
            end_date = end_dt.date()

        if end_date is None:
            continue

        prev_success = last_success_by_carrier.get(cid_key)
        if prev_success is None or end_date > prev_success:
            last_success_by_carrier[cid_key] = end_date

    return last_success_by_carrier

def _previous_month_window(today):
    first_day_current_month = today.replace(day=1)
    prev_month_end = first_day_current_month - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    return prev_month_start, prev_month_end


def _load_exc_log_rows_for_prev_month(today):
    start_day, end_day = _previous_month_window(today)

    conn = get_synapse_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT file_name,
               upper(ltrim(rtrim(process_type))) AS process_type,
               load_status,
               process_date_start,
               destination_table
        FROM raw.ops_inbound_file_log
        WHERE CAST(process_date_start AS DATE) BETWEEN ? AND ?
          AND upper(ltrim(rtrim(process_type))) = 'EXC'
        """,
        (start_day, end_day),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _load_all_exc_log_rows():
    conn = get_synapse_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT file_name,
               upper(ltrim(rtrim(process_type))) AS process_type,
               load_status,
               process_date_start,
               destination_table
        FROM raw.ops_inbound_file_log
        WHERE upper(ltrim(rtrim(process_type))) = 'EXC'
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def _build_exc_month_state(today):
    """
    EXC rule:
    - Look only at previous calendar month.
    - All 3 required prefixes must have at least one success.
    - last_run_date comes from process_date_start, not filename.
    """
    rows = _load_exc_log_rows_for_prev_month(today)

    success_by_prefix = {prefix: None for prefix in EXC_REQUIRED_PREFIXES}
    latest_seen_by_prefix = {prefix: None for prefix in EXC_REQUIRED_PREFIXES}

    for file_name, process_type, load_status, process_date_start, destination_table in rows:
        if not file_name or load_status is None:
            continue

        name = _normalize_log_filename(file_name)
        val = _status_to_file_value(load_status)
        if val is None:
            continue

        dt_val = None
        if isinstance(process_date_start, datetime):
            dt_val = process_date_start.date()
        elif isinstance(process_date_start, date):
            dt_val = process_date_start
        else:
            dt_val = _to_date(process_date_start)

        for prefix in EXC_REQUIRED_PREFIXES:
            if name.startswith(prefix):
                prev_latest = latest_seen_by_prefix[prefix]
                if prev_latest is None or (dt_val is not None and dt_val > prev_latest):
                    latest_seen_by_prefix[prefix] = dt_val

                if val == 1:
                    prev_success = success_by_prefix[prefix]
                    if prev_success is None or (dt_val is not None and dt_val > prev_success):
                        success_by_prefix[prefix] = dt_val
                break

    all_success = all(success_by_prefix[prefix] is not None for prefix in EXC_REQUIRED_PREFIXES)

    latest_success_dt = None
    if all_success:
        latest_success_dt = max(success_by_prefix.values())

    missing_prefixes = [prefix for prefix, dt in success_by_prefix.items() if dt is None]

    return {
        "process_value": 1 if all_success else 0,
        "last_run_date": latest_success_dt,
        "success_by_prefix": success_by_prefix,
        "latest_seen_by_prefix": latest_seen_by_prefix,
        "missing_prefixes": missing_prefixes,
        "has_any_run": any(v is not None for v in latest_seen_by_prefix.values()),
        "all_success": all_success,
    }


def _build_exc_all_time_last_success():
    rows = _load_all_exc_log_rows()
    latest_success_dt = None

    for file_name, process_type, load_status, process_date_start, destination_table in rows:
        if not file_name or load_status is None:
            continue

        name = _normalize_log_filename(file_name)
        if not any(name.startswith(prefix) for prefix in EXC_REQUIRED_PREFIXES):
            continue

        val = _status_to_file_value(load_status)
        if val != 1:
            continue

        dt_val = None
        if isinstance(process_date_start, datetime):
            dt_val = process_date_start.date()
        elif isinstance(process_date_start, date):
            dt_val = process_date_start
        else:
            dt_val = _to_date(process_date_start)

        if dt_val is None:
            continue

        if latest_success_dt is None or dt_val > latest_success_dt:
            latest_success_dt = dt_val

    return latest_success_dt

# ===========================================================
# 🔁 Inbound updates
# ===========================================================
def update_from_inbound_logs(today, run_ts, acu_varies_set, bob_varies_set, com_varies_set):
    prefix_map = load_inbound_prefix_map()
    rows_30 = _load_inbound_log_rows(today, lookback_days=30)
    seen_30 = _build_seen_from_inbound_rows(rows_30, today, prefix_map)
    due_today = load_due_map_for_today(today)
    prev_day_process_map = _load_previous_day_process_map(today)

    hcsc_rules = load_hcsc_shared_schedule()
    acu_hcsc_keys = {str(x).strip() for x in ACU_HCSC_FORCE_ACTIVE}
    bob_hcsc_keys = {str(x).strip() for x in BOB_HCSC_FORCE_ACTIVE}

    def _is_hcsc_due_on(check_date, proc):
        rule = hcsc_rules.get(proc)
        if not rule:
            return False

        cad = rule["cadence"]
        td_list = rule["target_dates"]

        dow = check_date.weekday() + 1
        dom = check_date.day

        if cad == "daily":
            return True
        if cad == "weekly":
            return bool(td_list) and dow in td_list
        if cad == "monthly":
            return bool(td_list) and dom in td_list
        if cad in ("", "cadence varies"):
            return True

        return True

    def _latest_hcsc_due_state(cid_key, proc, ref_date):
        earliest_day = ref_date - timedelta(days=30)
        check_day = ref_date - timedelta(days=1)

        while check_day >= earliest_day:
            if _is_hcsc_due_on(check_day, proc):
                rec = seen_30.get((check_day, proc, cid_key))
                return rec["val"] if rec is not None else 0
            check_day -= timedelta(days=1)

        return 0
    
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT carrier_id,
               acu_cadence, bob_cadence, com_cadence,
               acu_proc_automated, bob_proc_automated, com_proc_automated
        FROM ops_srv.ops_automation_dashboard
        WHERE record_date = %s
        """,
        (today,),
    )
    dash_rows = cur.fetchall()
    cur.close()
    conn.close()

    dash = {}
    for cid, acu_cad, bob_cad, com_cad, acu_pa, bob_pa, com_pa in dash_rows:
        dash[str(cid).strip()] = {
            "acu_cadence": acu_cad,
            "bob_cadence": bob_cad,
            "com_cadence": com_cad,
            "acu_proc_automated": acu_pa,
            "bob_proc_automated": bob_pa,
            "com_proc_automated": com_pa,
        }

    dates_touched = set()
    com_state_map, com_process_map, _com_last_success_30 = _build_com_30day_state_map(today, lookback_days=30)
    com_last_success_all_time = _build_com_all_time_last_success_map(today)

    conn = get_postgres_connection()
    cur = conn.cursor()

    for proc in ("ACU", "BOB", "COM"):
        col = proc.lower() + "_process"
        updates = []

        if proc == "COM":
            for cid_key, vals in dash.items():
                if vals.get("com_proc_automated") != 1:
                    continue
                latest_result = com_process_map.get(cid_key, 0)
                updates.append((latest_result, run_ts, today, cid_key, latest_result))
                dates_touched.add(today)

            if updates:
                execute_batch(
                    cur,
                    f"""
                    UPDATE ops_srv.ops_automation_dashboard
                    SET {col} = %s,
                        last_updated = %s
                    WHERE record_date = %s
                      AND carrier_id = %s
                      AND {col} IS DISTINCT FROM %s
                    """,
                    updates,
                )
            continue

        for (file_date, p, cid_key), rec in seen_30.items():
            if p != proc:
                continue
            updates.append((rec["val"], run_ts, file_date, cid_key, rec["val"]))
            dates_touched.add(file_date)

        carrier_ids = {
            cid_key
            for cid_key, vals in dash.items()
            if vals.get(proc.lower() + "_proc_automated") == 1
        }

        if proc == "ACU":
            active_prefix_ids = {str(cid).strip() for _prefix, cid in prefix_map["ACU"]}
            carrier_ids |= ({str(x).strip() for x in ACU_HCSC_FORCE_ACTIVE} & active_prefix_ids)
            carrier_ids |= ({str(x).strip() for x in ACU_SMA_FORCE_ACTIVE} & active_prefix_ids)
        elif proc == "BOB":
            active_prefix_ids = {str(cid).strip() for _prefix, cid in prefix_map["BOB"]}
            carrier_ids |= ({str(x).strip() for x in BOB_HCSC_FORCE_ACTIVE} & active_prefix_ids)
            carrier_ids |= ({str(x).strip() for x in BOB_SMA_FORCE_ACTIVE} & active_prefix_ids)

        for cid_key in sorted(carrier_ids):
            pa_key = proc.lower() + "_proc_automated"
            if dash.get(cid_key, {}).get(pa_key) != 1:
                continue

            cad_key = proc.lower() + "_cadence"
            cad = (dash.get(cid_key, {}).get(cad_key) or "").strip().lower()
            is_weekend = today.weekday() >= 5

            is_hcsc_shared = (
                (proc == "ACU" and cid_key in acu_hcsc_keys) or
                (proc == "BOB" and cid_key in bob_hcsc_keys)
            )

            if cad == "weekly" and proc in ("ACU", "BOB"):
                v = 1 if _has_success_in_window(seen_30, cid_key, proc, today, 7) else 0
                updates.append((v, run_ts, today, cid_key, v))
                dates_touched.add(today)
                continue

            if is_hcsc_shared:
                if is_weekend:
                    prev_vals = prev_day_process_map.get(cid_key, {})
                    v = prev_vals.get(f"{proc.lower()}_process", 1)
                    if v is None:
                        v = 1
                    updates.append((v, run_ts, today, cid_key, v))
                    dates_touched.add(today)
                    continue
                
                is_due = _is_hcsc_due_on(today, proc)

                if is_due:
                    rec = seen_30.get((today, proc, cid_key))
                    v = rec["val"] if rec is not None else 0
                    updates.append((v, run_ts, today, cid_key, v))
                    dates_touched.add(today)
                    continue

                rec_today = seen_30.get((today, proc, cid_key))
                if rec_today is not None and rec_today["val"] == 1:
                    v = 1
                else:
                    v = _latest_hcsc_due_state(cid_key, proc, today)

                updates.append((v, run_ts, today, cid_key, v))
                dates_touched.add(today)
                continue

            is_30day = cad in ("monthly", "", "cadence varies")

            if is_30day:
                any_success = any(
                    p == proc and str(c).strip() == cid_key and rec["val"] == 1
                    for (file_date, p, c), rec in seen_30.items()
                )
                v = 1 if any_success else 0
                updates.append((v, run_ts, today, cid_key, v))
                dates_touched.add(today)
                continue

            if is_weekend and proc in ("ACU", "BOB"):
                prev_vals = prev_day_process_map.get(cid_key, {})
                v = prev_vals.get(f"{proc.lower()}_process", 1)
                if v is None:
                    v = 1

                updates.append((v, run_ts, today, cid_key, v))
                dates_touched.add(today)
                continue

            is_due = bool(due_today.get((int(cid_key), proc), False))
            if not is_due:
                updates.append((1, run_ts, today, cid_key, 1))
                dates_touched.add(today)
                continue

            rec = seen_30.get((today, proc, cid_key))
            v = rec["val"] if rec is not None else 0
            updates.append((v, run_ts, today, cid_key, v))
            dates_touched.add(today)

        if updates:
            execute_batch(
                cur,
                f"""
                UPDATE ops_srv.ops_automation_dashboard
                SET {col} = %s,
                    last_updated = %s
                WHERE record_date = %s
                  AND carrier_id = %s
                  AND {col} IS DISTINCT FROM %s
                """,
                updates,
            )

    seen_all = _build_seen_from_inbound_rows(_load_all_inbound_log_rows(), today, prefix_map)
    json_updates = []

    for proc in ("ACU", "BOB"):
        last_success = {}
        for (file_date, p, cid_key), rec in seen_all.items():
            if p != proc or rec["val"] != 1:
                continue
            if cid_key not in last_success or file_date > last_success[cid_key]:
                last_success[cid_key] = file_date

        if proc == "ACU":
            for group in ({str(x).strip() for x in ACU_HCSC_FORCE_ACTIVE}, {str(x).strip() for x in ACU_SMA_FORCE_ACTIVE}):
                group_dates = [last_success[cid] for cid in group if cid in last_success]
                if group_dates:
                    max_dt = max(group_dates)
                    for cid in group:
                        last_success[cid] = max_dt
        elif proc == "BOB":
            for group in ({str(x).strip() for x in BOB_HCSC_FORCE_ACTIVE}, {str(x).strip() for x in BOB_SMA_FORCE_ACTIVE}):
                group_dates = [last_success[cid] for cid in group if cid in last_success]
                if group_dates:
                    max_dt = max(group_dates)
                    for cid in group:
                        last_success[cid] = max_dt

        for cid_key, dt_val in last_success.items():
            json_updates.append((proc.lower(), str(dt_val), run_ts, today, cid_key))

    for cid_key, dt_val in com_last_success_all_time.items():
        json_updates.append(("com", str(dt_val), run_ts, today, cid_key))

    if json_updates:
        execute_batch(
            cur,
            """
            UPDATE ops_srv.ops_automation_dashboard
            SET last_run_date =
                '{"acu":"","bob":"","com":"","acc":"","acr":""}'::jsonb
                || COALESCE(last_run_date, '{}'::jsonb)
                || jsonb_build_object(%s, %s),
                last_updated = %s
            WHERE record_date = %s
              AND carrier_id = %s
            """,
            json_updates,
        )

    conn.commit()
    cur.close()
    conn.close()
    return dates_touched


# ===========================================================
# 🔁 Status recompute
# ===========================================================
def recompute_statuses(dates_to_update, run_ts):
    if not dates_to_update:
        print("[STATUS] No dates to recompute.")
        return

    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT record_date,
               carrier_id,
               acu_download, acu_process,
               bob_download, bob_process,
               com_download, com_process,
               acc_rpa, acc_process,
               acr_rpa, acr_process
        FROM ops_srv.ops_automation_dashboard
        WHERE record_date = ANY(%s)
        """,
        (list(dates_to_update),),
    )
    rows = cur.fetchall()

    updates = []
    for rec_date, cid, acu_d, acu_p, bob_d, bob_p, com_d, com_p, acc_rpa, acc_p, acr_rpa, acr_p in rows:
        acu_status = derive_stage(acu_d, acu_p)
        bob_status = derive_stage(bob_d, bob_p)
        com_status = derive_stage(com_d, com_p)

        if acc_rpa == 3 or acc_p == 3:
            acc_status = 3
        elif acc_rpa == 2 and acc_p == 2:
            acc_status = 2
        else:
            acc_status = derive_stage(acc_rpa, acc_p)

        if acr_rpa == 3 or acr_p == 3:
            acr_status = 3
        elif acr_rpa == 2 and acr_p == 2:
            acr_status = 2
        elif int(cid) in ACR_FORCE_ACTIVE:
            acr_status = 1
        else:
            acr_status = derive_stage(acr_rpa, acr_p)

        updates.append(
            (
                acu_status, bob_status, com_status,
                acc_status, acr_status,
                run_ts, rec_date, str(cid),
                acu_status, bob_status, com_status,
                acc_status, acr_status,
            )
        )

    if updates:
        execute_batch(
            cur,
            """
            UPDATE ops_srv.ops_automation_dashboard
            SET acu_status = %s,
                bob_status = %s,
                com_status = %s,
                acc_status = %s,
                acr_status = %s,
                last_updated = %s
            WHERE record_date = %s
              AND carrier_id = %s
              AND (
                    acu_status IS DISTINCT FROM %s
                 OR bob_status IS DISTINCT FROM %s
                 OR com_status IS DISTINCT FROM %s
                 OR acc_status IS DISTINCT FROM %s
                 OR acr_status IS DISTINCT FROM %s
              )
            """,
            updates,
        )
        conn.commit()

    cur.close()
    conn.close()


# ===========================================================
# 🚨 Service interruption engine
# ===========================================================
def _get_due_and_cadence_maps(today):
    due_today = load_due_map_for_today(today)
    hcsc_rules = load_hcsc_shared_schedule()

    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT carrier_id,
               carrier_name,
               acu_cadence, bob_cadence, com_cadence, acc_cadence, acr_cadence,
               acu_proc_automated, bob_proc_automated, com_proc_automated,
               acc_automated, acr_automated
        FROM ops_srv.ops_automation_dashboard
        WHERE record_date = %s
        """,
        (today,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    cad_map = {}
    for cid, cname, acu_c, bob_c, com_c, acc_c, acr_c, acu_pa, bob_pa, com_pa, acc_a, acr_a in rows:
        cad_map[str(cid).strip()] = {
            "carrier_name": cname,
            "acu_cadence": acu_c,
            "bob_cadence": bob_c,
            "com_cadence": com_c,
            "acc_cadence": acc_c,
            "acr_cadence": acr_c,
            "acu_proc_automated": acu_pa,
            "bob_proc_automated": bob_pa,
            "com_proc_automated": com_pa,
            "acc_automated": acc_a,
            "acr_automated": acr_a,
        }

    return due_today, cad_map, hcsc_rules

def _get_business_lead_info(process_name):
    proc = str(process_name or "").strip().upper()

    if proc == "COM":
        return ("Alma Hernandez", "3ccd46ed-63fc-4f79-9fae-0cc321664c24")
    if proc in ("ACC", "ACR"):
        return ("Perla Correa", "51bd563f-589b-4a71-b70c-17c2d05047a1")
    if proc in ("ACU", "BOB"):
        return ("Luis Benavides", "12c838a7-96e8-410e-8c73-dc381c4f0c0f")

    return (None, None)

def load_process_id_map():
    conn = get_postgres_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            process_id,
            upper(trim(process_type)) AS process_type
        FROM ops_srv.process_type
        WHERE upper(trim(process_type)) IN (
            'COM',
            'ACU',
            'BOB',
            'EXC',
            'ACC(SENT TO CARRIER)',
            'ACR(SENT TO AGENT)'
        )
        """
    )
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return {
        str(process_type).strip().upper(): str(process_id)
        for process_id, process_type in rows
        if process_id and process_type
    }


def _map_si_process_to_process_type(process_name):
    proc = str(process_name or "").strip().upper()

    mapping = {
        "COM": "COM",
        "ACU": "ACU",
        "BOB": "BOB",
        "EXC": "EXC",
        "ACC": "ACC(SENT TO CARRIER)",
        "ACR": "ACR(SENT TO AGENT)",
    }

    return mapping.get(proc)


def _get_process_id(process_name):
    mapped_process_type = _map_si_process_to_process_type(process_name)
    if not mapped_process_type:
        return None
    return PROCESS_ID_MAP.get(mapped_process_type)


def _insert_interruption_process(cur, *, today, process_name, carrier_id, carrier_name,
                                 raw_file_name, cadence, issue_description,
                                 received_flag, processed_flag, issue_count=1):
    business_lead, business_lead_id = _get_business_lead_info(process_name)
    process_id = _get_process_id(process_name)
    cur.execute(
        """
        INSERT INTO ops_srv.service_interruption (
            report_date,
            process_name,
            carrier_id,
            carrier_name,
            raw_file_name,
            received,
            processed,
            issue_description,
            issue_status,
            issue_date,
            cadence,
            entity_id,
            sub_entity_id,
            buisness_entity,
            buisness_sub_entity,
            business_lead,
            business_lead_id,
            issue_count,
            process_id,
            rpa,
            updated_on
        ) VALUES (
            %s,
            %s,%s,%s,%s,
            %s,%s,
            %s,'Open',
            %s,%s,
            %s,%s,
            %s,%s,
            %s,%s,
            %s,
            %s,
            %s,
            NULL
        )
        """,
        (
            today,
            process_name,
            None if carrier_id is None else str(carrier_id),
            carrier_name,
            raw_file_name,
            bool(received_flag),
            bool(processed_flag),
            issue_description,
            today,
            cadence,
            entity_id,
            sub_entity_id,
            "270681372",
            "270681372001",
            business_lead,
            business_lead_id,
            int(issue_count),
            process_id,
            0,
        ),
    )

def resolve_existing_process_interruptions(today):
    prefix_map = load_inbound_prefix_map()
    seen_all = _build_seen_from_inbound_rows(_load_all_inbound_log_rows(), today, prefix_map)

    latest_success = {}
    _unused_com_state_map, com_process_map, _unused_com_last_success_30 = _build_com_30day_state_map(today)
    _unused_acc_acr_today, acc_acr_latest_success = _build_acc_acr_log_state_maps(today)
    for (file_date, proc, cid_key), rec in seen_all.items():
        if rec.get("val") != 1:
            continue

        key = (proc, str(cid_key).strip())
        existing = latest_success.get(key)
        if existing is None or file_date > existing:
            latest_success[key] = file_date

    conn = get_postgres_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id,
               process_name,
               carrier_id,
               issue_date
        FROM ops_srv.service_interruption
        WHERE issue_status = 'Open'
          AND rpa = 0
          AND process_name IN ('ACU', 'BOB', 'COM', 'ACC', 'ACR')
        """
    )
    open_rows = cur.fetchall()

    resolved_count = 0

    for si_id, process_name, carrier_id, issue_date in open_rows:
        proc = str(process_name or "").strip().upper()
        cid_key = str(carrier_id).strip()
        si_issue_date = _to_date(issue_date)

        if si_issue_date is None:
            continue

        if proc == "COM":
            success_dt = today if com_process_map.get(cid_key) == 1 else None
        elif proc == "ACC":
            success_dt = acc_acr_latest_success["ACC"].get(cid_key)
        elif proc == "ACR":
            if cid_key in {str(x).strip() for x in ACR_FORCE_ACTIVE}:
                success_dt = today
            else:
                success_dt = acc_acr_latest_success["ACR"].get(cid_key)
        else:
            success_dt = latest_success.get((proc, cid_key))

        if success_dt is not None and success_dt >= si_issue_date:
            cur.execute(
                """
                UPDATE ops_srv.service_interruption
                SET issue_status = 'Resolved',
                    resolution_date = %s,
                    resolution_description = 'Process was Successful in the Later run',
                    updated_on = NOW()
                WHERE id = %s
                  AND issue_status = 'Open'
                  AND rpa = 0
                """,
                (today, si_id),
            )
            resolved_count += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"[SI_RESOLVE] Resolved {resolved_count} open process interruption(s)")

def resolve_non_automated_process_interruptions(today):
    conn = get_postgres_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE ops_srv.service_interruption si
        SET issue_status = 'Resolved',
            resolution_date = %s,
            resolution_description = 'Process is no longer automated',
            updated_on = NOW()
        FROM ops_srv.ops_automation_dashboard d
        WHERE si.issue_status = 'Open'
          AND si.rpa = 0
          AND si.carrier_id IS NOT NULL
          AND d.record_date = %s
          AND d.carrier_id = si.carrier_id
          AND (
                (upper(btrim(si.process_name)) = 'ACU' AND COALESCE(d.acu_proc_automated, 0) <> 1)
             OR (upper(btrim(si.process_name)) = 'BOB' AND COALESCE(d.bob_proc_automated, 0) <> 1)
             OR (upper(btrim(si.process_name)) = 'COM' AND COALESCE(d.com_proc_automated, 0) <> 1)
             OR (upper(btrim(si.process_name)) = 'ACC' AND COALESCE(d.acc_automated, 0) <> 1)
             OR (upper(btrim(si.process_name)) = 'ACR' AND COALESCE(d.acr_automated, 0) <> 1)
          )
        """,
        (today, today),
    )

    resolved_count = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    print(f"[SI_RESOLVE] Resolved {resolved_count} open non-automated process interruption(s)")

def sync_ops_dashboard_interruptions(today, run_ts):
    """
    Rebuild ops_automation_dashboard.interruptions from the currently open
    interruptions in ops_srv.service_interruption.
    """
    conn = get_postgres_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT carrier_id
        FROM ops_srv.ops_automation_dashboard
        WHERE record_date = %s
        """,
        (today,),
    )
    dashboard_carriers = [str(r[0]).strip() for r in cur.fetchall()]

    cur.execute(
        """
        SELECT
            carrier_id,
            upper(btrim(process_name)) AS process_name,
            interruption_id,
            issue_date
        FROM ops_srv.service_interruption
        WHERE issue_status = 'Open'
          AND interruption_id IS NOT NULL
          AND issue_date IS NOT NULL
          AND CAST(issue_date AS DATE) <= %s
          AND upper(btrim(process_name)) IN ('ACU', 'BOB', 'COM', 'ACC', 'ACR')
        ORDER BY carrier_id, process_name, interruption_id
        """,
        (today,),
    )
    open_rows = cur.fetchall()

    interruption_map = {cid: {} for cid in dashboard_carriers}

    for carrier_id, process_name, interruption_id, issue_date in open_rows:
        cid_key = str(carrier_id).strip()
        proc_name_clean = str(process_name or "").strip().upper()
        proc_key = proc_name_clean[:3]

        if not proc_key:
            continue

        iid = str(interruption_id).strip()
        if not iid:
            continue

        if cid_key not in interruption_map:
            continue

        interruption_map[cid_key].setdefault(proc_key, [])
        if iid not in interruption_map[cid_key][proc_key]:
            interruption_map[cid_key][proc_key].append(iid)

    updates = []
    for cid_key in dashboard_carriers:
        payload = {
            proc: ",".join(ids)
            for proc, ids in interruption_map[cid_key].items()
            if ids
        }

        updates.append(
            (
                json.dumps(payload, ensure_ascii=False),
                run_ts,
                today,
                cid_key,
            )
        )

    if updates:
        execute_batch(
            cur,
            """
            UPDATE ops_srv.ops_automation_dashboard
            SET interruptions = %s::jsonb,
                last_updated = %s
            WHERE record_date = %s
              AND carrier_id = %s
            """,
            updates,
        )

    conn.commit()
    cur.close()
    conn.close()

    print(f"[SYNC_INTERRUPTION] Synced interruptions JSON for {len(updates)} dashboard rows")


def _evaluate_process_issue_state(proc, cid, today, seen, due_today, cad_map, hcsc_rules):
    """
    Returns current process-side issue state for one carrier/process.
    """
    cid_key = str(cid).strip()
    carrier_name = cad_map.get(cid_key, {}).get("carrier_name") or f"Carrier_{cid_key}"
    cadence = cad_map.get(cid_key, {}).get(proc.lower() + "_cadence") or ""
    pa_val = cad_map.get(cid_key, {}).get(proc.lower() + "_proc_automated")

    if pa_val != 1:
        return None

    acu_hcsc_keys = {str(x).strip() for x in ACU_HCSC_FORCE_ACTIVE}
    bob_hcsc_keys = {str(x).strip() for x in BOB_HCSC_FORCE_ACTIVE}

    def _is_30day_cadence(cad):
        return (cad or "").strip().lower() in ("monthly", "", "cadence varies")

    def _is_hcsc_due_on(check_date, proc_name):
        rule = hcsc_rules.get(proc_name)
        if not rule:
            return False

        cad = rule["cadence"]
        td_list = rule["target_dates"]

        dow = check_date.weekday() + 1
        dom = check_date.day

        if cad == "daily":
            return True
        if cad == "weekly":
            return bool(td_list) and dow in td_list
        if cad == "monthly":
            return bool(td_list) and dom in td_list
        if cad in ("", "cadence varies"):
            return True

        return True

    def _latest_hcsc_due_state(ref_date):
        earliest_day = ref_date - timedelta(days=30)
        check_day = ref_date - timedelta(days=1)

        while check_day >= earliest_day:
            if _is_hcsc_due_on(check_day, proc):
                rec = seen.get((check_day, proc, cid_key))
                return rec
            check_day -= timedelta(days=1)

        return None
    
    if (cadence or "").strip().lower() == "weekly" and proc in ("ACU", "BOB"):
        has_recent_success = _has_success_in_window(seen, cid_key, proc, today, 7)
        latest_recent = _latest_record_in_window(seen, cid_key, proc, today, 7)

        if has_recent_success:
            return {
                "has_issue": False,
                "issue_desc": None,
                "raw_file": latest_recent[1].get("file_name") if latest_recent else None,
                "received_flag": True,
                "processed_flag": True,
                "cadence": cadence,
                "carrier_name": carrier_name,
            }

        return {
            "has_issue": True,
            "issue_desc": "No successful file was found in the last 7 days.",
            "raw_file": latest_recent[1].get("file_name") if latest_recent else None,
            "received_flag": bool(latest_recent),
            "processed_flag": False,
            "cadence": cadence,
            "carrier_name": carrier_name,
        }
    
    is_hcsc_shared = (
        (proc == "ACU" and cid_key in acu_hcsc_keys) or
        (proc == "BOB" and cid_key in bob_hcsc_keys)
    )

    if is_hcsc_shared:
        is_due = _is_hcsc_due_on(today, proc)

        if is_due:
            rec = seen.get((today, proc, cid_key))

            if rec is None:
                return {
                    "has_issue": True,
                    "issue_desc": "No file to process",
                    "raw_file": None,
                    "received_flag": False,
                    "processed_flag": False,
                    "cadence": cadence,
                    "carrier_name": carrier_name,
                }

            if rec["val"] == 1:
                return {
                    "has_issue": False,
                    "issue_desc": None,
                    "raw_file": rec.get("file_name"),
                    "received_flag": True,
                    "processed_flag": True,
                    "cadence": cadence,
                    "carrier_name": carrier_name,
                }

            return {
                "has_issue": True,
                "issue_desc": "File processing error.",
                "raw_file": rec.get("file_name"),
                "received_flag": True,
                "processed_flag": False,
                "cadence": cadence,
                "carrier_name": carrier_name,
            }

        rec_today = seen.get((today, proc, cid_key))
        if rec_today is not None and rec_today["val"] == 1:
            return {
                "has_issue": False,
                "issue_desc": None,
                "raw_file": rec_today.get("file_name"),
                "received_flag": True,
                "processed_flag": True,
                "cadence": cadence,
                "carrier_name": carrier_name,
            }

        prior_due_rec = _latest_hcsc_due_state(today)
        if prior_due_rec is None:
            return {
                "has_issue": True,
                "issue_desc": "No file to process",
                "raw_file": None,
                "received_flag": False,
                "processed_flag": False,
                "cadence": cadence,
                "carrier_name": carrier_name,
            }

        if prior_due_rec["val"] == 1:
            return {
                "has_issue": False,
                "issue_desc": None,
                "raw_file": prior_due_rec.get("file_name"),
                "received_flag": True,
                "processed_flag": True,
                "cadence": cadence,
                "carrier_name": carrier_name,
            }

        return {
            "has_issue": True,
            "issue_desc": "File processing error.",
            "raw_file": prior_due_rec.get("file_name"),
            "received_flag": True,
            "processed_flag": False,
            "cadence": cadence,
            "carrier_name": carrier_name,
        }

    if _is_30day_cadence(cadence):
        candidates = [
            ((file_date, p, c), rec)
            for (file_date, p, c), rec in seen.items()
            if p == proc and str(c).strip() == cid_key
        ]

        if any(rec["val"] == 1 for _k, rec in candidates):
            return {
                "has_issue": False,
                "issue_desc": None,
                "raw_file": None,
                "received_flag": False,
                "processed_flag": True,
                "cadence": cadence,
                "carrier_name": carrier_name,
            }

        latest = None
        for (file_date, _p, _c), rec in candidates:
            if latest is None or file_date > latest[0]:
                latest = (file_date, rec)

        if latest is None:
            return {
                "has_issue": True,
                "issue_desc": "No file to process",
                "raw_file": None,
                "received_flag": False,
                "processed_flag": False,
                "cadence": cadence,
                "carrier_name": carrier_name,
            }

        return {
            "has_issue": True,
            "issue_desc": "File processing error.",
            "raw_file": latest[1].get("file_name"),
            "received_flag": True,
            "processed_flag": False,
            "cadence": cadence,
            "carrier_name": carrier_name,
        }

    is_due = bool(due_today.get((cid, proc), False))
    if not is_due:
        return {
            "has_issue": False,
            "issue_desc": None,
            "raw_file": None,
            "received_flag": False,
            "processed_flag": True,
            "cadence": cadence,
            "carrier_name": carrier_name,
        }

    rec = seen.get((today, proc, cid_key))

    if rec is None:
        return {
            "has_issue": True,
            "issue_desc": "No file to process",
            "raw_file": None,
            "received_flag": False,
            "processed_flag": False,
            "cadence": cadence,
            "carrier_name": carrier_name,
        }

    if rec["val"] == 1:
        return {
            "has_issue": False,
            "issue_desc": None,
            "raw_file": rec.get("file_name"),
            "received_flag": True,
            "processed_flag": True,
            "cadence": cadence,
            "carrier_name": carrier_name,
        }

    return {
        "has_issue": True,
        "issue_desc": "File processing error.",
        "raw_file": rec.get("file_name"),
        "received_flag": True,
        "processed_flag": False,
        "cadence": cadence,
        "carrier_name": carrier_name,
    }

def run_process_service_interruption_engine(today):
    exc_state = _build_exc_month_state(today)
    prefix_map = load_inbound_prefix_map()
    seen = _build_seen_from_inbound_rows(_load_inbound_log_rows(today, 30), today, prefix_map)
    due_today, cad_map, hcsc_rules = _get_due_and_cadence_maps(today)
    com_state_map, com_process_map, _com_last_success_30 = _build_com_30day_state_map(today, lookback_days=30)
    acc_acr_today_outcomes, _acc_acr_latest_success = _build_acc_acr_log_state_maps(today)
    _expected_today, acc_carriers, acr_carriers = load_rpa_schedule_for_today(today)

    conn = get_postgres_connection()
    cur = conn.cursor()

    for proc in ("ACU", "BOB", "COM", "ACC", "ACR"):
        if proc == "ACU":
            carrier_ids = {cid for _prefix, cid in prefix_map["ACU"] if cid}
            carrier_ids |= set(ACU_HCSC_FORCE_ACTIVE) | set(ACU_SMA_FORCE_ACTIVE)

        elif proc == "BOB":
            carrier_ids = {cid for _prefix, cid in prefix_map["BOB"] if cid}
            carrier_ids |= set(BOB_HCSC_FORCE_ACTIVE) | set(BOB_SMA_FORCE_ACTIVE)

        elif proc == "COM":
            carrier_ids = {
                int(cid_key)
                for cid_key, vals in cad_map.items()
                if vals.get("com_proc_automated") == 1 and str(cid_key).strip().isdigit()
            }
            carrier_ids |= set(COM_FORCE_ACTIVE)

        elif proc == "ACC":
            carrier_ids = {int(cid) for cid in acc_carriers if cid}

        else:  # ACR
            carrier_ids = {int(cid) for cid in acr_carriers if cid}
            carrier_ids |= set(ACR_FORCE_ACTIVE)

        for cid in sorted(carrier_ids):
            cid_key = str(cid).strip()

            dash_vals = cad_map.get(cid_key, {})

            if proc == "ACU":
                if dash_vals.get("acu_proc_automated") != 1:
                    continue
            elif proc == "BOB":
                if dash_vals.get("bob_proc_automated") != 1:
                    continue
            elif proc == "ACC":
                if dash_vals.get("acc_automated") != 1:
                    continue
            elif proc == "ACR":
                if dash_vals.get("acr_automated") != 1:
                    continue 

            if proc == "COM":
                rec = com_state_map.get(cid_key)
                carrier_name = cad_map.get(cid_key, {}).get("carrier_name") or f"Carrier_{cid_key}"
                cadence = cad_map.get(cid_key, {}).get("com_cadence") or ""

                if rec is None or not rec.get("has_any_run"):
                    state = {
                        "has_issue": True,
                        "issue_desc": "No success runs were found for this carrier in the last 30 days.",
                        "raw_file": None,
                        "received_flag": False,
                        "processed_flag": False,
                        "cadence": cadence,
                        "carrier_name": carrier_name,
                    }
                elif rec.get("has_success"):
                    state = {
                        "has_issue": False,
                        "issue_desc": None,
                        "raw_file": None,
                        "received_flag": True,
                        "processed_flag": True,
                        "cadence": cadence,
                        "carrier_name": carrier_name,
                    }
                else:
                    state = {
                        "has_issue": True,
                        "issue_desc": "No success runs were found for this carrier in the last 30 days.",
                        "raw_file": None,
                        "received_flag": True,
                        "processed_flag": False,
                        "cadence": cadence,
                        "carrier_name": carrier_name,
                    }

            elif proc == "ACC":
                if cid in ACC_DEV:
                    continue

                carrier_name = cad_map.get(cid_key, {}).get("carrier_name") or f"Carrier_{cid_key}"
                cadence = cad_map.get(cid_key, {}).get("acc_cadence") or "Daily"
                outcome = acc_acr_today_outcomes.get(("ACC", cid_key))

                if outcome == "success":
                    state = {
                        "has_issue": False,
                        "issue_desc": None,
                        "raw_file": None,
                        "received_flag": True,
                        "processed_flag": True,
                        "cadence": cadence,
                        "carrier_name": carrier_name,
                    }
                elif outcome == "error":
                    state = {
                        "has_issue": True,
                        "issue_desc": "No success runs were found for this carrier.",
                        "raw_file": None,
                        "received_flag": True,
                        "processed_flag": False,
                        "cadence": cadence,
                        "carrier_name": carrier_name,
                    }
                else:
                    state = {
                        "has_issue": True,
                        "issue_desc": "No success runs were found for this carrier.",
                        "raw_file": None,
                        "received_flag": False,
                        "processed_flag": False,
                        "cadence": cadence,
                        "carrier_name": carrier_name,
                    }

            elif proc == "ACR":
                if cid in ACR_DEV:
                    continue
                if cid in {UHC, AMBETTER}:
                    continue
                if cid in ACR_NO_ACTION and cid not in ACR_FORCE_ACTIVE:
                    continue

                carrier_name = cad_map.get(cid_key, {}).get("carrier_name") or f"Carrier_{cid_key}"
                cadence = cad_map.get(cid_key, {}).get("acr_cadence") or "Daily"

                if cid in ACR_FORCE_ACTIVE:
                    state = {
                        "has_issue": False,
                        "issue_desc": None,
                        "raw_file": None,
                        "received_flag": True,
                        "processed_flag": True,
                        "cadence": cadence,
                        "carrier_name": carrier_name,
                    }
                else:
                    outcome = acc_acr_today_outcomes.get(("ACR", cid_key))

                    if outcome == "success":
                        state = {
                            "has_issue": False,
                            "issue_desc": None,
                            "raw_file": None,
                            "received_flag": True,
                            "processed_flag": True,
                            "cadence": cadence,
                            "carrier_name": carrier_name,
                        }
                    elif outcome == "error":
                        state = {
                            "has_issue": True,
                            "issue_desc": "No success runs were found for this carrier.",
                            "raw_file": None,
                            "received_flag": True,
                            "processed_flag": False,
                            "cadence": cadence,
                            "carrier_name": carrier_name,
                        }
                    else:
                        state = {
                            "has_issue": True,
                            "issue_desc": "No success runs were found for this carrier.",
                            "raw_file": None,
                            "received_flag": False,
                            "processed_flag": False,
                            "cadence": cadence,
                            "carrier_name": carrier_name,
                        }

            else:
                state = _evaluate_process_issue_state(proc, cid, today, seen, due_today, cad_map, hcsc_rules)

            if state is None:
                continue

            carrier_name = state["carrier_name"]
            cadence = state["cadence"]
            issue_desc = state["issue_desc"]
            raw_file = state["raw_file"]
            received_flag = state["received_flag"]
            processed_flag = state["processed_flag"]
            has_issue = state["has_issue"]

            cur.execute(
                """
                SELECT id, issue_date, issue_description, issue_status, issue_count
                FROM ops_srv.service_interruption
                WHERE process_name = %s
                  AND carrier_id = %s
                  AND rpa = 0
                ORDER BY issue_date DESC, id DESC
                LIMIT 1
                """,
                (proc, cid_key),
            )
            existing = cur.fetchone()

            if not existing:
                if has_issue:
                    _insert_interruption_process(
                        cur,
                        today=today,
                        process_name=proc,
                        carrier_id=cid_key,
                        carrier_name=carrier_name,
                        raw_file_name=raw_file,
                        cadence=cadence,
                        issue_description=issue_desc,
                        received_flag=received_flag,
                        processed_flag=processed_flag,
                        issue_count=1,
                    )
                continue

            prev_id, prev_issue_date_raw, prev_desc, prev_status, prev_count = existing
            prev_issue_date = _to_date(prev_issue_date_raw)

            if (not has_issue) and (prev_status == "Open"):
                cur.execute(
                    """
                    UPDATE ops_srv.service_interruption
                    SET issue_status = 'Resolved',
                        resolution_date = %s,
                        resolution_description = 'Process was Successful in the Later run',
                        updated_on = NOW()
                    WHERE id = %s
                      AND issue_status = 'Open'
                      AND rpa = 0
                    """,
                    (today, prev_id),
                )
                continue

            if has_issue:
                if prev_status == "Open":
                    if prev_issue_date is not None and today > prev_issue_date:
                        cur.execute(
                            """
                            UPDATE ops_srv.service_interruption
                            SET issue_count = COALESCE(issue_count, 1) + 1,
                                issue_date = %s,
                                raw_file_name = %s,
                                received = %s,
                                processed = %s,
                                updated_on = NOW()
                            WHERE id = %s
                              AND issue_status = 'Open'
                              AND rpa = 0
                            """,
                            (today, raw_file, bool(received_flag), bool(processed_flag), prev_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE ops_srv.service_interruption
                            SET raw_file_name = %s,
                                received = %s,
                                processed = %s,
                                updated_on = NOW()
                            WHERE id = %s
                              AND issue_status = 'Open'
                              AND rpa = 0
                            """,
                            (raw_file, bool(received_flag), bool(processed_flag), prev_id),
                        )
                    continue

                if prev_status != "Open":
                    _insert_interruption_process(
                        cur,
                        today=today,
                        process_name=proc,
                        carrier_id=cid_key,
                        carrier_name=carrier_name,
                        raw_file_name=raw_file,
                        cadence=cadence,
                        issue_description=issue_desc,
                        received_flag=received_flag,
                        processed_flag=processed_flag,
                        issue_count=1,
                    )
                    continue

    # ===========================================================
    # EXC process interruption - single process-level interruption
    # ===========================================================
    proc = "EXC"
    exc_carrier_id = None
    exc_carrier_name = "Exclusion_report"
    exc_cadence = EXC_CADENCE

    cur.execute(
        """
        SELECT id, issue_date, issue_description, issue_status, issue_count
        FROM ops_srv.service_interruption
            WHERE process_name = %s
            AND carrier_id IS NULL
            AND rpa = 0
        ORDER BY issue_date DESC, id DESC
        LIMIT 1
        """,
        (proc,),
    )
    existing = cur.fetchone()

    if exc_state.get("all_success"):
        state = {
            "has_issue": False,
            "issue_desc": None,
            "raw_file": None,
            "received_flag": True,
            "processed_flag": True,
            "cadence": exc_cadence,
            "carrier_name": exc_carrier_name,
        }
    else:
        has_any_run = exc_state.get("has_any_run", False)
        missing_prefixes = exc_state.get("missing_prefixes", [])

        state = {
            "has_issue": True,
            "issue_desc": (
                "No successful Exclusion run was found for all 3 required files in the previous month."
                if has_any_run else
                "No Exclusion files were processed successfully in the previous month."
            ),
            "raw_file": ", ".join(missing_prefixes) if missing_prefixes else None,
            "received_flag": has_any_run,
            "processed_flag": False,
            "cadence": exc_cadence,
            "carrier_name": exc_carrier_name,
        }

    carrier_name = state["carrier_name"]
    cadence = state["cadence"]
    issue_desc = state["issue_desc"]
    raw_file = state["raw_file"]
    received_flag = state["received_flag"]
    processed_flag = state["processed_flag"]
    has_issue = state["has_issue"]

    if not existing:
        if has_issue:
            _insert_interruption_process(
                cur,
                today=today,
                process_name=proc,
                carrier_id=exc_carrier_id,
                carrier_name=carrier_name,
                raw_file_name=raw_file,
                cadence=cadence,
                issue_description=issue_desc,
                received_flag=received_flag,
                processed_flag=processed_flag,
                issue_count=1,
            )
    else:
        prev_id, prev_issue_date_raw, prev_desc, prev_status, prev_count = existing
        prev_issue_date = _to_date(prev_issue_date_raw)

        if (not has_issue) and (prev_status == "Open"):
            cur.execute(
                """
                UPDATE ops_srv.service_interruption
                SET issue_status = 'Resolved',
                    resolution_date = %s,
                    resolution_description = 'Process was Successful in the Later run',
                    updated_on = NOW()
                WHERE id = %s
                  AND issue_status = 'Open'
                  AND rpa = 0
                """,
                (today, prev_id),
            )

        elif has_issue:
            if (
                prev_status == "Open"
                and prev_desc == issue_desc
                and prev_issue_date is not None
                and today > prev_issue_date
            ):
                cur.execute(
                    """
                    UPDATE ops_srv.service_interruption
                    SET issue_count = COALESCE(issue_count, 1) + 1,
                        issue_date = %s,
                        raw_file_name = %s,
                        received = %s,
                        processed = %s,
                        updated_on = NOW()
                    WHERE id = %s
                      AND issue_status = 'Open'
                      AND rpa = 0
                    """,
                    (today, raw_file, bool(received_flag), bool(processed_flag), prev_id),
                )

            elif (
                prev_status == "Open"
                and prev_issue_date == today
                and prev_desc == issue_desc
            ):
                pass

            elif prev_status == "Open" and prev_desc != issue_desc:
                if prev_issue_date == today:
                    cur.execute(
                        """
                        UPDATE ops_srv.service_interruption
                        SET issue_description = %s,
                            raw_file_name = %s,
                            received = %s,
                            processed = %s,
                            updated_on = NOW()
                        WHERE id = %s
                          AND issue_status = 'Open'
                          AND rpa = 0
                        """,
                        (issue_desc, raw_file, bool(received_flag), bool(processed_flag), prev_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE ops_srv.service_interruption
                        SET issue_status = 'Resolved',
                            resolution_date = %s,
                            resolution_description = 'Issue type changed — superseded by new interruption',
                            updated_on = NOW()
                        WHERE id = %s
                          AND issue_status = 'Open'
                          AND rpa = 0
                        """,
                        (today, prev_id),
                    )

                    _insert_interruption_process(
                        cur,
                        today=today,
                        process_name=proc,
                        carrier_id=exc_carrier_id,
                        carrier_name=carrier_name,
                        raw_file_name=raw_file,
                        cadence=cadence,
                        issue_description=issue_desc,
                        received_flag=received_flag,
                        processed_flag=processed_flag,
                        issue_count=1,
                    )

            elif prev_status != "Open":
                _insert_interruption_process(
                    cur,
                    today=today,
                    process_name=proc,
                    carrier_id=exc_carrier_id,
                    carrier_name=carrier_name,
                    raw_file_name=raw_file,
                    cadence=cadence,
                    issue_description=issue_desc,
                    received_flag=received_flag,
                    processed_flag=processed_flag,
                    issue_count=1,
                )
    conn.commit()
    cur.close()
    conn.close()


# ===========================================================
# 🚀 Main execution
# ===========================================================
def run_for_date(target_date):
    global PROCESS_ID_MAP

    cst = pytz.timezone("America/Chicago")
    run_ts = datetime.now(cst)
    PROCESS_ID_MAP = load_process_id_map()
    print(f"Starting automation dashboard engine for {target_date} (CST)")

    (
        carriers,
        acu_dl,
        bob_dl,
        com_dl,
        acu_ps,
        bob_ps,
        com_ps,
        acc,
        acr,
        acc_rpa_on,
        acr_rpa_on,
        acu_auto_set,
        bob_auto_set,
        com_auto_set,
        acc_all_set,
        acr_all_set,
        acu_varies_set,
        bob_varies_set,
        com_varies_set,
    ) = load_matrices()

    if FORCE_BASELINE_TODAY or not baseline_exists(target_date):
        conn_prev = get_postgres_connection()
        rows = []
        for _, c in carriers.iterrows():
            prev_notes, prev_interruptions, prev_carry = get_previous_record_state(conn_prev, str(c.carrier_id), target_date)
            today_notes, today_interruptions, today_carry, today_last_run = get_existing_today_state(conn_prev, str(c.carrier_id), target_date)

            today_notes_json = _normalize_notes_json(today_notes)
            prev_notes_json = _normalize_notes_json(prev_notes)

            if today_notes_json:
                carried_notes = today_notes_json
            else:
                carried_notes = prev_notes_json
            carried_interruptions = _normalize_interruptions_json(today_interruptions)
            carried_carry = today_carry if today_carry is not None else (prev_carry or {})
            carried_last_run = today_last_run if today_last_run is not None else _empty_last_run_date()

            row_data = {
                **c.to_dict(),
                **baseline_row(
                    c,
                    acu_dl,
                    bob_dl,
                    com_dl,
                    acu_ps,
                    bob_ps,
                    com_ps,
                    acc,
                    acr,
                    acc_rpa_on,
                    acr_rpa_on,
                    acu_auto_set,
                    bob_auto_set,
                    com_auto_set,
                    acc_all_set,
                    acr_all_set,
                    acu_varies_set,
                    bob_varies_set,
                    com_varies_set,
                ),
                "notes": carried_notes,
                "interruptions": carried_interruptions or {},
                "carry_over_flag": carried_carry or {},
                "last_run_date": carried_last_run or _empty_last_run_date(),
            }
            rows.append(row_data)

        exc_row = build_exc_dashboard_baseline(conn_prev, target_date)

        conn_prev.close()
        df = pd.DataFrame(rows)
        insert_snapshot(df, target_date, run_ts)
        insert_or_update_exc_snapshot(exc_row, target_date, run_ts)
        print(f"[BASELINE] Snapshot built for {target_date} CST ✓")
    else:
        conn_prev = get_postgres_connection()
        exc_row = build_exc_dashboard_baseline(conn_prev, target_date)
        conn_prev.close()
        insert_or_update_exc_snapshot(exc_row, target_date, run_ts)
        print(f"[BASELINE] Snapshot for {target_date} already exists — skipping baseline insert.")

    print("[UPDATE] Applying RPA log updates...")
    dates_rpa = update_from_rpa_logs(target_date, run_ts, acu_varies_set, bob_varies_set, com_varies_set)

    print("[UPDATE] Applying inbound file log updates...")
    dates_inbound = update_from_inbound_logs(target_date, run_ts, acu_varies_set, bob_varies_set, com_varies_set)

    dates_all = set(dates_rpa) | set(dates_inbound)
    print(f"[UPDATE] Recomputing status for {len(dates_all)} distinct date(s): {sorted(dates_all)}")
    recompute_statuses(dates_all, run_ts)

    print("[EXC] Updating exclusion report dashboard...")
    update_exc_dashboard(target_date, run_ts)

    print("[SI] Resolving existing process interruptions...")
    resolve_existing_process_interruptions(target_date)

    print("[SI] Resolving non-automated process interruptions...")
    resolve_non_automated_process_interruptions(target_date)

    now_cst = datetime.now(cst)
    if now_cst.hour == 16 and now_cst.weekday() < 5: 
        run_process_service_interruption_engine(target_date)
        print("Ran process Service Interruption engine.")
    else:
        print("Not 4 PM CST — skipping Service Interruption engine.")

    print("[OPS] Syncing open process interruptions into ops dashboard...")
    sync_ops_dashboard_interruptions(target_date, run_ts)

    print("[EXC] Syncing EXC interruptions into exclusion dashboard...")
    sync_exc_dashboard_interruptions(target_date, run_ts)

    print(f"[DONE] Automation dashboard updated for {target_date} @ {datetime.now(cst)} CST")   

if __name__ == "__main__":
    cst = pytz.timezone("America/Chicago")
    if MANUAL_DATE_STR:
        target_date = datetime.strptime(MANUAL_DATE_STR, "%Y-%m-%d").date()
    else:
        target_date = datetime.now(cst).date()
    run_for_date(target_date)






