"""Module-level constants, selectors, and CSV field-size setup.

Split out of pf_soap_sync_v5_16.py (v5.16) verbatim -- see that module's docstring
history for behavioral notes attached to these values.
"""

import csv
import os
import shutil
import sys
from typing import Optional
from zoneinfo import ZoneInfo

try:
    # Loads PF_USERNAME/PF_PASSWORD/etc from the repo-root .env (python-dotenv walks
    # upward from this file's directory to find it), without overriding any value the
    # environment already set explicitly (e.g. a systemd unit's Environment= lines).
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=False))
except ImportError:  # pragma: no cover - python-dotenv not installed
    pass

BUILD_ID = "PF-SOAP-SYNC-v5.16.0-batch-appointment-metadata"

LOGIN_URL = "https://static.practicefusion.com/apps/ehr/index.html#/login"
EHR_BASE_URL = "https://static.practicefusion.com/apps/ehr/index.html"
PF_USERNAME_SELECTOR = "#inputUsername, input[name='inputUsername']"
PF_PASSWORD_SELECTOR = "#inputPswd, input[name='inputPswdTest']"
PF_LOGIN_BUTTON_SELECTOR = "#loginButton"
DEFAULT_TIMEOUT = 30_000
SHORT_TIMEOUT = 5_000
# v5.4: the practice timezone drives default report dates. NWARK Internal Medicine
# is in Rogers, Arkansas (Central). The previous hardcoded America/Detroit value was
# a leftover from another practice and made an unattended nightly run between
# 11:00 PM and midnight Central pull the following day's report.
PRACTICE_TZ_NAME = os.environ.get("PF_PRACTICE_TIMEZONE", "America/Chicago").strip() or "America/Chicago"

# --- rcm-attachments Azure delivery (see pf_sync_pkg/rcm_upload.py) --------
# Same storage account/container as myops/ehr/zipbuild.py's Tebra delivery (shared
# rcm-attachments container across EHR integrations) -- reusing the identical env
# var names on purpose so both projects read the same repo-root .env values.
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
RCM_ATTACHMENTS_CONTAINER = os.environ.get("RCM_ATTACHMENTS_CONTAINER", "rcm-attachments").strip() or "rcm-attachments"
# Unlike Tebra (myops/ehr/zipbuild.py), where folder_structure arrives per-request
# from an external caller, Practice Fusion has no such caller today -- this is a
# single-practice deployment, so the destination folder is fixed rather than
# threaded through from anywhere. Confirmed/assigned by the operator 2026-08-11.
PF_RCM_FOLDER_STRUCTURE = "1553326257-1553326257001"
try:
    PRACTICE_TZ = ZoneInfo(PRACTICE_TZ_NAME)
except Exception:  # pragma: no cover - invalid tz name supplied by operator
    print(
        f"WARNING: unknown PF_PRACTICE_TIMEZONE {PRACTICE_TZ_NAME!r}; falling back to America/Chicago.",
        flush=True,
    )
    PRACTICE_TZ_NAME = "America/Chicago"
    PRACTICE_TZ = ZoneInfo(PRACTICE_TZ_NAME)

PATIENT_NAME_SELECTOR = "[data-element='full-name']"
PATIENT_RECORD_NUMBER_SELECTOR = "[data-element='prn-text']"

DEFAULT_IGNORED_STATUSES = {
    "cancelled",
    "canceled",
    "no show",
    "no-show",
    "rescheduled",
    "deleted",
    "void",
}

DEFAULT_SEEN_STATUSES = {
    "seen",
    "completed",
    "complete",
    "checked out",
    "checked-out",
    "signed",
}

PROFILE_CACHE_IGNORE = shutil.ignore_patterns(
    "Cache",
    "Code Cache",
    "GPUCache",
    "GraphiteDawnCache",
    "DawnCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "ShaderCache",
    "GrShaderCache",
    "Crashpad",
    "component_crx_cache",
    "optimization_guide_model_store",
    "*.tmp",
    "*.log",
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
)


def configure_csv_field_limit() -> int:
    """Raise Python's CSV cell-size limit for PF exports containing large JSON fields.

    Patient registry exports may include raw_patient_json, summary, or note columns that
    exceed csv's default 131,072-character ceiling. Use the largest platform-supported
    value, reducing it only when the local C runtime rejects the integer.
    """
    candidate = sys.maxsize
    while candidate > 131_072:
        try:
            csv.field_size_limit(candidate)
            return candidate
        except OverflowError:
            candidate //= 10
    csv.field_size_limit(131_072)
    return 131_072


CSV_FIELD_LIMIT = configure_csv_field_limit()

# 2026-09-24: sync-schedules-by-date is now the only supported Practice Fusion
# facesheet pipeline. The other paths that can create/touch ehr.ehr_pf_queue_rows
# (ingest, nightly, full-sync-by-date, facesheet-pull-by-date, full-sync, refresh)
# are disabled, not removed -- they raced against sync-schedules-by-date's own
# scheduled runs (each unaware of the other) and produced 308 duplicate rows for
# the same real appointments; see pf_sync_pkg/cli.py's run_sync_schedules_by_date
# docstring/comments and dedupe_queue_rows.py for the incident and cleanup.
# Kept as a shared message so the CLI (cli.py main()) and the HTTP API (server.py)
# report the identical reason for every one of the 6.
DISABLED_COMMANDS = {
    "ingest": "sync-schedules-by-date discovers and ingests appointments itself -- a separate 'ingest' run duplicates it.",
    "nightly": "nightly's pull-report/ingest/match/process pipeline duplicates sync-schedules-by-date's own discovery.",
    "full-sync-by-date": "full-sync-by-date's report-based discovery duplicates sync-schedules-by-date's own discovery.",
    "facesheet-pull-by-date": "facesheet-pull-by-date's report-based discovery duplicates sync-schedules-by-date's own discovery.",
    "full-sync": "full-sync's registry-based discovery duplicates sync-schedules-by-date's own discovery.",
    "refresh": "refresh creates its own synthetic queue row outside sync-schedules-by-date's dedup logic.",
}


def disabled_command_message(command: str) -> str:
    reason = DISABLED_COMMANDS.get(command, "")
    return (
        f"'{command}' is disabled: sync-schedules-by-date is now the only supported "
        f"Practice Fusion sync pipeline (see pf_sync_v5_6/README.md). {reason} "
        f"Use 'sync-schedules-by-date' instead. (Pre-existing duplicate rows this "
        f"already created: see pf_sync_v5_6/dedupe_queue_rows.py.)"
    )
