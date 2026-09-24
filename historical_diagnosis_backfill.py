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
#   # 4. Cover everyone else too (Cancelled/No-show/Rescheduled/etc --
#   #    normally skipped), IN ADDITION TO the normal Seen patients:
#   python historical_diagnosis_backfill.py --mode unique-patients \
#       --start-date 2026-06-02 --end-date 2026-09-16 --include-not-seen
#
#   # 5. Once the Seen patients for a range are already fully done, mop up
#   #    ONLY the not-seen ones -- Seen patients are excluded from the batch
#   #    entirely (not just auto-skipped-if-already-done):
#   python historical_diagnosis_backfill.py --mode unique-patients \
#       --start-date 2026-06-02 --end-date 2026-09-16 --only-not-seen
#
# Other useful flags (combine with any of the above):
#   --include-not-seen / --only-not-seen (mutually exclusive)
#                                  attempt patients with NO Seen visit in range
#                                  (Cancelled/No-show/etc) using their latest visit
#                                  overall as the chart-open anchor -- Notes is never
#                                  touched either way, so this just needs the chart to
#                                  exist at all. --include-not-seen adds them alongside
#                                  Seen patients; --only-not-seen excludes Seen
#                                  patients from the batch entirely. Either way, the
#                                  summary JSON's "totals" always counts the FULL
#                                  seen+not-seen population for the range, regardless
#                                  of which of these two flags (if any) is passed.
#   --headless                    no visible Chrome window for this run (safe once
#                                  your profile has already done PF's one-time OTP
#                                  login headed; --headed forces a window back on)
#   --patient-guid <guid>          limit to one patient, by ehr_patient_guid. unique-
#                                  patients mode: always forces a real PF pull for that
#                                  one patient (bypasses the DB pre-check and the
#                                  cross-file already-delivered skip). Given WITHOUT
#                                  --start-date/--end-date, skips Schedule discovery
#                                  entirely and goes straight to that patient's chart
#                                  (name/dob scraped live off the chart, today's date
#                                  used as the nominal appt_date) -- no date range
#                                  needed at all in that case:
#                                    python historical_diagnosis_backfill.py \
#                                        --mode unique-patients \
#                                        --patient-guid <ehr_patient_guid>
#                                  Pass a date range alongside --patient-guid instead
#                                  for the old behavior (scan that range on the
#                                  Schedule, then filter down to this one GUID), e.g.
#                                  to fold this patient's result into a specific
#                                  range's own summary file:
#                                    python historical_diagnosis_backfill.py \
#                                        --mode unique-patients --include-not-seen \
#                                        --start-date 2026-06-01 --end-date 2026-09-30 \
#                                        --patient-guid <ehr_patient_guid>
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

Both modes reprint with only Demographics/Insurance/Diagnoses checked (Print
Chart's Notes panel is never opened or touched at all -- Facesheet content is
patient-level, not tied to any one visit's SOAP note, and this run doesn't
need one) and forward each fresh PDF straight to the RCM backend's
pfFacesheetProcessing.processFacesheet mutation -- bypassing the normal
zip-and-upload-to-Azure delivery path (pf_sync_pkg/rcm_upload.py) entirely,
exactly as instructed: call the backend directly from the just-downloaded
PDF, before any zip step exists.

Every call is flagged specialHistoricalDiagnosisRun=True (see
myops/ehr/pf_facesheet_processor.py's _call_facesheet_processing_api) so the
backend can tell this apart from the normal nightly/refresh path -- confirmed
live: the backend responds with either {"status": "special_run_updated",
historicalDiagnosesSaved, visitsUpdated, entriesRefreshed} when it actually
saved something, or {"status": "skipped", "reason": ...} (extraction_empty,
facility_not_resolved, ocr_extraction_failed, patient_header_not_resolved)
when it received the file but declined to.

This never touches Azure Blob Storage and never triggers
myops/ehr/pf_facesheet_processor.py's normal blob-scanning job.

--mode unique-patients ALWAYS classifies and counts the full Seen + not-Seen
population for [--start-date, --end-date] regardless of --include-not-seen/
--only-not-seen (those two only decide who gets a chart actually opened this
run) -- the summary JSON's top-level "totals" (total_patients/seen/not_seen/
success/failure/not_yet_attempted) and each patient's own entry (seen flag,
outcome, and the backend's full response detail when reached) are always
kept current for the whole range, seeded immediately at the start of every
run and never clobbered for a GUID that's already been processed.

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

# QueueRecord.status_reason marker set on synthetic records built from a
# patient with NO Seen visit in range (discover_unique_patients_from_schedule)
# -- checked wherever behavior needs to differ for a forced not-seen pull vs a
# normal Seen one (is_ignored bypass, batch counts, backend "seen" flag).
NOT_SEEN_STATUS_REASON = "unique_patient_historical_diagnosis_pull_not_seen"


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
    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Run Chrome headless (no visible window) for this run only -- overrides "
            "PF_PLAYWRIGHT_HEADLESS from .env without changing it. Safe once your "
            "chrome_user_data_dir profile has already completed PF's one-time OTP "
            "login in headed mode; if PF ever challenges for OTP again, re-run headed "
            "(--headed, or drop both flags) to solve it."
        ),
    )
    headless_group.add_argument(
        "--headed",
        action="store_true",
        help="Force a visible Chrome window for this run, overriding .env the other way.",
    )
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
        help=(
            "Inclusive; blank = no lower bound. Required for --mode unique-patients "
            "UNLESS --patient-guid is also given with no dates at all -- that skips "
            "Schedule discovery entirely and goes straight to that patient's chart "
            "(see --patient-guid)."
        ),
    )
    parser.add_argument(
        "--end-date",
        default="",
        help=(
            "Inclusive; blank = no upper bound. Required for --mode unique-patients "
            "UNLESS --patient-guid is also given with no dates at all (see --patient-guid)."
        ),
    )
    parser.add_argument(
        "--patient-guid",
        default="",
        help=(
            "Limit the run to one patient (ehr_patient_guid). unique-patients mode: "
            "also forces a real PF pull for that one patient, bypassing both the DB "
            "pre-check and the cross-file already-delivered skip -- a targeted "
            "single-patient request always actually runs, regardless of what's "
            "already on file. Given WITHOUT --start-date/--end-date, skips Schedule "
            "discovery entirely and goes straight to that patient's chart (name/dob "
            "scraped live off the chart itself, today's date used as the nominal "
            "appt_date) -- no date range needed at all in that case, since the only "
            "reason a range exists elsewhere is to let a Schedule scan happen to "
            "land on a day this patient appears; a known GUID doesn't need that. "
            "Pass a date range alongside --patient-guid instead if you want the old "
            "behavior (scan that range on the Schedule, then filter to this GUID)."
        ),
    )
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
        "--redo",
        action="store_true",
        help=(
            "unique-patients mode only: also reprocess patients already marked "
            "special_run_updated/already_in_db in the summary file (default: "
            "auto-skipped -- everyone else, including every skipped_<reason> "
            "outcome, is already retried on a plain rerun with no flags at "
            "all), and skip the DB pre-check too -- pull from PF regardless "
            "of what's already on file."
        ),
    )
    parser.add_argument(
        "--skip-db-check",
        action="store_true",
        help=(
            "unique-patients mode only: don't check EDI_Tebra.patient_header for "
            "an existing historical_diagnosis before pulling from PF (default: "
            "checked, and any patient who already has one there is skipped "
            "entirely -- no chart opened, no backend call)."
        ),
    )
    not_seen_group = parser.add_mutually_exclusive_group()
    not_seen_group.add_argument(
        "--include-not-seen",
        action="store_true",
        help=(
            "unique-patients mode only: attempt patients who had NO Seen visit in "
            "range (Cancelled/No-show/etc, normally reported in not_seen_in_range "
            "and skipped) IN ADDITION TO the normal Seen patients. Uses their "
            "latest visit overall as the chart-open anchor; process_one_record's "
            "existing most-recent-note fallback still decides whether anything is "
            "actually printable -- a patient with a genuinely empty chart correctly "
            "ends up 'review', not a crash. Bypasses is_ignored for exactly these "
            "records (their real appointment status is very likely in "
            "DEFAULT_IGNORED_STATUSES, which would otherwise skip them before ever "
            "opening the chart)."
        ),
    )
    not_seen_group.add_argument(
        "--only-not-seen",
        action="store_true",
        help=(
            "unique-patients mode only: same not-seen handling as "
            "--include-not-seen, but the batch is restricted to ONLY those "
            "patients -- normal Seen patients are excluded entirely, even if "
            "they haven't been delivered yet. Use once the Seen patients for a "
            "range are already done and you just want to mop up the rest."
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
    if (
        args.mode == "unique-patients"
        and not (args.start_date and args.end_date)
        and not args.patient_guid
    ):
        parser.error(
            "--mode unique-patients requires either both --start-date and --end-date, "
            "or --patient-guid (with no dates, for a direct GUID-only lookup)."
        )
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
    patient (by ehr_patient_guid) found in that range -- ALWAYS both those
    with at least one Seen appointment AND those with none (Cancelled/
    No-show/Rescheduled/etc), each tagged via status_reason so callers can
    tell them apart. Every patient is classified regardless of
    --include-not-seen/--only-not-seen -- those two flags decide who
    run_unique_patients actually opens a chart for this run, not who gets
    counted; the seen/not-seen population total needs everyone either way.
    `never_seen` is the same not-seen set again, as human-readable strings
    for the console printout.

    Representative visit per patient: the MOST RECENT Seen appointment date
    in range for a Seen patient, or the latest visit overall (whatever its
    real status) for a not-seen one. The Diagnoses/notes sections reflect
    the patient's chart as of print time regardless of which visit's chart
    is open, so any visit would technically work -- most recent is the
    safest choice since it's likeliest to have a SOAP note on file.

    These records are intentionally never written to the local queue (see
    module docstring) -- a synthetic one-row-per-patient shape doesn't fit
    its visit-level (guid, date) primary key, so this stays a side,
    in-memory-only discovery.

    Stops scanning further days early once at least (--skip + --limit)
    unique Seen patients have been found -- --limit alone isn't the right
    threshold here: with --skip N set, the first N found get sliced away in
    run_unique_patients, so stopping at exactly --limit would silently hand
    back an empty (or too-small) batch. 0 (either flag unset) walks the full
    range, same as before this existed. This only bounds how many DAYS get
    scanned, not how many end up in the final batch -- run_unique_patients
    still applies the real skip/limit slice (and the completed-guid filter)
    to whatever this returns.

    This early-stop is disabled entirely (full range always scanned) under
    --only-not-seen/--include-not-seen: "never marked Seen" can only be
    confirmed once every visit for that GUID in the whole range has been
    seen -- a patient with a non-Seen visit on day 1 and a Seen one on day
    50 would be misclassified as not-seen if we stopped after day 1's count
    looked sufficient. No such risk on the Seen side: one Seen visit
    anywhere settles that GUID for good, regardless of what's scanned
    afterward. A plain --limit test with neither flag set still early-stops
    on Seen count alone (fast smoke-testing stays fast); its seen/not-seen
    population TOTALS just won't be accurate off a truncated scan -- only a
    full, no-limit run's totals should be trusted for reporting.
    """
    from pf_sync_pkg import patient_scraper as ps

    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    schedule_config = ScheduleScrapeConfig.load(args.schedule_config_json)

    stop_after = 0 if (args.only_not_seen or args.include_not_seen) else (
        (args.skip + args.limit) if args.limit > 0 else 0
    )
    stop_when = None
    if stop_after > 0:
        def stop_when(results):  # noqa: F811
            unique_seen = {
                a.patient.ehr_patient_guid
                for a in results
                if a.patient.ehr_patient_guid and is_seen_status(a.patient.appointment_status, config)
            }
            return len(unique_seen) >= stop_after

    appointments = ps.discover_appointments_via_schedule_range(
        page, start_date, end_date, config=schedule_config, stop_when=stop_when
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
        if seen_visits:
            latest = max(seen_visits, key=lambda v: v.appointment_date)
            not_seen = False
        else:
            rp = visits[0].patient
            never_seen.append(
                f"{rp.first_name} {rp.last_name} ({guid}) -- {len(visits)} visit(s) in range, "
                f"none marked Seen"
            )
            # No Seen visit anywhere in range -- always classify and build a
            # record for this patient too (regardless of --include-not-seen/
            # --only-not-seen: those two flags decide who actually gets
            # PROCESSED this run, not who gets counted -- run_unique_patients
            # needs the full seen+not-seen population to report accurate
            # totals). Falls back to this patient's latest visit overall
            # (whatever its real status is: Cancelled, No-show, Confirmed,
            # ...) as the chart-open anchor -- process_candidate's
            # skip_note_selection=True means no note is ever selected here
            # anyway, so what matters is just that the chart itself exists.
            latest = max(visits, key=lambda v: v.appointment_date)
            not_seen = True

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
                # is_ignored(record, config) in process_candidate would
                # otherwise skip these before ever opening the chart --
                # Cancelled/No-show/Rescheduled are all in
                # DEFAULT_IGNORED_STATUSES. This status_reason is how
                # run_unique_patients's loop recognizes a forced pull and
                # tells process_candidate to bypass that check for exactly
                # (and only) these records.
                status_reason=(
                    NOT_SEEN_STATUS_REASON
                    if not_seen else "unique_patient_historical_diagnosis_pull"
                ),
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )

    records.sort(key=lambda r: r.patient_name or "")
    return records, never_seen


def discover_single_patient_by_guid(page, args: argparse.Namespace) -> list:
    """True GUID-only lookup, used by run_unique_patients when --patient-guid
    is given WITHOUT --start-date/--end-date: skip Schedule discovery
    entirely and go straight to this one patient's own chart.

    --start-date/--end-date exist only because discover_unique_patients_from_
    schedule's sole data source is Practice Fusion's Schedule -- a day-by-day
    calendar with no "look up by GUID" capability, so a date range was the
    only way to make the scan land on a day this patient appears (which also
    happened to be where patient_name/dob/a representative visit date came
    from). A known GUID doesn't need any of that: it goes straight to
    patient_summary_url(guid), same as process_one_record itself does a
    moment later for the actual print. name/dob are scraped directly off the
    chart page (same data/elements patient_scraper.py's own profile scrape
    reads via safe_text_by_data(page, "full-name"/"birth-date-text")), and
    TODAY is used as the nominal appointment_date -- historical_diagnosis is
    patient-level data, not tied to any one visit; the backend's manifest
    schema just requires appt_date to be non-null, not tied to a real past
    appointment.

    process_one_record navigates to this same summary_url again right after
    this returns -- a second, identical page.goto a moment later is harmless.
    It does the exact same "goto summary_url, then try/except-swallow a
    wait_for on PATIENT_NAME_SELECTOR" dance immediately afterward (see its
    skip_encounter_lookup branch), which is why the wait below is swallowed
    the same way here rather than left to raise: confirmed live 2026-09-23
    that this element isn't reliably visible-in-time on the summary page even
    for a perfectly valid GUID, so a hard failure here was rejecting good
    patients before Print Chart was ever attempted. safe_text_by_data itself
    never raises either way (returns "" on a missing element), so a slow- or
    non-rendering name/dob just means a blanker manifest entry for this
    patient, not a failed run -- process_one_record's own subsequent
    navigation and Print Chart attempt are the real authority on whether this
    GUID actually resolves to a chart at all.
    """
    from pf_sync_pkg.chart_ui import patient_summary_url
    from pf_sync_pkg.constants import PATIENT_NAME_SELECTOR
    from pf_sync_pkg.patient_scraper import safe_text_by_data

    page.goto(patient_summary_url(args.patient_guid), wait_until="domcontentloaded")
    try:
        page.locator(PATIENT_NAME_SELECTOR).first.wait_for(state="visible", timeout=15_000)
    except Exception:
        pass
    patient_name = safe_text_by_data(page, "full-name") or args.patient_guid
    patient_dob = safe_text_by_data(page, "birth-date-text")

    record = QueueRecord(
        row_id=str(uuid.uuid4()),
        practice=args.practice,
        ehr_patient_guid=args.patient_guid,
        patient_name=patient_name,
        patient_dob=patient_dob,
        appointment_date=datetime.now().strftime("%Y-%m-%d"),
        appointment_status="seen",
        appointment_type="",
        provider="",
        service_location="",
        patient_id="",
        patient_match_status="matched",
        patient_match_method="direct_guid_lookup",
        status="ready",
        status_reason="unique_patient_historical_diagnosis_pull",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    return [record]


def derive_outcome_from_backend_status(backend_response: dict) -> str:
    """The stored outcome for a delivered facesheet IS the backend's own
    verdict, not a generic "the POST succeeded" label -- see
    runPfFacesheetSpecialHistoricalDiagnosisRun's return shape:
      - {"status": "special_run_updated", ...}      -> real save
      - {"status": "skipped", "reason": "..."}       -> declined, not saved
    A single "sent_to_backend" outcome used to cover both, which made
    _completed_records treat a declined delivery as permanently done --
    confirmed live 2026-09-22: 209+21 patients stuck that way, never
    retried, with no record of which kind of decline it even was.
    Encoding the reason directly into the outcome string (skipped_<reason>)
    means a plain future run -- no special flags -- automatically retries
    anyone who wasn't a real save, since _completed_records only recognizes
    "special_run_updated"/"already_in_db" as done.
    """
    status = backend_response.get("status") if backend_response else None
    if status == "special_run_updated":
        return "special_run_updated"
    if status == "skipped":
        return f"skipped_{backend_response.get('reason') or 'unknown'}"
    return f"backend_unexpected_status_{status or 'none'}"


def process_candidate(page, record, config, args, session, force_process: bool = False) -> tuple:
    """Reprint one chart and, unless disabled, deliver it straight to the
    backend. Returns (outcome, backend_response) -- backend_response is the
    raw dict runPfFacesheetSpecialHistoricalDiagnosisRun returned (status,
    reason, historicalDiagnosesSaved, visitsUpdated, entriesRefreshed,
    logFile) when the backend was actually called and responded, else None.

    force_process=True skips the is_ignored check -- used only for
    --include-not-seen records (status_reason ends in "_not_seen"), whose
    whole point is to attempt a chart despite a Cancelled/No-show/etc
    status that DEFAULT_IGNORED_STATUSES would otherwise skip outright.
    Identity resolution (patient_match_status/ehr_patient_guid) is still
    required either way -- that's about whether we can open the right
    chart at all, not about the appointment's status.
    """
    if not force_process and is_ignored(record, config):
        print("  ignored", flush=True)
        return "ignored", None
    if record.patient_match_status != "matched" or not record.ehr_patient_guid:
        print("  needs_attention: patient not resolved", flush=True)
        return "needs_attention", None

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
            # allow_most_recent_note_fallback is irrelevant here -- left at its
            # default (False); skip_note_selection=True below means
            # process_one_record never reaches note selection at all.
            #
            # This whole tool only cares about Demographics/Insurance/Diagnoses
            # for historical-diagnosis extraction -- the SOAP note is never
            # needed, and Practice Fusion's Notes panel is never opened,
            # selected, or cleared; Print Chart fires with whatever its own
            # default note-selection state already is. Also sidesteps
            # PRINT_DOCUMENT_NOT_FOUND false rejections that would otherwise
            # hit on forced not-seen pulls whose only available note is dated
            # earlier than this record's appointment_date.
            skip_note_selection=True,
        )
    except Exception as exc:
        state = handle_process_error(record, config, exc)
        print(f"  {state}: {record.error_message}", flush=True)
        return state, None
    finally:
        # Always tear the Print Chart modal down, success or failure, so it
        # can't be left open over the next patient's chart (same rule
        # process_records_on_page follows).
        close_print_chart(page, config)

    print(f"  reprinted in {record.elapsed_seconds:.3f}s -> {record.pdf_path}", flush=True)

    if args.dry_run:
        return "reprinted_dry_run", None

    if args.no_backend_call:
        return "reprinted_only", None

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
        return "backend_failed", None

    if not args.keep_local_pdfs:
        pdf_path.unlink(missing_ok=True)
    # The outcome IS the backend's own verdict, not a generic "we POSTed
    # successfully" label -- a delivered-but-declined response used to be
    # stored as the same "sent_to_backend" outcome as a real save, which
    # made the auto-skip logic treat both as permanently done and never
    # retry the declined ones. See derive_outcome_from_backend_status's
    # docstring for the exact mapping.
    return derive_outcome_from_backend_status(result), result


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
            outcome, _backend_response = process_candidate(page, record, config, args, session)
            counts[outcome] = counts.get(outcome, 0) + 1
            # Side, direct-to-backend delivery -- record.status is left exactly
            # as process_one_record/handle_process_error set it (normally
            # unchanged from "processed" on the happy path), only the derived
            # fields (pdf_path, elapsed_seconds, etc.) get persisted.
            save_row(args.queue_json, record)
        return counts

    return browser_command_wrapper(args, callback)


def run_unique_patients(args: argparse.Namespace) -> dict:
    # Printed up front, same as _start_tee_logging's log-path line in main() --
    # this file is where every patient's outcome/backend detail actually
    # lands, so it should be exactly as discoverable as the console log path,
    # not something only visible buried in the final "Done: {...}" summary.
    print(
        f"[HISTORICAL-DIAGNOSIS-BACKFILL] Manifest for this run: "
        f"{_unique_patients_summary_path(args)}",
        flush=True,
    )

    # Discovery itself requires a real PF session (the patient list can only
    # come from PF's own Schedule), so -- unlike reprocess-queue mode --
    # --plan-only still logs in here; it just stops before opening any chart
    # or calling the backend.
    config = build_full_sync_by_date_config(args)
    session = None if (args.dry_run or args.no_backend_call or args.plan_only) else _login(print)

    result: dict = {}

    def callback(page):
        guid_only = bool(args.patient_guid) and not (args.start_date and args.end_date)
        if guid_only:
            # No date range at all -- skip Schedule discovery entirely and go
            # straight to this one patient's chart. See
            # discover_single_patient_by_guid's docstring for why the date
            # range isn't needed here even though it is for the normal scan.
            all_records = discover_single_patient_by_guid(page, args)
            never_seen = []
            print(
                f"[HISTORICAL-DIAGNOSIS-BACKFILL] --patient-guid {args.patient_guid} resolved "
                f"directly from PF's chart -- {all_records[0].patient_name} -- no Schedule scan, "
                f"no date range needed.",
                flush=True,
            )
        else:
            all_records, never_seen = discover_unique_patients_from_schedule(page, args, config)

        seen_records = [r for r in all_records if r.status_reason != NOT_SEEN_STATUS_REASON]
        not_seen_records = [r for r in all_records if r.status_reason == NOT_SEEN_STATUS_REASON]

        if not guid_only:
            print(
                f"[HISTORICAL-DIAGNOSIS-BACKFILL] {len(all_records)} unique patient(s) total between "
                f"{args.start_date} and {args.end_date} -- {len(seen_records)} Seen, "
                f"{len(not_seen_records)} not Seen.",
                flush=True,
            )

        # Seed EVERY discovered patient into the summary right away, never
        # overwriting an existing entry -- so the file always reflects the
        # full July-September population (with its seen/not-seen split) even
        # for patients this particular run's mode won't attempt at all.
        if args.only_not_seen:
            to_consider = not_seen_records
        elif args.include_not_seen:
            to_consider = all_records
        else:
            to_consider = seen_records

        if args.patient_guid:
            # A targeted single-patient request: narrow to just this GUID
            # (whichever population it's already in -- Seen or not-seen,
            # regardless of --include-not-seen/--only-not-seen) and always
            # actually run it for real; see the DB-check/completed-records
            # bypasses below.
            to_consider = [r for r in all_records if r.ehr_patient_guid == args.patient_guid]
            if not to_consider:
                where = (
                    "on their own chart"
                    if guid_only
                    else f"on the Schedule for {args.start_date} to {args.end_date}"
                )
                print(
                    f"[HISTORICAL-DIAGNOSIS-BACKFILL] --patient-guid {args.patient_guid} was not "
                    f"found {where} -- nothing to do.",
                    flush=True,
                )

        to_consider_guids = {r.ehr_patient_guid for r in to_consider}
        _apply_patient_updates(
            args, never_seen,
            [
                _pending_entry(r, in_scope=r.ehr_patient_guid in to_consider_guids)
                for r in all_records
            ],
            overwrite=False,
        )

        # Check the RCM DB before ever touching PF: a patient whose
        # historical_diagnosis is already on file needs neither a chart
        # opened nor a backend call this run. One batched query for the
        # whole population, not one per patient. Skipped entirely for a
        # targeted --patient-guid request -- that's an explicit "pull this
        # one now" ask, not something the DB pre-check should second-guess.
        if not args.redo and not args.skip_db_check and not args.patient_guid:
            already_in_db = _fetch_existing_historical_diagnosis(
                [r.ehr_patient_guid for r in to_consider]
            )
            if already_in_db:
                print(
                    f"[HISTORICAL-DIAGNOSIS-BACKFILL] {len(already_in_db)} patient(s) already have "
                    f"historical_diagnosis on file in the DB -- skipping the PF pull for them "
                    f"(pass --skip-db-check or --redo to force a real pull anyway).",
                    flush=True,
                )
                _apply_patient_updates(
                    args, never_seen,
                    [
                        _already_in_db_entry(r, already_in_db[r.ehr_patient_guid])
                        for r in to_consider if r.ehr_patient_guid in already_in_db
                    ],
                )
                to_consider = [r for r in to_consider if r.ehr_patient_guid not in already_in_db]

        if not args.patient_guid and not args.redo:
            done_records = _completed_records(args)
            if done_records:
                carried = [r for r in to_consider if r.ehr_patient_guid in done_records]
                to_consider = [r for r in to_consider if r.ehr_patient_guid not in done_records]
                print(
                    f"[HISTORICAL-DIAGNOSIS-BACKFILL] {len(carried)} patient(s) already "
                    f"delivered successfully in a prior run -- auto-skipped (pass --redo to reprocess "
                    f"them anyway).",
                    flush=True,
                )
                if carried:
                    # Carry the real prior result into THIS file too -- see
                    # _completed_records' docstring for why skipping alone
                    # (without this) left a permanently wrong "pending"
                    # placeholder behind for every patient resolved this way.
                    _apply_patient_updates(
                        args, never_seen,
                        [
                            _carry_forward_entry(r, done_records[r.ehr_patient_guid])
                            for r in carried
                        ],
                    )

        batch = to_consider[args.skip :]
        if args.limit > 0:
            batch = batch[: args.limit]

        print(
            f"[HISTORICAL-DIAGNOSIS-BACKFILL] {len(to_consider)} patient(s) eligible for this run's mode; "
            f"this batch: {len(batch)} (skip={args.skip}, limit={args.limit or 'none'}).",
            flush=True,
        )
        for record in batch:
            tag = " (not seen -- forced attempt)" if record.status_reason == NOT_SEEN_STATUS_REASON else ""
            print(
                f"  - {record.patient_name} | representative visit {record.appointment_date} "
                f"| {record.ehr_patient_guid}{tag}",
                flush=True,
            )

        if args.plan_only or not batch:
            totals = _apply_patient_updates(args, never_seen, [], overwrite=False)
            result.update({
                "batch_size": len(batch), "plan_only": args.plan_only, "totals": totals,
                "summary_path": str(_unique_patients_summary_path(args)),
            })
            return result

        # Mark this batch "pending" right before working through it.
        _apply_patient_updates(args, never_seen, [_pending_entry(r, in_scope=True) for r in batch])

        counts: dict = {"batch_size": len(batch)}
        totals: dict = {}
        for index, record in enumerate(batch, start=1):
            forced = record.status_reason == NOT_SEEN_STATUS_REASON
            print(
                f"[{index}/{len(batch)}] {record.patient_name} | "
                f"{record.appointment_date} | {record.ehr_patient_guid}"
                f"{' (not seen -- forced attempt)' if forced else ''}",
                flush=True,
            )
            outcome, backend_response = process_candidate(
                page, record, config, args, session, force_process=forced
            )
            counts[outcome] = counts.get(outcome, 0) + 1
            # Write after EVERY patient, not just at the end -- a crash
            # partway through this batch then only ever costs the one
            # in-flight patient, not the whole batch.
            totals = _apply_patient_updates(
                args, never_seen, [_patient_entry(record, outcome, backend_response)]
            )
            # No save_row here: these are synthetic, one-per-patient records
            # that don't belong in the visit-level queue (see module
            # docstring) -- the JSON summary file is their only record.

        result.update(counts)
        result["totals"] = totals
        result["summary_path"] = str(_unique_patients_summary_path(args))
        return result

    return browser_command_wrapper(args, callback)


def _unique_patients_summary_path(args: argparse.Namespace) -> Path:
    if args.patient_guid and not (args.start_date and args.end_date):
        # GUID-only lookup (see discover_single_patient_by_guid) -- no date
        # range to name the file after, so key it by GUID instead. Singular
        # "unique_patient_" (not "unique_patients_") keeps it visually
        # distinct from a date-ranged file while still matching
        # _completed_records' "unique_patient*.json" glob below.
        return Path(args.downloads_dir) / f"unique_patient_{args.patient_guid}.json"
    return Path(args.downloads_dir) / f"unique_patients_{args.start_date}_to_{args.end_date}.json"


def _completed_records(args: argparse.Namespace) -> dict:
    """The FULL prior patient entry (not just the GUID) for every GUID
    already marked "special_run_updated" or "already_in_db" in ANY
    unique_patient*.json summary file under --downloads-dir (date-ranged or
    single-GUID) -- those are the only two outcomes that mean
    historical_diagnosis was actually saved.
    Everything else ("pending", "not_processed_this_run", "failed",
    "review", "needs_attention", "backend_failed", "reprinted_only",
    "reprinted_dry_run", "ignored", and every "skipped_<reason>" a declined
    backend response produces) stays eligible for a future run -- including
    a plain rerun with no special flags, since none of those count as done
    here. Confirmed live 2026-09-22: storing every delivered-but-declined
    response under one generic "sent_to_backend" outcome made this function
    treat a real save and a permanent decline as equally "complete", so
    230+ declined/unconfirmed patients were never retried and nobody
    noticed until the backend detail was actually inspected.

    Pools across EVERY summary file (both date-ranged unique_patients_*.json
    and single-GUID unique_patient_<guid>.json files from a GUID-only lookup
    -- see _unique_patients_summary_path), not just the one matching this
    run's exact --start-date/--end-date -- confirmed live 2026-09-21: July and
    August were each already fully delivered under their own per-month
    date ranges (unique_patients_2026-07-01_to_2026-07-31.json,
    unique_patients_2026-08-01_to_2026-08-31.json), then a later run
    spanning --start-date 2026-07-01 --end-date 2026-09-21 looked only for
    a summary file named after THAT exact range, found none, and
    reprocessed all ~780 already-delivered patients from scratch before
    anyone noticed. A patient delivered under any date range must stay
    skipped regardless of what range a later call happens to use.

    Returning the full entry (not just a membership set) lets the caller
    carry the real prior result into THIS run's own summary file too --
    confirmed live 2026-09-22: returning only a set correctly skipped
    reprocessing these patients, but left them stuck at "pending" in this
    file forever (never updated to reflect that they're actually done),
    so this file's own "not_yet_attempted" total stayed permanently wrong
    for every patient resolved this way, even though nothing was actually
    left to do for them.
    """
    completed: dict = {}
    downloads_dir = Path(args.downloads_dir)
    if not downloads_dir.is_dir():
        return completed
    for path in downloads_dir.glob("unique_patient*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in data.get("patients", []):
            guid = p.get("ehr_patient_guid")
            if guid and p.get("outcome") in ("special_run_updated", "already_in_db"):
                completed.setdefault(guid, p)
    return completed


def _fetch_existing_historical_diagnosis(guids: list) -> dict:
    """Batched pre-check against the RCM database's own EDI_Tebra.patient_header
    table (same RCM_DB_* credentials pf_sync_pkg/store.py uses for the queue
    tables, connected with the same connect_timeout/statement_timeout guard --
    see that module's _connect docstring for why an unbounded connection to
    this shared Azure Postgres instance is dangerous). One row per Practice
    Fusion patient already has historical_diagnosis written by an earlier
    successful special run: source='practice_fusion', source_id=<the same
    ehr_patient_guid this script already uses> -- confirmed live against real
    patientHeaderId values from prior 'special_run_updated' backend
    responses (Adam Guzman/Adam Sigle, 2026-09-22).

    Runs ONE query for the whole discovered population, not one per patient --
    this is meant to replace hundreds of PF chart-opens with a single cheap
    lookup, not add hundreds of small ones.

    Returns {guid: {"patient_header_id": ..., "historical_diagnosis_count": N}}
    for only the GUIDs that already have a non-empty historical_diagnosis; a
    GUID missing from the result still needs a real PF pull. Any DB error
    (network hiccup, credentials, schema drift) is swallowed and treated as
    "unknown" -- the safe default is falling through to a real PF pull for
    everyone, never silently skipping someone we couldn't actually confirm.
    """
    if not guids:
        return {}
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("RCM_DB_HOST", "").strip(),
            dbname=os.environ.get("RCM_DB_NAME", "").strip(),
            user=os.environ.get("RCM_DB_USER", "").strip(),
            password=os.environ.get("RCM_DB_PASSWORD", "").strip(),
            sslmode="require",
            connect_timeout=10,
            options="-c statement_timeout=20000",
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT TRIM(source_id) AS guid, patient_header_id,
                           jsonb_array_length(historical_diagnosis) AS diag_count
                    FROM "EDI_Tebra".patient_header
                    WHERE source = 'practice_fusion'
                      AND TRIM(source_id) = ANY(%s)
                      AND historical_diagnosis IS NOT NULL
                      AND jsonb_array_length(historical_diagnosis) > 0
                    """,
                    (list(guids),),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        print(
            f"[HISTORICAL-DIAGNOSIS-BACKFILL] DB pre-check failed ({exc}) -- "
            f"falling back to a real PF pull for everyone this run.",
            flush=True,
        )
        return {}

    return {
        guid: {"patient_header_id": str(patient_header_id), "historical_diagnosis_count": diag_count}
        for guid, patient_header_id, diag_count in rows
    }


def _pending_entry(record, in_scope: bool) -> dict:
    """Placeholder patient entry: known to exist, not yet (re)attempted this
    run. in_scope=False marks a patient this run's --mode/--only-not-seen/
    --include-not-seen selection doesn't even consider (e.g. a Seen patient
    during an --only-not-seen run) -- distinct from "pending" (queued, just
    not gotten to yet) so the report can tell "haven't tried" apart from
    "this run was never going to try". Both are overwrite=False seeds: never
    clobbers an existing (possibly already-successful) entry for that GUID.
    """
    return {
        "patient_name": record.patient_name,
        "ehr_patient_guid": record.ehr_patient_guid,
        "representative_appointment_date": record.appointment_date,
        "seen": record.status_reason != NOT_SEEN_STATUS_REASON,
        "outcome": "pending" if in_scope else "not_processed_this_run",
        "success": None,
        "backend_status": None,
        "backend_reason": None,
        "historical_diagnoses_saved": None,
        "visits_updated": None,
        "entries_refreshed": None,
        "backend_log_file": None,
        "error_message": "",
    }


def _compute_success(outcome: str) -> "bool | None":
    """None = not a real attempt yet (pending/not attempted/deliberately
    non-delivering test run) -- neither success nor failure. True only for
    "special_run_updated" (the backend actually saved historicalDiagnoses)
    or "already_in_db" (the DB pre-check already found data on file, no PF
    pull or backend call needed). Everything else -- including every
    "skipped_<reason>" outcome derive_outcome_from_backend_status produces
    for a delivered-but-declined response -- is a real failure for this
    report's purposes, even though our own POST succeeded.
    """
    if outcome in ("pending", "not_processed_this_run", "reprinted_only", "reprinted_dry_run"):
        return None
    return outcome in ("already_in_db", "special_run_updated")


def _already_in_db_entry(record, db_info: dict) -> dict:
    """Real, final result for a patient the DB pre-check found already has
    historical_diagnosis on file -- no PF chart was opened and no backend
    call was made this run, so backend_status is 'already_in_db' (not one of
    the real API's own status values) to make that visible in the report.
    """
    entry = _pending_entry(record, in_scope=True)
    entry["outcome"] = "already_in_db"
    entry["backend_status"] = "already_in_db"
    entry["historical_diagnoses_saved"] = db_info.get("historical_diagnosis_count")
    entry["error_message"] = (
        f"Skipped PF pull -- historical_diagnosis already on file "
        f"(patient_header_id={db_info.get('patient_header_id')})."
    )
    entry["success"] = True
    return entry


def _carry_forward_entry(record, prior: dict) -> dict:
    """Reuse a prior run's real result for this GUID found in a DIFFERENT
    summary file (via _completed_records) inside THIS file too -- keeps
    THIS run's own identity/date fields (representative_appointment_date
    can legitimately differ file to file; historical_diagnosis is
    patient-level, not tied to which visit anchored the pull) but copies
    every outcome-related field across so this file's own totals reflect
    reality instead of leaving a phantom "pending" placeholder for a
    patient who's actually already done.
    """
    entry = _pending_entry(record, in_scope=True)
    for key in (
        "outcome", "success", "backend_status", "backend_reason",
        "historical_diagnoses_saved", "visits_updated", "entries_refreshed",
        "backend_log_file", "error_message",
    ):
        entry[key] = prior.get(key)
    return entry


def _patient_entry(record, outcome: str, backend_response) -> dict:
    """Real per-patient result after process_candidate runs: our own outcome
    plus, when the backend was actually reached, its full response detail --
    see runPfFacesheetSpecialHistoricalDiagnosisRun's return shape
    (status/reason/historicalDiagnosesSaved/visitsUpdated/entriesRefreshed/
    logFile).
    """
    entry = _pending_entry(record, in_scope=True)
    entry["outcome"] = outcome
    entry["error_message"] = record.error_message or ""
    if backend_response:
        entry["backend_status"] = backend_response.get("status")
        entry["backend_reason"] = backend_response.get("reason")
        entry["historical_diagnoses_saved"] = backend_response.get("historicalDiagnosesSaved")
        entry["visits_updated"] = backend_response.get("visitsUpdated")
        entry["entries_refreshed"] = backend_response.get("entriesRefreshed")
        entry["backend_log_file"] = backend_response.get("logFile")
    entry["success"] = _compute_success(outcome)
    return entry


_FAILURE_REASON_LABELS = {
    # skipped_<reason> is derive_outcome_from_backend_status's encoding of
    # runPfFacesheetSpecialHistoricalDiagnosisRun's own {"status": "skipped",
    # "reason": ...} response -- these four are the exact reason strings
    # that backend mutation returns (see its docstring/derive_outcome_from_
    # backend_status above). Everything else here is one of this script's
    # OWN outcome values (handle_process_error's states, or a local failure
    # before the backend was ever reached).
    "skipped_extraction_empty": "no data found (extraction empty)",
    "skipped_facility_not_resolved": "facility not resolved",
    "skipped_ocr_extraction_failed": "failed extraction (OCR)",
    "skipped_patient_header_not_resolved": "patient header not resolved",
    "skipped_unknown": "skipped (backend gave no reason)",
    "backend_failed": "backend call failed (network/exception)",
    "needs_attention": "patient not resolved (needs attention)",
    "review": "needs manual review",
    "ignored": "ignored (appointment status excluded)",
}


def _failure_reason_label(outcome: str) -> str:
    """Human-readable label for one failed patient's outcome, used to key the
    "failure_reasons" breakdown in _compute_totals. Falls back to the raw
    outcome string (underscores turned to spaces) for anything not in the
    table above -- e.g. a backend_unexpected_status_<status> outcome, or a
    future skipped_<reason> the backend starts returning that isn't listed
    here yet -- so a new failure mode still shows up in the count under its
    own name instead of silently disappearing into a generic bucket.
    """
    return _FAILURE_REASON_LABELS.get(outcome, (outcome or "unknown").replace("_", " "))


def _compute_totals(patients: list) -> dict:
    failure_reasons: dict = {}
    for p in patients:
        if p.get("success") is False:
            label = _failure_reason_label(p.get("outcome") or "")
            failure_reasons[label] = failure_reasons.get(label, 0) + 1
    return {
        "total_patients": len(patients),
        "seen": sum(1 for p in patients if p.get("seen")),
        "not_seen": sum(1 for p in patients if not p.get("seen")),
        "success": sum(1 for p in patients if p.get("success") is True),
        "failure": sum(1 for p in patients if p.get("success") is False),
        # Most-common reason first -- see _failure_reason_label.
        "failure_reasons": dict(sorted(failure_reasons.items(), key=lambda kv: -kv[1])),
        "not_yet_attempted": sum(1 for p in patients if p.get("success") is None),
    }


def _apply_patient_updates(
    args: argparse.Namespace, never_seen: list, patient_updates: list, overwrite: bool = True
) -> dict:
    """Read-modify-write the summary file and return the freshly recomputed
    totals. Same "converge on one file no matter how many calls it takes"
    pattern pdf_pipeline.write_appointments_metadata_json uses for its
    manifest_run_id merging -- lets multiple --skip/--limit batches (or a
    resumed run after a crash) accumulate into one summary instead of each
    batch producing its own fragment.

    overwrite=True (the normal per-patient-result path): a fresher entry for
    a GUID always replaces the old one. overwrite=False (used once per run
    to seed the full discovered population): only inserts entries for GUIDs
    not already present, so seeding never clobbers an existing, possibly
    already-successful result.
    """
    destination = _unique_patients_summary_path(args)
    destination.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    by_guid = {p["ehr_patient_guid"]: p for p in existing.get("patients", []) if p.get("ehr_patient_guid")}
    for update in patient_updates:
        guid = update["ehr_patient_guid"]
        if overwrite or guid not in by_guid:
            by_guid[guid] = update

    patients = sorted(by_guid.values(), key=lambda p: p.get("patient_name") or "")
    totals = _compute_totals(patients)
    summary = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "totals": totals,
        "patients": patients,
        # Overwritten with whatever the most recent discovery call found --
        # informational only, not merged (discovery itself isn't batched).
        "not_seen_in_range": never_seen if never_seen else existing.get("not_seen_in_range", []),
    }
    destination.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return totals


def run(args: argparse.Namespace) -> dict:
    if args.mode == "unique-patients":
        return run_unique_patients(args)
    return run_reprocess_queue(args)


def main() -> None:
    args = parse_args()

    # Must happen before anything imports/calls into build_browser --
    # pf_sync_pkg.browser._pf_headless() reads this env var fresh at launch
    # time, so setting it here (rather than editing .env) scopes the
    # override to this process only, leaving other PF automation's default
    # (headed, for OTP) untouched.
    if args.headless:
        os.environ["PF_PLAYWRIGHT_HEADLESS"] = "true"
    elif args.headed:
        os.environ["PF_PLAYWRIGHT_HEADLESS"] = "false"

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
