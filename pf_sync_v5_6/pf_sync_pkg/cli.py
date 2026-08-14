"""Argparse CLI orchestration: subcommands, browser wrapper, doctor diagnostics, main()."""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Dict, Tuple

from playwright.sync_api import Page

from pf_sync_pkg.browser import build_browser, close_browser, find_chrome_exe, wait_for_pf_login
from pf_sync_pkg.constants import BUILD_ID, CSV_FIELD_LIMIT, PRACTICE_TZ_NAME
from pf_sync_pkg.ingest import (
    OPTIONAL_APPOINTMENT_FIELDS,
    REQUIRED_APPOINTMENT_FIELDS,
    ingest_appointments,
    map_appointment_row,
)
from pf_sync_pkg.matching import load_patient_registry, match_patients, resolve_patient_manually, select_queue_rows
from pf_sync_pkg.models import AppointmentReportConfig, SyncConfig
from pf_sync_pkg.pdf_pipeline import default_process_candidates, full_sync_on_page, process_records_on_page
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


def run_nightly(args: argparse.Namespace) -> dict:
    """Pull -> ingest -> match -> process, factored out of main() so server.py's
    /nightly endpoint can call the exact same orchestration the CLI uses.
    """
    start_date, end_date = resolve_report_dates(args)
    report_file = args.appointments_file
    if not report_file:
        report_file = args.report_output_csv or (
            f"appointments_{start_date.isoformat()}_to_{end_date.isoformat()}.csv"
        )

    config = SyncConfig.load(args.config_json)
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
        )
        return {
            "report_file": report_file,
            "ingest": ingest_counts,
            "match": match_counts,
            "process": counts,
        }

    return browser_command_wrapper(args, callback)


def run_full_sync_by_date(args: argparse.Namespace) -> dict:
    """One-call pipeline: discover (Schedule-scoped, falling back to the full
    age-bucket sweep if that fails) -> merge into the canonical patients
    registry -> pull-report -> ingest -> match-patients -> process.

    This exists because every actual mistake in a manual run this repo has hit
    traces back to a human doing these steps one at a time: a stale/wrong
    registry file left over from a previous session, forgetting to re-run
    match-patients after refreshing it, or not knowing which of several
    scattered CSV copies was current. One call resolves all of that in a fixed
    order, every time.

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

        if not discovered:
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
        config = SyncConfig.load(args.config_json)
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
                manifest_run_id,
            )
        except Exception as exc:
            stages["process"] = {"error": f"{type(exc).__name__}: {exc}"}

        # --- Stage 4: zip the PDFs this run produced + upload to rcm-attachments
        if args.dry_run:
            stages["rcm_upload"] = {"skipped": True, "reason": "dry_run=True -- no PDFs were generated"}
        else:
            manifest_path = ""
            process_result = stages.get("process")
            if isinstance(process_result, dict):
                manifest_path = process_result.get("metadata_manifest_path", "")
            try:
                from pf_sync_pkg.rcm_upload import build_and_upload_zip

                stages["rcm_upload"] = build_and_upload_zip(
                    manifest_path, args.downloads_dir, args.practice,
                )
            except Exception as exc:
                stages["rcm_upload"] = {"error": f"{type(exc).__name__}: {exc}"}

        return stages

    return browser_command_wrapper(args, callback)


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
