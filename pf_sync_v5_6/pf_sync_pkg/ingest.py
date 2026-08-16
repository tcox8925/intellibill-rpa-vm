"""Appointment report ingestion: column mapping and queue upsert."""

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pf_sync_pkg.identity import normalize_person_name, normalize_phone
from pf_sync_pkg.models import QueueRecord, SyncConfig
from pf_sync_pkg.store import append_run, empty_store, finish_run, load_store, save_store, store_rows
from pf_sync_pkg.tabular import read_tabular_rows
from pf_sync_pkg.utils import clean, is_ignored_status, normalize_header, normalize_status, now_iso, parse_date

COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "appointment_id": (
        "appointment_id", "appointment id", "appt_id", "appt id", "confirmation number"
    ),
    "encounter_id": (
        "encounter_id", "encounter id", "visit_id", "visit id"
    ),
    "patient_id": (
        "patient_id", "patient id", "record_number", "record number", "prn"
    ),
    "ehr_patient_guid": (
        "ehr_patient_guid", "patient_guid", "patient guid", "pf_patient_guid"
    ),
    "patient_name": (
        "patient_name", "patient name", "name", "patient"
    ),
    "patient_first_name": (
        "patient first name", "first name", "patient_first_name"
    ),
    "patient_last_name": (
        "patient last name", "last name", "patient_last_name"
    ),
    "patient_dob": (
        "patient_dob", "patient dob", "dob", "date of birth", "birth date"
    ),
    "patient_phone": (
        "patient_phone", "patient phone", "phone", "phone number", "mobile phone",
        "home phone", "preferred contact"
    ),
    "appointment_date": (
        # "appointment time" is the header PF actually exports (AppointmentTime).
        "appointment_date", "appointment date", "appointment date time",
        "appointment time", "appointmenttime", "appt time",
        "appt date", "appt date time", "date/time", "date time", "date",
        "date of service", "service date", "scheduled date", "start time",
        "appointment start"
    ),
    "appointment_status": (
        "appointment_status", "appointment status", "appt status", "status"
    ),
    "appointment_type": (
        "appointment_type", "appointment type", "appt type", "visit type", "reason"
    ),
    "provider": (
        # "seen by" is the header PF actually exports (SeenBy).
        "provider", "provider_name", "provider name", "seen by provider",
        "seen by", "seenby", "rendering provider",
        "resource", "staff"
    ),
    "service_location": (
        # "Facility" is the header PF actually exports.
        "service_location", "service location", "facility", "facility name",
        "location", "practice location"
    ),
}

# Fields that must map to a non-empty value on at least one row of a real report.
# Anything listed here is treated as a hard mapping failure rather than a blank column.
REQUIRED_APPOINTMENT_FIELDS: Dict[str, str] = {
    "appointment_date": "appointment date/time",
    "patient_name": "patient name",
    "patient_dob": "patient DOB",
    "appointment_status": "appointment status",
    "appointment_type": "appointment type",
    "provider": "provider (seen by)",
}

# Fields that are expected but not fatal; a warning keeps a silent blank visible.
OPTIONAL_APPOINTMENT_FIELDS: Dict[str, str] = {
    "patient_phone": "patient phone",
    "service_location": "facility/service location",
}


def alias_value(normalized: Dict[str, str], aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = normalized.get(normalize_header(alias), "")
        if value:
            return value
    return ""


def map_appointment_row(source: Dict[str, Any]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for key, value in source.items():
        normalized_key = normalize_header(key)
        cleaned_value = clean(value)
        # Preserve a non-empty value when two source headers normalize to the same
        # token (for example PHONE and Phone in synthetic/merged files).
        if normalized_key not in normalized or cleaned_value:
            normalized[normalized_key] = cleaned_value
    mapped = {
        target: alias_value(normalized, aliases)
        for target, aliases in COLUMN_ALIASES.items()
    }
    if not mapped["patient_name"]:
        mapped["patient_name"] = clean(
            f"{mapped['patient_first_name']} {mapped['patient_last_name']}"
        )
    return mapped


def validate_appointment_report_mapping(
    source_rows: Sequence[Dict[str, Any]], mapped_rows: Sequence[Dict[str, str]]
) -> None:
    """Fail early when a PF export column was not recognized.

    This prevents a whole report from entering the queue with blank appointment dates
    or patient identities. Practice Fusion currently exports headers such as
    DATE/TIME, APPT. TYPE, APPT. STATUS, and SEEN BY PROVIDER.
    """
    if not source_rows:
        return
    headers = [clean(key) for key in source_rows[0].keys() if clean(key)]

    def any_populated(field_name: str) -> bool:
        return any(clean(row.get(field_name, "")) for row in mapped_rows)

    # v5.4: this used to check only date/name/DOB. A blank appointment_status is the
    # most dangerous silent failure in the pipeline: status_matches() returns False on
    # an empty string, so the ignored gate never fires and cancelled/no-show
    # appointments enter the queue as "ready" and get driven through the browser.
    missing = [
        label for field_name, label in REQUIRED_APPOINTMENT_FIELDS.items()
        if not any_populated(field_name)
    ]
    if missing:
        raise ValueError(
            "Appointment report column mapping failed for "
            + ", ".join(missing)
            + f". Actual CSV headers: {headers}"
            + f". Normalized headers: {sorted({normalize_header(h) for h in headers})}"
        )

    for field_name, label in OPTIONAL_APPOINTMENT_FIELDS.items():
        if not any_populated(field_name):
            print(
                f"WARNING: no source column mapped to {label}; "
                f"phone-based match tie-breaking will be unavailable.",
                flush=True,
            )


def index_registry_by_dob(registry: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for patient in registry:
        dob = clean(patient.get("dob"))
        if dob:
            buckets.setdefault(dob, []).append(patient)
    return buckets


def record_key(mapped: Dict[str, str], practice: str) -> str:
    if mapped.get("appointment_id"):
        return f"{practice}|appointment|{mapped['appointment_id']}"
    if mapped.get("encounter_id"):
        return f"{practice}|encounter|{mapped['encounter_id']}"
    fallback = "|".join(
        [
            practice,
            normalize_person_name(mapped.get("patient_name", "")),
            parse_date(mapped.get("patient_dob", "")).isoformat()
            if parse_date(mapped.get("patient_dob", "")) else "",
            clean(mapped.get("appointment_date", "")),
            clean(mapped.get("provider", "")),
        ]
    )
    digest = hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:24]
    return f"{practice}|fallback|{digest}"


def ingest_appointments(
    appointments_file: str,
    queue_json: str,
    practice: str,
    source_report_name: str = "",
    reset_existing: bool = False,
    config: Optional[SyncConfig] = None,
) -> Dict[str, int]:
    """Ingest an appointment report file (CSV/JSON/XLSX) into the queue.

    Thin wrapper around ingest_appointment_rows -- see that function for the actual
    upsert logic. Kept separate so callers that already have parsed report rows in
    memory (e.g. run_facesheet_pull_by_date, when the DOM-scrape fallback fires
    instead of Practice Fusion's own CSV export) can skip the file read entirely.
    """
    source_rows = read_tabular_rows(appointments_file)
    return ingest_appointment_rows(
        source_rows,
        queue_json,
        practice,
        source_report_name=source_report_name or os.path.basename(appointments_file),
        reset_existing=reset_existing,
        config=config,
        run_details={"appointments_file": str(Path(appointments_file).resolve())},
    )


def ingest_appointment_rows(
    source_rows: List[Dict[str, Any]],
    queue_json: str,
    practice: str,
    source_report_name: str = "",
    reset_existing: bool = False,
    config: Optional[SyncConfig] = None,
    run_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Upsert already-parsed appointment report rows into the queue.

    source_rows: raw report rows (same dict-per-row shape read_tabular_rows/csv.
    DictReader produce -- PF's own export headers, unmapped). run_details: extra
    fields recorded on the ingest run-log entry alongside practice/source_rows;
    ingest_appointments passes the resolved appointments_file path here, callers
    with no backing file can pass whatever's meaningful for their case or leave
    it blank.
    """
    # v5.4: ingest previously read the module-level DEFAULT_IGNORED_STATUSES directly
    # while process() read config.ignored_statuses. Editing ignored_statuses in
    # pf_pdf_sync_config.json therefore changed only half the pipeline. Both paths now
    # resolve the gate through is_ignored_status() against the same config.
    if config is None:
        config = SyncConfig()
    store = empty_store() if reset_existing else load_store(queue_json)
    rows = [] if reset_existing else store_rows(store)
    by_id = {row.row_id: row for row in rows}
    mapped_rows = [map_appointment_row(source) for source in source_rows]
    validate_appointment_report_mapping(source_rows, mapped_rows)
    if source_rows:
        actual_headers = [clean(key) for key in source_rows[0].keys() if clean(key)]
        sample = mapped_rows[0]
        print(f"Appointment report headers: {actual_headers}", flush=True)
        print(
            "Mapped sample: "
            f"date={sample.get('appointment_date', '')!r}, "
            f"patient={sample.get('patient_name', '')!r}, "
            f"status={sample.get('appointment_status', '')!r}, "
            f"type={sample.get('appointment_type', '')!r}, "
            f"provider={sample.get('provider', '')!r}",
            flush=True,
        )

    source_name = source_report_name or "in_memory_rows"
    # v5.1 could create unusable queue rows because PF's DATE/TIME header was not
    # recognized. Remove only those unprocessed, date-less rows from this same
    # source report before re-ingesting the corrected export.
    malformed_ids = {
        row.row_id for row in rows
        if not clean(row.appointment_date)
        and clean(row.source_report_name) == clean(source_name)
        and row.status != "processed"
    }
    if malformed_ids:
        rows = [row for row in rows if row.row_id not in malformed_ids]
        by_id = {row.row_id: row for row in rows}

    counts = {
        "inserted": 0, "updated": 0, "ignored": 0,
        "removed_malformed_missing_date": len(malformed_ids),
    }
    timestamp = now_iso()

    for source, mapped in zip(source_rows, mapped_rows):
        row_id = record_key(mapped, practice)
        appointment_status = normalize_status(mapped["appointment_status"])
        ignored = is_ignored_status(appointment_status, config)
        prior = by_id.get(row_id)

        if prior is None:
            prior = QueueRecord(
                row_id=row_id,
                practice=practice,
                appointment_id=mapped["appointment_id"],
                appointment_date=mapped["appointment_date"],
                appointment_status=mapped["appointment_status"],
                appointment_type=mapped["appointment_type"],
                provider=mapped["provider"],
                service_location=mapped["service_location"],
                patient_name=mapped["patient_name"],
                patient_dob=mapped["patient_dob"],
                patient_phone=mapped["patient_phone"],
                patient_phone_normalized=normalize_phone(mapped["patient_phone"]),
                patient_id=mapped["patient_id"],
                ehr_patient_guid=mapped["ehr_patient_guid"],
                encounter_id=mapped["encounter_id"],
                status="ignored" if ignored else "ready",
                status_reason=(
                    f"ignored_appointment_status:{appointment_status}"
                    if ignored else "appointment_report_loaded"
                ),
                patient_match_status=(
                    "matched" if mapped["patient_id"] and mapped["ehr_patient_guid"] else "unmatched"
                ),
                patient_match_method=(
                    "appointment_report" if mapped["patient_id"] and mapped["ehr_patient_guid"] else ""
                ),
                source_report_name=source_name,
                source_row_json={clean(k): clean(v) for k, v in source.items()},
                created_at=timestamp,
                updated_at=timestamp,
                first_ready_at=timestamp if not ignored else "",
            )
            by_id[row_id] = prior
            counts["inserted"] += 1
        else:
            # Preserve successful encounter/PDF history but refresh report facts.
            prior.practice = practice or prior.practice
            prior.appointment_id = mapped["appointment_id"] or prior.appointment_id
            prior.appointment_date = mapped["appointment_date"] or prior.appointment_date
            prior.appointment_status = mapped["appointment_status"] or prior.appointment_status
            prior.appointment_type = mapped["appointment_type"] or prior.appointment_type
            prior.provider = mapped["provider"] or prior.provider
            prior.service_location = mapped["service_location"] or prior.service_location
            prior.patient_name = mapped["patient_name"] or prior.patient_name
            prior.patient_dob = mapped["patient_dob"] or prior.patient_dob
            prior.patient_phone = mapped["patient_phone"] or prior.patient_phone
            prior.patient_phone_normalized = normalize_phone(prior.patient_phone)
            prior.patient_id = mapped["patient_id"] or prior.patient_id
            prior.ehr_patient_guid = mapped["ehr_patient_guid"] or prior.ehr_patient_guid
            prior.encounter_id = mapped["encounter_id"] or prior.encounter_id
            prior.source_report_name = source_name
            prior.source_row_json = {clean(k): clean(v) for k, v in source.items()}
            prior.updated_at = timestamp

            if ignored:
                prior.status = "ignored"
                prior.status_reason = f"ignored_appointment_status:{appointment_status}"
            elif prior.status == "ignored":
                prior.status = "ready"
                prior.status_reason = "appointment_reactivated"
            elif prior.status not in {"processed", "processing"}:
                # Review/failed/unmatched rows are eligible to be reconsidered.
                if prior.patient_match_status == "needs_attention":
                    prior.status = "needs_attention"
                else:
                    prior.status = "ready"
                    prior.status_reason = "appointment_report_reloaded"
                prior.error_message = ""
                if not prior.first_ready_at:
                    prior.first_ready_at = timestamp
            counts["updated"] += 1

        if ignored:
            counts["ignored"] += 1

    final_rows = list(by_id.values())
    final_rows.sort(
        key=lambda item: (
            parse_date(item.appointment_date) or date.min,
            normalize_person_name(item.patient_name),
            item.row_id,
        )
    )
    run_id = append_run(
        store,
        "ingest",
        {
            **(run_details or {}),
            "source_report_name": source_name,
            "practice": practice,
            "source_rows": len(source_rows),
        },
    )
    finish_run(store, run_id, "success", counts)
    save_store(queue_json, store, final_rows)
    return counts
