"""Argparse CLI orchestration: subcommands, browser wrapper, doctor diagnostics, main()."""

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from playwright.sync_api import Page

from pf_sync_pkg.browser import build_browser, close_browser, find_chrome_exe, wait_for_pf_login
from pf_sync_pkg.constants import BUILD_ID, CSV_FIELD_LIMIT, PRACTICE_TZ_NAME
from pf_sync_pkg.identity import normalize_person_name, normalize_phone
from pf_sync_pkg.ingest import (
    OPTIONAL_APPOINTMENT_FIELDS,
    REQUIRED_APPOINTMENT_FIELDS,
    ingest_appointment_rows,
    ingest_appointments,
    map_appointment_row,
)
from pf_sync_pkg.matching import (
    load_patient_registry,
    match_patients,
    match_patients_against_registry,
    resolve_patient_manually,
    select_queue_rows,
)
from pf_sync_pkg.models import AppointmentReportConfig, SyncConfig
from pf_sync_pkg.pdf_pipeline import (
    default_process_candidates,
    full_sync_on_page,
    process_records_concurrently,
    process_records_on_page,
)
from pf_sync_pkg.queue_admin import queue_status, reset_rows
from pf_sync_pkg.refresh import refresh_patient_latest_on_page
from pf_sync_pkg.report_pull import pull_appointment_report_on_page
from pf_sync_pkg.selftest import run_self_test
from pf_sync_pkg.store import append_run, atomic_write_json, finish_run, load_store, save_store, store_rows
from pf_sync_pkg.tabular import read_tabular_rows
from pf_sync_pkg.utils import clean, normalize_header, parse_date, practice_today, require_date


def add_browser_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--attach",
        action="store_true",
        help="Attach to Chrome already started with --remote-debugging-port.",
    )
    parser.add_argument(
        "--chrome-user-data-dir",
        default=os.path.join(os.getenv("USERPROFILE", ""), "pf_rpa_chrome")
        if os.getenv("USERPROFILE") else "",
        help=(
            "Dedicated reusable Practice Fusion Chrome profile directory. "
            "Defaults to %%USERPROFILE%%\\pf_rpa_chrome on Windows."
        ),
    )
    parser.add_argument(
        "--source-user-data-dir",
        default="",
        help="Real Chrome User Data root used only for the initial clone.",
    )
    parser.add_argument(
        "--source-profile",
        default="Profile 11",
        help="Chrome source profile folder, normally Profile 11.",
    )
    parser.add_argument(
        "--refresh-profile",
        action="store_true",
        help="Replace the dedicated profile with a fresh source profile copy.",
    )
    parser.add_argument("--chrome-exe", default="")
    parser.add_argument("--debug-port", default="9222")
    parser.add_argument(
        "--username",
        default=os.getenv("PF_USERNAME", ""),
        help="Practice Fusion username. Prefer the PF_USERNAME environment variable.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("PF_PASSWORD", ""),
        help="Practice Fusion password. Prefer PF_PASSWORD so it is not stored in shell history.",
    )
    parser.add_argument(
        "--typing-delay-ms",
        type=int,
        default=65,
        help="Delay between login keystrokes for reliable Ember input events (default 65 ms).",
    )
    parser.add_argument(
        "--login-timeout-seconds",
        type=int,
        default=900,
        help="Maximum time to wait for login/security verification (default 900).",
    )
    parser.add_argument("--keep-browser-open", action="store_true")


def add_report_dates(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report-date", default="", help="Single report day; overrides start/end.")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")


def resolve_report_dates(args: argparse.Namespace) -> Tuple[date, date]:
    if getattr(args, "report_date", ""):
        value = require_date(args.report_date, "report date")
        return value, value
    today = practice_today()
    start = require_date(args.start_date, "start date") if getattr(args, "start_date", "") else today
    end = require_date(args.end_date, "end date") if getattr(args, "end_date", "") else today
    return start, end


def require_browser_args(args: argparse.Namespace) -> None:
    if not args.attach and not args.chrome_user_data_dir:
        raise ValueError("--chrome-user-data-dir is required unless --attach is used.")


def browser_command_wrapper(args: argparse.Namespace, callback):
    require_browser_args(args)
    playwright = context = page = None
    try:
        playwright, context, page = build_browser(args)
        page = wait_for_pf_login(context, page, args)
        return callback(page)
    finally:
        if playwright is not None and context is not None and page is not None:
            close_browser(args, playwright, context, page)


def browser_command_wrapper_with_context(args: argparse.Namespace, callback):
    """Same as browser_command_wrapper, but also hands the callback the
    BrowserContext -- only needed by callers that open extra tabs of their
    own (context.new_page()) for concurrent processing, e.g.
    run_facesheet_pull_by_date's retry pass. Left as a separate function
    rather than changing browser_command_wrapper's signature so the other six
    single-tab commands (process/nightly/refresh/full-sync/full-sync-by-date/
    sync-schedules-by-date) are untouched."""
    require_browser_args(args)
    playwright = context = page = None
    try:
        playwright, context, page = build_browser(args)
        page = wait_for_pf_login(context, page, args)
        return callback(page, context)
    finally:
        if playwright is not None and context is not None and page is not None:
            close_browser(args, playwright, context, page)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Practice Fusion appointment, encounter, and SOAP PDF sync"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print the exact worker build ID.")
    sub.add_parser("self-test", help="Run local synthetic tests without Practice Fusion.")

    doctor = sub.add_parser("doctor", help="Validate local files/config/browser prerequisites.")
    doctor.add_argument("--config-json", required=True)
    doctor.add_argument("--report-config-json", required=True)
    doctor.add_argument("--patients-file", default="")
    doctor.add_argument("--queue-json", default="")
    doctor.add_argument(
        "--appointments-file",
        default="",
        help="Validate appointment column mapping before ingest.",
    )
    add_browser_arguments(doctor)

    pull = sub.add_parser("pull-report", help="Pull Appointment & eligibility report.")
    pull.add_argument("--report-config-json", required=True)
    pull.add_argument("--output-csv", required=True)
    add_report_dates(pull)
    add_browser_arguments(pull)

    # Full patient roster discover/scrape is a separate, standalone tool:
    # pf_sync_pkg/patient_scraper.py (run via pull_patients.py at the repo root).
    # It has its own argparse CLI (mode discover/scrape/both, age-bucket sweep,
    # Postgres/file-backed queue) rather than being folded into this parser, since its
    # shape (multi-phase, resumable, queue-driven) doesn't match the other one-shot
    # subcommands here.

    ingest = sub.add_parser("ingest", help="Upsert appointment CSV/XLSX/JSON into queue JSON.")
    ingest.add_argument("--appointments-file", "--appointments-csv", dest="appointments_file", required=True)
    ingest.add_argument("--queue-json", required=True)
    ingest.add_argument("--practice", required=True)
    ingest.add_argument("--source-report-name", default="")
    ingest.add_argument("--reset-existing", action="store_true")
    # v5.4: ingest now resolves the ignored-status gate through the same config that
    # process uses, so a customized ignored_statuses list applies to both.
    ingest.add_argument(
        "--config-json",
        default="",
        help="PDF sync config; supplies ignored_statuses so ingest and process share one gate.",
    )

    match = sub.add_parser("match-patients", help="Match appointment patients to patient registry.")
    match.add_argument("--queue-json", required=True)
    match.add_argument("--patients-file", "--patients-csv", dest="patients_file", required=True)
    match.add_argument("--fuzzy-threshold", type=float, default=0.82)
    match.add_argument(
        "--dob-match-threshold",
        type=float,
        default=0.85,
        help="Name threshold applied inside an exact-DOB bucket (default 0.85).",
    )
    match.add_argument("--rematch-all", action="store_true")

    resolve = sub.add_parser("resolve-patient", help="Manually resolve a needs_attention appointment.")
    resolve.add_argument("--queue-json", required=True)
    target = resolve.add_mutually_exclusive_group(required=True)
    target.add_argument("--row-id", default="")
    target.add_argument("--appointment-id", default="")
    # v5.4: --patient-id is no longer required. Supply either it (with --patients-file)
    # or --ehr-patient-guid; the GUID is what chart navigation needs.
    resolve.add_argument("--patient-id", default="")
    resolve.add_argument("--ehr-patient-guid", default="")
    resolve.add_argument("--patients-file", default="")
    resolve.add_argument("--resolved-patient-name", default="")

    process = sub.add_parser("process", help="Process ready/review appointments and create PDFs.")
    process.add_argument("--queue-json", required=True)
    process.add_argument("--config-json", required=True)
    process.add_argument("--downloads-dir", required=True)
    process.add_argument("--limit", type=int, default=0)
    process.add_argument("--dry-run", action="store_true")
    process.add_argument("--include-failed", action="store_true")
    process.add_argument(
        "--run-id", default="",
        help="Pass the same value across multiple 'process' calls that belong to one logical "
             "pull (e.g. resuming after an interruption) so they converge on one "
             "appointments_<run-id>.json manifest instead of each call producing its own "
             "fragment. Leave unset for the old per-call random-uuid manifest naming.",
    )
    add_browser_arguments(process)

    full_sync = sub.add_parser(
        "full-sync",
        help="Discover historical SOAP-note dates for patient registry rows and create PDFs.",
    )
    full_sync.add_argument("--queue-json", required=True)
    full_sync.add_argument("--config-json", required=True)
    full_sync.add_argument("--patients-file", required=True)
    full_sync.add_argument("--downloads-dir", required=True)
    full_sync.add_argument("--limit-patients", type=int, default=0)
    full_sync.add_argument("--max-encounters-per-patient", type=int, default=0)
    full_sync.add_argument("--dry-run", action="store_true")
    full_sync.add_argument("--rescrape-all", action="store_true")
    add_browser_arguments(full_sync)

    refresh = sub.add_parser("refresh", help="Refresh one appointment/encounter or newest patient encounter.")
    refresh.add_argument("--queue-json", required=True)
    refresh.add_argument("--config-json", required=True)
    refresh.add_argument("--downloads-dir", required=True)
    selector = refresh.add_mutually_exclusive_group(required=True)
    selector.add_argument("--row-id", default="")
    selector.add_argument("--appointment-id", default="")
    selector.add_argument("--encounter-id", default="")
    selector.add_argument(
        "--patient-id",
        default="",
        help="Practice Fusion PRN/record number (for example PE751838).",
    )
    selector.add_argument(
        "--ehr-patient-guid", "--patient-guid",
        dest="ehr_patient_guid",
        default="",
        help="Practice Fusion chart UUID from the patient URL.",
    )
    refresh.add_argument("--dry-run", action="store_true")
    add_browser_arguments(refresh)

    nightly = sub.add_parser("nightly", help="Pull -> ingest -> match -> process in one run.")
    nightly.add_argument("--queue-json", required=True)
    nightly.add_argument("--config-json", required=True)
    nightly.add_argument("--report-config-json", required=True)
    nightly.add_argument("--patients-file", required=True)
    nightly.add_argument("--downloads-dir", required=True)
    nightly.add_argument("--practice", required=True)
    nightly.add_argument("--appointments-file", default="", help="Use an existing report file instead of pulling PF.")
    nightly.add_argument("--report-output-csv", default="")
    nightly.add_argument("--limit", type=int, default=0)
    nightly.add_argument("--dry-run", action="store_true")
    nightly.add_argument("--include-failed", action="store_true")
    nightly.add_argument("--fuzzy-threshold", type=float, default=0.82)
    nightly.add_argument("--dob-match-threshold", type=float, default=0.85)
    add_report_dates(nightly)
    add_browser_arguments(nightly)

    full_sync_by_date = sub.add_parser(
        "full-sync-by-date",
        help="Discover (Schedule-scoped, full-sweep fallback) -> merge registry -> pull -> ingest -> match -> process, in one run.",
    )
    full_sync_by_date.add_argument("--queue-json", required=True)
    full_sync_by_date.add_argument("--config-json", required=True)
    full_sync_by_date.add_argument("--report-config-json", required=True)
    full_sync_by_date.add_argument("--patients-file", required=True)
    full_sync_by_date.add_argument("--downloads-dir", required=True)
    full_sync_by_date.add_argument("--practice", required=True)
    full_sync_by_date.add_argument("--report-output-csv", default="")
    full_sync_by_date.add_argument("--limit", type=int, default=0)
    full_sync_by_date.add_argument("--dry-run", action="store_true")
    full_sync_by_date.add_argument("--include-failed", action="store_true")
    full_sync_by_date.add_argument("--fuzzy-threshold", type=float, default=0.82)
    full_sync_by_date.add_argument("--dob-match-threshold", type=float, default=0.85)
    add_report_dates(full_sync_by_date)
    add_browser_arguments(full_sync_by_date)

    facesheet_pull_by_date = sub.add_parser(
        "facesheet-pull-by-date",
        help=(
            "Discover (Schedule-scoped) -> pull-report -> ingest -> match against the live "
            "discovery -> process, forcing every Facesheet section on for this run only -- the "
            "default for process/nightly/refresh/full-sync stays notes-only; full-sync-by-date's "
            "default is notes + demographics + active insurance. No patients_file CSV: "
            "schedule discovery resolves real patient GUIDs directly from PF, in memory, and "
            "those become the match registry. The whole-practice-scrape fallback is blocked "
            "since a single date has no business triggering that."
        ),
    )
    facesheet_pull_by_date.add_argument("--queue-json", required=True)
    facesheet_pull_by_date.add_argument("--config-json", required=True)
    facesheet_pull_by_date.add_argument("--report-config-json", required=True)
    facesheet_pull_by_date.add_argument("--downloads-dir", required=True)
    facesheet_pull_by_date.add_argument("--practice", required=True)
    facesheet_pull_by_date.add_argument("--report-output-csv", default="")
    facesheet_pull_by_date.add_argument("--limit", type=int, default=0)
    facesheet_pull_by_date.add_argument("--dry-run", action="store_true")
    facesheet_pull_by_date.add_argument("--include-failed", action="store_true")
    facesheet_pull_by_date.add_argument("--fuzzy-threshold", type=float, default=0.82)
    facesheet_pull_by_date.add_argument("--dob-match-threshold", type=float, default=0.85)
    facesheet_pull_by_date.add_argument(
        "--retry-concurrency", type=int, default=3,
        help=(
            "Extra Chrome tabs (same logged-in session) to run the failed/review retry pass "
            "across concurrently. 1 = today's single-tab behavior. Not yet validated against "
            "PF's own tolerance for simultaneous tabs -- keep this small (2-3) until proven out."
        ),
    )
    add_report_dates(facesheet_pull_by_date)
    add_browser_arguments(facesheet_pull_by_date)

    sync_schedules_by_date = sub.add_parser(
        "sync-schedules-by-date",
        help=(
            "Standalone catch-up pass, independent of the Eligibility Report pull/ingest/"
            "match pipeline: walks the Schedule for the given date range, keeps every "
            "appointment marked Seen there, diffs against the queue's existing "
            "(patient, date) pairs, and injects + processes a synthetic record straight "
            "from the patient chart for whatever PF's report hasn't caught up on yet. "
            "With no explicit date given, defaults to today plus --lookback-days back, so "
            "a status that flips to Seen a few days late still gets picked up next call "
            "without having to remember which past date to re-check."
        ),
    )
    sync_schedules_by_date.add_argument("--queue-json", required=True)
    sync_schedules_by_date.add_argument("--config-json", required=True)
    sync_schedules_by_date.add_argument("--schedule-config-json", default="")
    sync_schedules_by_date.add_argument("--downloads-dir", required=True)
    sync_schedules_by_date.add_argument("--practice", required=True)
    sync_schedules_by_date.add_argument("--limit", type=int, default=0)
    sync_schedules_by_date.add_argument("--dry-run", action="store_true")
    sync_schedules_by_date.add_argument("--include-failed", action="store_true")
    sync_schedules_by_date.add_argument(
        "--lookback-days", type=int, default=3,
        help="Only applied when --report-date/--start-date/--end-date are all omitted: "
             "scans [today - lookback_days, today] instead of just today, so a patient "
             "missed on a prior day still gets caught on a later call. Explicit dates "
             "always win over this default.",
    )
    sync_schedules_by_date.add_argument(
        "--retry-concurrency", type=int, default=3,
        help=(
            "Extra Chrome tabs (same logged-in session) to run the failed/review retry pass "
            "across concurrently. 1 = single-tab behavior. Not yet validated against PF's own "
            "tolerance for simultaneous tabs -- keep this small (2-3) until proven out."
        ),
    )
    add_report_dates(sync_schedules_by_date)
    add_browser_arguments(sync_schedules_by_date)

    appointments_by_date = sub.add_parser(
        "appointments-by-date",
        help=(
            "Read-only Schedule lookup: returns every appointment scraped off the "
            "Schedule 'Appointments' view for [--start-date, --end-date] (today for "
            "either side left blank -- see resolve_report_dates). Appointments only "
            "-- never opens a patient's chart, never pulls a facesheet/SOAP note, "
            "never touches the queue."
        ),
    )
    appointments_by_date.add_argument("--schedule-config-json", default="")
    appointments_by_date.add_argument(
        "--output-json", default="",
        help=(
            "Write the result to this JSON file too (not just the return value/HTTP "
            "response). Defaults to appointments_by_date_<start>_to_<end>.json in the "
            "current directory when left blank."
        ),
    )
    add_report_dates(appointments_by_date)
    add_browser_arguments(appointments_by_date)

    status = sub.add_parser("status", help="Show queue counts and unresolved/review rows.")
    status.add_argument("--queue-json", required=True)
    status.add_argument("--show-limit", type=int, default=20)

    reset = sub.add_parser("reset", help="Reset rows to ready for a repeat test.")
    reset.add_argument("--queue-json", required=True)
    selector = reset.add_mutually_exclusive_group(required=True)
    selector.add_argument("--row-id", default="")
    selector.add_argument("--appointment-id", default="")
    selector.add_argument("--patient-id", default="")
    selector.add_argument("--all-processed", action="store_true")

    zip_upload = sub.add_parser(
        "zip-upload",
        help="Zip an existing appointments manifest + its PDFs and upload to rcm-attachments.",
    )
    zip_upload.add_argument("--manifest-json", required=True, help="Path to an appointments_<uuid>.json manifest written by a prior 'process' run.")
    zip_upload.add_argument("--downloads-dir", required=True)
    zip_upload.add_argument("--practice", required=True)
    zip_upload.add_argument("--no-upload", action="store_true", help="Build the zip locally but skip the Azure upload (inspect before sending).")
    zip_upload.add_argument(
        "--keep-local",
        action="store_true",
        help="Keep the source PDFs and the zip on disk after a confirmed upload "
             "(default: delete them once Azure has the zip, so PHI doesn't linger locally).",
    )

    write_config = sub.add_parser("write-config", help="Write the complete PDF config.")
    write_config.add_argument("--config-json", required=True)
    write_report_config = sub.add_parser("write-report-config", help="Write the report config.")
    write_report_config.add_argument("--report-config-json", required=True)
    write_schedule_config = sub.add_parser(
        "write-schedule-config",
        help="Write the Schedule-view scrape config (sync-schedules-by-date's --schedule-config-json).",
    )
    write_schedule_config.add_argument("--schedule-config-json", required=True)

    return parser


def run_doctor(args: argparse.Namespace) -> int:
    errors = []
    warnings = []
    print(f"PDF config path: {os.path.abspath(args.config_json)}")
    print(f"Report config path: {os.path.abspath(args.report_config_json)}")
    try:
        sync_config = SyncConfig.load(args.config_json)
        required = {
            "print_chart_button_selector": sync_config.print_chart_button_selector,
            "print_modal_ready_selectors": sync_config.print_modal_ready_selectors,
            "facesheet_checkbox_selectors": sync_config.facesheet_checkbox_selectors,
            "print_modal_select_none_selector": sync_config.print_modal_select_none_selector,
            "notes_group_checkbox_selector": sync_config.notes_group_checkbox_selector,
            "generate_pdf_button_selector": sync_config.generate_pdf_button_selector,
            "printable_preview_ready_selector": sync_config.printable_preview_ready_selector,
        }
        errors.extend([f"Missing sync config: {key}" for key, value in required.items() if not value])
    except Exception as exc:
        errors.append(f"Could not load PDF config: {exc}")
    try:
        report_config = AppointmentReportConfig.load(args.report_config_json)
        required = {
            "reports_menu_selector": report_config.reports_menu_selector,
            "appointment_report_link_selector": report_config.appointment_report_link_selector,
            "report_start_date_selector": report_config.report_start_date_selector,
            "report_end_date_selector": report_config.report_end_date_selector,
            "run_report_button_selectors": report_config.run_report_button_selectors,
        }
        errors.extend([f"Missing report config: {key}" for key, value in required.items() if not value])
    except Exception as exc:
        errors.append(f"Could not load report config: {exc}")
    # v5.4: doctor now maps the supplied appointment report. Previously it validated
    # configs, Chrome and the registry but never touched the report file, so a column
    # mapping failure only surfaced two steps later at ingest.
    if getattr(args, "appointments_file", ""):
        try:
            source_rows = read_tabular_rows(args.appointments_file)
            if not source_rows:
                warnings.append(f"Appointment report has no data rows: {args.appointments_file}")
            else:
                mapped_rows = [map_appointment_row(row) for row in source_rows]
                headers = [clean(key) for key in source_rows[0].keys() if clean(key)]
                print(f"Appointment report: {len(source_rows)} rows, headers {headers}")
                blank = [
                    label
                    for field_name, label in REQUIRED_APPOINTMENT_FIELDS.items()
                    if not any(clean(row.get(field_name, "")) for row in mapped_rows)
                ]
                if blank:
                    errors.append(
                        "Appointment column mapping produced no values for: "
                        + ", ".join(blank)
                        + f". Normalized headers: {sorted({normalize_header(h) for h in headers})}"
                    )
                else:
                    sample = mapped_rows[0]
                    print(
                        "  mapped sample: "
                        f"date={sample['appointment_date']!r}, status={sample['appointment_status']!r}, "
                        f"type={sample['appointment_type']!r}, provider={sample['provider']!r}, "
                        f"phone={sample['patient_phone']!r}"
                    )
                for field_name, label in OPTIONAL_APPOINTMENT_FIELDS.items():
                    if not any(clean(row.get(field_name, "")) for row in mapped_rows):
                        warnings.append(f"No source column mapped to {label}.")
        except Exception as exc:
            errors.append(f"Appointment report check failed: {type(exc).__name__}: {exc}")

    if args.patients_file:
        try:
            registry = load_patient_registry(args.patients_file)
            count = len(registry)
            if count == 0:
                warnings.append("Patient registry loaded but contains zero usable patient IDs/GUIDs.")
            else:
                without_prn = sum(1 for item in registry if not item["patient_id"])
                without_dob = sum(1 for item in registry if not item["dob"])
                print(f"Patient registry: {count} usable rows")
                if without_prn:
                    print(
                        f"  {without_prn} rows have no patient_id/PRN "
                        "(matched by GUID; PDFs will be named using the GUID)"
                    )
                if without_dob:
                    warnings.append(
                        f"{without_dob} registry rows have no parseable DOB and cannot use "
                        "DOB-first matching."
                    )
        except Exception as exc:
            errors.append(f"Patient registry failed: {exc}")
    if args.queue_json and os.path.exists(args.queue_json):
        try:
            print(f"Queue rows: {len(store_rows(load_store(args.queue_json)))}")
        except Exception as exc:
            errors.append(f"Queue JSON failed: {exc}")
    if not args.attach:
        if not args.chrome_user_data_dir:
            warnings.append("No --chrome-user-data-dir supplied; browser commands will require it.")
        try:
            chrome = find_chrome_exe(args.chrome_exe)
            print(f"Chrome: {chrome}")
        except SystemExit as exc:
            errors.append(str(exc))
    print(f"CSV field limit: {CSV_FIELD_LIMIT}")
    print(f"Practice timezone: {PRACTICE_TZ_NAME} (today = {practice_today().isoformat()})")
    print("Playwright import: OK")
    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("DOCTOR PASSED")
    return 0


def run_refresh(args: argparse.Namespace) -> dict:
    """Refresh one appointment/encounter or the newest encounter for a patient.

    Factored out of main() so server.py's /refresh endpoint can reuse the exact same
    patient_id/ehr_patient_guid vs row/appointment/encounter-id selection logic the
    CLI uses, instead of reimplementing it.
    """
    store = load_store(args.queue_json)
    rows = store_rows(store)
    config = SyncConfig.load(args.config_json)

    def callback(page: Page):
        if args.patient_id or args.ehr_patient_guid:
            return refresh_patient_latest_on_page(
                page,
                args.queue_json,
                config,
                args.downloads_dir,
                args.patient_id,
                args.ehr_patient_guid,
                args.dry_run,
                store,
                rows,
            )
        candidates = select_queue_rows(
            rows,
            row_id=args.row_id,
            appointment_id=args.appointment_id,
            encounter_id=args.encounter_id,
        )
        if not candidates:
            raise ValueError("No queue row matched the refresh selector.")
        return process_records_on_page(
            page,
            args.queue_json,
            config,
            args.downloads_dir,
            [max(candidates, key=lambda record: parse_date(record.appointment_date) or date.min)],
            rows,
            store,
            1,
            args.dry_run,
            True,
        )

    return browser_command_wrapper(args, callback)


def run_nightly(
    args: argparse.Namespace,
    config: "SyncConfig | None" = None,
    manifest_run_id: str = "",
) -> dict:
    """Pull -> ingest -> match -> process, factored out of main() so server.py's
    /nightly endpoint can call the exact same orchestration the CLI uses.

    config: pass an already-built SyncConfig to use in place of loading
    args.config_json fresh -- run_facesheet_pull_by_date uses this to force every
    Facesheet section on for one call without touching the on-disk config default
    (which stays notes-only).

    manifest_run_id: passed straight through to process_records_on_page. Left blank
    by default so plain /nightly calls keep their existing per-call random-UUID
    manifest naming; run_facesheet_pull_by_date passes a date-scoped value so
    repeated calls for the same date converge on one manifest instead of each
    producing its own fragment (see write_appointments_metadata_json's docstring).
    """
    start_date, end_date = resolve_report_dates(args)
    report_file = args.appointments_file
    if not report_file:
        report_file = args.report_output_csv or (
            f"appointments_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"
        )

    config = config or SyncConfig.load(args.config_json)
    report_config = AppointmentReportConfig.load(args.report_config_json)

    def callback(page: Page):
        if not args.appointments_file:
            pull_result = pull_appointment_report_on_page(
                page, report_config, start_date, end_date, report_file
            )
            print("Report pull:", json.dumps(pull_result, indent=2))
        ingest_counts = ingest_appointments(
            report_file, args.queue_json, args.practice, config=config
        )
        print("Ingest:", json.dumps(ingest_counts, indent=2))
        match_counts = match_patients(
            args.queue_json,
            args.patients_file,
            args.fuzzy_threshold,
            dob_match_threshold=args.dob_match_threshold,
        )
        print("Patient matching:", json.dumps(match_counts, indent=2))

        # Reload after ingest/match because those functions persist a new store.
        store = load_store(args.queue_json)
        rows = store_rows(store)
        candidates = default_process_candidates(rows, args.include_failed)
        counts = process_records_on_page(
            page,
            args.queue_json,
            config,
            args.downloads_dir,
            candidates,
            rows,
            store,
            args.limit,
            args.dry_run,
            False,
            manifest_run_id,
        )
        return {
            "report_file": report_file,
            "ingest": ingest_counts,
            "match": match_counts,
            "process": counts,
        }

    return browser_command_wrapper(args, callback)


def build_full_sync_by_date_config(args: argparse.Namespace) -> SyncConfig:
    """SyncConfig for full-sync-by-date: the appointment-date SOAP note (as configured)
    plus Patient demographics and Patient insurance -- filtered to Active insurance only --
    on every printed chart.

    v5.19: full-sync-by-date is the one command this applies to by default. process,
    nightly, refresh, and plain full-sync all keep reading the on-disk config's notes-only
    default untouched (see prepare_print_chart_sections/include_facesheet_sections). This
    never edits the on-disk config file -- same pattern run_facesheet_pull_by_date already
    uses to force facesheet sections on for one call without changing everyone else's
    default, except scoped to just demographics + insurance rather than every section.
    """
    base_config = SyncConfig.load(args.config_json)
    return replace(
        base_config,
        include_facesheet_sections=True,
        facesheet_checkbox_selectors=[
            "[data-element='chk-patient-demographics'] input[type='checkbox']",
            "[data-element='print-insurance-options'] input[type='checkbox']",
        ],
    )


def run_full_sync_by_date(
    args: argparse.Namespace,
    config: "SyncConfig | None" = None,
    allow_full_sweep_fallback: bool = True,
) -> dict:
    """One-call pipeline: discover (Schedule-scoped, falling back to the full
    age-bucket sweep if that fails) -> merge into the canonical patients
    registry -> pull-report -> ingest -> match-patients -> process.

    This exists because every actual mistake in a manual run this repo has hit
    traces back to a human doing these steps one at a time: a stale/wrong
    registry file left over from a previous session, forgetting to re-run
    match-patients after refreshing it, or not knowing which of several
    scattered CSV copies was current. One call resolves all of that in a fixed
    order, every time.

    config: pass an already-built SyncConfig to use in place of the default this function
    builds itself (build_full_sync_by_date_config: notes + demographics + active insurance).
    run_facesheet_pull_by_date uses this to force every Facesheet section on for one call
    instead.

    allow_full_sweep_fallback: when discovery genuinely comes back empty, the
    default (True) falls back to a full, live scrape of every patient in the
    practice. run_facesheet_pull_by_date passes False -- a single date's
    facesheet pull has no business kicking off a whole-practice scrape just
    because that one date's Schedule view came back empty; it reports that
    instead of firing the expensive fallback.

    Each stage is wrapped individually so ONE stage failing surfaces in the
    returned dict instead of taking down the whole request -- the goal is "do
    everything possible and report exactly what happened", not "all-or-nothing".
    Discovery specifically tries the fast, date-scoped method first and only
    falls back to the slow full sweep if that genuinely comes back empty or
    errors -- see patient_scraper.discover_via_schedule_range's own docstring
    for why the fast path can legitimately return 0 for the two literal
    non-appointment days it starts on, which is not itself a failure.
    """
    from pf_sync_pkg import patient_scraper as ps

    start_date, end_date = resolve_report_dates(args)
    stages: dict = {}

    def callback(page: Page):
        # --- Stage 1: discover -----------------------------------------
        discovered: Dict[str, "ps.ReportPatient"] = {}
        try:
            discovered = ps.discover_via_schedule_range(page, start_date, end_date)
            stages["discover"] = {"method": "schedule_range", "unique_patients": len(discovered)}
        except Exception as exc:
            stages["discover"] = {"method": "schedule_range", "error": f"{type(exc).__name__}: {exc}"}

        if not discovered and not allow_full_sweep_fallback:
            stages["discover_fallback"] = {
                "skipped": True,
                "reason": "allow_full_sweep_fallback=False -- schedule discovery came back "
                          "empty, but a whole-practice scrape was not triggered.",
            }
        elif not discovered:
            # Fallback: the full age-bucket sweep is slower but doesn't depend
            # on the Schedule view at all -- a different code path, so a bug or
            # PF UI change specific to one doesn't take out both.
            try:
                ps.open_patient_list_report(page)
                seed_rows = ps.collect_all_report_patients_bucketed(
                    page, start=0, stop=120, size=5, max_empty=0, limit=0,
                    checkpoint_path=args.patients_file + ".discover_fallback.checkpoint.json",
                )
                discovered = {r.ehr_patient_guid: r for r in seed_rows if r.ehr_patient_guid}
                stages["discover_fallback"] = {"method": "full_age_sweep", "unique_patients": len(discovered)}
            except Exception as exc:
                stages["discover_fallback"] = {"method": "full_age_sweep", "error": f"{type(exc).__name__}: {exc}"}

        # --- Stage 2: merge into the canonical registry (never wipe it) -
        try:
            existing = ps.load_existing_rows(args.patients_file)
            merged = dict(existing)
            for guid, rp in discovered.items():
                row = merged.get(guid, {col: "" for col in ps.CSV_COLUMNS})
                row["id"] = guid
                row["ehr_patient_guid"] = guid
                row["ehr_patient_url"] = rp.ehr_patient_url or row.get("ehr_patient_url", "")
                full_name = f"{rp.first_name} {rp.last_name}".strip()
                if full_name:
                    row["patient_name"] = full_name
                if rp.dob:
                    row["dob"] = rp.dob
                if rp.preferred_contact and not row.get("mobile_phone"):
                    row["mobile_phone"] = rp.preferred_contact
                merged[guid] = row
            ps.rewrite_csv(args.patients_file, merged)
            stages["registry_merge"] = {
                "total_patients_on_file": len(merged),
                "added_or_refreshed_this_run": len(discovered),
            }
        except Exception as exc:
            stages["registry_merge"] = {"error": f"{type(exc).__name__}: {exc}"}
            # Matching would run against a possibly-stale/missing registry from
            # here on -- still proceed (best-effort), the caller can see this
            # failure in the response and re-run if it matters for their case.

        # --- Stage 3: pull-report -> ingest -> match -> process ---------
        report_file = args.report_output_csv or (
            f"appointments_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"
        )
        nonlocal config
        config = config or build_full_sync_by_date_config(args)
        report_config = AppointmentReportConfig.load(args.report_config_json)

        try:
            stages["pull_report"] = pull_appointment_report_on_page(
                page, report_config, start_date, end_date, report_file
            )
        except Exception as exc:
            stages["pull_report"] = {"error": f"{type(exc).__name__}: {exc}"}
            return stages  # nothing downstream can run without the report

        try:
            stages["ingest"] = ingest_appointments(
                report_file, args.queue_json, args.practice, config=config
            )
        except Exception as exc:
            stages["ingest"] = {"error": f"{type(exc).__name__}: {exc}"}
            return stages  # nothing downstream can run without an ingested queue

        try:
            stages["match"] = match_patients(
                args.queue_json, args.patients_file, args.fuzzy_threshold,
                dob_match_threshold=args.dob_match_threshold,
            )
        except Exception as exc:
            stages["match"] = {"error": f"{type(exc).__name__}: {exc}"}
            # process still runs below against whatever matched on a prior run.

        try:
            store = load_store(args.queue_json)
            rows = store_rows(store)
            candidates = default_process_candidates(rows, args.include_failed)
            # Stable across every invocation of THIS pull (same queue file + same
            # date range) -- if full-sync-by-date gets interrupted and re-run for
            # the same range, or a caller retries, every process() call converges
            # on one manifest instead of each producing its own fragment (see
            # write_appointments_metadata_json's docstring).
            manifest_run_id = f"{Path(args.queue_json).stem}_{start_date.isoformat()}_to_{end_date.isoformat()}"
            stages["process"] = process_records_on_page(
                page, args.queue_json, config, args.downloads_dir,
                candidates, rows, store, args.limit, args.dry_run, False,
                manifest_run_id, use_timeline_fallback=True,
            )
        except Exception as exc:
            stages["process"] = {"error": f"{type(exc).__name__}: {exc}"}

        # --- Stage 4: zip the PDFs this run produced + upload to rcm-attachments
        if args.dry_run:
            stages["rcm_upload"] = {"skipped": True, "reason": "dry_run=True -- no PDFs were generated"}
        else:
            from pf_sync_pkg.rcm_upload import build_and_upload_zip, retry_orphaned_zips

            # Retry any zip a PRIOR run built but failed to deliver before touching
            # today's records -- otherwise a failed upload is orphaned forever,
            # since build_and_upload_zip below only ever looks at this run's own
            # manifest. See retry_orphaned_zips' docstring.
            try:
                stages["rcm_upload_retry"] = retry_orphaned_zips(args.downloads_dir)
            except Exception as exc:
                stages["rcm_upload_retry"] = {"error": f"{type(exc).__name__}: {exc}"}

            manifest_path = ""
            process_result = stages.get("process")
            if isinstance(process_result, dict):
                manifest_path = process_result.get("metadata_manifest_path", "")
            try:
                stages["rcm_upload"] = build_and_upload_zip(
                    manifest_path, args.downloads_dir, args.practice,
                )
            except Exception as exc:
                stages["rcm_upload"] = {"error": f"{type(exc).__name__}: {exc}"}

        return stages

    return browser_command_wrapper(args, callback)


def resolve_sync_schedules_dates(args: argparse.Namespace) -> Tuple[date, date]:
    """Same as resolve_report_dates, except with no explicit date at all (no
    --report-date/--start-date/--end-date), it defaults to a LOOKBACK WINDOW
    [today - lookback_days, today] instead of just today.

    Why: this command's whole purpose is catching a patient who was Confirmed
    (not yet Seen, correctly skipped) on a prior call and only shows Seen a few
    days later -- if every un-dated call only ever looked at "today", that
    patient's earlier visit date would never get re-checked once today moves
    past it. An explicit date/range always wins over this default -- it only
    fires when the caller passed nothing at all.
    """
    if getattr(args, "report_date", "") or getattr(args, "start_date", "") or getattr(args, "end_date", ""):
        return resolve_report_dates(args)
    today = practice_today()
    lookback = max(0, getattr(args, "lookback_days", 0) or 0)
    return today - timedelta(days=lookback), today


def run_sync_schedules_by_date(
    args: argparse.Namespace,
    config: "SyncConfig | None" = None,
) -> dict:
    """Standalone catch-up pass, deliberately independent of full-sync-by-date's
    report pull/ingest/match pipeline: walks the Schedule for [start_date,
    end_date] via discover_appointments_via_schedule_range (one entry per VISIT,
    not per patient -- see that function's docstring for why
    discover_via_schedule_range itself can't be reused here), keeps only the
    appointments actually marked Seen there, diffs those against the queue's
    existing (ehr_patient_guid, appointment_date) pairs, and injects + processes
    a synthetic QueueRecord straight from the patient chart for whatever's
    missing -- Practice Fusion's own Eligibility Report never enters into it.

    Exists as its own command/endpoint (not folded into full-sync-by-date as an
    extra stage) so it can be re-run on its own rolling window -- see
    resolve_sync_schedules_dates's docstring for why the default date range IS
    that rolling window, not just "today" -- to catch a status that flips from
    Confirmed to Seen a few days late, without re-pulling/re-ingesting/re-matching
    the whole Eligibility Report every time just to check.

    A patient with two Seen visits in the requested range gets TWO synthetic
    records, one per visit date -- discover_appointments_via_schedule_range keeps
    every row scraped, and the (guid, date) key below treats each date
    independently, so neither visit shadows the other.

    Every Schedule-view selector this uses comes from ScheduleScrapeConfig
    (args.schedule_config_json), not a hardcoded literal -- see that dataclass's
    docstring in models.py.
    """
    from pf_sync_pkg import patient_scraper as ps
    from pf_sync_pkg.models import QueueRecord, ScheduleScrapeConfig
    from pf_sync_pkg.utils import is_seen_status

    start_date, end_date = resolve_sync_schedules_dates(args)
    schedule_config = ScheduleScrapeConfig.load(getattr(args, "schedule_config_json", ""))
    stages: dict = {}

    def callback(page: Page, context):
        nonlocal config
        config = config or build_full_sync_by_date_config(args)

        try:
            appointments = ps.discover_appointments_via_schedule_range(
                page, start_date, end_date, config=schedule_config
            )
            stages["discover"] = {
                "method": "schedule_range_per_visit",
                "date_range": f"{start_date.isoformat()}_to_{end_date.isoformat()}",
                "rows_scraped": len(appointments),
            }
        except Exception as exc:
            stages["discover"] = {"error": f"{type(exc).__name__}: {exc}"}
            return stages

        try:
            store = load_store(args.queue_json)
            rows_for_inject = store_rows(store)
            # rows_for_inject is a List[QueueRecord] (dataclass instances, no
            # .get()) -- attribute access, not dict-style .get(). Keyed on
            # (guid, appointment_date), NOT guid alone: ingest_appointments' own
            # record_key() dedupes per-appointment (practice+name+dob+date+
            # provider), so a repeat patient legitimately gets one row per visit
            # date -- a guid-only check here would see ANY prior row for that
            # patient, from a completely different date, and wrongly treat
            # today's visit as already covered.
            ingested_guid_dates = {
                (row.ehr_patient_guid, parse_date(row.appointment_date))
                for row in rows_for_inject
                if row.ehr_patient_guid
            }

            to_inject = []
            not_seen_skipped = []
            for appt in appointments:
                # Scoped to THIS filtering pass only -- deliberately not named
                # `guid` (a prior version did, and that name leaking into the
                # unrelated loop below that builds synthetic_rows was exactly
                # the incident this comment is warning against).
                #
                # Dedup here is GUID-only, on purpose: this GUID comes straight
                # off Practice Fusion's own href on the Schedule page -- it's
                # authoritative, not fuzzy-derived, which is this whole
                # endpoint's reason to exist (see SYNC_SCHEDULES_BY_DATE.md).
                # A fuzzy name-based duplicate check was tried and reverted the
                # same day: it re-introduces exactly the identity fragility
                # this endpoint is built to avoid, and risks the opposite
                # failure -- wrongly treating a patient's genuinely separate
                # visit, or a different patient with a similar name, as
                # "already covered". The real 2026-08-21 incident was a plain
                # stale-loop-variable bug (see ehr_patient_guid=rp.... below),
                # not a GUID reliability problem, and needed a code fix, not a
                # name-matching safety net.
                candidate_guid = appt.patient.ehr_patient_guid
                if not candidate_guid or (candidate_guid, appt.appointment_date) in ingested_guid_dates:
                    continue
                if is_seen_status(appt.patient.appointment_status, config):
                    to_inject.append(appt)
                else:
                    # Discovered + missing from the report but not Seen yet is
                    # expected, not a bug -- the report just hasn't run for a
                    # still-upcoming visit. Visible, not silently dropped.
                    not_seen_skipped.append(appt)

            synthetic_rows = []
            for appt in to_inject:
                rp = appt.patient
                appt_date = appt.appointment_date.isoformat()
                if rp.appointment_start_time:
                    # Same combined date+time shape ingest.COLUMN_ALIASES documents
                    # for the report's own AppointmentTime column; parse_date
                    # tolerates the trailing time token fine.
                    appt_date = f"{appt_date} {rp.appointment_start_time}"
                synthetic_rows.append(
                    QueueRecord(
                        row_id=str(uuid.uuid4()),
                        practice=args.practice,
                        # rp.ehr_patient_guid, NOT the outer filtering loop's `guid` --
                        # that variable belongs to the loop above (building
                        # to_inject/not_seen_skipped) and holds whatever appt was
                        # last iterated there, not this appt. Referencing it here
                        # stamped EVERY synthetic record in a batch with the SAME
                        # stale guid -- confirmed live 2026-08-21: ~40 different
                        # patients' rows all got one patient's real GUID, so every
                        # one of them opened THAT patient's chart and pulled THEIR
                        # SOAP note under a different name. See
                        # SYNC_SCHEDULES_BY_DATE.md's incident writeup.
                        ehr_patient_guid=rp.ehr_patient_guid,
                        patient_name=f"{rp.first_name} {rp.last_name}".strip(),
                        patient_dob=rp.dob,
                        appointment_date=appt_date,
                        appointment_status=rp.appointment_status or "seen",
                        appointment_type=rp.appointment_type,
                        provider=rp.provider_name,
                        # Deliberately blank, not a bug: unlike DOB, the Schedule
                        # page's row DOM never exposes facility/location at all --
                        # only the Eligibility Report's "Facility" column has it,
                        # and this whole flow exists to bypass that report.
                        service_location="",
                        patient_id=rp.patient_id,
                        patient_match_status="matched",  # Already matched by GUID
                        patient_match_method="discovered_from_schedule",
                        status="ready",
                        status_reason="discovered_from_schedule_not_in_report",
                    )
                )

            if synthetic_rows:
                rows_for_inject.extend(synthetic_rows)
                save_store(args.queue_json, store, rows_for_inject)

            stages["inject_discovered"] = {
                "synthetic_records_created": len(synthetic_rows),
                "discovered_visits_not_in_report": [
                    f"{appt.patient.first_name} {appt.patient.last_name} "
                    f"on {appt.appointment_date.isoformat()} (GUID: {appt.patient.ehr_patient_guid[:8]}...)"
                    for appt in to_inject
                ],
                "not_seen_skipped": [
                    f"{appt.patient.first_name} {appt.patient.last_name} "
                    f"on {appt.appointment_date.isoformat()} "
                    f"({appt.patient.appointment_status or 'no status read'})"
                    for appt in not_seen_skipped
                ],
            }
        except Exception as exc:
            stages["inject_discovered"] = {"error": f"{type(exc).__name__}: {exc}"}
            return stages

        try:
            store = load_store(args.queue_json)
            rows = store_rows(store)
            candidates = default_process_candidates(rows, args.include_failed)
            manifest_run_id = (
                f"{Path(args.queue_json).stem}_sync_schedules_"
                f"{start_date.isoformat()}_to_{end_date.isoformat()}"
            )
            # Neither Summary nor Timeline is checked for encounter existence here:
            # Summary can lag behind Schedule (that lag is this endpoint's reason
            # to exist), and Timeline hangs PF (got the automation stuck live on
            # 2026-08-21, same reason nightly never uses it -- see
            # find_encounter_for_appointment's docstring). Instead we skip straight
            # to the Print Chart modal and let its own SOAP-note list answer the
            # question: found for this date -> print it; not found -> fall back to
            # the most recent note dated on/before the appointment date (never a
            # future-dated note -- explicit user decision, 2026-08-21, see
            # chart_ui.select_soap_note_for_date). Still nothing on/before that
            # date -> record goes to "review" and is retried on a later poll.
            # full-sync-by-date (line ~794 above) is untouched and still uses the
            # Summary/Timeline pre-check with no most-recent fallback.
            stages["process"] = process_records_on_page(
                page, args.queue_json, config, args.downloads_dir,
                candidates, rows, store, args.limit, args.dry_run, False,
                manifest_run_id, use_timeline_fallback=False, skip_encounter_lookup=True,
                allow_most_recent_note_fallback=True,
            )
        except Exception as exc:
            stages["process"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Retry failed/review rows from THIS run immediately, same run, before
        # the zip goes out -- otherwise a row that failed here just sits until
        # someone happens to trigger a whole new run. Run across a few extra
        # tabs (context.new_page(), same logged-in session) concurrently --
        # see process_records_concurrently's docstring for why this is safe.
        # Bounded to one retry pass: a row that fails again goes out as
        # failed/review in the response same as today, to be picked up by the
        # next scheduled run rather than looping here indefinitely.
        try:
            store = load_store(args.queue_json)
            rows = store_rows(store)
            retry_candidates = [r for r in rows if r.status in {"failed", "review"}]
            if retry_candidates:
                print(
                    f"Retrying {len(retry_candidates)} failed/review row(s) from this run "
                    f"across up to {args.retry_concurrency} tab(s) before upload...",
                    flush=True,
                )
                stages["process_retry"] = process_records_concurrently(
                    context, args.queue_json, config, args.downloads_dir,
                    retry_candidates, rows, store, manifest_run_id,
                    use_timeline_fallback=False, skip_encounter_lookup=True,
                    allow_most_recent_note_fallback=True, dry_run=args.dry_run,
                    concurrency=args.retry_concurrency,
                )
        except Exception as exc:
            stages["process_retry"] = {"error": f"{type(exc).__name__}: {exc}"}

        if args.dry_run:
            stages["rcm_upload"] = {"skipped": True, "reason": "dry_run=True -- no PDFs were generated"}
        else:
            from pf_sync_pkg.rcm_upload import build_and_upload_zip, retry_orphaned_zips

            try:
                stages["rcm_upload_retry"] = retry_orphaned_zips(args.downloads_dir)
            except Exception as exc:
                stages["rcm_upload_retry"] = {"error": f"{type(exc).__name__}: {exc}"}

            manifest_path = ""
            process_result = stages.get("process")
            if isinstance(process_result, dict):
                manifest_path = process_result.get("metadata_manifest_path", "")
            try:
                stages["rcm_upload"] = build_and_upload_zip(
                    manifest_path, args.downloads_dir, args.practice,
                )
            except Exception as exc:
                stages["rcm_upload"] = {"error": f"{type(exc).__name__}: {exc}"}

        return stages

    return browser_command_wrapper_with_context(args, callback)


def run_appointments_by_date(args: argparse.Namespace) -> dict:
    """Read-only Schedule lookup across [start_date, end_date] -- today for
    either side left blank, see resolve_report_dates. Reuses
    discover_appointments_via_schedule_range (same scroll/paginate/row-count
    handling sync-schedules-by-date relies on) across that range.

    Deliberately appointments-only: unlike sync-schedules-by-date, this never
    reads or writes the queue, never opens a patient's chart, never pulls a
    facesheet/SOAP note, and never uploads anything -- just what the Schedule
    page shows for that date range. Stays on the Schedule screen only -- no
    other PF screen (e.g. the Appointment & Eligibility Report) is ever
    navigated to here.

    service_location comes from the Schedule screen's OWN toolbar facility
    selector (patient_scraper.read_schedule_facility, confirmed live
    2026-08-26 -- a composable-select toggle reading e.g. "NWARK Internal
    Medicine"), read once per call and stamped onto every appointment. This is
    the one facility/location signal that actually lives on the Schedule
    screen -- per-row facility does not (only the separate Report has that,
    and pulling that report in here was tried and reverted the same day
    specifically because it navigated off the Schedule screen).
    """
    from pf_sync_pkg import patient_scraper as ps
    from pf_sync_pkg.models import ScheduleScrapeConfig

    start_date, end_date = resolve_report_dates(args)
    schedule_config = ScheduleScrapeConfig.load(getattr(args, "schedule_config_json", ""))

    def callback(page: Page):
        # Every date in range gets a diagnostic entry (navigated?, PF's own
        # header count, what actually got scraped) -- see
        # discover_appointments_via_schedule_range's on_day_diagnostic
        # docstring. Surfaced in the JSON result below (day_diagnostics) so
        # "0 appointments" is never a dead end: it's either "couldn't
        # navigate here at all" (navigated=false -- a real bug worth
        # reporting), "PF's own header says 0" (header_count=0 -- genuinely
        # nothing that day), or "header said N, scraped fewer" (a scrape gap).
        day_diagnostics: List[Dict[str, Any]] = []

        def _record_day(target_date, info: Dict[str, Any]) -> None:
            day_diagnostics.append({"date": target_date.isoformat(), **info})

        # require_guid=False: this is a read-only listing, no chart/queue
        # interaction -- unlike sync-schedules-by-date, it has no reason to
        # drop a row just because PF didn't render a clickable chart link for
        # its status (Confirmed/No-show rows commonly don't). See
        # discover_appointments_via_schedule_range's require_guid docstring.
        appointments = ps.discover_appointments_via_schedule_range(
            page, start_date, end_date, config=schedule_config, require_guid=False,
            on_day_diagnostic=_record_day,
        )
        # Read AFTER the walk above (not before): discover_appointments_via_
        # schedule_range calls open_schedule_appointments_view internally,
        # which is what puts the toolbar/facility selector on screen in the
        # first place -- reading it first could race an unrendered toolbar.
        service_location = ps.read_schedule_facility(page, schedule_config)
        result = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "count": len(appointments),
            "service_location": service_location,
            "day_diagnostics": day_diagnostics,
            "appointments": [
                {
                    "appointment_date": appt.appointment_date.isoformat(),
                    "service_location": service_location,
                    **asdict(appt.patient),
                }
                for appt in appointments
            ],
        }

        output_json = getattr(args, "output_json", "") or (
            f"appointments_by_date_{start_date.isoformat()}_to_{end_date.isoformat()}.json"
        )
        try:
            atomic_write_json(output_json, result)
            result["output_json_path"] = str(Path(output_json).resolve())
            print(f"Wrote {result['count']} appointment(s) to {result['output_json_path']}", flush=True)
        except Exception as exc:
            # Never let a write failure erase the already-scraped, already-returned
            # result -- same "surface, don't swallow the real work" pattern as
            # build_and_upload_zip's own docstring.
            result["output_json_error"] = f"{type(exc).__name__}: {exc}"
            print(f"  WARNING: could not write {output_json}: {exc}", flush=True)

        return result

    return browser_command_wrapper(args, callback)


def _registry_row_from_report_patient(rp) -> Dict[str, Any]:
    """Convert a patient_scraper.ReportPatient (live Schedule scrape) into the same
    dict shape matching.map_patient_registry_row produces from a registry file, so
    match_patients_against_registry can score it identically either way."""
    name = clean(f"{rp.first_name} {rp.last_name}")
    phone = normalize_phone(rp.preferred_contact)
    return {
        "patient_id": rp.patient_id,
        "ehr_patient_guid": rp.ehr_patient_guid,
        "patient_name": name,
        "normalized_name": normalize_person_name(name),
        "dob": parse_date(rp.dob).isoformat() if parse_date(rp.dob) else "",
        "phones": [phone] if phone else [],
        "patient_status": rp.status,
    }


def run_facesheet_pull_by_date(args: argparse.Namespace) -> dict:
    """Discover (Schedule-scoped) -> pull-report -> ingest -> match against the live
    discovery, not a registry file -> process, forcing every Facesheet section on for
    this call only.

    v5.18's production default for process/nightly/refresh/plain full-sync is notes-only --
    see chart_ui.prepare_print_chart_sections; the on-disk config's include_facesheet_sections
    default stays False for all of them. v5.19 gave full-sync-by-date its own default of
    notes + demographics + active insurance (see build_full_sync_by_date_config) -- this
    command exists on top of that for an on-demand pull of EVERY Facesheet section (vitals,
    diagnoses, allergies, etc., not just demographics/insurance), scoped to one date/date
    range, without changing full-sync-by-date's own default for other calls.

    No patients_file CSV anywhere in this path: discover_via_schedule_range() checks
    the actual Practice Fusion Schedule for that date and returns real patient GUIDs
    directly from PF, in memory. Those become the match registry
    (match_patients_against_registry), so identity resolution is exact-GUID-scoped to
    just the patients on that date's schedule -- not fuzzy name/DOB matching against
    the whole practice's static registry file. The one CSV kept is the appointment
    report itself (report_file), pulled fresh every call, purely so ingest can read
    appointment_status/type/provider (the Schedule scrape doesn't expose those) --
    ingest's ignored_statuses gate still screens out canceled/no-show/rescheduled
    appointments before they reach process. Resolved GUIDs flow straight into the
    queue DB (ehr_pf_queue_rows), same as every other command.

    A whole-practice scrape is never triggered here: if discover_via_schedule_range
    for that date comes back completely empty, this reports that in "discover" and
    stops -- a single date has no business kicking off a scan of every patient in
    the practice.
    """
    import dataclasses

    from pf_sync_pkg import patient_scraper as ps

    base_config = SyncConfig.load(args.config_json)
    facesheet_config = dataclasses.replace(
        base_config,
        include_facesheet_sections=True,
        facesheet_checkbox_selectors=list(base_config.facesheet_known_option_selectors),
    )
    config = facesheet_config
    start_date, end_date = resolve_report_dates(args)
    report_config = AppointmentReportConfig.load(args.report_config_json)
    report_file = args.report_output_csv or (
        f"appointments_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"
    )
    manifest_run_id = f"{Path(args.queue_json).stem}_{start_date.isoformat()}_to_{end_date.isoformat()}"
    stages: dict = {}

    def callback(page: Page, context):
        discovered = ps.discover_via_schedule_range(page, start_date, end_date)
        stages["discover"] = {"method": "schedule_range", "unique_patients": len(discovered)}
        registry = [
            _registry_row_from_report_patient(rp)
            for rp in discovered.values()
            if rp.ehr_patient_guid
        ]

        try:
            pull_result = pull_appointment_report_on_page(
                page, report_config, start_date, end_date, report_file,
                include_rows_data=True,
            )
            rows_data = pull_result.pop("rows_data", [])
            stages["pull_report"] = pull_result
        except Exception as exc:
            stages["pull_report"] = {"error": f"{type(exc).__name__}: {exc}"}
            return stages  # nothing downstream can run without the report

        try:
            # Rows are already in memory from pull_result above -- ingest_appointment_rows
            # takes them directly instead of ingest_appointments re-reading report_file.
            # The download method still lands report_file on disk (Practice Fusion
            # produces that file itself, not something this code can avoid), but it is
            # never read a second time here.
            stages["ingest"] = ingest_appointment_rows(
                rows_data,
                args.queue_json,
                args.practice,
                source_report_name=os.path.basename(report_file),
                config=config,
                run_details={"report_file": str(Path(report_file).resolve())},
            )
        except Exception as exc:
            stages["ingest"] = {"error": f"{type(exc).__name__}: {exc}"}
            return stages  # nothing downstream can run without an ingested queue

        try:
            stages["match"] = match_patients_against_registry(
                args.queue_json,
                registry,
                args.fuzzy_threshold,
                dob_match_threshold=args.dob_match_threshold,
                run_details={"source": "schedule_discovery", "report_date_range": f"{start_date.isoformat()}_to_{end_date.isoformat()}"},
            )
        except Exception as exc:
            stages["match"] = {"error": f"{type(exc).__name__}: {exc}"}
            # process still runs below against whatever matched on a prior run.

        try:
            store = load_store(args.queue_json)
            rows = store_rows(store)
            candidates = default_process_candidates(rows, args.include_failed)
            # skip_encounter_lookup=True: same reasoning as sync-schedules-by-date
            # above -- Summary's "recent encounters" panel can lag behind a
            # patient who was genuinely just seen, and when it does, the old
            # Summary/Timeline pre-check raised EncounterNotFoundError before
            # Print Chart was ever opened, dropping the facesheet entirely for
            # a visit that WAS in Print Chart's own note list all along
            # (2026-08-25 fix). allow_most_recent_note_fallback is left at its
            # default False -- exact date or skip, never a substitute-date note.
            stages["process"] = process_records_on_page(
                page, args.queue_json, config, args.downloads_dir,
                candidates, rows, store, args.limit, args.dry_run, False,
                manifest_run_id, use_timeline_fallback=False, skip_encounter_lookup=True,
                allow_most_recent_note_fallback=True,
            )
        except Exception as exc:
            stages["process"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Retry failed/review rows from THIS run immediately, same run, before
        # the zip goes out -- otherwise a row that failed here just sits until
        # someone happens to trigger a whole new run. Run across a few extra
        # tabs (context.new_page(), same logged-in session) concurrently
        # rather than one more slow serial pass -- see
        # process_records_concurrently's docstring for why this is safe.
        # Bounded to one retry pass: a row that fails again goes out as
        # failed/review in the response same as today, to be picked up by the
        # next scheduled run rather than looping here indefinitely.
        try:
            store = load_store(args.queue_json)
            rows = store_rows(store)
            retry_candidates = [r for r in rows if r.status in {"failed", "review"}]
            if retry_candidates:
                print(
                    f"Retrying {len(retry_candidates)} failed/review row(s) from this run "
                    f"across up to {args.retry_concurrency} tab(s) before upload...",
                    flush=True,
                )
                stages["process_retry"] = process_records_concurrently(
                    context, args.queue_json, config, args.downloads_dir,
                    retry_candidates, rows, store, manifest_run_id,
                    use_timeline_fallback=False, skip_encounter_lookup=True,
                    allow_most_recent_note_fallback=True, dry_run=args.dry_run,
                    concurrency=args.retry_concurrency,
                )
        except Exception as exc:
            stages["process_retry"] = {"error": f"{type(exc).__name__}: {exc}"}

        return stages

    return browser_command_wrapper_with_context(args, callback)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "version":
            print(BUILD_ID)
            return 0

        if args.command == "self-test":
            return run_self_test()

        if args.command == "doctor":
            return run_doctor(args)

        if args.command == "write-config":
            atomic_write_json(args.config_json, asdict(SyncConfig()))
            print(f"Complete PDF config written to {args.config_json}")
            return 0

        if args.command == "write-report-config":
            atomic_write_json(args.report_config_json, asdict(AppointmentReportConfig()))
            print(f"Appointment report config written to {args.report_config_json}")
            return 0

        if args.command == "write-schedule-config":
            from pf_sync_pkg.models import ScheduleScrapeConfig

            atomic_write_json(args.schedule_config_json, asdict(ScheduleScrapeConfig()))
            print(f"Schedule scrape config written to {args.schedule_config_json}")
            return 0

        if args.command == "ingest":
            counts = ingest_appointments(
                args.appointments_file,
                args.queue_json,
                args.practice,
                args.source_report_name,
                args.reset_existing,
                config=SyncConfig.load(args.config_json),
            )
            print(json.dumps(counts, indent=2))
            return 0

        if args.command == "match-patients":
            counts = match_patients(
                args.queue_json,
                args.patients_file,
                args.fuzzy_threshold,
                args.rematch_all,
                args.dob_match_threshold,
            )
            print(json.dumps(counts, indent=2))
            return 0

        if args.command == "resolve-patient":
            counts = resolve_patient_manually(
                args.queue_json,
                args.patient_id,
                args.ehr_patient_guid,
                args.row_id,
                args.appointment_id,
                args.patients_file,
                args.resolved_patient_name,
            )
            print(json.dumps(counts, indent=2))
            return 0

        if args.command == "status":
            queue_status(args.queue_json, args.show_limit)
            return 0

        if args.command == "reset":
            count = reset_rows(
                args.queue_json,
                args.row_id,
                args.appointment_id,
                args.patient_id,
                args.all_processed,
            )
            print(f"Reset {count} row(s) to ready.")
            return 0

        if args.command == "pull-report":
            start_date, end_date = resolve_report_dates(args)
            result = browser_command_wrapper(
                args,
                lambda page: pull_appointment_report_on_page(
                    page,
                    AppointmentReportConfig.load(args.report_config_json),
                    start_date,
                    end_date,
                    args.output_csv,
                ),
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "process":
            store = load_store(args.queue_json)
            rows = store_rows(store)
            candidates = default_process_candidates(rows, args.include_failed)
            if not candidates:
                print("No ready/review records to process.")
                return 0
            config = SyncConfig.load(args.config_json)
            run_id = append_run(store, "process", {"candidates": len(candidates), "dry_run": args.dry_run})

            def callback(page: Page):
                counts = process_records_on_page(
                    page,
                    args.queue_json,
                    config,
                    args.downloads_dir,
                    candidates,
                    rows,
                    store,
                    args.limit,
                    args.dry_run,
                    False,
                    args.run_id,
                )
                finish_run(store, run_id, "success", counts)
                save_store(args.queue_json, store, rows)
                return counts

            counts = browser_command_wrapper(args, callback)
            print(json.dumps(counts, indent=2))
            return 1 if counts.get("failed", 0) else 0

        if args.command == "full-sync":
            store = load_store(args.queue_json)
            rows = store_rows(store)
            config = SyncConfig.load(args.config_json)
            run_id = append_run(
                store,
                "full-sync",
                {
                    "patients_file": str(Path(args.patients_file).resolve()),
                    "limit_patients": args.limit_patients,
                    "max_encounters_per_patient": args.max_encounters_per_patient,
                    "dry_run": args.dry_run,
                    "rescrape_all": args.rescrape_all,
                },
            )

            def callback(page: Page):
                counts = full_sync_on_page(
                    page,
                    args.queue_json,
                    config,
                    args.downloads_dir,
                    args.patients_file,
                    store,
                    rows,
                    args.limit_patients,
                    args.max_encounters_per_patient,
                    args.dry_run,
                    args.rescrape_all,
                )
                finish_run(store, run_id, "success", counts)
                save_store(args.queue_json, store, rows)
                return counts

            counts = browser_command_wrapper(args, callback)
            print(json.dumps(counts, indent=2))
            return 0

        if args.command == "refresh":
            counts = run_refresh(args)
            print(json.dumps(counts, indent=2))
            return 1 if counts.get("failed", 0) else 0

        if args.command == "nightly":
            result = run_nightly(args)
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "full-sync-by-date":
            result = run_full_sync_by_date(args)
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "facesheet-pull-by-date":
            result = run_facesheet_pull_by_date(args)
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "sync-schedules-by-date":
            result = run_sync_schedules_by_date(args)
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "appointments-by-date":
            result = run_appointments_by_date(args)
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "zip-upload":
            from pf_sync_pkg.rcm_upload import build_and_upload_zip

            result = build_and_upload_zip(
                args.manifest_json, args.downloads_dir, args.practice,
                no_upload=args.no_upload,
                delete_local_after_upload=not args.keep_local,
            )
            print(json.dumps(result, indent=2))
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except KeyboardInterrupt:
        print("Cancelled by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
