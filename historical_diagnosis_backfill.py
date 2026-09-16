#!/usr/bin/env python3
# ----------------------------------------------------------------------------
# HOW TO RUN (recommended order -- this hits production PF + the RCM backend,
# so go step by step rather than straight to a full range):
#
#   cd /Users/srinivasbodduru/projects/RPA-VM/intellibill-rpa-vm
#   source .venv/bin/activate
#
# ---- mode 1: reprocess-queue (default) -- redeliver rows ALREADY in the
#              local queue (visit-level: one row per appointment) ----
#
#   # 1. Plan only -- read-only, just queries Postgres, no browser/PF/backend:
#   python historical_diagnosis_backfill.py \
#       --start-date 2026-01-01 --end-date 2026-09-15 --plan-only
#
#   # 2. Test on a single row before trusting it with more:
#   python historical_diagnosis_backfill.py \
#       --start-date 2026-01-01 --end-date 2026-09-15 --limit 1
#
#   # 3. Full range once step 2 looks right:
#   python historical_diagnosis_backfill.py \
#       --start-date 2026-01-01 --end-date 2026-09-15
#
# ---- mode 2: unique-patients -- discover every unique patient with a Seen
#              appointment in a date range STRAIGHT FROM PRACTICE FUSION
#              (ignores the local queue entirely) and pull exactly ONE
#              facesheet per patient (their most recent Seen visit in range,
#              which is the visit most likely to have a printable SOAP note
#              and reflect their current diagnosis list) ----
#
#   # 1. Plan only -- still logs into PF (the patient list can only come from
#   #    PF itself) but opens no chart and calls no backend; just prints the
#   #    unique-patient count + list:
#   python historical_diagnosis_backfill.py --mode unique-patients \
#       --start-date 2026-06-02 --end-date 2026-09-16 --plan-only
#
#   # 2. Test on one patient:
#   python historical_diagnosis_backfill.py --mode unique-patients \
#       --start-date 2026-06-02 --end-date 2026-09-16 --limit 1
#
#   # 3. Full range once step 2 looks right:
#   python historical_diagnosis_backfill.py --mode unique-patients \
#       --start-date 2026-06-02 --end-date 2026-09-16
#
# Other useful flags (combine with any of the above):
#   --patient-guid <guid>        limit to one patient
#   --statuses processed,review  (reprocess-queue only) widen beyond "processed"
#   --no-backend-call            generate PDFs locally only, skip the RCM POST
#                                 (pair with --keep-local-pdfs to inspect them)
#   --dry-run                    log in and select sections/notes but skip
#                                 PDF generation and the backend call entirely
#   --limit N                    cap how many patients/rows get reprocessed
#
# Full flag reference: python historical_diagnosis_backfill.py --help
# ----------------------------------------------------------------------------
"""
Manually-triggered, one-off backfill for the Diagnoses section.

build_full_sync_by_date_config (pf_sync_v5_6/pf_sync_pkg/cli.py) now includes
Diagnoses in every printed Practice Fusion chart, but every facesheet already
delivered before that change is missing it. Two modes, selected with --mode:

--mode reprocess-queue (default): re-opens the chart for each already-
processed row ALREADY IN THE LOCAL QUEUE (visit-level -- one row per
appointment) within [--start-date, --end-date] and reprints it.

--mode unique-patients: ignores the local queue entirely and discovers every
unique patient with a Seen appointment in [--start-date, --end-date] straight
from Practice Fusion's own Schedule, then pulls exactly ONE facesheet per
patient (their most recent Seen visit in the range). Use this when you want
"every patient we saw between two dates, one facesheet each" rather than one
facesheet per visit -- see discover_unique_patients_from_schedule's docstring
below for the dedup/representative-visit logic. These synthetic per-patient
records are NOT written to the local queue (they don't fit its visit-level
(guid, date) model) -- results are printed and saved to a JSON summary file
in --downloads-dir instead.

Both modes reprint with the current (Diagnoses-included) config and forward
each fresh PDF straight to the RCM backend's
pfFacesheetProcessing.processFacesheet mutation -- bypassing the normal
zip-and-upload-to-Azure delivery path (pf_sync_pkg/rcm_upload.py) entirely,
exactly as instructed: call the backend directly from the just-downloaded
PDF, before any zip step exists.

Every call is flagged specialHistoricalDiagnosisRun=True (see
myops/ehr/pf_facesheet_processor.py's _call_facesheet_processing_api) so the
backend can tell a deliberate re-delivery/historical pull apart from the
normal nightly/refresh path -- IMPORTANT: this only has an effect once the
backend itself is updated to read that field; until then it's accepted but
ignored (or rejected with a 400 if the backend's schema validation there
doesn't allow unknown/passthrough fields yet).

This never touches Azure Blob Storage and never triggers
myops/ehr/pf_facesheet_processor.py's normal blob-scanning job.

See the HOW TO RUN comment block at the top of this file for full examples.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PF_SYNC_DIR = REPO_ROOT / "pf_sync_v5_6"
MYOPS_DIR = REPO_ROOT / "myops"
# Same pattern as the root server.py's _load_app: each sub-package's own
# internal imports (pf_sync_pkg.*, ehr.*) assume their own directory is
# directly on sys.path, the same way it is when each is run standalone.
sys.path.insert(0, str(PF_SYNC_DIR))
sys.path.insert(0, str(MYOPS_DIR))

from pf_sync_pkg.chart_ui import close_print_chart  # noqa: E402
from pf_sync_pkg.cli import (  # noqa: E402
    add_browser_arguments,
    browser_command_wrapper,
    build_full_sync_by_date_config,
)
from pf_sync_pkg.models import QueueRecord, ScheduleScrapeConfig  # noqa: E402
from pf_sync_pkg.pdf_pipeline import (  # noqa: E402
    appointment_metadata_row,
    handle_process_error,
    is_ignored,
    process_one_record,
)
from pf_sync_pkg.store import load_store, save_row, store_rows  # noqa: E402
from pf_sync_pkg.utils import is_seen_status, now_iso, parse_date  # noqa: E402

from ehr.pf_facesheet_processor import _call_facesheet_processing_api, _login  # noqa: E402


def _start_tee_logging(log_path: Path) -> None:
    """Duplicate this process's stdout/stderr to `log_path`, in addition to
    the terminal, at the OS file-descriptor level (like shell `| tee`) rather
    than swapping Python's sys.stdout object.

    The fd-level approach matters here specifically because build_browser
    (pf_sync_pkg/browser.py) launches real Chrome as a subprocess
    (subprocess.Popen), which inherits this process's stdout/stderr file
    descriptors directly -- all of Chrome's own console noise (DevTools
    listening on ws://..., the chrome/updater/* GoogleUpdater lines, etc.)
    would never reach a log file if we only reassigned Python's sys.stdout;
    it bypasses that object entirely. Redirecting the actual fd catches
    Chrome's output too, exactly like `python script.py ... | tee file.log`
    would from the shell -- this just does it automatically so nobody has to
    remember the `| tee` themselves.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Spawn tee BEFORE touching our own fds, so tee's own stdout still
    # inherits the real terminal -- only OUR future writes get redirected
    # into its stdin pipe below.
    tee = subprocess.Popen(["tee", "-a", str(log_path)], stdin=subprocess.PIPE)
    os.dup2(tee.stdin.fileno(), sys.stdout.fileno())
    os.dup2(tee.stdin.fileno(), sys.stderr.fileno())


def _default_chrome_user_data_dir() -> str:
    """Same fallback pf_sync_v5_6/server.py's own _default_chrome_user_data_dir
    uses -- duplicated here (rather than imported) so this script doesn't have
    to load that whole FastAPI app module just for a two-line helper."""
    if os.getenv("USERPROFILE"):
        return os.path.join(os.getenv("USERPROFILE"), "pf_rpa_chrome")
    return os.path.join(os.path.expanduser("~"), "pf_rpa_chrome")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_browser_arguments(parser)
    # add_browser_arguments' own --chrome-user-data-dir default only fills in
    # on Windows (%USERPROFILE%\pf_rpa_chrome); give it the same Mac/Linux
    # fallback (~/pf_rpa_chrome) build_browser's own default already uses
    # elsewhere, so this runs out of the box on a dev Mac too.
    parser.set_defaults(chrome_user_data_dir=_default_chrome_user_data_dir())

    parser.add_argument(
        "--queue-json", default=str(PF_SYNC_DIR / "pf_appointment_queue.json")
    )
    parser.add_argument(
        "--config-json", default=str(PF_SYNC_DIR / "config" / "pf_pdf_sync_config.json")
    )
    parser.add_argument(
        "--downloads-dir", default=str(PF_SYNC_DIR / "pf_encounter_pdfs_historical")
    )
    parser.add_argument(
        "--schedule-config-json",
        default=str(PF_SYNC_DIR / "config" / "pf_schedule_scrape_config.json"),
        help="unique-patients mode only: ScheduleScrapeConfig used to walk the Schedule.",
    )
    parser.add_argument("--practice", default="NWARK Internal Medicine")
    parser.add_argument(
        "--mode",
        choices=("reprocess-queue", "unique-patients"),
        default="reprocess-queue",
        help=(
            "reprocess-queue (default): redeliver rows already in the local queue "
            "(visit-level). unique-patients: discover every unique patient with a "
            "Seen appointment in [--start-date, --end-date] straight from PF's "
            "Schedule and pull exactly one facesheet per patient."
        ),
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="Inclusive; blank = no lower bound (required for --mode unique-patients).",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="Inclusive; blank = no upper bound (required for --mode unique-patients).",
    )
    parser.add_argument("--patient-guid", default="", help="Limit the run to one patient.")
    parser.add_argument(
        "--statuses",
        default="processed",
        help="reprocess-queue mode only: comma-separated QueueRecord statuses to reprocess.",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Cap the number of rows/patients reprocessed (0 = no cap)."
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help=(
            "unique-patients mode only: skip the first N patients (after sorting by name) "
            "-- combine with --limit to run in batches, e.g. --skip 0 --limit 150, then "
            "--skip 150 --limit 150, etc. Each batch merges into the same summary JSON "
            "(keyed by ehr_patient_guid), so a crash partway only costs the in-flight patient."
        ),
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the selected candidates and exit -- no browser, no PF login, no backend call.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log into PF and select sections/notes but skip PDF generation and the backend call.",
    )
    parser.add_argument(
        "--no-backend-call",
        action="store_true",
        help="Generate PDFs locally but skip forwarding them to the RCM backend.",
    )
    parser.add_argument(
        "--keep-local-pdfs",
        action="store_true",
        help="Don't delete the local PDF after a successful backend call (dry-run/no-backend-call always keep it).",
    )
    parser.add_argument(
        "--log-file",
        default="",
        help=(
            "Tee all console output to this file too (default: auto-generated "
            "under ./logs/, named by mode and start time). Pass --no-log to "
            "disable logging to a file entirely."
        ),
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Don't write a log file -- console only.",
    )
    args = parser.parse_args()
    if args.mode == "unique-patients" and not (args.start_date and args.end_date):
        parser.error("--mode unique-patients requires both --start-date and --end-date.")
    return args


def select_candidates(args: argparse.Namespace) -> list:
    store = load_store(args.queue_json)
    rows = store_rows(store)
    wanted_statuses = {s.strip() for s in args.statuses.split(",") if s.strip()}
    start = parse_date(args.start_date) if args.start_date else None
    end = parse_date(args.end_date) if args.end_date else None

    candidates = []
    for row in rows:
        if row.status not in wanted_statuses:
            continue
        if args.patient_guid and row.ehr_patient_guid != args.patient_guid:
            continue
        row_date = parse_date(row.appointment_date)
        if start and (not row_date or row_date < start):
            continue
        if end and (not row_date or row_date > end):
            continue
        candidates.append(row)

    candidates.sort(key=lambda r: (r.appointment_date or "", r.patient_name or ""))
    if args.limit > 0:
        candidates = candidates[: args.limit]
    return candidates


def discover_unique_patients_from_schedule(page, args: argparse.Namespace, config) -> tuple:
    """Walk Practice Fusion's Schedule for [--start-date, --end-date] and
    return (records, never_seen): one synthetic QueueRecord per UNIQUE
    patient (by ehr_patient_guid) who had at least one Seen appointment in
    that range, plus a list of patients the Schedule showed in range but who
    were never actually marked Seen (cancelled/no-show/etc -- nothing to
    print for them, reported so they're not silently missing from the count).

    Representative visit per patient: the MOST RECENT Seen appointment date
    in range. The Diagnoses/notes sections reflect the patient's chart as of
    print time regardless of which visit's chart is open, so any Seen visit
    would technically work -- most recent is the safest choice since it's
    the visit most likely to already have a SOAP note on file.

    These records are intentionally never written to the local queue (see
    module docstring) -- a synthetic one-row-per-patient shape doesn't fit
    its visit-level (guid, date) primary key, so this stays a side,
    in-memory-only discovery.
    """
    from pf_sync_pkg import patient_scraper as ps

    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    schedule_config = ScheduleScrapeConfig.load(args.schedule_config_json)

    appointments = ps.discover_appointments_via_schedule_range(
        page, start_date, end_date, config=schedule_config
    )

    by_guid: dict = {}
    for appt in appointments:
        guid = appt.patient.ehr_patient_guid
        if not guid:
            continue
        by_guid.setdefault(guid, []).append(appt)

    records: list = []
    never_seen: list = []
    for guid, visits in by_guid.items():
        seen_visits = [v for v in visits if is_seen_status(v.patient.appointment_status, config)]
        if not seen_visits:
            rp = visits[0].patient
            never_seen.append(
                f"{rp.first_name} {rp.last_name} ({guid}) -- {len(visits)} visit(s) in range, "
                f"none marked Seen"
            )
            continue

        latest = max(seen_visits, key=lambda v: v.appointment_date)
        rp = latest.patient
        appt_date = latest.appointment_date.isoformat()
        if rp.appointment_start_time:
            appt_date = f"{appt_date} {rp.appointment_start_time}"

        records.append(
            QueueRecord(
                row_id=str(uuid.uuid4()),
                practice=args.practice,
                ehr_patient_guid=rp.ehr_patient_guid,
                patient_name=f"{rp.first_name} {rp.last_name}".strip(),
                patient_dob=rp.dob,
                appointment_date=appt_date,
                appointment_status=rp.appointment_status or "seen",
                appointment_type=rp.appointment_type,
                provider=rp.provider_name,
                service_location="",
                patient_id=rp.patient_id,
                patient_match_status="matched",
                patient_match_method="discovered_from_schedule",
                status="ready",
                status_reason="unique_patient_historical_diagnosis_pull",
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )

    records.sort(key=lambda r: r.patient_name or "")
    return records, never_seen


def process_candidate(page, record, config, args, session) -> str:
    """Reprint one chart and, unless disabled, deliver it straight to the
    backend. Returns a short outcome label for the run summary."""
    if is_ignored(record, config):
        print("  ignored", flush=True)
        return "ignored"
    if record.patient_match_status != "matched" or not record.ehr_patient_guid:
        print("  needs_attention: patient not resolved", flush=True)
        return "needs_attention"

    try:
        process_one_record(
            page,
            record,
            config,
            args.downloads_dir,
            scrape_run_id="historical-diagnosis-backfill",
            exact_refresh=False,
            dry_run=args.dry_run,
            all_rows=(),
            use_timeline_fallback=False,
            skip_encounter_lookup=True,
            allow_most_recent_note_fallback=True,
        )
    except Exception as exc:
        state = handle_process_error(record, config, exc)
        print(f"  {state}: {record.error_message}", flush=True)
        return state
    finally:
        # Always tear the Print Chart modal down, success or failure, so it
        # can't be left open over the next patient's chart (same rule
        # process_records_on_page follows).
        close_print_chart(page, config)

    print(f"  reprinted in {record.elapsed_seconds:.3f}s -> {record.pdf_path}", flush=True)

    if args.dry_run:
        return "reprinted_dry_run"

    if args.no_backend_call:
        return "reprinted_only"

    manifest_entry = appointment_metadata_row(record)
    pdf_path = Path(record.pdf_path)
    try:
        result = _call_facesheet_processing_api(
            print,
            pdf_path.read_bytes(),
            manifest_entry["pdf_file"],
            session,
            manifest_entry,
            special_historical_diagnosis_run=True,
        )
        print(f"  backend response: {result}", flush=True)
    except Exception as exc:
        print(f"  backend call FAILED, leaving local PDF in place: {exc}", flush=True)
        return "backend_failed"

    if not args.keep_local_pdfs:
        pdf_path.unlink(missing_ok=True)
    return "sent_to_backend"


def run_reprocess_queue(args: argparse.Namespace) -> dict:
    candidates = select_candidates(args)
    print(
        f"[HISTORICAL-DIAGNOSIS-BACKFILL] {len(candidates)} candidate row(s) selected "
        f"(statuses={args.statuses!r}, start={args.start_date or '(none)'}, "
        f"end={args.end_date or '(none)'}, patient_guid={args.patient_guid or '(any)'}).",
        flush=True,
    )
    for row in candidates:
        print(f"  - {row.patient_name} | {row.appointment_date} | {row.ehr_patient_guid}", flush=True)

    if args.plan_only or not candidates:
        return {"candidates": len(candidates), "plan_only": args.plan_only}

    config = build_full_sync_by_date_config(args)
    session = None if (args.dry_run or args.no_backend_call) else _login(print)

    counts: dict = {"candidates": len(candidates)}

    def callback(page):
        for index, record in enumerate(candidates, start=1):
            print(
                f"[{index}/{len(candidates)}] {record.patient_name} | "
                f"{record.appointment_date} | {record.ehr_patient_guid}",
                flush=True,
            )
            outcome = process_candidate(page, record, config, args, session)
            counts[outcome] = counts.get(outcome, 0) + 1
            # Side, direct-to-backend delivery -- record.status is left exactly
            # as process_one_record/handle_process_error set it (normally
            # unchanged from "processed" on the happy path), only the derived
            # fields (pdf_path, elapsed_seconds, etc.) get persisted.
            save_row(args.queue_json, record)
        return counts

    return browser_command_wrapper(args, callback)


def run_unique_patients(args: argparse.Namespace) -> dict:
    # Discovery itself requires a real PF session (the patient list can only
    # come from PF's own Schedule), so -- unlike reprocess-queue mode --
    # --plan-only still logs in here; it just stops before opening any chart
    # or calling the backend.
    config = build_full_sync_by_date_config(args)
    session = None if (args.dry_run or args.no_backend_call or args.plan_only) else _login(print)

    result: dict = {}

    def callback(page):
        all_records, never_seen = discover_unique_patients_from_schedule(page, args, config)
        batch = all_records[args.skip :]
        if args.limit > 0:
            batch = batch[: args.limit]

        print(
            f"[HISTORICAL-DIAGNOSIS-BACKFILL] {len(all_records)} unique patient(s) total with a "
            f"Seen appointment between {args.start_date} and {args.end_date} "
            f"({len(never_seen)} other patient(s) seen on the Schedule but never marked Seen); "
            f"this batch: {len(batch)} (skip={args.skip}, limit={args.limit or 'none'}).",
            flush=True,
        )
        for record in batch:
            print(
                f"  - {record.patient_name} | representative visit {record.appointment_date} "
                f"| {record.ehr_patient_guid}",
                flush=True,
            )
        if never_seen:
            print("  Not Seen in range (skipped):", flush=True)
            for line in never_seen:
                print(f"    - {line}", flush=True)

        # Seed every patient in THIS batch as "pending" before processing
        # starts, then overwrite each one's outcome as it completes (merged
        # into the same file across batches, keyed by ehr_patient_guid) --
        # so even a crash before the first patient finishes still leaves a
        # readable record of what this batch was supposed to cover.
        _merge_unique_patients_summary(
            args,
            never_seen,
            [
                {
                    "patient_name": r.patient_name,
                    "ehr_patient_guid": r.ehr_patient_guid,
                    "representative_appointment_date": r.appointment_date,
                    "outcome": "pending",
                }
                for r in batch
            ],
        )

        if args.plan_only or not batch:
            result.update({"unique_patient_count": len(all_records), "batch_size": len(batch), "plan_only": args.plan_only})
            return result

        counts: dict = {"unique_patient_count": len(all_records), "batch_size": len(batch)}
        for index, record in enumerate(batch, start=1):
            print(
                f"[{index}/{len(batch)}] {record.patient_name} | "
                f"{record.appointment_date} | {record.ehr_patient_guid}",
                flush=True,
            )
            outcome = process_candidate(page, record, config, args, session)
            counts[outcome] = counts.get(outcome, 0) + 1
            # Write after EVERY patient, not just at the end -- a crash
            # partway through this batch then only ever costs the one
            # in-flight patient, not the whole batch.
            _merge_unique_patients_summary(
                args,
                never_seen,
                [
                    {
                        "patient_name": record.patient_name,
                        "ehr_patient_guid": record.ehr_patient_guid,
                        "representative_appointment_date": record.appointment_date,
                        "outcome": outcome,
                    }
                ],
            )
            # No save_row here: these are synthetic, one-per-patient records
            # that don't belong in the visit-level queue (see module
            # docstring) -- the JSON summary file is their only record.

        result.update(counts)
        result["summary_path"] = str(_unique_patients_summary_path(args))
        return result

    return browser_command_wrapper(args, callback)


def _unique_patients_summary_path(args: argparse.Namespace) -> Path:
    return Path(args.downloads_dir) / f"unique_patients_{args.start_date}_to_{args.end_date}.json"


def _merge_unique_patients_summary(args: argparse.Namespace, never_seen: list, patient_updates: list) -> None:
    """Merge `patient_updates` into the on-disk summary, keyed by
    ehr_patient_guid, and rewrite it. Same "converge on one file no matter
    how many calls it takes" pattern pdf_pipeline.write_appointments_
    metadata_json uses for its manifest_run_id merging -- lets multiple
    --skip/--limit batches (or a resumed run after a crash) accumulate into
    one summary instead of each batch producing its own fragment.
    """
    destination = _unique_patients_summary_path(args)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    else:
        existing = {}

    by_guid = {p["ehr_patient_guid"]: p for p in existing.get("patients", []) if p.get("ehr_patient_guid")}
    for update in patient_updates:
        by_guid[update["ehr_patient_guid"]] = update

    patients = sorted(by_guid.values(), key=lambda p: p.get("patient_name") or "")
    summary = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "unique_patient_count": len(patients),
        "patients": patients,
        # Overwritten with whatever the most recent discovery call found --
        # informational only, not merged (discovery itself isn't batched).
        "not_seen_in_range": never_seen if never_seen else existing.get("not_seen_in_range", []),
    }
    destination.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def run(args: argparse.Namespace) -> dict:
    if args.mode == "unique-patients":
        return run_unique_patients(args)
    return run_reprocess_queue(args)


def main() -> None:
    args = parse_args()

    if not args.no_log:
        log_path = Path(args.log_file) if args.log_file else (
            REPO_ROOT / "logs"
            / f"historical_diagnosis_backfill_{args.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        _start_tee_logging(log_path)
        print(f"[HISTORICAL-DIAGNOSIS-BACKFILL] Logging this run to {log_path}", flush=True)

    result = run(args)
    print(f"\n[HISTORICAL-DIAGNOSIS-BACKFILL] Done: {result}", flush=True)


if __name__ == "__main__":
    main()
