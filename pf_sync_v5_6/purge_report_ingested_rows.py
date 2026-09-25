"""One-time purge of every report-ingested row from ehr.ehr_pf_queue_rows.

2026-09-24 decision: ingest/nightly/full-sync-by-date/facesheet-pull-by-date/
full-sync/refresh are permanently disabled (see pf_sync_pkg/constants.py's
DISABLED_COMMANDS) -- sync-schedules-by-date is the only pipeline that will
ever write to this table again. The name+DOB backfill logic added the same
day to run_sync_schedules_by_date (see its own removal, same commit as this
script) existed only to reconcile with rows the now-dead ingest path left
behind. With ingest gone for good, there is no reason to keep those rows or
that logic around -- this script clears the rows; the logic was removed
directly from cli.py.

A report-ingested row is anything whose patient_match_method is NOT
"discovered_from_schedule" -- i.e. everything ingest.py's record_key()
created and match-patients (fuzzy_name_dob_phone) or a saved manual mapping
(saved_mapping) later touched, plus anything still unmatched ("").

This is a genuine, informed data-loss decision, not a cleanup of redundant
copies -- confirmed live 2026-09-24: 449 report-ingested rows, only 308 of
which duplicate a schedule-discovered row (see dedupe_queue_rows.py). The
other 141 have no schedule-side counterpart at all, including 22 rows with a
real pdf_path from a facesheet that was actually generated. sync-schedules-by
-date's default 3-day lookback will NOT rediscover those dates on its own --
running it once with an explicit --start-date covering the deleted range
before relying on it going forward is how you'd recover equivalent coverage,
not this script.

Usage:
    python3 purge_report_ingested_rows.py                    # dry run, report only
    python3 purge_report_ingested_rows.py --queue-key pf_appointment_queue.json
    python3 purge_report_ingested_rows.py --execute           # writes a backup JSON, then deletes

Safety: dry-run by default; --execute first writes every row about to be
deleted to a timestamped backup JSON (--backup-dir, default: current
directory), then deletes inside one transaction (commits once at the end,
rolls back entirely on any error).
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import find_dotenv, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

load_dotenv(find_dotenv(usecwd=False))

ROWS_TABLE = "ehr.ehr_pf_queue_rows"
SCHEDULE_DISCOVERED_METHOD = "discovered_from_schedule"

SELECT_COLUMNS = [
    "queue_key", "row_id", "ehr_patient_guid", "appointment_date", "patient_name",
    "patient_dob", "patient_match_method", "status", "status_reason", "pdf_path",
    "source_report_name", "created_at", "updated_at",
]


def _connect():
    host = os.environ.get("RCM_DB_HOST", "").strip()
    dbname = os.environ.get("RCM_DB_NAME", "").strip()
    user = os.environ.get("RCM_DB_USER", "").strip()
    password = os.environ.get("RCM_DB_PASSWORD", "").strip()
    if not (host and dbname and user and password):
        raise RuntimeError(
            "RCM_DB_HOST/RCM_DB_NAME/RCM_DB_USER/RCM_DB_PASSWORD are required. Set them in .env."
        )
    return psycopg2.connect(
        host=host, dbname=dbname, user=user, password=password, sslmode="require",
        connect_timeout=10,
        options="-c statement_timeout=30000 -c idle_in_transaction_session_timeout=30000",
    )


def fetch_report_ingested_rows(conn, queue_key: str = ""):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    sql = f"""
        SELECT {', '.join(SELECT_COLUMNS)} FROM {ROWS_TABLE}
        WHERE patient_match_method IS DISTINCT FROM %s
    """
    params = [SCHEDULE_DISCOVERED_METHOD]
    if queue_key:
        sql += " AND queue_key = %s"
        params.append(queue_key)
    cur.execute(sql, params)
    return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue-key", default="", help="Limit to one queue_key (default: every queue in the table).")
    parser.add_argument("--execute", action="store_true", help="Actually delete. Default is dry-run/report only.")
    parser.add_argument("--backup-dir", default=".", help="Where to write the pre-delete backup JSON (only used with --execute).")
    parser.add_argument("--show-limit", type=int, default=25, help="Max rows to print per section (default 25).")
    args = parser.parse_args()

    conn = _connect()
    try:
        rows = fetch_report_ingested_rows(conn, args.queue_key)
        print(
            f"Report-ingested rows found (patient_match_method != '{SCHEDULE_DISCOVERED_METHOD}'): {len(rows)}"
            + (f" (queue_key={args.queue_key})" if args.queue_key else " (all queues)")
        )
        print("  by status:", dict(Counter(r["status"] for r in rows)))
        print("  by patient_match_method:", dict(Counter(r["patient_match_method"] or "(blank)" for r in rows)))

        processed_with_pdf = [r for r in rows if r["status"] == "processed" and r["pdf_path"]]
        print(f"\nRISKY: {len(processed_with_pdf)} of these are 'processed' with a real pdf_path -- the only record that facesheet was ever generated.")
        for r in processed_with_pdf[: args.show_limit]:
            print(f"  - {r['patient_name']} on {r['appointment_date']} -> {r['pdf_path']}")
        if len(processed_with_pdf) > args.show_limit:
            print(f"  ... and {len(processed_with_pdf) - args.show_limit} more")

        if not args.execute:
            print(f"\nDRY RUN -- no rows deleted. {len(rows)} row(s) would be deleted. Re-run with --execute to actually delete.")
            return 0

        if not rows:
            print("\nNothing to delete.")
            return 0

        backup_dir = Path(args.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"report_ingested_rows_purge_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
        backup_path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        print(f"\nBackup of {len(rows)} row(s) about to be deleted written to {backup_path}")

        cur = conn.cursor()
        try:
            for r in rows:
                cur.execute(
                    f"DELETE FROM {ROWS_TABLE} WHERE queue_key = %s AND row_id = %s",
                    (r["queue_key"], r["row_id"]),
                )
            conn.commit()
            print(f"Deleted {len(rows)} report-ingested row(s).")
        except Exception:
            conn.rollback()
            raise
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
