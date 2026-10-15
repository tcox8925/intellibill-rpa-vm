"""Counts what a sync run actually changed, read back from
"EDI_Tebra".patient_audit_history.

That table is written by a DB trigger on patient_header/patient_coverages
(intellibill server/migrations/00173_patient_audit_history.sql), one row per
row that really changed - an update that rewrites identical values logs
nothing. Each row's change_source is the writing connection's
application_name, so a run that connects with a unique label (see
audit_source_label) can count exactly its own changes - the same numbers the
patient's Audit History tab shows.
"""

from sqlalchemy import text

from utils.db import get_engine

# patient_audit_history.action -> the key reported in the run summary.
ACTION_KEYS = {
    "PATIENT_CREATED": "patients_inserted",
    "DEMOGRAPHICS_UPDATED": "patients_updated",
    "PATIENT_DELETED": "patients_deleted",
    "COVERAGE_ADDED": "coverages_inserted",
    "COVERAGE_UPDATED": "coverages_updated",
    "COVERAGE_DEACTIVATED": "coverages_deactivated",
    "COVERAGE_REACTIVATED": "coverages_reactivated",
    "COVERAGE_DELETED": "coverages_deleted",
}


def audit_source_label(job_setting: str, run_id: str) -> str:
    """The application_name a run connects with. Postgres truncates
    application_name to 63 bytes, so this has to stay short enough to keep the
    run id intact ("rpa:TEBRA_PATIENT_SYNC:<uuid>" is 59)."""
    label = f"rpa:{job_setting}:{run_id}"
    if len(label.encode()) > 63:
        raise ValueError(f"audit source label too long for application_name: {label!r}")
    return label


def count_patient_audit_changes(audit_source: str) -> dict:
    """{patients_inserted, patients_updated, coverages_inserted, ...} for one
    run - each the number of distinct patients/coverages with that kind of
    change. Every key in ACTION_KEYS is present (0 when nothing happened)."""
    counts = {key: 0 for key in ACTION_KEYS.values()}
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                'SELECT action, count(DISTINCT entity_id) FROM "EDI_Tebra".patient_audit_history '
                "WHERE change_source = :source GROUP BY action"
            ),
            {"source": audit_source},
        ).all()
    for action, count in rows:
        key = ACTION_KEYS.get(action)
        if key:
            counts[key] = count
    return counts
