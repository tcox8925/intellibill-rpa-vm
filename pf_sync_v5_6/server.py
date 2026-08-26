"""
Practice Fusion SOAP/PDF sync API
==================================
FastAPI server for pf_sync_v5_6.

In production this app is mounted as a sub-application by the repo-root
server.py (see server.py's `app.mount("/pf-sync", pf_sync_app)`), alongside a
same-way-mounted myops/server.py, so both Tebra and Practice Fusion sync are
served by one uvicorn process on one port (8010) -- every route below is
reachable at /pf-sync/<route> in that setup. Because the combined process's
cwd/entrypoint is the repo root (not this directory), every relative-path
request-field default here is anchored via _pf_path() to this file's own
directory rather than left as a bare relative string.

For local/standalone development it can still be run on its own:
    python -m uvicorn server:app --host 0.0.0.0 --port 8011
or, honoring the environment override:
    python -m uvicorn server:app --host 0.0.0.0 --port %PF_SYNC_API_PORT%
This is not the production path -- see server.py and myops/DEPLOYMENT.md.

Every endpoint here is a thin wrapper calling straight into pf_sync_pkg.*  -- the
same code the pf_soap_sync_v5_16.py CLI drives. Only one browser/CDP session can
exist at a time, so every endpoint that touches the browser is serialized behind
one global lock (mirroring myops/server.py's per-key locking pattern, but keyed on
a single fixed resource here rather than per-practice).
"""

import argparse
import contextlib
import io
import os
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pf_sync_pkg.cli import (
    browser_command_wrapper,
    resolve_report_dates,
    run_appointments_by_date,
    run_doctor,
    run_facesheet_pull_by_date,
    run_full_sync_by_date,
    run_nightly,
    run_refresh,
    run_sync_schedules_by_date,
)
from pf_sync_pkg.constants import BUILD_ID
from pf_sync_pkg.ingest import ingest_appointments
from pf_sync_pkg.matching import match_patients, resolve_patient_manually
from pf_sync_pkg.models import AppointmentReportConfig, SyncConfig
from pf_sync_pkg.pdf_pipeline import default_process_candidates, full_sync_on_page, process_records_on_page
from pf_sync_pkg.queue_admin import queue_status, reset_rows
from pf_sync_pkg.report_pull import pull_appointment_report_on_page
from pf_sync_pkg.store import append_run, finish_run, load_store, save_store, store_rows

CST = ZoneInfo("America/Chicago")


def _slog(message: str) -> None:
    print(f"[PF-SYNC-SERVER] [{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %Z')}] {message}", flush=True)


def _env_name() -> str:
    return os.environ.get("PF_SYNC_API_ENV", "development").strip().lower()


def _docs_enabled() -> bool:
    return _env_name() in {"dev", "development", "local"}


app = FastAPI(
    title="Practice Fusion SOAP/PDF Sync API",
    docs_url="/docs" if _docs_enabled() else None,
    redoc_url="/redoc" if _docs_enabled() else None,
    openapi_url="/openapi.json" if _docs_enabled() else None,
)

PF_SYNC_API_PORT = int(os.environ.get("PF_SYNC_API_PORT", 8011))

# Only one Chrome/CDP session can be driven at a time, so every browser-driving
# endpoint shares this single lock.
_locks = {}
_locks_guard = threading.Lock()
_BROWSER_LOCK_KEY = "__pf_sync__"


def _acquire_key_lock(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A pf_sync browser job is already running.")
    return lock


# ---------------------------------------------------------------------------
# Shared request field groups
# ---------------------------------------------------------------------------


from pathlib import Path as _Path

PF_SYNC_DIR = _Path(__file__).resolve().parent


def _pf_path(rel: str) -> str:
    """Anchor a relative default path to this file's own directory, not the
    process's cwd. Needed because this app can now be mounted as a sub-app
    inside myops/server.py's process (see module docstring above), whose cwd
    is myops/ -- without this, defaults like "pf_appointment_queue.json"
    would silently read/write inside myops/ instead of pf_sync_v5_6/."""
    return str(PF_SYNC_DIR / rel)


def _default_chrome_user_data_dir() -> str:
    """The reusable, already-logged-in Practice Fusion Chrome profile.

    %USERPROFILE%\\pf_rpa_chrome on the Windows VM this normally runs on. On a
    dev box with no USERPROFILE (this Mac), falls back to ~/pf_rpa_chrome --
    the actual trusted profile lives at /Users/hmg/pf_rpa_chrome here. Without
    this fallback the field silently defaulted to "" on Mac, which is exactly
    what let Swagger's "string" placeholder get submitted instead and spin up
    a brand-new, never-logged-in profile (hence the repeated OTP prompts).
    """
    if os.getenv("USERPROFILE"):
        return os.path.join(os.getenv("USERPROFILE"), "pf_rpa_chrome")
    return os.path.join(os.path.expanduser("~"), "pf_rpa_chrome")


class BrowserFieldsNoCreds(BaseModel):
    """Mirrors cli.add_browser_arguments()'s argparse defaults, minus username/
    password. Base for endpoints where credentials must always resolve from
    .env and should never appear as request fields at all -- see
    FacesheetPullByDateRequest."""

    attach: bool = False
    chrome_user_data_dir: str = Field(
        default_factory=_default_chrome_user_data_dir,
        # default_factory results don't surface in the OpenAPI schema's "default", so
        # Swagger's "Try it out" would otherwise pre-fill the generic "string"
        # placeholder instead of the real path -- examples= makes Swagger show (and
        # auto-fill) the actual resolved profile path instead.
        examples=[_default_chrome_user_data_dir()],
    )
    source_user_data_dir: str = ""
    source_profile: str = "Profile 11"
    refresh_profile: bool = False
    chrome_exe: str = ""
    debug_port: str = "9222"
    typing_delay_ms: int = 65
    login_timeout_seconds: int = 900
    keep_browser_open: bool = False


class BrowserFields(BrowserFieldsNoCreds):
    """Mirrors cli.add_browser_arguments()'s argparse defaults."""

    username: str = Field(default_factory=lambda: os.getenv("PF_USERNAME", ""))
    password: str = Field(default_factory=lambda: os.getenv("PF_PASSWORD", ""))


class ReportDateFields(BaseModel):
    """Mirrors cli.add_report_dates()'s argparse defaults."""

    report_date: str = ""
    start_date: str = ""
    end_date: str = ""


def _namespace(model: BaseModel, **extra) -> argparse.Namespace:
    payload = model.model_dump()
    # Swagger/most JSON clients always send every field, including "username": ""
    # explicitly -- that beats BrowserFields' default_factory (which only fires when
    # a field is *omitted*), so a blank credential in the request body silently wins
    # over .env instead of falling back to it. Credentials must always resolve from
    # .env, not from client input, so re-apply the env fallback here whenever the
    # request left them blank.
    if "username" in payload and not payload["username"]:
        payload["username"] = os.getenv("PF_USERNAME", "")
    if "password" in payload and not payload["password"]:
        payload["password"] = os.getenv("PF_PASSWORD", "")
    payload.update(extra)
    return argparse.Namespace(**payload)


def _namespace_with_env_creds(model: BaseModel, **extra) -> argparse.Namespace:
    """_namespace(), plus always injecting username/password straight from .env.

    For request models built on BrowserFieldsNoCreds (no username/password fields
    at all -- every /process, /full-sync, /refresh, /nightly, /full-sync-by-date,
    /pull-report, /facesheet-pull-by-date endpoint), this is the only place
    credentials get set. Never reads them off the request body.
    """
    return _namespace(
        model,
        username=os.getenv("PF_USERNAME", ""),
        password=os.getenv("PF_PASSWORD", ""),
        **extra,
    )


def _run_browser_job(lock_key: str, callback):
    """Run callback() under the global browser lock, releasing it when done."""
    lock = _acquire_key_lock(lock_key)
    try:
        return callback()
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _dispatch_browser_job(wait_for_completion: bool, job_name: str, callback):
    """Run a browser job inline (default) or in a background thread.

    Mirrors myops/server.py: wait_for_completion=True runs inline and returns the
    result (or raises HTTPException on failure); False starts a background thread
    and returns 202 immediately with a job_id.
    """
    if wait_for_completion:
        started = time.monotonic()
        outcome: dict = {}

        def _runner():
            try:
                outcome["result"] = _run_browser_job(_BROWSER_LOCK_KEY, callback)
            except BaseException as exc:  # re-raised on the caller's thread below
                # BaseException, not Exception -- SystemExit/KeyboardInterrupt raised
                # inside this background thread otherwise escape this handler
                # entirely, get silently swallowed by Python's default handling of
                # an uncaught SystemExit in a non-main thread, and leave `outcome`
                # with neither "result" nor "error" set -- surfacing below as a
                # confusing `KeyError: 'result'` instead of the real failure.
                outcome["error"] = exc

        # FastAPI runs a plain `def` endpoint via Starlette's implicit threadpool --
        # a worker thread that anyio/uvloop machinery has already touched. Playwright's
        # sync API refuses to run there ("Sync API inside the asyncio loop"), even
        # though nothing is actually running on that thread's loop. A fresh, plain
        # threading.Thread here has never been touched by that machinery, so
        # Playwright's guard passes; .join() still blocks this request until the
        # browser job finishes, exactly like calling it inline would have.
        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()

        if "error" in outcome:
            exc = outcome["error"]
            if isinstance(exc, HTTPException):
                raise exc
            _slog(f"{job_name} failed: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

        _slog(f"{job_name} completed in {time.monotonic() - started:.1f}s")
        return {"status": "completed", "result": outcome["result"]}

    job_id = str(uuid.uuid4())

    def _runner():
        _slog(f"{job_name} background job_id={job_id} starting")
        try:
            _run_browser_job(_BROWSER_LOCK_KEY, callback)
            _slog(f"{job_name} background job_id={job_id} done")
        except BaseException as exc:
            # BaseException, not Exception -- see the wait_for_completion=True
            # branch above for why (SystemExit otherwise vanishes silently here,
            # with no "done" or "failed" log line at all).
            _slog(f"{job_name} background job_id={job_id} failed: {type(exc).__name__}: {exc}")

    threading.Thread(target=_runner, daemon=True).start()
    return JSONResponse(status_code=202, content={"status": "started", "job_id": job_id})


# ---------------------------------------------------------------------------
# Non-browser request models
# ---------------------------------------------------------------------------


class DoctorRequest(BaseModel):
    config_json: str
    report_config_json: str
    patients_file: str = ""
    queue_json: str = ""
    appointments_file: str = ""
    chrome_exe: str = ""
    attach: bool = False
    chrome_user_data_dir: str = Field(
        default_factory=_default_chrome_user_data_dir,
        # default_factory results don't surface in the OpenAPI schema's "default", so
        # Swagger's "Try it out" would otherwise pre-fill the generic "string"
        # placeholder instead of the real path -- examples= makes Swagger show (and
        # auto-fill) the actual resolved profile path instead.
        examples=[_default_chrome_user_data_dir()],
    )


class IngestRequest(BaseModel):
    appointments_file: str
    queue_json: str
    practice: str
    source_report_name: str = ""
    reset_existing: bool = False
    config_json: str = ""


class MatchPatientsRequest(BaseModel):
    queue_json: str
    patients_file: str
    fuzzy_threshold: float = 0.82
    rematch_all: bool = False
    dob_match_threshold: float = 0.85


class ResolvePatientRequest(BaseModel):
    queue_json: str
    patient_id: str = ""
    ehr_patient_guid: str = ""
    row_id: str = ""
    appointment_id: str = ""
    patients_file: str = ""
    resolved_patient_name: str = ""


class StatusRequest(BaseModel):
    queue_json: str
    show_limit: int = 20


class ResetRequest(BaseModel):
    queue_json: str
    row_id: str = ""
    appointment_id: str = ""
    patient_id: str = ""
    all_processed: bool = False


# ---------------------------------------------------------------------------
# Browser-job request models
# ---------------------------------------------------------------------------


class PullReportRequest(BrowserFieldsNoCreds, ReportDateFields):
    report_config_json: str
    output_csv: str
    wait_for_completion: bool = True


class ProcessRequest(BrowserFieldsNoCreds):
    queue_json: str
    config_json: str
    downloads_dir: str
    limit: int = 0
    dry_run: bool = False
    include_failed: bool = False
    run_id: str = Field(
        default="",
        description="Pass the same value across multiple /process calls that belong to one "
                    "logical pull so they converge on one appointments_<run_id>.json manifest "
                    "instead of each call producing its own fragment. Leave unset for the old "
                    "per-call random-uuid manifest naming.",
    )
    wait_for_completion: bool = True


class FullSyncRequest(BrowserFieldsNoCreds):
    queue_json: str
    config_json: str
    patients_file: str
    downloads_dir: str
    limit_patients: int = 0
    max_encounters_per_patient: int = 0
    dry_run: bool = False
    rescrape_all: bool = False
    wait_for_completion: bool = True


class RefreshRequest(BrowserFieldsNoCreds):
    queue_json: str
    config_json: str
    downloads_dir: str
    row_id: str = ""
    appointment_id: str = ""
    encounter_id: str = ""
    patient_id: str = ""
    ehr_patient_guid: str = ""
    dry_run: bool = False
    wait_for_completion: bool = True


class NightlyRequest(BrowserFieldsNoCreds, ReportDateFields):
    queue_json: str
    config_json: str
    report_config_json: str
    patients_file: str
    downloads_dir: str
    practice: str
    appointments_file: str = ""
    report_output_csv: str = ""
    limit: int = 0
    dry_run: bool = False
    include_failed: bool = False
    fuzzy_threshold: float = 0.82
    dob_match_threshold: float = 0.85
    wait_for_completion: bool = True


class FullSyncByDateRequest(BrowserFieldsNoCreds, ReportDateFields):
    """Discover (Schedule-scoped, full-sweep fallback) -> merge registry ->
    pull-report -> ingest -> match-patients -> process, in one call. Unlike
    /nightly, this refreshes the patient registry itself first instead of
    assuming whatever's already on disk at patients_file is current -- see
    cli.run_full_sync_by_date's docstring for why that mattered in practice.
    start_date/end_date (from ReportDateFields) scope BOTH the discovery walk
    and the appointment report pull to the same window.
    """

    # Real defaults (not bare `str`) on purpose -- see FacesheetPullByDateRequest's
    # docstring and _default_chrome_user_data_dir's docstring above: a required
    # field with no default gets Swagger's literal "string" placeholder submitted
    # if a caller forgets to override it in "Try it out", which then gets used as
    # a real path (e.g. downloads_dir="string" makes generate_pdf's mkdir collide
    # with a stray file of that name and fail every record with FileExistsError).
    # Defaults below point at this repo's real, current files/practice so a normal
    # call only needs report_date and chrome_user_data_dir. Anchored via _pf_path()
    # to pf_sync_v5_6/'s own directory -- see _pf_path's docstring -- so they still
    # resolve correctly when this app is mounted as a sub-app under myops/server.py
    # with a different process cwd.
    queue_json: str = Field(
        default_factory=lambda: _pf_path("pf_appointment_queue.json"),
        examples=[_pf_path("pf_appointment_queue.json")],
    )
    config_json: str = Field(
        default_factory=lambda: _pf_path("config/pf_pdf_sync_config.json"),
        examples=[_pf_path("config/pf_pdf_sync_config.json")],
    )
    report_config_json: str = Field(
        default_factory=lambda: _pf_path("config/pf_appointment_report_config.json"),
        examples=[_pf_path("config/pf_appointment_report_config.json")],
    )
    patients_file: str = Field(
        default_factory=lambda: _pf_path("practice_fusion_patients.csv"),
        examples=[_pf_path("practice_fusion_patients.csv")],
    )
    downloads_dir: str = Field(
        default_factory=lambda: _pf_path("pf_encounter_pdfs"),
        examples=[_pf_path("pf_encounter_pdfs")],
    )
    practice: str = "NWARK Internal Medicine"
    report_output_csv: str = ""
    limit: int = 0
    dry_run: bool = False
    include_failed: bool = False
    fuzzy_threshold: float = 0.82
    dob_match_threshold: float = 0.85
    wait_for_completion: bool = True


class FacesheetPullByDateRequest(BrowserFieldsNoCreds, ReportDateFields):
    """Discover (Schedule-scoped) -> pull-report -> ingest -> match against the live
    discovery -> process, forcing every Facesheet section on for this call only. The
    default (/full-sync-by-date, /process, /nightly) stays notes-only -- pass a date
    (or start_date/end_date) here to pull complete Facesheet + notes PDFs for every
    appointment on that date without changing the on-disk config default.

    No username/password fields at all -- credentials always resolve from
    PF_USERNAME/PF_PASSWORD in .env, never from the request body (see the endpoint
    below, which injects them directly rather than reading them off this model).

    No patients_file CSV anywhere in this path: schedule discovery resolves real
    patient GUIDs directly from PF, in memory, and those become the match registry --
    not fuzzy name/DOB matching against a static registry CSV. The whole-practice-
    scrape fallback (triggered only if schedule discovery comes back completely
    empty) is blocked for this command -- a single date's facesheet pull has no
    business kicking off a scrape of every patient in the practice.

    Defaults below point at this repo's real, current files/practice so a normal call
    only needs report_date and chrome_user_data_dir -- override any of them only when
    you actually mean a different file or practice. Anchored via _pf_path() for the
    same sub-app-mounting reason as FullSyncByDateRequest above."""

    queue_json: str = Field(
        default_factory=lambda: _pf_path("pf_appointment_queue.json"),
        examples=[_pf_path("pf_appointment_queue.json")],
    )
    config_json: str = Field(
        default_factory=lambda: _pf_path("config/pf_pdf_sync_config.json"),
        examples=[_pf_path("config/pf_pdf_sync_config.json")],
    )
    report_config_json: str = Field(
        default_factory=lambda: _pf_path("config/pf_appointment_report_config.json"),
        examples=[_pf_path("config/pf_appointment_report_config.json")],
    )
    downloads_dir: str = Field(
        default_factory=lambda: _pf_path("pf_encounter_pdfs"),
        examples=[_pf_path("pf_encounter_pdfs")],
    )
    practice: str = "NWARK Internal Medicine"
    report_output_csv: str = ""
    limit: int = 0
    dry_run: bool = False
    include_failed: bool = False
    fuzzy_threshold: float = 0.82
    dob_match_threshold: float = 0.85
    retry_concurrency: int = 3
    wait_for_completion: bool = True


class FacesheetPullByDateRequestSlim(BaseModel):
    """The only fields that matter day-to-day for /facesheet-pull-by-date.

    Everything else on FacesheetPullByDateRequest (chrome_user_data_dir,
    queue_json, config_json, report_config_json, downloads_dir, practice,
    limit, dry_run, thresholds, ...) keeps that model's own safe, already-
    anchored defaults -- there's no reason to make a caller see or override
    them in Swagger, and doing so is exactly what caused the FileExistsError:
    'string' bug (a default_factory field left at Swagger's placeholder value
    and submitted literally). Hiding them here removes that failure mode
    entirely for this endpoint, not just the display.
    """

    report_date: str = ""
    start_date: str = ""
    end_date: str = ""
    wait_for_completion: bool = True


class SyncSchedulesByDateRequest(BrowserFieldsNoCreds, ReportDateFields):
    """Standalone catch-up pass, deliberately independent of /full-sync-by-date's
    report pull/ingest/match pipeline: walks the Schedule for the requested date
    range, keeps every appointment actually marked Seen there, diffs against the
    queue's existing (patient, date) pairs, and injects + processes a synthetic
    record straight from the patient chart for whatever Practice Fusion's own
    Eligibility Report hasn't caught up on yet -- the report is never read here.

    With report_date/start_date/end_date all left blank, defaults to a rolling
    lookback window [today - lookback_days, today] instead of just today -- see
    cli.resolve_sync_schedules_dates's docstring for why: a patient who was
    Confirmed (correctly skipped) on a prior call and only flips to Seen a few
    days later still needs to be re-checked on THAT date, not just today's.

    Anchored via _pf_path() for the same sub-app-mounting reason as
    FullSyncByDateRequest above."""

    queue_json: str = Field(
        default_factory=lambda: _pf_path("pf_appointment_queue.json"),
        examples=[_pf_path("pf_appointment_queue.json")],
    )
    config_json: str = Field(
        default_factory=lambda: _pf_path("config/pf_pdf_sync_config.json"),
        examples=[_pf_path("config/pf_pdf_sync_config.json")],
    )
    schedule_config_json: str = Field(
        default_factory=lambda: _pf_path("config/pf_schedule_scrape_config.json"),
        examples=[_pf_path("config/pf_schedule_scrape_config.json")],
    )
    downloads_dir: str = Field(
        default_factory=lambda: _pf_path("pf_encounter_pdfs"),
        examples=[_pf_path("pf_encounter_pdfs")],
    )
    practice: str = "NWARK Internal Medicine"
    limit: int = 0
    dry_run: bool = False
    include_failed: bool = False
    lookback_days: int = 3
    retry_concurrency: int = 3
    wait_for_completion: bool = True


class SyncSchedulesByDateRequestSlim(BaseModel):
    """The only fields that matter day-to-day for /sync-schedules-by-date.

    Everything else on SyncSchedulesByDateRequest (chrome_user_data_dir,
    queue_json, config_json, schedule_config_json, downloads_dir, practice,
    limit, dry_run, ...) keeps that model's own safe, already-anchored defaults
    -- see FacesheetPullByDateRequestSlim's docstring above for why those
    fields aren't exposed here at all. Leaving report_date/start_date/end_date
    all blank is the normal call shape: it lets lookback_days pick the rolling
    window instead of requiring a caller to track dates themselves.

    pull_failed_sheets: default False leaves existing `failed` rows alone --
    a row here failed for a REASON (e.g. the "Printable chart preview did not
    appear" print-chart timeout, unrelated to the Eligibility Report gap this
    endpoint otherwise targets), so it's not retried automatically just
    because this call happens to touch that patient/date again. Pass true to
    also retry those failed rows this call -- maps straight onto the CLI's
    --include-failed / cli.default_process_candidates' own include_failed arg.
    """

    report_date: str = ""
    start_date: str = ""
    end_date: str = ""
    lookback_days: int = 3
    pull_failed_sheets: bool = False
    wait_for_completion: bool = True


class AppointmentsByDateRequest(BrowserFieldsNoCreds, ReportDateFields):
    """Read-only Schedule lookup across [start_date, end_date] -- today for
    either side left blank, same resolve_report_dates default every other
    *-by-date endpoint uses. Appointments only -- no facesheet/SOAP pull, no
    queue read/write, no Azure upload.

    Anchored via _pf_path() for the same sub-app-mounting reason as
    FullSyncByDateRequest above."""

    schedule_config_json: str = Field(
        default_factory=lambda: _pf_path("config/pf_schedule_scrape_config.json"),
        examples=[_pf_path("config/pf_schedule_scrape_config.json")],
    )
    output_json: str = ""
    wait_for_completion: bool = True


class AppointmentsByDateRequestSlim(BaseModel):
    """The only fields that matter day-to-day for /appointments-by-date -- see
    FacesheetPullByDateRequestSlim's docstring above for why the browser/config
    fields aren't exposed here at all."""

    report_date: str = ""
    start_date: str = ""
    end_date: str = ""
    wait_for_completion: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"build_id": BUILD_ID}


@app.post("/doctor")
def doctor(request: DoctorRequest):
    args = argparse.Namespace(**request.model_dump())
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = run_doctor(args)
    return {"passed": exit_code == 0, "output": buffer.getvalue()}


@app.post("/ingest")
def ingest(request: IngestRequest):
    config = SyncConfig.load(request.config_json) if request.config_json else SyncConfig()
    try:
        counts = ingest_appointments(
            request.appointments_file,
            request.queue_json,
            request.practice,
            request.source_report_name,
            request.reset_existing,
            config=config,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return counts


@app.post("/match-patients")
def match_patients_endpoint(request: MatchPatientsRequest):
    try:
        counts = match_patients(
            request.queue_json,
            request.patients_file,
            request.fuzzy_threshold,
            request.rematch_all,
            request.dob_match_threshold,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return counts


@app.post("/resolve-patient")
def resolve_patient_endpoint(request: ResolvePatientRequest):
    try:
        counts = resolve_patient_manually(
            request.queue_json,
            request.patient_id,
            request.ehr_patient_guid,
            request.row_id,
            request.appointment_id,
            request.patients_file,
            request.resolved_patient_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return counts


@app.post("/status")
def status_endpoint(request: StatusRequest):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = queue_status(request.queue_json, request.show_limit)
    result = dict(result)
    result["output"] = buffer.getvalue()
    return result


@app.post("/reset")
def reset_endpoint(request: ResetRequest):
    try:
        count = reset_rows(
            request.queue_json,
            request.row_id,
            request.appointment_id,
            request.patient_id,
            request.all_processed,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"reset": count}


@app.post("/pull-report")
def pull_report_endpoint(request: PullReportRequest):
    args = _namespace_with_env_creds(request)
    start_date, end_date = resolve_report_dates(args)

    def job():
        return browser_command_wrapper(
            args,
            lambda page: pull_appointment_report_on_page(
                page,
                AppointmentReportConfig.load(request.report_config_json),
                start_date,
                end_date,
                request.output_csv,
            ),
        )

    return _dispatch_browser_job(request.wait_for_completion, "pull-report", job)


@app.post("/process")
def process_endpoint(request: ProcessRequest):
    args = _namespace_with_env_creds(request)

    def job():
        store = load_store(request.queue_json)
        rows = store_rows(store)
        candidates = default_process_candidates(rows, request.include_failed)
        if not candidates:
            return {"processed": 0, "message": "No ready/review records to process."}
        config = SyncConfig.load(request.config_json)
        run_id = append_run(store, "process", {"candidates": len(candidates), "dry_run": request.dry_run})

        def callback(page):
            counts = process_records_on_page(
                page,
                request.queue_json,
                config,
                request.downloads_dir,
                candidates,
                rows,
                store,
                request.limit,
                request.dry_run,
                False,
                request.run_id,
            )
            finish_run(store, run_id, "success", counts)
            save_store(request.queue_json, store, rows)
            return counts

        return browser_command_wrapper(args, callback)

    return _dispatch_browser_job(request.wait_for_completion, "process", job)


@app.post("/full-sync")
def full_sync_endpoint(request: FullSyncRequest):
    args = _namespace_with_env_creds(request)

    def job():
        store = load_store(request.queue_json)
        rows = store_rows(store)
        config = SyncConfig.load(request.config_json)
        run_id = append_run(
            store,
            "full-sync",
            {
                "patients_file": request.patients_file,
                "limit_patients": request.limit_patients,
                "max_encounters_per_patient": request.max_encounters_per_patient,
                "dry_run": request.dry_run,
                "rescrape_all": request.rescrape_all,
            },
        )

        def callback(page):
            counts = full_sync_on_page(
                page,
                request.queue_json,
                config,
                request.downloads_dir,
                request.patients_file,
                store,
                rows,
                request.limit_patients,
                request.max_encounters_per_patient,
                request.dry_run,
                request.rescrape_all,
            )
            finish_run(store, run_id, "success", counts)
            save_store(request.queue_json, store, rows)
            return counts

        return browser_command_wrapper(args, callback)

    return _dispatch_browser_job(request.wait_for_completion, "full-sync", job)


@app.post("/refresh")
def refresh_endpoint(request: RefreshRequest):
    args = _namespace_with_env_creds(request)

    def job():
        # Same patient_id/ehr_patient_guid vs row/appointment/encounter-id selection
        # logic as the CLI's refresh command -- reused via cli.run_refresh, not
        # reimplemented here.
        return run_refresh(args)

    return _dispatch_browser_job(request.wait_for_completion, "refresh", job)


@app.post("/nightly")
def nightly_endpoint(request: NightlyRequest):
    args = _namespace_with_env_creds(request)

    def job():
        # Same pull -> ingest -> match -> process orchestration as the CLI's nightly
        # command -- reused via cli.run_nightly, not reimplemented here.
        return run_nightly(args)

    return _dispatch_browser_job(request.wait_for_completion, "nightly", job)


class FullSyncByDateRequestSlim(BaseModel):
    """The only fields that matter day-to-day for /full-sync-by-date.

    Everything else on FullSyncByDateRequest (chrome_user_data_dir, queue_json,
    config_json, report_config_json, patients_file, downloads_dir, practice,
    limit, dry_run, thresholds, ...) keeps that model's own real, anchored
    defaults -- see FacesheetPullByDateRequestSlim's docstring above for why
    those fields aren't exposed here at all.
    """

    report_date: str = ""
    start_date: str = ""
    end_date: str = ""
    wait_for_completion: bool = True


@app.post("/full-sync-by-date")
def full_sync_by_date_endpoint(request: FullSyncByDateRequestSlim):
    # Expand the slim request into the full model so every other field keeps
    # FullSyncByDateRequest's own real, anchored defaults instead of a
    # caller-supplied Swagger placeholder.
    full_request = FullSyncByDateRequest(
        report_date=request.report_date,
        start_date=request.start_date,
        end_date=request.end_date,
        wait_for_completion=request.wait_for_completion,
    )
    args = _namespace_with_env_creds(full_request)

    def job():
        # Same discover -> merge registry -> pull -> ingest -> match -> process
        # orchestration as the CLI's full-sync-by-date command -- reused via
        # cli.run_full_sync_by_date, not reimplemented here.
        return run_full_sync_by_date(args)

    return _dispatch_browser_job(request.wait_for_completion, "full-sync-by-date", job)


@app.post("/facesheet-pull-by-date")
def facesheet_pull_by_date_endpoint(request: FacesheetPullByDateRequestSlim):
    # Expand the slim request into the full model so every other field keeps
    # FacesheetPullByDateRequest's own real, anchored defaults (never a
    # caller-supplied "string" placeholder) -- see FacesheetPullByDateRequestSlim's
    # docstring for why those fields aren't exposed here at all.
    full_request = FacesheetPullByDateRequest(
        report_date=request.report_date,
        start_date=request.start_date,
        end_date=request.end_date,
        wait_for_completion=request.wait_for_completion,
    )
    args = _namespace_with_env_creds(full_request)

    def job():
        # Same discover -> merge registry -> pull -> ingest -> match -> process
        # orchestration as /full-sync-by-date, but forces every Facesheet section on
        # for this call only -- reused via cli.run_facesheet_pull_by_date, which
        # builds the forced-on SyncConfig and hands it to run_full_sync_by_date
        # instead of reimplementing the pipeline here.
        return run_facesheet_pull_by_date(args)

    return _dispatch_browser_job(request.wait_for_completion, "facesheet-pull-by-date", job)


@app.post("/sync-schedules-by-date")
def sync_schedules_by_date_endpoint(request: SyncSchedulesByDateRequestSlim):
    # Expand the slim request into the full model so every other field keeps
    # SyncSchedulesByDateRequest's own real, anchored defaults instead of a
    # caller-supplied Swagger placeholder -- see SyncSchedulesByDateRequestSlim's
    # docstring for why those fields aren't exposed here at all.
    full_request = SyncSchedulesByDateRequest(
        report_date=request.report_date,
        start_date=request.start_date,
        end_date=request.end_date,
        lookback_days=request.lookback_days,
        include_failed=request.pull_failed_sheets,
        wait_for_completion=request.wait_for_completion,
    )
    args = _namespace_with_env_creds(full_request)

    def job():
        # Schedule scrape -> Seen-status filter -> inject synthetic record ->
        # process, entirely independent of the Eligibility Report -- reused via
        # cli.run_sync_schedules_by_date, not reimplemented here.
        return run_sync_schedules_by_date(args)

    return _dispatch_browser_job(request.wait_for_completion, "sync-schedules-by-date", job)


@app.post("/appointments-by-date")
def appointments_by_date_endpoint(request: AppointmentsByDateRequestSlim):
    # Expand the slim request into the full model so every other field keeps
    # AppointmentsByDateRequest's own real, anchored defaults instead of a
    # caller-supplied Swagger placeholder -- see AppointmentsByDateRequestSlim's
    # docstring for why those fields aren't exposed here at all.
    full_request = AppointmentsByDateRequest(
        report_date=request.report_date,
        start_date=request.start_date,
        end_date=request.end_date,
        wait_for_completion=request.wait_for_completion,
    )
    args = _namespace_with_env_creds(full_request)

    def job():
        # Read-only Schedule scrape across [start_date, end_date] -- no chart,
        # no facesheet, no queue writes -- reused via
        # cli.run_appointments_by_date, not reimplemented here.
        return run_appointments_by_date(args)

    return _dispatch_browser_job(request.wait_for_completion, "appointments-by-date", job)
