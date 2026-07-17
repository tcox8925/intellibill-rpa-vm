"""
CLI entry point. Infers mode from the args:

  # daily (present day, all practices, missed-charge + charge checks)
  python -m ehr.cli

  # backfill a window
  python -m ehr.cli --practice "PrePost+Tennessee" --start 2026-04-01 --end 2026-04-29

  # target one appointment (produces its own ZIP)
  python -m ehr.cli --appt-id 1519
  python -m ehr.cli --patient "Moore" --date 2026-02-16 --practice "PrePostPlus Germantown"
"""

import argparse
from datetime import datetime

from .selector import WorkSelector
from .pipeline import run


def _date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    ap = argparse.ArgumentParser(description="EHR scrape pipeline")
    ap.add_argument("--practice", default=None, help="Practice name (default: all)")
    ap.add_argument("--start", type=_date, default=None, help="Window start YYYY-MM-DD")
    ap.add_argument("--end", type=_date, default=None, help="Window end YYYY-MM-DD")
    ap.add_argument("--appt-id", default=None, help="Target a single appointment id")
    ap.add_argument("--patient", default=None, help="Target by patient name (ILIKE)")
    ap.add_argument("--date", type=_date, default=None, help="Target a single date")
    ap.add_argument("--skip-patients", action="store_true",
                    help="Skip the patient roster scrape (default: skipped for target mode)")
    ap.add_argument("--scrape-patients", action="store_true",
                    help="Force the patient roster scrape even in target mode")
    ap.add_argument("--no-upload", action="store_true",
                    help="Dry run: build the ZIP locally but skip SFTP upload and "
                         "file_path write-back (rows stay undelivered)")
    args = ap.parse_args()

    # A lone --date is a target-on-date; --start/--end is a backfill window.
    start = args.start
    if args.date and not (args.start or args.end):
        start = args.date

    sel = WorkSelector.from_args(
        start_date=start,
        end_date=args.end,
        appt_id=args.appt_id,
        patient_name=args.patient,
        practice=args.practice,
    )
    print(f"[CLI] Resolved selector: mode={sel.mode} practice={sel.practice} "
          f"start={sel.start_date} end={sel.end_date} appt_id={sel.appt_id} "
          f"patient={sel.patient_name}")

    # Explicit flags override the mode-based default (None = let run() decide).
    scrape_patients = None
    if args.skip_patients:
        scrape_patients = False
    elif args.scrape_patients:
        scrape_patients = True

    run(sel, scrape_patients=scrape_patients, no_upload=args.no_upload)


if __name__ == "__main__":
    main()
