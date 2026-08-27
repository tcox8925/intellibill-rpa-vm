"""Insert Practice Fusion appointments (appointments-by-date JSON output) into
"EDI_Tebra".patient_visit_header.

Usage:
    python3 sync_patient_visits_to_edi_tebra.py --appointments-json output_json/appointments_by_date_2026-06-29_to_2026-08-28.json
    python3 sync_patient_visits_to_edi_tebra.py --appointments-json <path> --dry-run

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
  3. Maps appointment_status -> 'cancelled' (cancelled/no-show) or
     'confirmed' (everything else).
  4. Skips (does not duplicate) any appointment that already has a
     patient_visit_header row for the same (patient_header_id, dos) --
     patient_visit_header has no natural-key unique constraint, so this
     script enforces idempotency itself via a SELECT-before-INSERT, so it's
     safe to re-run against the same JSON.

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

Each appointment is processed inside its own SAVEPOINT so one bad row
(unparseable data, an unexpected DB constraint) doesn't roll back the whole
batch -- everything else in the run still commits.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=False))

SCHEMA = "EDI_Tebra"
DEFAULT_RCM_SYSTEM_EMAIL = os.environ.get("RCM_SYSTEM_EMAIL", "rcmsystem@834labs.com")

# Northwest Arkansas Internal Medicine -- the only practice this pipeline
# serves for now. See module docstring above for how these were resolved.
CLIENT_ID = 9
GROUP_ID = 12
PRACTICE_ID = 8

_DOB_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y")


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


def _map_status(appointment_status: str) -> str:
    text = _clean(appointment_status).lower()
    if "cancel" in text or "no show" in text or "no-show" in text or "noshow" in text:
        return "cancelled"
    return "confirmed"


def resolve_created_by(cur, email: str) -> str:
    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f"No users row found for email={email!r} -- created_by cannot be resolved. "
            "Confirm the rcm_system service account exists (and that 'users' is the right table)."
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


def insert_patient_visit(cur, appt: dict, dos: date, patient_header_id: str,
                          provider_id: Optional[str], created_by: str) -> None:
    visit_time = _parse_time(appt.get("appointment_start_time", ""))
    status = _map_status(appt.get("appointment_status", ""))
    cur.execute(
        f"""
        INSERT INTO "{SCHEMA}".patient_visit_header
            (client_id, group_id, practice_id, patient_header_id, dos, created_by,
             visit_time, provider_id, status, source_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        ),
    )


def process_appointment(cur, appt: dict, created_by: str, counts: dict) -> None:
    dob = _parse_dob(appt.get("dob", ""))
    if dob is None:
        counts["skipped_bad_dob"] += 1
        print(
            f"  SKIP (unparseable dob {appt.get('dob')!r}): "
            f"{appt.get('first_name')} {appt.get('last_name')} on {appt.get('appointment_date')}",
            flush=True,
        )
        return

    dos = date.fromisoformat(appt["appointment_date"])

    patient_header_id = find_patient_header_by_source_id(cur, appt["ehr_patient_guid"])
    if patient_header_id is None:
        patient_header_id = create_patient_header(cur, appt, dob)
        counts["patients_created"] += 1
    else:
        counts["patients_matched"] += 1

    if visit_already_exists(cur, patient_header_id, dos):
        counts["visits_skipped_existing"] += 1
        return

    provider_id = find_provider_id(cur, appt.get("provider_name", ""))
    insert_patient_visit(cur, appt, dos, patient_header_id, provider_id, created_by)
    counts["visits_inserted"] += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--appointments-json", required=True)
    parser.add_argument("--rcm-system-email", default=DEFAULT_RCM_SYSTEM_EMAIL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(Path(args.appointments_json).read_text())
    appointments = payload.get("appointments", [])
    print(f"Loaded {len(appointments)} appointment(s) from {args.appointments_json}", flush=True)

    conn = _connect()
    conn.autocommit = False
    counts = {
        "patients_matched": 0,
        "patients_created": 0,
        "visits_inserted": 0,
        "visits_skipped_existing": 0,
        "skipped_bad_dob": 0,
        "row_errors": 0,
    }
    try:
        with conn.cursor() as cur:
            created_by = resolve_created_by(cur, args.rcm_system_email)
            print(
                f"Using client_id={CLIENT_ID}, group_id={GROUP_ID}, practice_id={PRACTICE_ID}, "
                f"created_by={created_by}",
                flush=True,
            )

            for i, appt in enumerate(appointments):
                cur.execute("SAVEPOINT row_sp")
                try:
                    process_appointment(cur, appt, created_by, counts)
                    cur.execute("RELEASE SAVEPOINT row_sp")
                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT row_sp")
                    counts["row_errors"] += 1
                    print(
                        f"  ERROR on {appt.get('first_name')} {appt.get('last_name')} "
                        f"on {appt.get('appointment_date')}: {type(exc).__name__}: {exc}",
                        flush=True,
                    )
                if (i + 1) % 100 == 0:
                    print(f"  ...{i + 1}/{len(appointments)} processed", flush=True)

            print(json.dumps({"total_appointments": len(appointments), **counts}, indent=2), flush=True)

            if args.dry_run:
                conn.rollback()
                print("DRY RUN -- rolled back, nothing written.", flush=True)
            else:
                conn.commit()
                print("Committed.", flush=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
