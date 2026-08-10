"""Refresh command: newest-encounter discovery and single-patient refresh."""

import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from playwright.sync_api import Page

from pf_sync_pkg.chart_ui import all_patient_encounters, patient_summary_url
from pf_sync_pkg.identity import normalize_phone
from pf_sync_pkg.models import DetectedEncounter, QueueRecord, SyncConfig
from pf_sync_pkg.pdf_pipeline import process_records_on_page
from pf_sync_pkg.utils import clean, now_iso, parse_date


def newest_encounter_after_last_processed(
    page: Page,
    config: SyncConfig,
    patient_guid: str,
    rows: Sequence[QueueRecord],
) -> Optional[DetectedEncounter]:
    last_dates = [
        parse_date(record.encounter_date)
        for record in rows
        if record.ehr_patient_guid == patient_guid and record.status == "processed" and parse_date(record.encounter_date)
    ]
    last_date = max(last_dates) if last_dates else None
    summary_url = patient_summary_url(patient_guid)
    page.goto(summary_url, wait_until="domcontentloaded")
    encounters = all_patient_encounters(page, config, patient_guid, include_timeline=True)
    for encounter in encounters:
        encounter_date = parse_date(encounter.encounter_date)
        if encounter_date and (last_date is None or encounter_date > last_date):
            return encounter
    return None


def resolve_refresh_patient_template(
    store: Dict[str, Any],
    rows: Sequence[QueueRecord],
    patient_id: str = "",
    ehr_patient_guid: str = "",
) -> QueueRecord:
    """Resolve a refresh target by PRN or by the Practice Fusion chart GUID.

    ``patient_id`` remains the optional PRN/record number. ``ehr_patient_guid`` is
    the UUID used in the Practice Fusion chart URL and is sufficient by itself.
    """
    patient_id = clean(patient_id)
    ehr_patient_guid = clean(ehr_patient_guid)
    if not patient_id and not ehr_patient_guid:
        raise ValueError("Refresh requires --patient-id or --ehr-patient-guid.")

    patient_rows = [
        record
        for record in rows
        if record.ehr_patient_guid
        and (
            (ehr_patient_guid and record.ehr_patient_guid == ehr_patient_guid)
            or (patient_id and record.patient_id == patient_id)
        )
    ]
    if patient_rows:
        distinct_guids = {record.ehr_patient_guid for record in patient_rows}
        if len(distinct_guids) != 1:
            raise ValueError(
                f"Refresh selector matched multiple patient GUIDs: {sorted(distinct_guids)}"
            )
        return max(
            patient_rows,
            key=lambda record: parse_date(record.appointment_date) or date.min,
        )

    mappings = [
        mapping
        for mapping in store.get("patient_mappings", [])
        if (
            (ehr_patient_guid and clean(mapping.get("ehr_patient_guid")) == ehr_patient_guid)
            or (patient_id and clean(mapping.get("patient_id")) == patient_id)
        )
    ]
    distinct_mapping_guids = {
        clean(mapping.get("ehr_patient_guid")) for mapping in mappings
        if clean(mapping.get("ehr_patient_guid"))
    }
    if len(distinct_mapping_guids) > 1:
        raise ValueError(
            f"Refresh selector matched multiple saved patient GUIDs: {sorted(distinct_mapping_guids)}"
        )
    mapping = mappings[0] if mappings else {}
    resolved_guid = ehr_patient_guid or clean(mapping.get("ehr_patient_guid"))
    if not resolved_guid:
        raise ValueError(
            f"Could not resolve a unique patient GUID for patient_id={patient_id}"
        )

    resolved_patient_id = patient_id or clean(mapping.get("patient_id"))
    selector_value = resolved_patient_id or resolved_guid
    return QueueRecord(
        row_id=f"refresh|{selector_value}|{uuid.uuid4()}",
        practice="",
        patient_id=resolved_patient_id,
        ehr_patient_guid=resolved_guid,
        patient_name=clean(mapping.get("patient_name")),
        patient_dob=clean(mapping.get("dob")),
        patient_phone=clean(mapping.get("phone")),
        patient_phone_normalized=normalize_phone(clean(mapping.get("phone"))),
        patient_match_status="matched",
        patient_match_method="saved_mapping" if mapping else "direct_guid",
        status="ready",
        created_at=now_iso(),
        updated_at=now_iso(),
    )


def refresh_patient_latest_on_page(
    page: Page,
    queue_json: str,
    config: SyncConfig,
    downloads_dir: str,
    patient_id: str,
    ehr_patient_guid: str,
    dry_run: bool,
    store: Dict[str, Any],
    rows: List[QueueRecord],
) -> Dict[str, int]:
    template = resolve_refresh_patient_template(
        store, rows, patient_id=patient_id, ehr_patient_guid=ehr_patient_guid
    )
    detected = newest_encounter_after_last_processed(
        page, config, template.ehr_patient_guid, rows
    )
    if detected is None:
        print("No new encounter exists after the last processed encounter.")
        return {"no_new_encounter": 1}

    existing = next((record for record in rows if record.encounter_key == detected.encounter_key), None)
    if existing is None:
        identity_value = template.patient_id or template.ehr_patient_guid
        existing = QueueRecord(
            row_id=f"{template.practice}|refresh|{identity_value}|{detected.encounter_key[:20]}",
            practice=template.practice,
            patient_id=template.patient_id,
            ehr_patient_guid=template.ehr_patient_guid,
            patient_name=template.patient_name,
            patient_dob=template.patient_dob,
            patient_phone=template.patient_phone,
            patient_phone_normalized=template.patient_phone_normalized,
            patient_match_status="matched",
            patient_match_method="refresh_patient",
            appointment_date=detected.encounter_date,
            appointment_status="seen",
            status="ready",
            status_reason="patient_refresh_new_encounter",
            encounter_key=detected.encounter_key,
            encounter_date=detected.encounter_date,
            encounter_type=detected.encounter_type,
            encounter_code=detected.encounter_code,
            encounter_chief_complaint=detected.chief_complaint,
            encounter_source=detected.source,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        rows.append(existing)
    # Return to Summary because all_patient_encounters may leave us on timeline.
    page.goto(patient_summary_url(existing.ehr_patient_guid), wait_until="domcontentloaded")
    return process_records_on_page(
        page,
        queue_json,
        config,
        downloads_dir,
        [existing],
        rows,
        store,
        limit=1,
        dry_run=dry_run,
        exact_refresh=True,
    )
