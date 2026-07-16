import os
import re
import psycopg2
import pandas as pd
from datetime import datetime
import pytz
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
import tempfile
from io import StringIO

from app.core.config import settings


# Config
# Key Vault
KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")

# Azure Blob
ATTACHMENT_ACCOUNT_NAME = settings.ATTACHMENT_ACCOUNT_NAME
BLOB_CONTAINER = "834analytics-dev"
BASE_BLOB_PATH = "raw/centene_pch/HEDIS Files"

# Local temp
# TMP_DIR = "./tmp_exports"
# TMP_DIR = os.getenv("TMP_DIR", "/tmp/tmp_exports")


# Database
POSTGRES_SERVER = "834-db-dev001.postgres.database.azure.com"
POSTGRES_DB = "postgres"
POSTGRES_USER = "834data_syndb_adm"

# File names
SERVICE_PREFIX = "PCH_WCG_QX_SERVICE"
LAB_PREFIX = "PCH_WCG_QX_LAB"

TZ = pytz.timezone("US/Central")
# os.makedirs(TMP_DIR, exist_ok=True)

def get_kv_secrets():
    kv_url = f"https://{KEY_VAULT_NAME}.vault.azure.net/"
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=kv_url, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return tenant_id, client_id, client_secret


def get_postgres_connection():
    tenant_id, client_id, client_secret = get_kv_secrets()
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)

    token = credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    return psycopg2.connect(
        host=POSTGRES_SERVER,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=token,
        sslmode="require"
    )


def get_blob_service():
    tenant_id, client_id, client_secret = get_kv_secrets()
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)

    account_url = f"https://{ATTACHMENT_ACCOUNT_NAME}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=credential)


SERVICE_COLUMNS = [
    "CNCMemberId","CNCPlanCode","MedicaidNbr","HICN","MBI",
    "LastName","FirstName","MiddleName","DOB",
    "ClaimNumber","DOS","DOSThru","Event Diagnosis",
    "ICDPx1","ICDPx2","ICDDxPri",
    *[f"ICDDxSec{i}" for i in range(1,40)],
    "ICDVersion","CPTPx","CPTMod","HCPCPSPx","HCPCPSMod",
    "UBRevenueCode","TOB","HCFAPOS","CVX",
    "ClaimAltID1","ClaimAltID2",
    "BPSystolic","BPDiastolic","BmiValue",
    "ProviderName","ProviderAddress1","ProviderAddress2",
    "ProviderCity","ProviderState","ProviderZipcode",
    "ProviderPhone","ProviderNPI","ProviderTIN",
    "TaxonomyCode","CmsSpecialtyCode","IntakeType"
]

LAB_COLUMNS = [
    "CNCMemberId","CNCPlanCode","MedicaidNbr","HICN","MBI",
    "LastName","FirstName","MiddleName","DOB",
    "ClaimNumber","DOS",
    "CPTPx","LOINC","LOINCAnswer","SNOMED",
    "Result","PosNegResult",
    "ClaimAltID1","ClaimAltID2",
    "ProviderName","ProviderAddress1","ProviderAddress2",
    "ProviderCity","ProviderState","ProviderZipcode",
    "ProviderPhone","ProviderNPI","ProviderTIN",
    "TaxonomyCode","CmsSpecialtyCode","IntakeType"
]

def now_cst():
    return datetime.now(TZ)

def file_date():
    return now_cst().strftime("%m%d%Y")

def month_folder():
    return now_cst().strftime("%m %b %Y")

def format_date(series):
    return pd.to_datetime(series, errors="coerce").dt.strftime("%m/%d/%Y").fillna("")

def extract_bp(vitals):
    if not vitals:
        return "", ""
    m = re.search(r'(\d{2,3})\s*/\s*(\d{2,3})', str(vitals))
    if m:
        return m.group(1), m.group(2)
    return "", ""

def ensure_columns(df, cols):
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df


def main():
    # tmp_dir = TMP_DIR
    # try:
    #     os.makedirs(tmp_dir, exist_ok=True)
    # except OSError:
    #     tmp_dir = tempfile.gettempdir()
    #     print(f"⚠️ Cannot create {TMP_DIR}; using system temp {tmp_dir}")

    # df = pd.read_sql("""
    #     SELECT *
    #     FROM wpo.sud_file_creation_vw
    #     WHERE intake_type IN ('Service','Lab')
    #     AND added_toreport = 0
    # """, conn)
    conn = get_postgres_connection()

    df = pd.read_sql("""
            SELECT *
            FROM wpo.sud_file_creation_vw2
            WHERE lower(trim(intake_type)) IN ('service','lab')
              AND COALESCE(added_toreport, 0) = 0
        """, conn)
    conn.close()

   
    print("rows fetched:", len(df))
    if df.empty:
        print("No new rows to export. Exiting.")
        return {"status": "no_new_rows", "exported": 0}
    
    if "supplemental_id" not in df.columns:
        raise ValueError("supplemental_id column is missing from the data.")    
    
    # Track exported supplemental IDs
    # exported_ids = df["supplemental_id"].dropna().unique().tolist()
    exported_ids = (
        df['supplemental_id'].dropna().astype(str).str.strip().unique().tolist()
    )

    print("exported_ids count:", len(exported_ids))
    print("sample ids:", exported_ids[:5])

    # Transformations
    df["CNCMemberId"] = (
        df["cnc_member_id"]
          .replace(r"^\s*$", pd.NA, regex=True)
          .fillna(df["mem_id"])
    )

    COLUMN_MAP = {
    "cnc_plan_code":"CNCPlanCode",
    "medicaid_nbr":"MedicaidNbr",
    "hichn":"HICN",
    "mbi":"MBI",
    "last_name":"LastName",
    "first_name":"FirstName",
    "middle_name":"MiddleName",
    "dob":"DOB",
    "claim_number":"ClaimNumber",
    "dos":"DOS",
    "dos_thru":"DOSThru",
    "event_diagnosis":"Event Diagnosis",
    "icd_px1":"ICDPx1",
    "icd_px2":"ICDPx2",
    "icd_dx_pri":"ICDDxPri",
    "icd_version":"ICDVersion",
    "cpt_px":"CPTPx",
    "cpt_mod":"CPTMod",
    "hcpc_ps_px":"HCPCPSPx",
    "hcpc_ps_mod":"HCPCPSMod",
    "ub_revenue_code":"UBRevenueCode",
    "tob":"TOB",
    "hcfapos":"HCFAPOS",
    "cvx":"CVX",
    "claim_alt_id1":"ClaimAltID1",
    "claim_alt_id2":"ClaimAltID2",
    "loinc":"LOINC",
    "loinc_answer":"LOINCAnswer",
    "snomed":"SNOMED",
    "result":"Result",
    "pos_neg_result":"PosNegResult",
    "provider_name":"ProviderName",
    "provider_address1":"ProviderAddress1",
    "provider_address2":"ProviderAddress2",
    "provider_city":"ProviderCity",
    "provider_state":"ProviderState",
    "provider_zipcode":"ProviderZipcode",
    "provider_phone":"ProviderPhone",
    "provider_npi":"ProviderNPI",
    "provider_tin":"ProviderTIN",
    "taxonomy_code":"TaxonomyCode",
    "cms_specialty_code":"CmsSpecialtyCode",
    "intake_type":"IntakeType"
}

    df = df.rename(columns=COLUMN_MAP)

    for i in range(1, 40):
        src = f"icd_dx_sec{i}"
        tgt = f"ICDDxSec{i}"
        df[tgt] = df[src] if src in df.columns else ""

    df["DOB"] = format_date(df["DOB"])
    df["DOS"] = format_date(df["DOS"])
    df["DOSThru"] = format_date(df["DOSThru"])

    df[["BPSystolic", "BPDiastolic"]] = df["vitals"].apply(
        lambda x: pd.Series(extract_bp(x))
    )
    df["BmiValue"] = df["bmi_value"].fillna("")

    # Split files
    service_df = ensure_columns(df[df["IntakeType"] == "Service"].copy(), SERVICE_COLUMNS)
    lab_df = ensure_columns(df[df["IntakeType"] == "Lab"].copy(), LAB_COLUMNS)

    # Write files
    date_str = file_date()
    month_dir = month_folder()

    service_file = f"{SERVICE_PREFIX}_{date_str}.txt"
    lab_file = f"{LAB_PREFIX}_{date_str}.txt"

    # service_path = os.path.join(TMP_DIR, service_file)
    # lab_path = os.path.join(TMP_DIR, lab_file)
    # service_path = os.path.join(tmp_dir, service_file)
    # lab_path = os.path.join(tmp_dir, lab_file)
    

    # pandas uses lineterminator
    # svc_buf = StringIO()
    # service_df[SERVICE_COLUMNS].to_csv(
    #    sep="|", index=False, header=True,
    #     encoding="utf-8", lineterminator="\r\n"
    # )
    # service_content = svc_buf.getvalue()

    # lab_buf = StringIO()
    # lab_df[LAB_COLUMNS].to_csv(
    #     sep="|", index=False, header=True,
    #     encoding="utf-8", lineterminator="\r\n"
    # )
    # lab_content = lab_buf.getvalue()
    service_content = service_df[SERVICE_COLUMNS].to_csv(
        None, sep="|", index=False, header=True, lineterminator="\r\n"
    )

    lab_content = lab_df[LAB_COLUMNS].to_csv(
        None, sep="|", index=False, header=True, lineterminator="\r\n"
    )
    # Upload to Blob
    blob_service = get_blob_service()
    container = blob_service.get_container_client(BLOB_CONTAINER)

    # for local_path, fname in [(service_df, service_file), (lab_df, lab_file)]:
    #     blob_path = f"{BASE_BLOB_PATH}/{month_dir}/{fname}"
    #     with open(local_path, "rb") as f:
    #         container.get_blob_client(blob_path).upload_blob(f, overwrite=True)
    for content, fname in [(service_content, service_file), (lab_content, lab_file)]:
        blob_path = f"{BASE_BLOB_PATH}/{month_dir}/{fname}"
        container.get_blob_client(blob_path).upload_blob(content.encode("utf-8"), overwrite=True)

    print("Service & Lab files created and uploaded successfully.")

    # Mark rows as reported
    conn = get_postgres_connection()
    cur = conn.cursor()

    # cur.execute("""
    #     UPDATE wpo.centene_supplemental
    #     SET added_toreport = 1
    #     WHERE id = ANY(%s::uuid[])
    #       AND added_toreport = 0
    # """, (exported_ids,))
    cur.execute("""
                UPDATE wpo.centene_supplemental
        SET added_toreport = 1
        WHERE id = ANY(%s::uuid[])
        AND COALESCE(added_toreport, 0) = 0
    """, (exported_ids,))
    print("updated rows:", cur.rowcount)
    conn.commit()
    cur.close()
    conn.close()

    print(f"Marked {len(exported_ids)} records as added_toreport = 1")

    return {
        "status": "exported",
        "exported": len(exported_ids),
        "service_file": service_file,
        "lab_file": lab_file,
        "month_dir": month_dir,
        "service_csv": service_content,
        "lab_csv": lab_content
    }

if __name__ == "__main__":
    main()
