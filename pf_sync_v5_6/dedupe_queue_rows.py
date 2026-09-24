"""One-time (and repeatable) cleanup for duplicate ehr.ehr_pf_queue_rows rows.

Root cause (fixed in pf_sync_pkg/cli.py's run_sync_schedules_by_date, see the
unmatched_identity_index comment there): a report-ingested row starts with no
ehr_patient_guid -- a CSV appointment report has no GUID column, so
patient_match_method only becomes "fuzzy_name_dob_phone" once a LATER
match-patients run resolves it. Because sync-schedules-by-date (the only
actively scheduled Practice Fusion pipeline) usually runs BEFORE that
resolution happens, its old dedup check -- which only looked at rows that
ALREADY had a guid -- couldn't see the not-yet-matched report row, so it
injected a second, synthetic row (patient_match_method="discovered_from_schedule")
for the exact same real-world appointment. Confirmed live 2026-09-24: 308
duplicate (queue_key, guid, date) pairs, 272 of them with BOTH copies already
fully processed (a facesheet independently generated and delivered twice).

This script finds those existing pairs and deletes the redundant one. Per
2026-09-24 decision: the report-ingested row (deterministic row_id from
ingest.py's record_key(), e.g. "<practice>|fallback|<hash>") is always kept;
the schedule-discovered row (random-uuid row_id, patient_match_method
"discovered_from_schedule") is always deleted -- even in the ~18 pairs where
the schedule-discovered row is the one that actually succeeded (status
"processed", a real pdf_path) and the kept row is "failed". Those cases are
printed explicitly under "risky" before any deletion happens. A row left in
"failed" is not stuck: sync-schedules-by-date retries every failed/review row
in the queue on every scheduled run (see run_sync_schedules_by_date's own
retry pass), so it will be attempted again on its own.

Only a group that is EXACTLY one "discovered_from_schedule" row plus one
non-schedule row is touched automatically. Anything else (3+ rows sharing an
identity+date, or a pair that doesn't fit that shape) is reported as
"ambiguous" and left alone for manual review -- this script never guesses.

Usage:
    python3 dedupe_queue_rows.py                     # dry run, full report, no deletes
    python3 dedupe_queue_rows.py --queue-key pf_appointment_queue.json
    python3 dedupe_queue_rows.py --execute            # writes a backup JSON, then deletes

Safety:
    - Dry-run (report only) unless --execute is passed.
    - --execute first writes every row about to be deleted to a timestamped
      backup JSON (--backup-dir, default: current directory) before touching
      the database, and only deletes inside one transaction (commits once, at
      the end; rolls back entirely on any error).
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import find_dotenv, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pf_sync_pkg.utils import parse_date  # noqa: E402

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


def fetch_rows(conn, queue_key: str = ""):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    sql = f"""
        SELECT {', '.join(SELECT_COLUMNS)} FROM {ROWS_TABLE}
        WHERE ehr_patient_guid <> ''
    """
    params = ()
    if queue_key:
        sql += " AND queue_key = %s"
        params = (queue_key,)
    cur.execute(sql, params)
    return cur.fetchall()


def find_duplicate_groups(rows):
    groups = defaultdict(list)
    for row in rows:
        parsed = parse_date(row["appointment_date"])
        if not parsed:
            continue
        groups[(row["queue_key"], row["ehr_patient_guid"], parsed)].append(row)
    return {key: members for key, members in groups.items() if len(members) > 1}


def classify(groups):
    to_delete = []
    ambiguous = []
    for key, members in groups.items():
        schedule_rows = [m for m in members if m["patient_match_method"] == SCHEDULE_DISCOVERED_METHOD]
        other_rows = [m for m in members if m["patient_match_method"] != SCHEDULE_DISCOVERED_METHOD]
        if len(members) == 2 and len(schedule_rows) == 1 and len(other_rows) == 1:
            to_delete.append({"key": key, "delete": schedule_rows[0], "keep": other_rows[0]})
        else:
            ambiguous.append({"key": key, "members": members})
    return to_delete, ambiguous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue-key", default="", help="Limit to one queue_key (default: every queue in the table).")
    parser.add_argument("--execute", action="store_true", help="Actually delete. Default is dry-run/report only.")
    parser.add_argument("--backup-dir", default=".", help="Where to write the pre-delete backup JSON (only used with --execute).")
    parser.add_argument("--show-limit", type=int, default=25, help="Max rows to print per section (default 25).")
    args = parser.parse_args()

    conn = _connect()
    try:
        rows = fetch_rows(conn, args.queue_key)
        print(f"Guid-matched rows scanned: {len(rows)}" + (f" (queue_key={args.queue_key})" if args.queue_key else " (all queues)"))

        groups = find_duplicate_groups(rows)
        to_delete, ambiguous = classify(groups)

        print(f"Duplicate (queue_key, guid, date) groups found: {len(groups)}")
        print(f"  Clean pairs (1 discovered_from_schedule + 1 report-ingested): {len(to_delete)}")
        print(f"  Ambiguous / left alone (not a clean 2-row pair): {len(ambiguous)}")

        risky = [
            d for d in to_delete
            if d["delete"]["status"] == "processed" and d["keep"]["status"] != "processed"
        ]
        print(f"\nRISKY: deleting an already-PROCESSED schedule-side row while the kept report-side row is NOT processed: {len(risky)}")
        for r in risky[: args.show_limit]:
            print(
                f"  - {r['delete']['patient_name']} on {r['key'][2]} "
                f"(deleting processed pdf_path={r['delete']['pdf_path']!r} status={r['delete']['status']!r}; "
                f"keeping row_id={r['keep']['row_id']!r} status={r['keep']['status']!r})"
            )
        if len(risky) > args.show_limit:
            print(f"  ... and {len(risky) - args.show_limit} more")

        if ambiguous:
            print(f"\nAmbiguous groups (NOT touched, review manually):")
            for a in ambiguous[: args.show_limit]:
                print(
                    f"  queue_key={a['key'][0]} guid={a['key'][1]} date={a['key'][2]} "
                    f"row_ids={[m['row_id'] for m in a['members']]} "
                    f"methods={[m['patient_match_method'] for m in a['members']]}"
                )
            if len(ambiguous) > args.show_limit:
                print(f"  ... and {len(ambiguous) - args.show_limit} more")

        if not args.execute:
            print(f"\nDRY RUN -- no rows deleted. {len(to_delete)} row(s) would be deleted. Re-run with --execute to actually delete.")
            return 0

        if not to_delete:
            print("\nNothing to delete.")
            return 0

        backup_dir = Path(args.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"queue_rows_dedup_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
        backup_payload = [d["delete"] for d in to_delete]
        backup_path.write_text(json.dumps(backup_payload, indent=2, default=str), encoding="utf-8")
        print(f"\nBackup of {len(backup_payload)} row(s) about to be deleted written to {backup_path}")

        cur = conn.cursor()
        try:
            for d in to_delete:
                cur.execute(
                    f"DELETE FROM {ROWS_TABLE} WHERE queue_key = %s AND row_id = %s",
                    (d["delete"]["queue_key"], d["delete"]["row_id"]),
                )
            conn.commit()
            print(f"Deleted {len(to_delete)} duplicate row(s).")
        except Exception:
            conn.rollback()
            raise
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
