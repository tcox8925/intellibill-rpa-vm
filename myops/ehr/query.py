"""
The ONE place appointment-selection SQL is built.

Every pass (notes, facesheets, charges) selects its work through
build_appointment_query(). The per-pass "gate" and the per-mode date/id/name
filters are defined here and nowhere else — so a fix to a gate (e.g. the
empty-string process_status bug) can never again land in one code path and
miss its twin.

`build_appointment_query` is pure: it returns (sql, params) and touches no
database, so it is unit-testable without a connection.
"""

from .config import TABLE_NAME

# Superset of columns any pass needs. Passes unpack what they use.
#   idx: 0   1        2             3    4            5          6          7
COLUMNS = "id, appt_id, patient_name, dob, appt_status, appt_date, appt_time, charge_status"

# Appointments in these statuses never actually happened, so Tebra has no
# real facesheet to open for them -- a signed note can still exist (staff
# documenting the no-show/cancellation itself), which otherwise lets these
# slip through the "appt_note IS NOT NULL" gate below and into a facesheet
# pull attempt that's guaranteed to fail ("Unable to open facesheet",
# browser.py's click_patient_row). Confirmed live 2026-09-03: a No Show
# appointment's failed facesheet click can strand the page and cascade into
# failing the next several patients in the same run (see passes.py's
# _process_by_patient/_download_and_mark cleanup) -- excluding these
# statuses here stops the attempt before it can ever trigger that.
NO_VISIT_STATUSES = ("Cancelled", "No Show", "Rescheduled")
_NOT_NO_VISIT = "appt_status NOT IN ('" + "', '".join(NO_VISIT_STATUSES) + "')"

# Per-pass eligibility gate. This is the single source of truth for "what
# counts as needing this step".
GATES = {
    # Needs a note collected.
    "notes": "appt_note IS NULL",
    # Signed and not yet successfully facesheeted (blank OR error, never NULL-only).
    "facesheets": (
        f"COALESCE(process_status, '') IN ('', 'Error') AND appt_note IS NOT NULL "
        f"AND {_NOT_NO_VISIT}"
    ),
    # Tebra shows a charge but we haven't captured it into the JSON yet.
    "charges": "charge_status = 'Charge in billing' AND charge_data IS NULL",
    # Flagged from Tebra's own "Missed Charges" view during the appt scrape —
    # re-download the facesheet (the charge is expected to appear on the
    # regenerated PDF). Independent of the 'charges' VIEW-CHARGE scrape.
    "missed_charges": (
        f"retry_flag = 1 AND retry_reason = 'Missed Charges' AND appt_note IS NOT NULL "
        f"AND {_NOT_NO_VISIT}"
    ),
}

# For backfill/target, "facesheets" should re-pull regardless of prior
# process_status — the caller explicitly asked for this window/target, so
# already-processed rows are redone too. Daily's unbounded sweep keeps the
# full gate so it doesn't reprocess the entire historical backlog every night.
UNGATED_REPULL = {
    "facesheets": f"appt_note IS NOT NULL AND {_NOT_NO_VISIT}",
}


def scope_clause(sel, alias=""):
    """
    Return (where_fragments, params) for the mode scope only (practice + the
    date/id/name filters) — no gate, no columns. Shared by the appointment
    query and the zip query so mode-scope lives in one place. `alias` prefixes
    columns (e.g. 'a.') when needed.
    """
    a = f"{alias}." if alias else ""
    where = [f"{a}entity = %s", f"{a}sub_entity = %s", f"{a}ehr_name = %s"]
    params = [sel.entity, sel.sub_entity, sel.ehr_name]

    if sel.practice:
        where.append(f"{a}practice = %s")
        params.append(sel.practice)

    if sel.mode == "backfill":
        where.append(f"{a}appt_date BETWEEN %s AND %s")
        params.extend([sel.start_date, sel.end_date])
    elif sel.mode == "target":
        if sel.appt_id:
            where.append(f"{a}appt_id = %s")
            params.append(sel.appt_id)
        if sel.patient_name:
            where.append(f"{a}patient_name ILIKE %s")
            params.append(f"%{sel.patient_name}%")
        if sel.start_date:
            where.append(f"{a}appt_date = %s")
            params.append(sel.start_date)
    return where, params


def build_appointment_query(sel, needing, table=TABLE_NAME):
    """
    Build (sql, params) selecting appointments for `sel` that need `needing`.

    needing: 'notes' | 'facesheets' | 'charges'

    Mode behavior:
      daily    -> no date/id/name bound (unbounded recheck / date-agnostic)
      backfill -> appt_date BETWEEN start AND end
      target   -> whichever of appt_id / patient_name / start_date given
    """
    if needing not in GATES:
        raise ValueError(f"Unknown 'needing': {needing!r} (expected one of {list(GATES)})")

    where = ["entity = %s", "sub_entity = %s", "ehr_name = %s"]
    params = [sel.entity, sel.sub_entity, sel.ehr_name]

    if sel.practice:
        where.append("practice = %s")
        params.append(sel.practice)

    # Per-pass gate (parenthesized so it composes safely with AND filters).
    # backfill/target re-pull regardless of prior process_status/file_path for
    # gates listed in UNGATED_REPULL, UNLESS the caller set
    # sel.ungated_repull=False (e.g. /run-tebra-recheck) to keep the normal
    # "skip if already Processed" gate even inside an explicit window. daily
    # always keeps the full "needs work" gate.
    if sel.mode != "daily" and needing in UNGATED_REPULL and sel.ungated_repull:
        where.append(f"({UNGATED_REPULL[needing]})")
    else:
        where.append(f"({GATES[needing]})")

    # Per-mode scope.
    if sel.mode == "backfill":
        where.append("appt_date BETWEEN %s AND %s")
        params.extend([sel.start_date, sel.end_date])
    elif sel.mode == "target":
        if sel.appt_id:
            where.append("appt_id = %s")
            params.append(sel.appt_id)
        if sel.patient_name:
            where.append("patient_name ILIKE %s")
            params.append(f"%{sel.patient_name}%")
        if sel.start_date:
            where.append("appt_date = %s")
            params.append(sel.start_date)
    # daily: intentionally no date/id/name clause.

    sql = (
        f"SELECT {COLUMNS} FROM {table} "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY appt_date, appt_time"
    )
    return sql, tuple(params)


def select_appointments(cur, sel, needing, table=TABLE_NAME):
    """Execute build_appointment_query against an open cursor and return rows."""
    sql, params = build_appointment_query(sel, needing, table=table)
    cur.execute(sql, params)
    return cur.fetchall()
