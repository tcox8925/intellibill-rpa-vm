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

# Per-pass eligibility gate. This is the single source of truth for "what
# counts as needing this step".
GATES = {
    # Needs a note collected.
    "notes": "appt_note IS NULL",
    # Signed and not yet successfully facesheeted (blank OR error, never NULL-only).
    "facesheets": "COALESCE(process_status, '') IN ('', 'Error') AND appt_note IS NOT NULL",
    # Tebra shows a charge but we haven't captured it into the JSON yet.
    "charges": "charge_status = 'Charge in billing' AND charge_data IS NULL",
    # Flagged from Tebra's own "Missed Charges" view during the appt scrape —
    # re-download the facesheet (the charge is expected to appear on the
    # regenerated PDF). Independent of the 'charges' VIEW-CHARGE scrape.
    "missed_charges": "retry_flag = 1 AND retry_reason = 'Missed Charges' AND appt_note IS NOT NULL",
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
