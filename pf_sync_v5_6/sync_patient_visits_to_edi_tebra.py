"""Insert Practice Fusion appointments (appointments-by-date JSON output) into
"EDI_Tebra".patient_visit_header.

Usage:
    python3 sync_patient_visits_to_edi_tebra.py --appointments-json output_json/appointments_by_date_2026-06-29_to_2026-08-28.json
    python3 sync_patient_visits_to_edi_tebra.py --appointments-json <path> --concurrency 4

For each appointment row:
  1. Look up EDI_Tebra.patient_header by source_id = the PF chart GUID
     (ehr_patient_guid, scoped to source='practice_fusion') -- exact match
     only, no name/dob fallback. If no match, insert a new patient_header
     row (fname/lname/dob, source_id stamped with the PF GUID so the next
     run finds it directly) -- see create_patient_header's docstring for why
     this is a best-effort mirror of the "facesheets pull" patient-create
     flow rather than an exact copy of it.
  2. Look up EDI_Tebra.lookup_providers by provider full name, scoped to
     GROUP_ID -- lower+trim match first, then a whitespace-stripped
     comparison on both sides as a fallback. Best-effort: leaves
     provider_id NULL on no match rather than failing the row.
  3. Writes appointment_status straight through to `status`, unmodified --
     whatever text Practice Fusion shows (e.g. "Seen", "Pending arrival",
     "Confirmed", "No Show", "Cancelled"). chief_complaint/intake_form/
     eligibility are also written through as raw text; copay/balance_due are
     parsed out of the scraped cell text into a $ Decimal (see _parse_money);
     confirmation_status is parsed into a nullable bool (see
     _parse_confirmation) rather than stored as PF's raw "Confirmed"/"Not
     confirmed" text, since the DB column is `confirmation boolean`.
  4. Skips (does not duplicate) any appointment that already has a
     patient_visit_header row for the same (patient_header_id, dos) --
     patient_visit_header has no natural-key unique constraint, so this
     script enforces idempotency itself via a SELECT-before-INSERT, so it's
     safe to re-run against the same JSON.

Two opt-in flags change that default (skip-if-exists) behavior -- BOTH are
destructive, read their warnings before passing either:

  update_appointments=True: a matching existing visit is DELETEd and the
    fresh row INSERTed in its place, instead of being skipped. WARNING: this
    deletes and replaces the row's own patient_visit_header.id, so anything
    that has come to reference the OLD id -- e.g.
    patient_visit_procedure_selection, which ON DELETE CASCADEs -- is
    destroyed with it. Only safe for a visit nothing downstream has touched
    yet.

  clean_and_insert=True: BEFORE processing anything, deletes EVERY
    patient_visit_header row for CLIENT_ID/GROUP_ID/PRACTICE_ID whose `dos`
    falls in [start_date, end_date] -- not just rows this script created.
    patient_visit_header has no column identifying which system inserted a
    given row, so this cannot distinguish a PF-sourced row from one any
    other integration wrote for the same practice/date range -- it deletes
    all of them. Same CASCADE-delete risk as update_appointments, at the
    scale of the whole date range. Requires start_date/end_date.

CLIENT_ID/GROUP_ID/PRACTICE_ID below are hardcoded, not resolved at runtime --
per 2026-08-27 direction, this pipeline only serves one practice for now.
Fetched once via:
    SELECT p.id, p.group_id, g.client_id, p.prct_name, g.grp_name, c.client_name
    FROM "EDI_Tebra".practice p
    JOIN "EDI_Tebra"."group" g ON g.id = p.group_id
    JOIN "EDI_Tebra".client c ON c.client_id = g.client_id
    WHERE p.prct_name ILIKE '%NWARK%'
-- resolved to practice_id=8 ("NWARK Internal Medicine"), group_id=12
("Northwest Arkansas Internal Medicine"), client_id=9 ("Northwest Arkansas
Internal Medicine"). NWARK = NW-ARK = Northwest Arkansas. Revisit this (a
real per-practice resolver) if/when this pipeline serves more than one
practice.

Concurrency: appointments are grouped by ehr_patient_guid and each GROUP runs
on its own worker thread/connection, committing after every appointment
individually -- one appointment never waits on the whole batch to finish, and
a crash partway through no longer discards already-processed rows (each has
already committed independently by then). Appointments for DIFFERENT patients
run fully in parallel; appointments for the SAME patient stay serialized
against each other on one connection, so the first one's patient_header
creation is always committed and visible before the next looks it up -- see
_sync_patient_group's docstring for why that matters.
"""

import argparse
import json
import os
import re
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

import psycopg2
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=False))

SCHEMA = "EDI_Tebra"
RCM_SCHEMA = "rcm"  # rcm.users -- see rcm_schema/schema.ts's `users` table.
DEFAULT_RCM_SYSTEM_EMAIL = os.environ.get("RCM_SYSTEM_EMAIL", "rcmsystem@834labs.com")
DEFAULT_SYNC_CONCURRENCY = 8
LOG_DIR = Path(__file__).resolve().parent / "logs"

# Northwest Arkansas Internal Medicine -- the only practice this pipeline
# serves for now. See module docstring above for how these were resolved.
CLIENT_ID = 9
GROUP_ID = 12
PRACTICE_ID = 8

_DOB_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y")


class _Tee:
    """Writes to every given stream -- used to mirror stdout/stderr into a
    per-run log file while still printing live to the terminal, without
    touching any of the existing print(...) call sites."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)

    def flush(self):
        for stream in self._streams:
            stream.flush()


def _setup_logging() -> Path:
    """Every run writes its full output (terminal print()s AND any traceback
    on stderr) to its own timestamped file under LOG_DIR, in addition to the
    terminal -- so a run's output is never lost once the terminal scrolls."""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"sync_patient_visits_{timestamp}.log"
    log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    return log_path


def _connect():
    host = os.environ.get("RCM_DB_HOST", "").strip()
    dbname = os.environ.get("RCM_DB_NAME", "").strip()
    user = os.environ.get("RCM_DB_USER", "").strip()
    password = os.environ.get("RCM_DB_PASSWORD", "").strip()
    if not (host and dbname and user and password):
        raise RuntimeError(
            "RCM_DB_HOST/RCM_DB_NAME/RCM_DB_USER/RCM_DB_PASSWORD are required -- set them in .env."
        )
    return psycopg2.connect(
        host=host, dbname=dbname, user=user, password=password, sslmode="require",
        connect_timeout=10,
        options="-c statement_timeout=30000 -c idle_in_transaction_session_timeout=30000",
    )


def _clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _parse_dob(value: str) -> Optional[date]:
    value = _clean(value)
    if not value:
        return None
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(value: str) -> Optional[dt_time]:
    value = _clean(value)
    if not value:
        return None
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _parse_money(value: str) -> Optional[Decimal]:
    """Pulls the first $-amount out of raw scraped cell text (e.g. "No copay
    info $20.00 due" -> Decimal("20.00")). Returns None for cell text with no
    dollar amount at all (e.g. "Nothing due", "" for an empty balance-due
    cell) -- that's the expected/common case, not a parse failure."""
    match = re.search(r"\$\s*([\d,]+\.\d{2})", _clean(value))
    if not match:
        return None
    return Decimal(match.group(1).replace(",", ""))


def _parse_confirmation(value: str) -> Optional[bool]:
    """appt['confirmation_status'] is raw text ("Confirmed"/"Not confirmed").
    "not confirmed" is checked before the bare "confirmed" substring since the
    former contains the latter. Returns None (not False) for blank/unrecognized
    text -- a scrape miss should read as unknown, not as an explicit "no"."""
    value = _clean(value).lower()
    if "not confirmed" in value:
        return False
    if "confirmed" in value:
        return True
    return None


def resolve_created_by(cur, email: str) -> str:
    cur.execute(f'SELECT id FROM "{RCM_SCHEMA}".users WHERE email = %s', (email,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f'No "{RCM_SCHEMA}".users row found for email={email!r} -- created_by cannot be resolved. '
            "Confirm the rcm_system service account exists."
        )
    return row[0]


def find_patient_header_by_source_id(cur, ehr_patient_guid: str) -> Optional[str]:
    """Exact match on the PF chart GUID this script itself stamps into
    source_id on create (see create_patient_header) -- cheap and unambiguous
    for a patient this pipeline has already linked on a prior run. Scoped to
    source='practice_fusion' so it never collides with an unrelated source's
    own source_id space (e.g. a real EDI 837 patient)."""
    cur.execute(
        f"""
        SELECT patient_header_id FROM "{SCHEMA}".patient_header
        WHERE source = 'practice_fusion' AND source_id = %s
        """,
        (ehr_patient_guid,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def create_patient_header(cur, appt: dict, dob: date) -> str:
    """Best-effort mirror of the existing facesheets-pull patient-create flow.

    That flow lives in a different (Node/Drizzle) codebase this repo doesn't
    have access to, so this fills every NOT NULL patient_header column with
    the best available Practice Fusion data and nothing more. Adjust here if
    the real facesheets-pull insert sets additional fields.
    """
    guid = appt["ehr_patient_guid"]
    cur.execute(
        f"""
        INSERT INTO "{SCHEMA}".patient_header
            (source, source_id, pat_id, client_id, group_id, practice_id,
             sub_lnam, pat_fnam, pat_dob, patient_full_name, active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING patient_header_id
        """,
        (
            "practice_fusion",
            guid,
            guid,
            CLIENT_ID,
            GROUP_ID,
            PRACTICE_ID,
            appt["last_name"],
            appt["first_name"],
            dob.isoformat(),
            f'{appt["first_name"]} {appt["last_name"]}'.strip(),
            True,
        ),
    )
    return cur.fetchone()[0]


def find_provider_id(cur, provider_name: str) -> Optional[str]:
    """Matches lookup_providers.full_name against the PF provider_name text.
    Tries an exact lower+trim match first, then falls back to comparing both
    sides with ALL whitespace stripped -- covers spacing differences (extra/
    missing internal spaces, e.g. "Ruben  Tejada" vs "RubenTejada") that a
    plain lower+trim equality would otherwise miss."""
    provider_name = _clean(provider_name)
    if not provider_name:
        return None
    normalized = provider_name.lower()

    cur.execute(
        f"""
        SELECT id FROM "{SCHEMA}".lookup_providers
        WHERE group_id = %s AND lower(trim(full_name)) = %s
        LIMIT 1
        """,
        (GROUP_ID, normalized),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    stripped = normalized.replace(" ", "")
    cur.execute(
        f"""
        SELECT id FROM "{SCHEMA}".lookup_providers
        WHERE group_id = %s AND lower(replace(trim(full_name), ' ', '')) = %s
        LIMIT 1
        """,
        (GROUP_ID, stripped),
    )
    row = cur.fetchone()
    return row[0] if row else None


def visit_already_exists(cur, patient_header_id: str, dos: date) -> bool:
    cur.execute(
        f'SELECT 1 FROM "{SCHEMA}".patient_visit_header WHERE patient_header_id = %s AND dos = %s',
        (patient_header_id, dos),
    )
    return cur.fetchone() is not None


def delete_visit(cur, patient_header_id: str, dos: date) -> None:
    """CASCADE deletes any patient_visit_procedure_selection (etc.) rows tied
    to the deleted patient_visit_header.id -- see update_appointments'
    warning in the module docstring."""
    cur.execute(
        f'DELETE FROM "{SCHEMA}".patient_visit_header WHERE patient_header_id = %s AND dos = %s',
        (patient_header_id, dos),
    )


def clean_visits_for_range(start_date: date, end_date: date) -> Dict[str, int]:
    """Deletes EVERY patient_visit_header row for CLIENT_ID/GROUP_ID/
    PRACTICE_ID with `dos` in [start_date, end_date], on its own short-lived
    connection/transaction -- see clean_and_insert's warning in the module
    docstring for why this is NOT scoped to PF-sourced rows only. Returns the
    number of rows deleted per dos (ISO date string) via RETURNING, so the
    caller can log removed-vs-added per date rather than just a grand total."""
    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM "{SCHEMA}".patient_visit_header
                WHERE client_id = %s AND group_id = %s AND practice_id = %s
                  AND dos BETWEEN %s AND %s
                RETURNING dos
                """,
                (CLIENT_ID, GROUP_ID, PRACTICE_ID, start_date, end_date),
            )
            deleted_rows = cur.fetchall()
        conn.commit()
        counts: Dict[str, int] = {}
        for (dos,) in deleted_rows:
            key = dos.isoformat()
            counts[key] = counts.get(key, 0) + 1
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_patient_visit(cur, appt: dict, dos: date, patient_header_id: str,
                          provider_id: Optional[str], created_by: str) -> None:
    visit_time = _parse_time(appt.get("appointment_start_time", ""))
    status = appt.get("appointment_status", "")
    visit_type = _clean(appt.get("appointment_type", "")) or None
    chief_complaint = _clean(appt.get("chief_complaint", "")) or None
    confirmation = _parse_confirmation(appt.get("confirmation_status", ""))
    copay = _parse_money(appt.get("copay", ""))
    intake_form = _clean(appt.get("intake_form_status", "")) or None
    eligibility = _clean(appt.get("eligibility_status", "")) or None
    balance_due = _parse_money(appt.get("balance_due", ""))
    cur.execute(
        f"""
        INSERT INTO "{SCHEMA}".patient_visit_header
            (client_id, group_id, practice_id, patient_header_id, dos, created_by,
             visit_time, provider_id, status, source_id, visit_type,
             chief_complaint, confirmation, copay, intake_form, eligibility, balance_due)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            CLIENT_ID,
            GROUP_ID,
            PRACTICE_ID,
            patient_header_id,
            dos,
            created_by,
            visit_time,
            provider_id,
            status,
            appt["ehr_patient_guid"],
            visit_type,
            chief_complaint,
            confirmation,
            copay,
            intake_form,
            eligibility,
            balance_due,
        ),
    )


def process_appointment(cur, appt: dict, created_by: str, update_appointments: bool = False) -> dict:
    """Does the actual match/create + insert work for ONE appointment and
    returns an outcome dict for the caller to log/tally -- no shared state
    touched here, so this is safe to call from any thread as long as `cur`
    belongs to that thread's own connection.

    Returns {"visit": "skipped_bad_dob", "appointment_date": ...}, or
            {"patient": "created"|"matched", "visit": "skipped_existing", "appointment_date": ...}, or
            {"patient": "created"|"matched", "visit": "inserted"|"updated",
             "provider": "found"|"not_found", "appointment_date": ...}.

    appointment_date rides along on every outcome (not just the successful
    ones) so callers can tally per-date inserted/removed/skipped counts --
    see sync_appointments_to_edi_tebra's clean_and_insert per-date summary.

    update_appointments: see its warning in the module docstring -- DELETEs
    the existing visit row (CASCADE) before inserting the fresh one, instead
    of skipping.
    """
    appointment_date = appt.get("appointment_date", "")
    dob = _parse_dob(appt.get("dob", ""))
    if dob is None:
        return {"visit": "skipped_bad_dob", "appointment_date": appointment_date}

    dos = date.fromisoformat(appointment_date)

    patient_header_id = find_patient_header_by_source_id(cur, appt["ehr_patient_guid"])
    if patient_header_id is None:
        patient_header_id = create_patient_header(cur, appt, dob)
        patient_kind = "created"
    else:
        patient_kind = "matched"

    visit_kind = "inserted"
    if visit_already_exists(cur, patient_header_id, dos):
        if not update_appointments:
            return {"patient": patient_kind, "visit": "skipped_existing", "appointment_date": appointment_date}
        delete_visit(cur, patient_header_id, dos)
        visit_kind = "updated"

    provider_id = find_provider_id(cur, appt.get("provider_name", ""))
    insert_patient_visit(cur, appt, dos, patient_header_id, provider_id, created_by)
    return {
        "patient": patient_kind,
        "visit": visit_kind,
        "provider": "found" if provider_id else "not_found",
        "appointment_date": appointment_date,
    }


def _appointment_label(appt: dict, index: int, total: int) -> str:
    return f"[{index}/{total}] {appt.get('first_name')} {appt.get('last_name')} on {appt.get('appointment_date')}"


def _outcome_message(outcome: dict) -> str:
    if outcome["visit"] == "skipped_bad_dob":
        return "SKIP -- unparseable dob"
    if outcome["visit"] == "skipped_existing":
        return f"patient={outcome['patient']}, visit=SKIPPED (already exists)"
    provider_text = "found" if outcome["provider"] == "found" else "NOT FOUND"
    return f"patient={outcome['patient']}, provider={provider_text}, visit={outcome['visit']}"


def _sync_patient_group(appts_with_indices: list, created_by: str, total: int, print_lock: threading.Lock,
                         update_appointments: bool = False) -> list:
    """Processes every appointment for ONE patient (same ehr_patient_guid)
    sequentially, on its own connection, committing after each appointment --
    a group's own appointments stay serialized against each other so the
    first one's patient_header creation is always committed and visible
    (not just locally cached in an uncommitted transaction) before the next
    one looks it up. DIFFERENT patients' groups run fully concurrently across
    worker threads (see sync_appointments_to_edi_tebra) -- two threads never
    race to create the same patient_header row, because they're never
    working on the same patient's appointments at the same time.

    appts_with_indices: list of (appointment_dict, 1-based_index_in_full_batch).
    Returns a list of outcome dicts, one per appointment, each tagged with
    "error" instead of "visit" if that row failed.
    """
    results = []
    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for appt, index in appts_with_indices:
                label = _appointment_label(appt, index, total)
                try:
                    outcome = process_appointment(cur, appt, created_by, update_appointments)
                    conn.commit()
                    with print_lock:
                        print(f"{label}: {_outcome_message(outcome)}", flush=True)
                    results.append(outcome)
                except Exception as exc:
                    conn.rollback()
                    with print_lock:
                        print(f"{label}: ERROR -- {type(exc).__name__}: {exc}", flush=True)
                    results.append({"error": str(exc), "appointment_date": appt.get("appointment_date", "")})
    finally:
        conn.close()
    return results


def sync_appointments_to_edi_tebra(appointments: list, rcm_system_email: str = DEFAULT_RCM_SYSTEM_EMAIL,
                                    concurrency: int = DEFAULT_SYNC_CONCURRENCY,
                                    update_appointments: bool = False, clean_and_insert: bool = False,
                                    start_date: str = "", end_date: str = "") -> dict:
    """Core sync entry point -- takes an in-memory appointments list (the same
    shape appointments-by-date's JSON `appointments` field uses), processes
    it concurrently (see module docstring), and returns the summary dict.

    update_appointments / clean_and_insert: both destructive, see their
    warnings in the module docstring. clean_and_insert requires start_date
    and end_date (YYYY-MM-DD) and runs before anything else, on its own
    connection -- fully committed before any group starts inserting, so
    there's no race between the wipe and the fresh inserts.

    Reusable from both the CLI (main, below, which loads the list from a
    file) and the /appointments-by-date HTTP endpoint (server.py, which
    passes the freshly-scraped list straight through, no file involved).
    Deliberately does NOT touch sys.stdout/stderr (that redirection is
    CLI-only, see _setup_logging) -- reassigning it here would hijack a live
    server process's output for every other request, not just this one.
    """
    total = len(appointments)
    counts = {
        "patients_matched": 0,
        "patients_created": 0,
        "visits_inserted": 0,
        "visits_updated": 0,
        "visits_skipped_existing": 0,
        "skipped_bad_dob": 0,
        "row_errors": 0,
    }
    if total == 0:
        return {"total_appointments": 0, **counts, "visits_removed": 0, "removed_by_date": {}, "added_by_date": {}}

    deleted_by_date: Dict[str, int] = {}
    if clean_and_insert:
        if not (start_date and end_date):
            raise ValueError("clean_and_insert requires start_date and end_date.")
        deleted_by_date = clean_visits_for_range(date.fromisoformat(start_date), date.fromisoformat(end_date))
        print(
            f"clean_and_insert: deleted {sum(deleted_by_date.values())} existing patient_visit_header row(s) "
            f"for client_id={CLIENT_ID}, group_id={GROUP_ID}, practice_id={PRACTICE_ID} "
            f"in [{start_date}, {end_date}]",
            flush=True,
        )

    conn = _connect()
    try:
        with conn.cursor() as cur:
            created_by = resolve_created_by(cur, rcm_system_email)
    finally:
        conn.close()
    print(
        f"Using client_id={CLIENT_ID}, group_id={GROUP_ID}, practice_id={PRACTICE_ID}, "
        f"created_by={created_by}, concurrency={concurrency}, update_appointments={update_appointments}",
        flush=True,
    )

    groups = defaultdict(list)
    for i, appt in enumerate(appointments):
        guid = appt.get("ehr_patient_guid") or f"__no_guid_{i}"
        groups[guid].append((appt, i + 1))

    added_by_date: Dict[str, int] = defaultdict(int)
    print_lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(_sync_patient_group, appts_with_indices, created_by, total, print_lock, update_appointments)
            for appts_with_indices in groups.values()
        ]
        for future in as_completed(futures):
            for outcome in future.result():
                if "error" in outcome:
                    counts["row_errors"] += 1
                    continue
                if outcome["visit"] == "skipped_bad_dob":
                    counts["skipped_bad_dob"] += 1
                    continue
                counts["patients_created" if outcome["patient"] == "created" else "patients_matched"] += 1
                if outcome["visit"] == "inserted":
                    counts["visits_inserted"] += 1
                    added_by_date[outcome.get("appointment_date", "")] += 1
                elif outcome["visit"] == "updated":
                    counts["visits_updated"] += 1
                    added_by_date[outcome.get("appointment_date", "")] += 1
                else:
                    counts["visits_skipped_existing"] += 1

    if clean_and_insert:
        # Removed comes from the upfront wipe (dos actually in the DB before
        # this run); added comes from what got (re-)inserted this run for
        # that same date -- printed together so a per-date mismatch (e.g. PF
        # showed fewer appointments today than the range used to have) is
        # visible at a glance instead of buried in the row-by-row log above.
        print("Per-date clean_and_insert summary (removed vs added):", flush=True)
        for day in sorted(set(deleted_by_date) | set(added_by_date)):
            print(f"  {day}: removed={deleted_by_date.get(day, 0)}, added={added_by_date.get(day, 0)}", flush=True)

    summary = {
        "total_appointments": total,
        **counts,
        "visits_removed": sum(deleted_by_date.values()),
        "removed_by_date": deleted_by_date,
        "added_by_date": dict(added_by_date),
    }
    print(json.dumps(summary, indent=2), flush=True)
    print("Done.", flush=True)
    return summary


def main() -> int:
    log_path = _setup_logging()
    print(f"Logging this run to {log_path}", flush=True)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--appointments-json", required=True)
    parser.add_argument("--rcm-system-email", default=DEFAULT_RCM_SYSTEM_EMAIL)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_SYNC_CONCURRENCY)
    parser.add_argument("--update-appointments", action="store_true",
                         help="DELETE+re-INSERT a matching existing visit instead of skipping it. "
                              "See update_appointments' warning in the module docstring.")
    parser.add_argument("--clean-and-insert", action="store_true",
                         help="DELETE every existing visit in the JSON's own [start_date, end_date] "
                              "for this practice before inserting -- NOT scoped to PF-sourced rows "
                              "only. See clean_and_insert's warning in the module docstring.")
    args = parser.parse_args()

    payload = json.loads(Path(args.appointments_json).read_text())
    appointments = payload.get("appointments", [])
    print(f"Loaded {len(appointments)} appointment(s) from {args.appointments_json}", flush=True)

    sync_appointments_to_edi_tebra(
        appointments, args.rcm_system_email, args.concurrency,
        update_appointments=args.update_appointments,
        clean_and_insert=args.clean_and_insert,
        start_date=payload.get("start_date", ""),
        end_date=payload.get("end_date", ""),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
