import os
# ==========================================================
#  utils/db_utils.py
# ==========================================================
"""
db_utils.py
------------
Provides secure connection helpers for Synapse and MyOps databases
using Azure Key Vault + Service Principal authentication.

⚙️  Responsibilities:
    - Fetch secrets from Azure Key Vault
    - Return authenticated pyodbc connections
    - NO insert/update/logging logic here
"""

import pyodbc
import psycopg2
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from datetime import datetime
from utils.logger_utils import safe_log
import pandas as pd
from typing import Optional, Union, List, Dict, Any
# ==========================================================
#  CONFIGURATION
# ==========================================================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

DB_CONFIG = {
    "server": "myopsprd.database.windows.net",
    "database": "myopsprd",
    "driver": "{ODBC Driver 17 for SQL Server}"
}

DB_CONFIG_SYNAPSE = {
    "server": "834analyticsynapse.sql.azuresynapse.net",
    "database": "834_analytics_dev",
    "driver": "{ODBC Driver 17 for SQL Server}"
}

POSTGRES_CONFIG = {
    "host": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}

# ==========================================================
#  KEY VAULT AUTHENTICATION HELPERS
# ==========================================================
def get_db_secrets():
    """
    Retrieve service principal credentials (Client ID, Secret, Tenant ID)
    for MyOps / Synapse authentication.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return client_id, client_secret, tenant_id


def get_db_secrets_synapse():
    """
    Retrieve Synapse-only credentials when Tenant ID not required.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value

    return client_id, client_secret

# ==========================================================
#  CONNECTION HELPERS
# ==========================================================
def get_myops_connection():
    """
    Return authenticated connection to MyOps (Prod SQL DB).
    """
    client_id, client_secret, tenant_id = get_db_secrets()
    conn_str = (
        f"DRIVER={DB_CONFIG['driver']};"
        f"SERVER={DB_CONFIG['server']};"
        f"DATABASE={DB_CONFIG['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};PWD={client_secret};Authority Id={tenant_id};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str, timeout=30)

def get_synapse_connection():
    """
    Return authenticated connection to Synapse (834_analytics_dev).
    Used for matrix, queue, and logs (Prod + Test).
    """
    client_id, client_secret, tenant_id = get_db_secrets()
    conn_str = (
        f"DRIVER={DB_CONFIG_SYNAPSE['driver']};"
        f"SERVER={DB_CONFIG_SYNAPSE['server']};"
        f"DATABASE={DB_CONFIG_SYNAPSE['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};PWD={client_secret};Authority Id={tenant_id};"
    )
    return pyodbc.connect(conn_str, timeout=30)

def get_postgres_connection():
    """
    Create a Postgres connection using AAD token auth
    via Service Principal stored in Key Vault.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    sp_credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )

    token = sp_credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        dbname=POSTGRES_CONFIG["database"],
        user=POSTGRES_CONFIG["user"],
        password=token,
        sslmode="require"
    )

# ==========================================================
# Update queue records with flexible WHERE clause
# ==========================================================
def update_queue_where(update_dict: dict, where_dict: dict) -> int:
    """
    Dynamically update records in [raw].[ops_acc_process_queue]
    using simple dicts for SET and WHERE clauses.

    Example:
        update_queue_where(
            {"status": "Success", "contract_status": "Sent to Carrier"},
            {"carrier_id": "2931751000116721001", "npn": "16505368"}
        )
    """

    import pyodbc
    from utils import config

    try:
        conn = get_postgres_connection()
        cur = conn.cursor()

        # Build dynamic SQL
        set_clause = ", ".join([f"{col} = %s" for col in update_dict.keys()])
        where_clause = " AND ".join([f"{col} = %s" for col in where_dict.keys()])

        sql = f"""
            UPDATE wpo.ops_acc_process_queue
               SET {set_clause}, updated_on = now() at time zone 'utc'
             WHERE {where_clause}
        """

        params = list(update_dict.values()) + list(where_dict.values())
        cur.execute(sql, params)
        affected = cur.rowcount
        conn.commit()
        conn.close()

        print(f"📝 Queue updated ({affected} row(s)) → WHERE {where_dict}")
        return affected

    except pyodbc.Error as e:
        safe_log(
            "ACC_RPA_DBUTILS",
            f"Queue update failed ({where_dict}): {e}",
            code="QUEUE_UPDATE_ERROR",
        )
        return 0

def query_crm(query_str: str) -> pd.DataFrame:
    """Execute an SQL query and return DataFrame."""
    conn = get_postgres_connection()

    try:
        df = pd.read_sql(query_str, conn)
    except Exception as e:
        print(f"❌ QUERY failed: {e}")
        print(f"❌ Given query: {query_str}")
        raise RuntimeError(f"PostgreSQL Query failed on: {e} ||| {query_str}")

    conn.close()
    print(f"✅ CRM query → {len(df)} record(s)")
    return df

def get_contracts(
    carrier_id: str,
    npn_list: Optional[list] = None,
    crm_filter=None,
    fields=None,
    batch_size: int = 200,
    allow_full_fetch: bool = False
) -> pd.DataFrame:
    """
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
    if not crm_filter:
        raise ValueError("crm_filter cannot be empty after normalization.")

    if isinstance(crm_filter, str):
        crm_filter = [s.strip() for s in crm_filter.replace(";", ",").split(",") if s.strip()]
    elif isinstance(crm_filter, (list, tuple, set)):
        crm_filter = [str(s).strip() for s in crm_filter if str(s).strip()]
    else:
        raise TypeError(f"Invalid crm_filter type: {type(crm_filter)}")


    if not fields:
        fields = [
            "contract.pk_id", "contract.name", "agent.full_name", "contract.carrier_id", "agent_id_crm", "agent.npn",
            "agent.first_name", "agent.last_name", "agent.email",
            "contract.status", "contract.status_date", "agent.gdriveextension__drive_folder_id", "contract.contract_id_crm", "agent.secondary_Email", "agent.phone",
            "agent.contracting_email", "agent.home_phone", "agent.other_phone", "contract.product_type"
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

    where_clause = ""
    if len(crm_filter) > 0:
        where_clause = f"contract.status = '{crm_filter[0]}'"
    if len(crm_filter) > 1:
        for fil in crm_filter[1:]:
            where_clause += f" OR contract.status = '{fil}'"

    if npn_list:
        where_clause = f"({where_clause}) AND (agent.npn = '{npn_list[0]}'"
        if len(npn_list) > 1:
            for fil in npn_list[1:]:
                where_clause += f" OR agent.npn = '{fil}'"
        where_clause += ')'
    elif not allow_full_fetch:
        print("⚠️ No NPNs provided and full fetch not allowed — returning empty DataFrame.")
        return pd.DataFrame()

    where_clause = f" ({where_clause}) AND (contract.carrier_id = '{carrier_id}')"

    select_fields = ""
    for field in fields[:-1]:
        select_fields += field
        select_fields += ", "
    if len(fields) > 1:
        select_fields += fields[-1]
    select_fields += "\n"
    query = f"""SELECT {select_fields}
    FROM wpo.lup_master_agents_contracts contract
    JOIN wpo.lup_agents agent on contract.npn = agent.npn
    WHERE {where_clause} 
    """
    print(f"Query being submitted:\n{query}")
    conn = get_postgres_connection()
    try:
        df = pd.read_sql(query, conn)
    except Exception as e:
        print(f"❌ QUERY failed: {e}")
        print(f"❌ Given query: {query}")
        raise RuntimeError(f"PostgreSQL Query failed on: {e} ||| {query}")
    conn.close()

    df.rename(columns={"gdriveextension__drive_folder_id": "google_drive_id"},inplace=True)

    print(f"✅ Retrieved {len(df)} contract record(s).")
    # Email fallback: Contracting_Email → Email → Secondary_Email
    print("Collapsing emails...")
    for i, row in df.iterrows():
        if not pd.isna(row.get('contracting_email')):
            df.at[i, 'email'] = row.get('contracting_email')
        elif not pd.isna(row.get('email')):
            continue
        elif pd.isna(row.get('email')) and not pd.isna(row.get('secondary_email')):
            df.at[i, 'email'] = row.get('secondary_email')

    # Phone fallback: Phone → Mobile → Other_Phone
    print("Collapsing phone numbers...")
    for i, row in df.iterrows():
        if not pd.isna(row.get('phone')):
            continue
        elif not pd.isna(row.get('home_phone')):
            df.at[i, 'phone'] = str(row.get('home_phone'))
        elif not pd.isna(row.get('other_phone')):
            df.at[i, 'phone'] = str(row.get('other_phone'))
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
        fields = ["npn", "first_name", "last_name", "email", "mailing_state", "type",  "secondary_email", "phone", "home_phone", "other_phone", "contracting_email"]

    select_fields = ""
    for field in fields[:-1]:
        select_fields += field
        select_fields += ", "
    if len(fields) > 1:
        select_fields += fields[-1]

    all_frames = []
    # 🔹 Split into chunks of up to 25 NPNs (Zoho limit)
    for start in range(0, len(npn_list), batch_size):
        batch = npn_list[start:start + batch_size]
        print(f"🔁 Fetching agent batch {start // batch_size + 1} ({len(batch)} NPNs)...")

        where_clause = f"npn = '{batch[0]}'"
        if len(batch) > 1:
            for fil in batch[1:]:
                where_clause += f" OR npn = '{fil}'"

        query = f"""SELECT {select_fields},
            CASE 
                WHEN alabama = 'Resident' THEN 'AL'
                WHEN alaska = 'Resident' THEN 'AK'
                WHEN arizona = 'Resident' THEN 'AZ'
                WHEN arkansas = 'Resident' THEN 'AR'
                WHEN california = 'Resident' THEN 'CA'
                WHEN colorado = 'Resident' THEN 'CO'
                WHEN connecticut = 'Resident' THEN 'CT'
                WHEN delaware = 'Resident' THEN 'DE'
                WHEN florida = 'Resident' THEN 'FL'
                WHEN georgia = 'Resident' THEN 'GA'
                WHEN hawaii = 'Resident' THEN 'HI'
                WHEN idaho = 'Resident' THEN 'ID'
                WHEN illinois = 'Resident' THEN 'IL'
                WHEN indiana = 'Resident' THEN 'IN'
                WHEN iowa = 'Resident' THEN 'IA'
                WHEN kansas = 'Resident' THEN 'KS'
                WHEN kentucky = 'Resident' THEN 'KY'
                WHEN louisiana1 = 'Resident' THEN 'LA'
                WHEN maine = 'Resident' THEN 'ME'
                WHEN maryland = 'Resident' THEN 'MD'
                WHEN massachusetts = 'Resident' THEN 'MA'
                WHEN michigan = 'Resident' THEN 'MI'
                WHEN minnesota = 'Resident' THEN 'MN'
                WHEN mississippi = 'Resident' THEN 'MS'
                WHEN missouri = 'Resident' THEN 'MO'
                WHEN montana = 'Resident' THEN 'MT'
                WHEN nebraska = 'Resident' THEN 'NE'
                WHEN nevada = 'Resident' THEN 'NV'
                WHEN new_hampshire = 'Resident' THEN 'NH'
                WHEN new_jersey = 'Resident' THEN 'NJ'
                WHEN new_mexico = 'Resident' THEN 'NM'
                WHEN new_york = 'Resident' THEN 'NY'
                WHEN north_carolina = 'Resident' THEN 'NC'
                WHEN north_dakota = 'Resident' THEN 'ND'
                WHEN ohio = 'Resident' THEN 'OH'
                WHEN oklahoma = 'Resident' THEN 'OK'
                WHEN oregon = 'Resident' THEN 'OR'
                WHEN pennsylvania = 'Resident' THEN 'PA'
                WHEN rhode_island = 'Resident' THEN 'RI'
                WHEN south_carolina = 'Resident' THEN 'SC'
                WHEN south_dakota = 'Resident' THEN 'SD'
                WHEN tennessee = 'Resident' THEN 'TN'
                WHEN texas = 'Resident' THEN 'TX'
                WHEN utah = 'Resident' THEN 'UT'
                WHEN vermont = 'Resident' THEN 'VT'
                WHEN virginia = 'Resident' THEN 'VA'
                WHEN washington = 'Resident' THEN 'WA'
                WHEN west_virginia = 'Resident' THEN 'WV'
                WHEN wisconsin = 'Resident' THEN 'WI'
                WHEN wyoming = 'Resident' THEN 'WY'
                WHEN district_of_columbia = 'Resident' THEN 'DC'
                WHEN puerto_rico = 'Resident' THEN 'PR'
            END as resident_state
            FROM wpo.lup_agents
            WHERE {where_clause}
            """
        
        conn = get_postgres_connection()
        df_batch = pd.DataFrame()
        try:
            df_batch = pd.read_sql(query, conn)
        except Exception as e:
            print(f"❌ QUERY failed: {e}")
            print(f"❌ Given query: {query}")
            raise RuntimeError(f"PostgreSQL Query failed on: {e} ||| {query}")
        conn.close()

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
        if not pd.isna(row.get('contracting_email')):
            df.at[i, 'email'] = row.get('contracting_email')
        elif not pd.isna(row.get('email')):
            continue
        elif pd.isna(row.get('email')) and not pd.isna(row.get('secondary_email')):
            df.at[i, 'email'] = row.get('secondary_email')

    # Phone fallback: Phone → Mobile → Other_Phone
    print("Collapsing phone numbers...")
    for i, row in df.iterrows():
        if not pd.isna(row.get('phone')):
            continue
        elif not pd.isna(row.get('home_phone')):
            df.at[i, 'phone'] = str(row.get('home_phone'))
        elif not pd.isna(row.get('other_phone')):
            df.at[i, 'phone'] = str(row.get('other_phone'))
    print('Agents DF:')
    print(df.to_string())
    return df

def fetch_responsible_agents(
    contact_id: str,
    fields: Optional[List[str]] = None,
    limit: int = 200,
    max_pages: int = 10
) -> pd.DataFrame:
    """
    Fetch all responsible agents under a Firm Contact via related list.
    Adds diagnostic output for tracing Zoho API calls.
    """
    if not contact_id:
        print("⚠️ fetch_responsible_agents called with no contact_id.")
        return []

    if fields is None:
        fields = ["first_name", "last_name", "npn", "email"]
    select_fields = ""
    for field in fields[:-1]:
        select_fields += field
        select_fields += ", "
    if len(fields) > 1:
        select_fields += fields[-1]

    print(f"🔎 Fetching Responsible_Agents for Contact ID: {contact_id}")
    results = pd.DataFrame()
    query = f"""SELECT {select_fields}
                FROM wpo.lup_agents
                WHERE responsible_agency = '{contact_id}'
                LIMIT {limit}
            """

    print(f"Query being submitted:\n{query}")
    conn = get_postgres_connection()
    try:
        results = pd.read_sql(query, conn)
    except Exception as e:
        print(f"❌ QUERY failed: {e}")
        print(f"❌ Given query: {query}")
        raise RuntimeError(f"PostgreSQL Query failed on: {e} ||| {query}")
    conn.close()

    print(f"✅ Total responsible agents fetched for {contact_id}: {len(results)}")
    if len(results) == 0:
        print("⚠️ No responsible agents returned from CRM.")
    else:
        print(results.to_string())  # print first few for inspection

    return results

def fetch_responsible_agent(contact_id: str) -> Optional[Dict[str, Any]]:
    """Shortcut to get the first responsible agent (principal) with debugging."""
    print(f"🔍 Looking up first principal agent for Contact ID: {contact_id}")
    agents = fetch_responsible_agents(contact_id, limit=1)
    if len(agents) == 0:
        print(f"⚠️ No Responsible Agent found for contact {contact_id}")
        return None
    agent = agents.iloc[0].to_dict()
    print(f"✅ Found Responsible Agent: {agent}")
    return agent

def bulk_update_crm(module, records, *, carrier_id, download_path, find_by=None):
    """Unified Bulk Write v2 update wrapper."""
    if not records:
        print("⚠️ No records to update.")
        return None

    if module == "wpo.lup_master_agents_contracts":
        find_by = "pk_id"
    elif module == "wpo.lup_agents":
        find_by = "pk_id"
    else:
        find_by = find_by or "pk_id"

    total = len(records)
    print(f"🚀 Bulk update {total} {module} records (find_by={find_by})")
    try:
        chunk_size = 5000
        chunks = [records[i:i + chunk_size] for i in range(0, total, chunk_size)]
        summary = {"module": module, "records": total, "success": 0, "failed": 0}

        conn = get_postgres_connection()

        col_list = list(records[0].keys())
        
        columns_str = normalize_sql_column_name(col_list[0])
        conflict_updates_str = f"{normalize_sql_column_name(col_list[0])} = EXCLUDED.{normalize_sql_column_name(col_list[0])}"
        for col in col_list[1:]:
            columns_str += f", {normalize_sql_column_name(col)}"
            conflict_updates_str += f", {normalize_sql_column_name(col)} = EXCLUDED.{normalize_sql_column_name(col)}"

        for idx, chunk in enumerate(chunks, start=1):
            values = f"('{list(chunk[0].values())[0]}'"
            for datum in list(chunk[0].values())[1:]:
                values += f",'{datum}'"
            values += ')'

            for dic in chunk[1:]:
                values += f",('{list(dic.values())[0]}'"
                for datum in list(dic.values())[1:]:
                    values += f",'{datum}'"
                values += ')'
                
            query = f"""INSERT INTO {module} ({columns_str})
                        VALUES {values}
                        ON CONFLICT ({find_by})
                        DO UPDATE SET
                            {conflict_updates_str}
                    """
            print("==== Bulk Update Query:")
            print(query)
            cur = conn.cursor()
            cur.execute(query)
            affected = cur.rowcount
            conn.commit()
            print(f'Affected rows in module {module}: {affected}')
    except Exception as e:
        print(f"Issue during bulk crm update: {e}")

        conn.close()

    print(f"🏁 Bulk Write Done → {summary['success']} success / {summary['failed']} failed")
    return summary

def normalize_sql_column_name(column: str) -> str:
    if column.count('.') > 0:
        return column.split('.',1)[1]
    return column

def upload_crm_notes(records):
    print("---Must Implement CRM Note Upload, still need API endpoint to do so---")
    print("===Contained CRM note data:")
    print(records)

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
