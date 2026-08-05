"""
Config for the Practice Fusion SOAP sync API bridge.

The worker itself (../pf-soap-sync/pf_soap_sync_v5_13.py) is invoked as a
subprocess — it is not imported. Everything here is just the set of paths and
identifiers needed to build that subprocess command line. Mirrors ehr/config.py's
.env-loading convention; the VM handoff for this script calls for these values
to eventually come from Azure Key Vault instead, which is a separate follow-up.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

from ehr.config import ENTITY, SUB_ENTITY  # same tenant/company as Tebra

_CONFIG_FILE = Path(__file__).resolve()
_ENV_CANDIDATES = [
    _CONFIG_FILE.parents[2] / ".env",  # repo root (preferred)
    _CONFIG_FILE.parents[1] / ".env",  # myops/.env fallback
]
for _env_file in _ENV_CANDIDATES:
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
        break

PF_ENTITY = ENTITY
PF_SUB_ENTITY = SUB_ENTITY
PF_EHR_NAME = "Practice Fusion"

_REPO_ROOT = _CONFIG_FILE.parents[2]
_PF_SYNC_DIR = _REPO_ROOT / "pf-soap-sync"

WORKER_SCRIPT_PATH = os.environ.get("PF_WORKER_SCRIPT_PATH", "").strip() or str(
    _PF_SYNC_DIR / "pf_soap_sync_v5_13.py"
)
PDF_CONFIG_JSON = os.environ.get("PF_SYNC_CONFIG_JSON", "").strip() or str(
    _PF_SYNC_DIR / "pf_pdf_sync_config.json"
)
REPORT_CONFIG_JSON = os.environ.get("PF_REPORT_CONFIG_JSON", "").strip() or str(
    _PF_SYNC_DIR / "pf_appointment_report_config.json"
)
QUEUE_JSON = os.environ.get("PF_QUEUE_JSON", "").strip() or str(
    _PF_SYNC_DIR / "pf_appointment_queue.json"
)
PATIENTS_FILE = os.environ.get("PF_PATIENTS_FILE", "").strip() or str(
    _PF_SYNC_DIR / "practice_fusion_patients.csv"
)
DOWNLOADS_DIR = os.environ.get("PF_DOWNLOADS_DIR", "").strip() or str(
    _PF_SYNC_DIR / "pf_encounter_pdfs"
)

PRACTICE_NAME = os.environ.get("PF_PRACTICE_NAME", "").strip() or "NWARK Internal Medicine"
CHROME_USER_DATA_DIR = os.environ.get("PF_CHROME_USER_DATA_DIR", "").strip()
CHROME_EXE = os.environ.get("PF_CHROME_EXE", "").strip()
DEBUG_PORT = os.environ.get("PF_DEBUG_PORT", "").strip() or "9222"

# PF_USERNAME / PF_PASSWORD are read directly by the worker script itself via
# os.getenv(...) — no need to pass them through this bridge or the API payload.

SUBPROCESS_TIMEOUT_SECONDS = int(os.environ.get("PF_SUBPROCESS_TIMEOUT_SECONDS", "1800"))

# Cross-process lock so this API and a separately-launched nightly script never
# drive the shared Chrome profile at the same time. Per the VM handoff (Section 7),
# any process that touches the browser must acquire this same lock file path —
# that includes a future run_nightly.ps1/.py wrapper, which is not built yet.
WORKER_LOCK_FILE = os.environ.get("PF_WORKER_LOCK_FILE", "").strip() or str(
    _PF_SYNC_DIR / ".pf_worker.lock"
)
WORKER_LOCK_TIMEOUT_SECONDS = float(os.environ.get("PF_WORKER_LOCK_TIMEOUT_SECONDS", "5"))
