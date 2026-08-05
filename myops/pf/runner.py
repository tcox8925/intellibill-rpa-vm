from __future__ import annotations

"""
Subprocess bridge to pf-soap-sync/pf_soap_sync_v5_13.py.

The worker is a standalone CLI (JSON-queue-backed, not an importable pipeline
like ehr.pipeline.run), so this wraps it the same way run_pf_sync_tests.ps1
and the VM handoff's bootstrap command do: shell out to
`python pf_soap_sync_v5_13.py <command> ...` and capture the result. No
changes to the worker script itself.
"""

import json
import subprocess
import sys

from filelock import FileLock, Timeout

from . import config


class PFCommandError(RuntimeError):
    def __init__(self, command, returncode, stdout, stderr):
        super().__init__(f"pf_soap_sync {command} exited {returncode}: {stderr[-2000:] or stdout[-2000:]}")
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class PFWorkerBusyError(RuntimeError):
    """Raised when another process (nightly or another refresh) holds the shared
    Chrome-profile lock. Any process that drives the browser must acquire
    config.WORKER_LOCK_FILE for this to actually prevent a collision — see the
    handoff's Section 7 (single worker owns the Chrome profile)."""


_worker_lock = FileLock(config.WORKER_LOCK_FILE)


def _browser_args() -> list[str]:
    args = ["--chrome-user-data-dir", config.CHROME_USER_DATA_DIR, "--debug-port", config.DEBUG_PORT]
    if config.CHROME_EXE:
        args += ["--chrome-exe", config.CHROME_EXE]
    return args


def _run(command: str, extra_args: list[str], use_worker_lock: bool = False) -> dict:
    cmd = [sys.executable, config.WORKER_SCRIPT_PATH, command, *extra_args]

    def _invoke() -> dict:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.SUBPROCESS_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            raise PFCommandError(command, completed.returncode, completed.stdout, completed.stderr)
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    if not use_worker_lock:
        return _invoke()

    try:
        with _worker_lock.acquire(timeout=config.WORKER_LOCK_TIMEOUT_SECONDS):
            return _invoke()
    except Timeout:
        raise PFWorkerBusyError(
            "Practice Fusion Chrome profile is in use by another nightly/refresh run."
        )


def get_appointment_row(row_id: str) -> dict | None:
    """Read the current queue JSON directly (no subprocess) to report an
    appointment's latest status/pdf_path after a refresh completes."""
    try:
        with open(config.QUEUE_JSON, "r", encoding="utf-8") as handle:
            store = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for row in store.get("rows", []):
        if isinstance(row, dict) and row.get("row_id") == row_id:
            return row
    return None


def run_nightly(
    practice: str | None = None,
    report_date: str | None = None,
    limit: int = 0,
    dry_run: bool = False,
) -> dict:
    args = [
        "--queue-json", config.QUEUE_JSON,
        "--config-json", config.PDF_CONFIG_JSON,
        "--report-config-json", config.REPORT_CONFIG_JSON,
        "--patients-file", config.PATIENTS_FILE,
        "--downloads-dir", config.DOWNLOADS_DIR,
        "--practice", practice or config.PRACTICE_NAME,
        *_browser_args(),
    ]
    if report_date:
        args += ["--report-date", report_date]
    if limit:
        args += ["--limit", str(limit)]
    if dry_run:
        args.append("--dry-run")
    return _run("nightly", args, use_worker_lock=True)


def run_refresh(
    row_id: str = "",
    appointment_id: str = "",
    encounter_id: str = "",
    patient_id: str = "",
    ehr_patient_guid: str = "",
    dry_run: bool = False,
) -> dict:
    selectors = {
        "--row-id": row_id,
        "--appointment-id": appointment_id,
        "--encounter-id": encounter_id,
        "--patient-id": patient_id,
        "--ehr-patient-guid": ehr_patient_guid,
    }
    chosen = [(flag, value) for flag, value in selectors.items() if value]
    if len(chosen) != 1:
        raise ValueError(
            "run_refresh requires exactly one of: row_id, appointment_id, "
            "encounter_id, patient_id, ehr_patient_guid"
        )
    flag, value = chosen[0]

    args = [
        "--queue-json", config.QUEUE_JSON,
        "--config-json", config.PDF_CONFIG_JSON,
        "--downloads-dir", config.DOWNLOADS_DIR,
        flag, value,
        *_browser_args(),
    ]
    if dry_run:
        args.append("--dry-run")
    return _run("refresh", args, use_worker_lock=True)


def get_status(show_limit: int = 20) -> dict:
    # status only reads the queue JSON, no browser involved, so it never
    # contends for the worker lock.
    return _run("status", ["--queue-json", config.QUEUE_JSON, "--show-limit", str(show_limit)])


def classify_refresh_outcome(row: dict | None, dry_run: bool) -> tuple[str, str, str | None]:
    """Map the worker's file-based outcome onto the handoff's job_status vocabulary
    (queued/running/processed/no_new_encounter/review/failed). Best-effort in the
    absence of the SQL job table the handoff specifies — see pf-soap-sync/README.md."""
    if row is None:
        return "failed", "Appointment row not found in the queue after refresh.", None

    status = row.get("status") or ""
    reason = row.get("status_reason") or ""
    message = row.get("message") or ""
    pdf_uri = row.get("pdf_path") or None

    if dry_run and status not in ("failed",):
        return "processed", message or "Dry run validated (no PDF written).", None
    if status == "processed":
        return "processed", message or "SOAP PDF created.", pdf_uri
    if status == "ready" and reason == "waiting_for_encounter":
        return "no_new_encounter", message or "Appointment has no matching encounter yet.", None
    if status in ("review", "needs_attention"):
        return "review", message or reason or "Needs manual review.", None
    if status == "failed":
        return "failed", message or row.get("error_message") or "Processing failed.", None
    return status or "failed", message, pdf_uri
