"""
Central configuration for the EHR scrape pipeline.

Everything that was previously duplicated across tebra_rpa.py, server.py and
run_appointments_backfill.py (entity ids, table name, download dir, connection
config) lives here now, so there is exactly one definition of each.
"""

import os
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ROOT_ENV_FILE, override=False)

CST_TZ = ZoneInfo("America/Chicago")

# ---- Tenant / EHR identity (single tenant today) ----
ENTITY = "270681372"
SUB_ENTITY = "270681372001"
EHR_NAME = "Tebra"

# ---- Data table ----
TABLE_NAME = "ehr.ehr_appointments"
PATIENTS_TABLE = "ehr.ehr_patients"

# ---- Local download dir ----
# Set this in .env for each runtime environment.
DOWNLOAD_DIR = os.environ.get("EHR_DOWNLOAD_DIR", "").strip()

# ---- Batch / misc ----
BATCH_SIZE = 50
MANUAL_DATE_FORMAT = "%m/%d/%Y"

# ---- Postgres (pch) ----
POSTGRES_CONFIG_PCH = {
    "host": os.environ.get("PCH_DB_HOST", "").strip(),
    "database": os.environ.get("PCH_DB_NAME", "").strip(),
    "user": os.environ.get("PCH_DB_USER", "").strip(),
}

POSTGRES_CONFIG_EHR = {
    "host": os.environ.get("RCM_DB_HOST", "").strip(),
    "database": os.environ.get("RCM_DB_NAME", "").strip(),
    "user": os.environ.get("RCM_DB_USER", "").strip(),
}

POSTGRES_CONFIG_MYOPS = {
    "host": os.environ.get("MYOPS_DB_HOST", "").strip(),
    "database": os.environ.get("MYOPS_DB_NAME", "").strip(),
    "user": os.environ.get("MYOPS_DB_USER", "").strip(),
}

# ---- Tebra login ----
LOGIN_URL = "https://app.kareo.com/v2/#/sign-in?"
EMAIL = os.environ.get("TEBRA_EMAIL", "").strip()
PASSWORD = os.environ.get("TEBRA_PASSWORD", "").strip()

# ---- Blob delivery (834labs-sftp) ----
STORAGE_ACCOUNT_NAME = os.environ.get("TEBRA_STORAGE_ACCOUNT_NAME", "").strip()
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
