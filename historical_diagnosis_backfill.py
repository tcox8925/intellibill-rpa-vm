#!/usr/bin/env python3
# ----------------------------------------------------------------------------
# HOW TO RUN (recommended order -- this hits production PF + the RCM backend,
# so go step by step rather than straight to a full range):
#
#   cd /Users/srinivasbodduru/projects/RPA-VM/intellibill-rpa-vm
#   source .venv/bin/activate
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
# Other useful flags (combine with any of the above):
#   --patient-guid <guid>        limit to one patient
#   --statuses processed,review  widen beyond the "processed" default
#   --no-backend-call            generate PDFs locally only, skip the RCM POST
#                                 (pair with --keep-local-pdfs to inspect them)
#   --dry-run                    log in and select sections/notes but skip
#                                 PDF generation and the backend call entirely
#   --limit N                    cap how many rows get reprocessed
#
# Full flag reference: python historical_diagnosis_backfill.py --help
# ----------------------------------------------------------------------------
"""
Manually-triggered, one-off backfill for the Diagnoses section.

build_full_sync_by_date_config (pf_sync_v5_6/pf_sync_pkg/cli.py) now includes
Diagnoses in every printed Practice Fusion chart, but every facesheet already
delivered before that change is missing it. This script re-opens the chart
for each already-processed queue row in the given range, reprints it with the
current (Diagnoses-included) config, and forwards the fresh PDF straight to
the RCM backend's pfFacesheetProcessing.processFacesheet mutation --
bypassing the normal zip-and-upload-to-Azure delivery path
(pf_sync_pkg/rcm_upload.py) entirely, exactly as instructed: call the backend
directly from the just-downloaded PDF, before any zip step exists.

Every call is flagged special_historical_diagnosis_run=True (see
myops/ehr/pf_facesheet_processor.py's _call_facesheet_processing_api) so the
backend can tell a deliberate re-delivery of an already-processed row apart
from the normal nightly/refresh path -- IMPORTANT: this only has an effect
once the backend itself is updated to read that field; until then it's
accepted but ignored (or rejected with a 400 if the backend's schema
validation there doesn't allow unknown/passthrough fields yet).

This never touches Azure Blob Storage and never triggers
myops/ehr/pf_facesheet_processor.py's normal blob-scanning job.

Usage:
    # See what would run, without opening a browser or touching PF/RCM:
    python historical_diagnosis_backfill.py --start-date 2026-01-01 --end-date 2026-06-30 --plan-only

    # Real run over a date range:
    python historical_diagnosis_backfill.py --start-date 2026-01-01 --end-date 2026-06-30

    # Small test batch first (strongly recommended before a full run):
    python historical_diagnosis_backfill.py --start-date 2026-01-01 --end-date 2026-06-30 --limit 3

    # One patient only:
    python historical_diagnosis_backfill.py --patient-guid <ehr_patient_guid>

    # Generate PDFs and inspect them locally without calling the backend:
    python historical_diagnosis_backfill.py --start-date 2026-01-01 --end-date 2026-06-30 --no-backend-call --keep-local-pdfs

Candidates default to queue rows already at status "processed" (i.e.
successfully delivered under the old, Diagnoses-less config) within
[--start-date, --end-date] -- pass --statuses to widen/narrow that set. This
never changes a row's queue status: it's a side, direct-to-backend
re-delivery, not a reprocessing of the queue's own state machine.
"""

import argparse
import os
import sys
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
from pf_sync_pkg.pdf_pipeline import (  # noqa: E402
    appointment_metadata_row,
    handle_process_error,
    is_ignored,
    process_one_record,
)
from pf_sync_pkg.store import load_store, save_row, store_rows  # noqa: E402
from pf_sync_pkg.utils import parse_date  # noqa: E402

from ehr.pf_facesheet_processor import _call_facesheet_processing_api, _login  # noqa: E402


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
    parser.add_argument("--practice", default="NWARK Internal Medicine")
    parser.add_argument("--start-date", default="", help="Inclusive; blank = no lower bound.")
    parser.add_argument("--end-date", default="", help="Inclusive; blank = no upper bound.")
    parser.add_argument("--patient-guid", default="", help="Limit the run to one patient.")
    parser.add_argument(
        "--statuses",
        default="processed",
        help="Comma-separated QueueRecord statuses to reprocess (default: processed).",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Cap the number of rows reprocessed (0 = no cap)."
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
    return parser.parse_args()


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


def run(args: argparse.Namespace) -> dict:
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


def main() -> None:
    args = parse_args()
    result = run(args)
    print(f"\n[HISTORICAL-DIAGNOSIS-BACKFILL] Done: {result}", flush=True)


if __name__ == "__main__":
    main()
