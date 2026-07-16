# ==========================================================
# utils/zoho_utils.py
# ==========================================================
"""
Zoho CRM Utilities for ACC RPA
------------------------------
Purpose:
    • Securely authenticate to Zoho CRM via refresh token in Key Vault
    • Support COQL queries, Bulk Read (v8), and Bulk Write (v2)
    • Provide agent/contract fetching and related-list lookups

Key Design Rules:
    • Auth, base URLs, and token caching kept exactly as tested
    • No database or matrix access — handlers/runner supply paths
    • 5-minute cooldown between refreshes; max 3 retries
    • All retry failures raise ZohoAuthError for runner to handle
"""

import os
import io
import time
import json
import zipfile
import requests
import pandas as pd
import pytz
from datetime import datetime
from collections import OrderedDict
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from typing import Optional, Union, List, Dict, Any

# ==========================================================
# CONFIGURATION
# ==========================================================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")
ZBASE = "https://www.zohoapis.com/crm/v2"
ZACCOUNTS = "https://accounts.zoho.com/oauth/v2"
ZORG_ID = "658450569"

_ZOHO_TOKEN_CACHE = {"access_token": None, "expires_at": 0, "last_refresh": 0}
_ZOHO_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "zoho_token_cache.json")

REFRESH_RETRY_LIMIT = 3
REFRESH_INTERVAL_SEC = 300  # 5 minutes

# ==========================================================
# EXCEPTIONS
# ==========================================================
class ZohoAuthError(Exception):
    """Raised when Zoho authentication fails or refresh token becomes invalid."""
    pass


# ==========================================================
# AUTHENTICATION
# ==========================================================
def get_zoho_secrets():
    """Fetch Zoho OAuth secrets from Azure Key Vault."""
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    return (
        client.get_secret("dataops-zoho-api-client-id").value,
        client.get_secret("dataops-zoho-api-client-secret").value,
        client.get_secret("dataops-zoho-api-refresh-token").value,
    )


def _perform_token_refresh(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Internal helper to call Zoho refresh endpoint once."""
    url = (
        f"{ZACCOUNTS}/token?"
        f"refresh_token={refresh_token}&client_id={client_id}"
        f"&client_secret={client_secret}&grant_type=refresh_token"
    )
    resp = requests.post(url)
    data = resp.json()
    if resp.status_code == 200 and "access_token" in data:
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        expires_at = time.time() + expires_in - 30
        _ZOHO_TOKEN_CACHE.update({
            "access_token": token,
            "expires_at": expires_at,
            "last_refresh": time.time()
        })
        with open(_ZOHO_TOKEN_FILE, "w") as f:
            json.dump(_ZOHO_TOKEN_CACHE, f)
        print("🔐 Zoho token refreshed successfully.")
        return token
    raise ZohoAuthError(f"Token refresh failed: {data}")


def get_zoho_token(force_refresh: bool = False) -> str:
    """
    Get Zoho OAuth access token with caching, file persistence, and safe refresh policy.
    Enforces 5-minute cooldown between refreshes (Zoho requirement).
    Retries up to 3 times if refresh fails.
    """
    global _ZOHO_TOKEN_CACHE

    # ✅ Reuse in-memory cache if valid
    if (
        not force_refresh
        and _ZOHO_TOKEN_CACHE["access_token"]
        and time.time() < _ZOHO_TOKEN_CACHE["expires_at"]
    ):
        return _ZOHO_TOKEN_CACHE["access_token"]

    # ✅ Load from file if available and still valid
    if not force_refresh and os.path.exists(_ZOHO_TOKEN_FILE):
        try:
            with open(_ZOHO_TOKEN_FILE, "r") as f:
                cached = json.load(f)
            if time.time() < cached.get("expires_at", 0):
                _ZOHO_TOKEN_CACHE.update(cached)
                return cached["access_token"]
        except Exception:
            pass

    # ✅ Enforce 5-minute cooldown between refreshes
    last_refresh = _ZOHO_TOKEN_CACHE.get("last_refresh", 0)
    since_last = time.time() - last_refresh
    if since_last < REFRESH_INTERVAL_SEC:
        wait_remaining = int(REFRESH_INTERVAL_SEC - since_last)
        print(f"⏳ Skipping refresh (last refresh {since_last/60:.1f} min ago). "
              f"Using existing token for {wait_remaining}s more.")
        if _ZOHO_TOKEN_CACHE["access_token"]:
            return _ZOHO_TOKEN_CACHE["access_token"]

    # 🚀 Perform up to 3 retries with 5-minute intervals
    client_id, client_secret, refresh_token = get_zoho_secrets()
    for attempt in range(1, REFRESH_RETRY_LIMIT + 1):
        try:
            print(f"🔁 Refresh attempt {attempt}/{REFRESH_RETRY_LIMIT}...")
            return _perform_token_refresh(client_id, client_secret, refresh_token)
        except ZohoAuthError as e:
            print(f"⚠️ Refresh attempt {attempt} failed: {e}")
            if attempt < REFRESH_RETRY_LIMIT:
                print(f"🕒 Waiting 5 minutes before retrying...")
                time.sleep(REFRESH_INTERVAL_SEC)
            else:
                raise ZohoAuthError("Token refresh failed after 3 retries.") from e


def validate_zoho_auth() -> bool:
    """Quick pre-run check to verify Zoho token accessibility."""
    try:
        token = get_zoho_token()
        if not token:
            raise ZohoAuthError("No token returned.")
        print("✅ Zoho auth validation successful.")
        return True
    except Exception as e:
        print(f"❌ Zoho auth validation failed: {e}")
        return False


# ==========================================================
# CRM QUERY HELPERS (COQL)
# ==========================================================
def query_crm(module: str, query_str: str) -> pd.DataFrame:
    """Execute a COQL query and return DataFrame."""
    token = get_zoho_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    resp = requests.post(f"{ZBASE}/coql", headers=headers, json={"select_query": query_str})

    if resp.status_code == 401 and "INVALID_TOKEN" in resp.text:
        print("🔁 Token expired mid-run — refreshing and retrying...")
        token = get_zoho_token(force_refresh=True)
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        resp = requests.post(f"{ZBASE}/coql", headers=headers, json={"select_query": query_str})

    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Zoho COQL failed ({resp.status_code}): {resp.text}")

    data = resp.json().get("data", [])
    print(f"✅ CRM query [{module}] → {len(data)} record(s)")
    return pd.DataFrame(data)


# ==========================================================
# BULK READ HELPERS
# ==========================================================
def get_contracts(
    carrier_id: str,
    npn_list: Optional[list] = None,
    crm_filter=None,
    fields=None,
    batch_size: int = 200,
    allow_full_fetch: bool = False
) -> pd.DataFrame:
    """
    Fetch contract records from Zoho CRM (Agent_Contracts) via Bulk Read v8.

    Behavior:
      • Respects TEST_MODE in config — only pulls TEST_NPNS
      • Uses Bulk Read v8 endpoint with criteria batching
      • Returns a clean DataFrame (empty if no results)
    """
    import io, time, zipfile, requests
    from collections import OrderedDict
    import pandas as pd
    from utils.config import TEST_MODE, TEST_NPNS

    # ======================================================
    # 0️⃣ Validation
    # ======================================================
    if not carrier_id:
        raise ValueError("carrier_id is required for contract fetch")

    if isinstance(crm_filter, str):
        crm_filter = [s.strip() for s in crm_filter.replace(";", ",").split(",") if s.strip()]
    elif isinstance(crm_filter, (list, tuple, set)):
        crm_filter = [str(s).strip() for s in crm_filter if str(s).strip()]
    else:
        raise TypeError(f"Invalid crm_filter type: {type(crm_filter)}")

    if not crm_filter:
        raise ValueError("crm_filter cannot be empty after normalization.")

    if not fields:
        fields = [
            "Name", "Carrier.id", "Agent.id", "Agent.NPN",
            "Agent.First_Name", "Agent.Last_Name", "Agent.Email",
            "Status", "Status_Date", "Google_Drive_ID", "id", "Agent.Secondary_Email", "Agent.Phone",
            "Agent.Contracting_Email", "Agent.Mobile", "Agent.Other_Phone"
        ]

    if npn_list:
        npn_list = list(OrderedDict.fromkeys([str(n).strip() for n in npn_list if str(n).strip()]))

    # ======================================================
    # 🧪 TEST MODE ENFORCEMENT
    # ======================================================
    if TEST_MODE:
        if TEST_NPNS:
            npn_list = TEST_NPNS
            allow_full_fetch = False
            print(f"🧪 TEST MODE ACTIVE → Restricting to NPN(s): {npn_list}")
        else:
            print("⚠️ TEST_MODE enabled but TEST_NPNS is empty — returning blank DataFrame.")
            return pd.DataFrame()

    # ======================================================
    # 1️⃣ Auth and Header Setup
    # ======================================================
    token = get_zoho_token(force_refresh=False)
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}

    # ======================================================
    # 2️⃣ Build Criteria
    # ======================================================
    criteria = [{"field": {"api_name": "Carrier.id"}, "comparator": "equal", "value": carrier_id}]

    # ✅ Handle CRM filter cleanly
    if len(crm_filter) == 1:
        # Single status — no need for a group
        criteria.append({
            "field": {"api_name": "Status"},
            "comparator": "equal",
            "value": crm_filter[0]
        })
    else:
        # Multiple statuses → group them with OR
        criteria.append({
            "group_operator": "or",
            "group": [
                {"field": {"api_name": "Status"}, "comparator": "equal", "value": s}
                for s in crm_filter
            ]
        })

    if npn_list:
        if len(npn_list) == 1:
            criteria.append({
                "field": {"api_name": "Agent.NPN"}, "comparator": "equal", "value": npn_list[0]
            })
        else:
            criteria.append({
                "group_operator": "or",
                "group": [
                    {"field": {"api_name": "Agent.NPN"}, "comparator": "equal", "value": n}
                    for n in npn_list
                ]
            })
    elif not allow_full_fetch:
        print("⚠️ No NPNs provided and full fetch not allowed — returning empty DataFrame.")
        return pd.DataFrame()

    payload = {
        "query": {
            "module": {"api_name": "Agent_Contracts"},
            "fields": fields,
            "criteria": {"group_operator": "and", "group": criteria},
        }
    }

    # ======================================================
    # 3️⃣ Submit Bulk Read Job
    # ======================================================
    url = "https://www.zohoapis.com/crm/bulk/v8/read"
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code not in (200, 201):
        raise Exception(f"Bulk read job creation failed: {resp.text}")
    job_id = resp.json()["data"][0]["details"]["id"]

    # ======================================================
    # 4️⃣ Poll for Completion
    # ======================================================
    poll_url = f"https://www.zohoapis.com/crm/bulk/v8/read/{job_id}"
    while True:
        poll = requests.get(poll_url, headers=headers)
        state = poll.json()["data"][0]["state"]
        if state in ("COMPLETED", "FAILURE"):
            break
        print(f"⏳ Job State: {state}")
        time.sleep(10)
    if state != "COMPLETED":
        raise Exception("Bulk job failed or incomplete.")

    # ======================================================
    # 5️⃣ Download and Parse CSV
    # ======================================================
    result_url = poll.json()["data"][0]["result"]["download_url"]
    result_full_url = f"https://www.zohoapis.com{result_url}"
    data_resp = requests.get(result_full_url, headers=headers)
    with zipfile.ZipFile(io.BytesIO(data_resp.content)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            df = pd.read_csv(f)

    print(f"✅ Retrieved {len(df)} contract record(s).")
    # Email fallback: Contracting_Email → Email → Secondary_Email
    print("Collapsing emails...")
    for i, row in df.iterrows():
        if not pd.isna(row.get('Agent.Contracting_Email')):
            df.at[i, 'Agent.Email'] = row.get('Agent.Contracting_Email')
        elif not pd.isna(row.get('Agent.Email')):
            continue
        elif pd.isna(row.get('Agent.Email')) and not pd.isna(row.get('Agent.Secondary_Email')):
            df.at[i, 'Agent.Email'] = row.get('Agent.Secondary_Email')

    # Phone fallback: Phone → Mobile → Other_Phone
    print("Collapsing phone numbers...")
    for i, row in df.iterrows():
        if not pd.isna(row.get('Agent.Phone')):
            continue
        elif not pd.isna(row.get('Agent.Mobile')):
            df.at[i, 'Agent.Phone'] = str(row.get('Agent.Mobile'))
        elif not pd.isna(row.get('Agent.Other_Phone')):
            df.at[i, 'Agent.Phone'] = str(row.get('Agent.Other_Phone'))
    print('Contracts DF:')
    print(df.to_string())
    return df



def get_agents(npn_list: list, fields=None, batch_size: int = 25) -> pd.DataFrame:
    """
    Bulk Read v8 for Contacts (Agent records) by NPN.
    Handles Zoho's 'CRITERIA_LIMIT_EXCEEDED' (max 25) by batching requests.
    Returns a single merged DataFrame of all batches.
    """
    import requests, time, io, zipfile, pandas as pd
    from collections import OrderedDict

    if not npn_list:
        print("⚠️ No NPNs provided for agent fetch.")
        return pd.DataFrame()

    npn_list = list(OrderedDict.fromkeys([str(n).strip() for n in npn_list if str(n).strip()]))
    if not npn_list:
        return pd.DataFrame()

    if not fields:
        fields = ["NPN", "First_Name", "Last_Name", "Email", "Mailing_State", "Type","Resident_State", "Secondary_Email", "Phone", "Mobile", "Other_Phone", "Contracting_Email"]

    token = get_zoho_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}

    all_frames = []
    # 🔹 Split into chunks of up to 25 NPNs (Zoho limit)
    for start in range(0, len(npn_list), batch_size):
        batch = npn_list[start:start + batch_size]
        print(f"🔁 Fetching agent batch {start // batch_size + 1} ({len(batch)} NPNs)...")

        if len(batch) == 1:
            criteria = {"field": {"api_name": "NPN"}, "comparator": "equal", "value": batch[0]}
        else:
            criteria = {
                "group_operator": "or",
                "group": [{"field": {"api_name": "NPN"}, "comparator": "equal", "value": n} for n in batch]
            }

        payload = {
            "query": {
                "module": {"api_name": "Contacts"},
                "fields": fields,
                "criteria": criteria
            }
        }

        url = "https://www.zohoapis.com/crm/bulk/v8/read"
        resp = requests.post(url, headers=headers, json=payload)
        if resp.status_code not in (200, 201):
            raise Exception(f"Bulk read creation failed: {resp.text}")

        job_id = resp.json()["data"][0]["details"]["id"]
        poll_url = f"https://www.zohoapis.com/crm/bulk/v8/read/{job_id}"

        # Poll until job completes
        while True:
            poll = requests.get(poll_url, headers=headers)
            state = poll.json()["data"][0]["state"]
            if state in ("COMPLETED", "FAILURE"):
                break
            print(f"⏳ Job State: {state}")
            time.sleep(10)

        if state != "COMPLETED":
            raise Exception(f"Bulk job failed or incomplete for batch starting at {start}.")

        result_url = poll.json()["data"][0]["result"]["download_url"]
        result_full_url = f"https://www.zohoapis.com{result_url}"
        data_resp = requests.get(result_full_url, headers=headers)

        with zipfile.ZipFile(io.BytesIO(data_resp.content)) as z:
            csv_name = z.namelist()[0]
            with z.open(csv_name) as f:
                df_batch = pd.read_csv(f)
                all_frames.append(df_batch)

        print(f"✅ Retrieved {len(df_batch)} agent record(s) in batch {start // batch_size + 1}.")

    if not all_frames:
        print("⚠️ No agent data returned from any batch.")
        return pd.DataFrame()

    df = pd.concat(all_frames, ignore_index=True)
    print(f"✅ Total retrieved {len(df)} agent record(s) across {len(all_frames)} batch(es).")

    # Email fallback: Contracting_Email → Email → Secondary_Email
    print("Collapsing emails...")
    for i, row in df.iterrows():
        if not pd.isna(row.get('Contracting_Email')):
            df.at[i, 'Email'] = row.get('Contracting_Email')
        elif not pd.isna(row.get('Email')):
            continue
        elif pd.isna(row.get('Email')) and not pd.isna(row.get('Secondary_Email')):
            df.at[i, 'Email'] = row.get('Secondary_Email')

    # Phone fallback: Phone → Mobile → Other_Phone
    print("Collapsing phone numbers...")
    for i, row in df.iterrows():
        if not pd.isna(row.get('Phone')):
            continue
        elif not pd.isna(row.get('Mobile')):
            df.at[i, 'Phone'] = str(row.get('Mobile'))
        elif not pd.isna(row.get('Other_Phone')):
            df.at[i, 'Phone'] = str(row.get('Other_Phone'))
    print('Agents DF:')
    print(df.to_string())
    return df



# ==========================================================
# BULK WRITE HELPERS
# ==========================================================
def _create_bulk_csv_zip(data: list, module: str, chunk_index: int, base_dir: str):
    os.makedirs(base_dir, exist_ok=True)
    csv_path = os.path.join(base_dir, f"{module}_bulk_{chunk_index}.csv")
    zip_path = os.path.join(base_dir, f"{module}_bulk_{chunk_index}.zip")
    pd.DataFrame(data).to_csv(csv_path, index=False)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(csv_path, arcname=os.path.basename(csv_path))
    print(f"📝 Created {os.path.basename(zip_path)} in {base_dir}")
    return csv_path, zip_path


def _upload_bulk_file(zip_file_path, token):
    url = "https://content.zohoapis.com/crm/v2/upload"
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "feature": "bulk-write",
        "X-CRM-ORG": ZORG_ID,
    }
    with open(zip_file_path, "rb") as f:
        resp = requests.post(url, headers=headers, files={"file": f})
    if resp.status_code == 200 and resp.json().get("code") == "FILE_UPLOAD_SUCCESS":
        fid = resp.json()["details"]["file_id"]
        print(f"✅ Uploaded bulk file → {fid}")
        return fid
    print(f"❌ Upload failed: {resp.text}")
    return None


def _submit_bulk_write_job(file_id, module, sample_record, token, find_by="id", operation="update"):
    url = "https://www.zohoapis.com/crm/bulk/v2/write"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    # ✅ Include find_by field in mapping — Zoho requires it
    field_mappings = [
        {"api_name": k, "index": i}
        for i, k in enumerate(sample_record.keys())
    ]

    body = {
        "operation": operation,
        "resource": [{
            "type": "data",
            "module": module,
            "file_id": file_id,
            "find_by": find_by,
            "field_mappings": field_mappings
        }]
    }

    resp = requests.post(url, headers=headers, json=body)
    if resp.status_code in (200, 201):
        job_id = resp.json().get("details", {}).get("id")
        print(f"🚀 Bulk Write job created → {job_id}")
        return job_id
    print(f"❌ Job creation failed: {resp.text}")
    return None




def _poll_bulk_write_status(job_id):
    token = get_zoho_token()
    url = f"https://www.zohoapis.com/crm/bulk/v2/write/{job_id}"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    print(f"📡 Polling Bulk Write job {job_id}...")

    for attempt in range(30):
        time.sleep(10)
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            continue
        data = resp.json()
        state = (
            data.get("details", {}).get("state")
            or (data.get("resource", [{}])[0].get("status") if data.get("resource") else None)
        )
        print(f"⏳ Attempt {attempt+1}/30 → {state}")
        if state and state.upper() in ("COMPLETED", "FAILED"):
            download_url = (
                data.get("details", {}).get("result", {}).get("download_url")
                or data.get("resource", [{}])[0]
                .get("file", {})
                .get("result", {})
                .get("download_url")
            )
            if not download_url:
                return state, pd.DataFrame([{"Status": "success"}])
            r = requests.get(download_url, headers=headers, stream=True)
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    df = pd.read_csv(f)
            df["upload_date_crm"] = datetime.now(pytz.timezone("US/Central")).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            return state, df
    return "TIMEOUT", None


def bulk_update_crm(module, records, *, carrier_id, download_path, find_by=None):
    """Unified Bulk Write v2 update wrapper."""
    if not records:
        print("⚠️ No records to update.")
        return None

    if module == "Agent_Contracts":
        find_by = "id"
    elif module == "Contacts":
        find_by = "NPN"
    else:
        find_by = find_by or "id"

    token = get_zoho_token()
    total = len(records)
    print(f"🚀 Bulk update {total} {module} records (find_by={find_by})")

    chunk_size = 25000
    chunks = [records[i:i + chunk_size] for i in range(0, total, chunk_size)]
    summary = {"module": module, "records": total, "success": 0, "failed": 0}

    for idx, chunk in enumerate(chunks, start=1):
        csv_path, zip_path = _create_bulk_csv_zip(chunk, module, idx, download_path)
        file_id = _upload_bulk_file(zip_path, token)
        if not file_id:
            print(f"⚠️ Skipping chunk {idx}: file upload failed.")
            continue

        job_id = _submit_bulk_write_job(file_id, module, chunk[0], token, find_by)
        if not job_id:
            print(f"⚠️ Skipping chunk {idx}: job creation failed.")
            continue

        state, result_df = _poll_bulk_write_status(job_id)

        if result_df is not None and isinstance(result_df, pd.DataFrame) and not result_df.empty:
            if "Status" in result_df.columns:
                success_count = (result_df["Status"].str.lower() == "success").sum()
                summary["success"] += int(success_count)
                summary["failed"] += len(result_df) - int(success_count)
            else:
                print(f"⚠️ Missing 'Status' column in bulk result for {module}.")
        else:
            print(f"⚠️ No valid result DataFrame returned for {module}, state={state}")

    print(f"🏁 Bulk Write Done → {summary['success']} success / {summary['failed']} failed")
    return summary



# ==========================================================
# RELATED LIST HELPERS
# ==========================================================
BASE = "https://www.zohoapis.com/crm/v2"
RELATED_LIST = "Responsible_Agents"


def fetch_responsible_agents(
    contact_id: str,
    fields: Optional[List[str]] = None,
    limit: int = 200,
    max_pages: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch all responsible agents under a Firm Contact via related list.
    Adds diagnostic output for tracing Zoho API calls.
    """
    if not contact_id:
        print("⚠️ fetch_responsible_agents called with no contact_id.")
        return []

    if fields is None:
        fields = ["First_Name", "Last_Name", "NPN", "Email"]

    token = get_zoho_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    params = {"fields": ",".join(fields), "page": 1, "per_page": limit}

    results: List[Dict[str, Any]] = []
    print(f"🔎 Fetching Responsible_Agents for Contact ID: {contact_id}")

    for page in range(1, max_pages + 1):
        url = f"{BASE}/Contacts/{contact_id}/{RELATED_LIST}"
        print(f"🌐 Requesting page {page} → {url}")

        resp = requests.get(url, headers=headers, params=params)

        # 🔁 Token refresh on expiry
        if resp.status_code == 401 and "INVALID_TOKEN" in resp.text:
            print("🔁 Token expired while fetching related list — refreshing...")
            token = get_zoho_token(force_refresh=True)
            headers["Authorization"] = f"Zoho-oauthtoken {token}"
            resp = requests.get(url, headers=headers, params=params)

        if resp.status_code != 200:
            print(f"❌ Zoho related list fetch failed ({resp.status_code}): {resp.text}")
            break

        json_resp = resp.json()
        data = json_resp.get("data", [])
        info = json_resp.get("info", {})

        print(f"📦 Page {page} returned {len(data)} records. more_records={info.get('more_records')}")

        results.extend(data)
        if not info.get("more_records"):
            break
        params["page"] += 1

    print(f"✅ Total responsible agents fetched for {contact_id}: {len(results)}")
    if len(results) == 0:
        print("⚠️ No responsible agents returned from Zoho.")
    else:
        print(json.dumps(results, indent=2)[:800])  # print first few for inspection

    return results


def fetch_responsible_agent(contact_id: str) -> Optional[Dict[str, Any]]:
    """Shortcut to get the first responsible agent (principal) with debugging."""
    print(f"🔍 Looking up first principal agent for Contact ID: {contact_id}")
    agents = fetch_responsible_agents(contact_id, limit=1)
    if len(agents) == 0:
        print(f"⚠️ No Responsible Agent found for contact {contact_id}")
        return None
    agent = agents[0]
    print(f"✅ Found Responsible Agent: {agent}")
    return agent



def build_crm_payload(records, mapping):
    """
    Build clean CRM payload from queue records using field_mapping.
    Ensures only valid API names are sent.
    """
    if not records:
        return []

    payload = []
    field_map = mapping.get("field_mapping", {})
    for rec in records:
        out = {}
        for api_name, local_col in field_map.items():
            if local_col in rec:
                out[api_name] = rec[local_col]
        payload.append(out)
    return payload

def upload_crm_notes(records):
    print('==Uploading notes to CRM...')

    zoho_auth = get_zoho_token()
    for i, contract in records.iterrows():
        contract_note = contract['crm_note']
        if contract_note is None or contract_note == 'None' or contract_note == '':
            print("No note present for given contract.")
            continue
        try:
            contract_id = contract['id']
            print(f"contract zoho id:\n{contract_id}")
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            data = f"""
                {{
                    "data": [
                        {{
                            "Parent_Id": {{
                                "module": {{
                                    "api_name": "Agent_Contracts"
                                }},
                                "id": "{contract_id}"
                            }},
                            "Note_Content": "{contract_note}"
                        }}
                    ]
                }}
                """
            response = requests.post(f'https://www.zohoapis.com/crm/v8/Agent_Contracts/{contract_id}/Notes', headers=headers,
                                     data=data)
            print(f"==Finished uploading contract note:\n{response.text}")
        except Exception as e:
            print(f"==Error uploading contract note: {e}")
            print(f"==Note that was attempted: {data}")
    print("\n==Finished all note uploads.")
    return None