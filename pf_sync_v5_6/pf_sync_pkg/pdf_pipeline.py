"""Per-record PDF generation pipeline: metadata manifest, one-record processing, batch loops."""

import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from playwright.sync_api import Page

from pf_sync_pkg.chart_ui import (
    close_print_chart,
    find_encounter_for_appointment,
    find_encounter_for_appointment_with_timeline_fallback,
    insurance_filter_toggle_label,
    open_print_chart,
    patient_summary_url,
    prepare_print_chart_sections,
    select_notes_for_record,
)
from pf_sync_pkg.constants import PATIENT_NAME_SELECTOR
from pf_sync_pkg.matching import load_patient_registry
from pf_sync_pkg.models import (
    DetectedEncounter,
    EncounterNotFoundError,
    QueueRecord,
    SoapNoteNotFoundError,
    SyncConfig,
)
from pf_sync_pkg.pdf_render import generate_pdf
from pf_sync_pkg.chart_ui import all_patient_encounters
from pf_sync_pkg.store import save_store
from pf_sync_pkg.utils import clean, is_ignored_status, is_seen_status, normalize_status, now_iso, parse_date


def appointment_time_text(value: str) -> str:
    """Return the appointment start time exactly as a friendly 12-hour value.

    Practice Fusion currently exports AppointmentTime as values such as
    ``07/31/2026 10:45 AM``.  We intentionally return only the time we actually
    receive; no end time is invented.
    """
    text = clean(value)
    if not text:
        return ""

    formats = (
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M %p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            pass

    match = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b", text, flags=re.I)
    if match:
        return re.sub(r"\s+", " ", match.group(1).upper()).strip()
    return ""


def appointment_metadata_row(record: QueueRecord) -> Dict[str, str]:
    """Return the downstream metadata object for one successfully generated PDF.

    Only values the workflow actually receives or resolves are emitted.  No synthetic
    appointment ID or facesheet ID is created.

    2026-08-11: an appointment_id field was added and then removed the same day --
    confirmed live that Practice Fusion never supplies one for this account, on
    any surface checked (CSV export headers, the Schedule page's row DOM, and
    the network responses the page itself makes). It would only ever be empty
    string, so it added nothing and was removed rather than carried as dead
    weight. See write_appointments_metadata_json's docstring for the dedup key
    this affects.
    """
    pdf_path = clean(record.pdf_path)
    appt_date = parse_date(record.appointment_date)
    dob = parse_date(record.patient_dob)
    return {
        "pdf_file": Path(pdf_path).name if pdf_path else "",
        "appt_date": appt_date.isoformat() if appt_date else clean(record.appointment_date),
        "appt_time": appointment_time_text(record.appointment_date),
        "patient_name": clean(record.patient_name),
        "dob": dob.isoformat() if dob else clean(record.patient_dob),
        "provider_name": clean(record.provider),
        "service_location": clean(record.service_location),
        "patient_id": clean(record.ehr_patient_guid),
    }


def write_appointments_metadata_json(
    records: Sequence[QueueRecord],
    downloads_dir: str,
    scrape_run_id: str,
    manifest_run_id: str = "",
) -> str:
    """Write a JSON manifest containing every PDF generated in this processing run.

    The manifest is rewritten atomically as each appointment succeeds, so an interrupted
    run still leaves one valid JSON file describing every PDF completed before the stop.

    manifest_run_id (added 2026-08-11): without it, every call gets its own fresh
    scrape_run_id-named file -- fine for a single uninterrupted process() call, but
    a real problem for a "pull" that spans more than one invocation (a run that got
    interrupted and resumed, or a caller like cli.run_full_sync_by_date that scopes
    a pull to a date range rather than a single call). That produced three separate
    appointments_<uuid>.json fragments for what was logically ONE 590-row pull,
    confirmed live 2026-08-11, which is what this parameter fixes: when the caller
    passes the same manifest_run_id across multiple invocations (e.g. one derived
    from the queue file + date range, stable for that whole pull), this function
    loads whatever's already at that path and MERGES the newly eligible records in
    instead of overwriting -- so the same pull always converges on exactly one
    manifest file no matter how many process() calls it actually took. Leave
    manifest_run_id unset for the old scrape_run_id-per-call behavior (still the
    right choice for one-off refresh()/full_sync_on_page() calls that aren't part
    of a scoped pull).

    Dedup/merge key: pdf_file. An appointment_id-preferred key was tried and
    removed the same day (2026-08-11) -- confirmed live that Practice Fusion
    never supplies an appointment ID anywhere reachable for this account (not
    in the CSV export, not in the Schedule page's row DOM, not in any network
    response the page itself makes), so it would only ever be empty and never
    actually take priority over pdf_file in practice. Checked the real
    processed queue and the actual delivered manifest for the concern that
    prompted adding it in the first place -- multiple appointments sharing one
    combined chart-print PDF, which would collide under a pdf_file-only key:
    zero such collisions exist in either (591/591 unique pdf_file values, 0
    queue rows sharing a pdf_path) -- so a plain pdf_file key isn't losing
    anything for this account's actual data. If that ever changes (a provider
    setting that combines multiple visits into one printed chart, for example),
    this key would need revisiting -- watch for a manifest's appointment count
    coming in lower than its input candidate count as the signal.
    """
    import json

    def _dedup_key(row: Dict[str, str]) -> str:
        return row.get("pdf_file") or ""

    eligible = [
        record
        for record in records
        if clean(record.pdf_path) and clean(record.pdf_path) != "DRY_RUN_NO_PDF"
    ]
    if not eligible:
        return ""

    destination_dir = Path(downloads_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    file_id = manifest_run_id or scrape_run_id
    destination = destination_dir / f"appointments_{file_id}.json"

    new_rows = [appointment_metadata_row(record) for record in eligible]
    if manifest_run_id and destination.exists():
        try:
            existing_data = json.loads(destination.read_text(encoding="utf-8-sig"))
            existing_rows = existing_data.get("appointments", [])
        except Exception:
            existing_rows = []
        by_key = {_dedup_key(row): row for row in existing_rows if _dedup_key(row)}
        for row in new_rows:
            by_key[_dedup_key(row)] = row
        merged_rows = list(by_key.values())
    else:
        merged_rows = new_rows

    metadata = {"appointments": merged_rows}

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    resolved = str(destination.resolve())

    # Every appointment generated in this run points to the same batch manifest.
    for record in eligible:
        record.metadata_json_path = resolved
    return resolved


def is_ignored(record: QueueRecord, config: SyncConfig) -> bool:
    return is_ignored_status(record.appointment_status, config)


def is_seen(record: QueueRecord, config: SyncConfig) -> bool:
    return is_seen_status(record.appointment_status, config)


def validate_patient_ready(record: QueueRecord) -> None:
    """Confirm the record can drive chart navigation.

    v5.4: this used to require patient_id as well as ehr_patient_guid, but 631 of the
    8288 rows in the PF patient export carry no PRN/record number. Those patients
    matched correctly on name + DOB, got a valid GUID, then hard-failed here with a
    message telling the operator to run match-patients -- which could never fix it,
    while resolve-patient refused to accept a blank --patient-id. The GUID is the only
    identifier chart navigation actually needs: patient_summary_url() takes the GUID
    and format_pdf_name() already falls back to it.
    """
    if not record.ehr_patient_guid:
        raise RuntimeError(
            "PATIENT_NOT_RESOLVED: no ehr_patient_guid; run match-patients or "
            "resolve-patient first."
        )


def process_one_record(
    page: Page,
    record: QueueRecord,
    config: SyncConfig,
    downloads_dir: str,
    scrape_run_id: str,
    exact_refresh: bool,
    dry_run: bool,
    all_rows: Sequence[QueueRecord] = (),
    use_timeline_fallback: bool = False,
) -> None:
    started = time.perf_counter()
    original_status = record.status
    validate_patient_ready(record)
    record.attempt_count += 1
    if exact_refresh:
        record.refresh_count += 1
    record.status = "processing"
    record.status_reason = "refresh_started" if exact_refresh else "nightly_pdf_started"
    record.processing_started_at = now_iso()
    record.updated_at = now_iso()
    record.scrape_run_id = scrape_run_id
    record.error_message = ""

    summary_url = patient_summary_url(record.ehr_patient_guid)
    page.goto(summary_url, wait_until="domcontentloaded")
    try:
        page.locator(PATIENT_NAME_SELECTOR).first.wait_for(state="visible", timeout=10_000)
    except Exception:
        pass

    # Use Timeline fallback for full-sync-by-date; Summary-only for nightly (avoids PF hangs)
    if use_timeline_fallback:
        detected = find_encounter_for_appointment_with_timeline_fallback(
            page, config, record.ehr_patient_guid, record.appointment_date
        )
    else:
        detected = find_encounter_for_appointment(
            page, config, record.ehr_patient_guid, record.appointment_date
        )
    record.encounter_key = detected.encounter_key
    record.encounter_date = detected.encounter_date
    record.encounter_type = detected.encounter_type
    record.encounter_code = detected.encounter_code
    record.encounter_chief_complaint = detected.chief_complaint
    record.encounter_source = detected.source

    if "/summary" not in (page.url or ""):
        page.goto(summary_url, wait_until="domcontentloaded")

    modal = open_print_chart(page, config)
    record.selected_sections = prepare_print_chart_sections(page, config, modal)
    record.insurance_filter_selected = (
        insurance_filter_toggle_label(page, config)
        if config.insurance_section_data_element in "".join(record.selected_sections)
        else ""
    )
    notes_mode, record.selected_soap_note_text = select_notes_for_record(
        page, config, record, all_rows
    )
    record.notes_selection_mode = notes_mode
    record.pdf_path = generate_pdf(page, config, record, downloads_dir, dry_run)
    record.metadata_json_path = ""

    record.last_checked_at = now_iso()
    record.updated_at = now_iso()
    record.elapsed_seconds = round(time.perf_counter() - started, 3)
    if dry_run:
        record.status = original_status if original_status in {"ready", "review", "failed"} else "ready"
        record.status_reason = "dry_run_validated"
        record.message = "Encounter and SOAP note were found; PDF generation was skipped."
    else:
        record.status = "processed"
        record.status_reason = "refresh_processed" if exact_refresh else "nightly_pdf_processed"
        record.processed_at = now_iso()
        record.message = ""


def handle_process_error(record: QueueRecord, config: SyncConfig, exc: Exception) -> str:
    record.last_checked_at = now_iso()
    record.updated_at = now_iso()
    record.elapsed_seconds = 0.0
    record.error_message = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, EncounterNotFoundError):
        if is_seen(record, config):
            record.status = "review"
            record.status_reason = "seen_appointment_missing_encounter"
            record.review_count += 1
            record.message = "Seen appointment has no matching encounter yet; it will be polled again."
        else:
            record.status = "ready"
            record.status_reason = "waiting_for_encounter"
            record.message = "Appointment has no matching encounter yet."
    elif isinstance(exc, SoapNoteNotFoundError):
        record.status = "review"
        record.status_reason = "soap_note_not_available_for_appointment_date"
        record.review_count += 1
        record.message = "Encounter exists but the SOAP note is not available in Print Chart yet."
    elif "PATIENT_NOT_RESOLVED" in str(exc):
        record.status = "needs_attention"
        record.status_reason = "patient_not_resolved"
        record.message = str(exc)
    else:
        record.status = "failed"
        record.status_reason = "pdf_worker_error"
        record.message = str(exc)
    return record.status


def process_records_on_page(
    page: Page,
    queue_json: str,
    config: SyncConfig,
    downloads_dir: str,
    candidates: Sequence[QueueRecord],
    all_rows: List[QueueRecord],
    store: Dict[str, Any],
    limit: int = 0,
    dry_run: bool = False,
    exact_refresh: bool = False,
    manifest_run_id: str = "",
    use_timeline_fallback: bool = False,
) -> Dict[str, int]:
    """manifest_run_id: pass the same value across multiple process() calls that
    belong to one logical pull (e.g. a date-scoped run that got interrupted and
    resumed) so they converge on one appointments_<manifest_run_id>.json manifest
    instead of each call producing its own fragment -- see
    write_appointments_metadata_json's docstring for the incident this fixes.
    Leave unset for the old per-call random-uuid manifest naming.
    """
    if limit > 0:
        candidates = list(candidates)[:limit]
    counts = {"processed": 0, "validated": 0, "review": 0, "ready": 0, "failed": 0, "ignored": 0, "needs_attention": 0}
    scrape_run_id = str(uuid.uuid4())
    processed_for_manifest: List[QueueRecord] = []
    metadata_manifest_path = ""

    for index, record in enumerate(candidates, start=1):
        label = record.appointment_id or record.encounter_key or record.row_id
        print(f"[{index}/{len(candidates)}] {record.patient_name} | {record.appointment_date} | {label}", flush=True)
        if is_ignored(record, config):
            record.status = "ignored"
            record.status_reason = f"ignored_appointment_status:{normalize_status(record.appointment_status)}"
            record.updated_at = now_iso()
            counts["ignored"] += 1
            save_store(queue_json, store, all_rows)
            print("  ignored", flush=True)
            continue
        if record.patient_match_status != "matched" or not record.ehr_patient_guid:
            record.status = "needs_attention"
            record.status_reason = "patient_not_resolved"
            record.message = "Patient ID/GUID must be resolved before encounter processing."
            record.updated_at = now_iso()
            counts["needs_attention"] += 1
            save_store(queue_json, store, all_rows)
            print("  needs_attention: patient not resolved", flush=True)
            continue
        try:
            process_one_record(
                page,
                record,
                config,
                downloads_dir,
                scrape_run_id,
                exact_refresh,
                dry_run,
                all_rows,
                use_timeline_fallback,
            )
            if dry_run:
                counts["validated"] += 1
                print(f"  validated in {record.elapsed_seconds:.3f}s", flush=True)
            else:
                processed_for_manifest.append(record)
                metadata_manifest_path = write_appointments_metadata_json(
                    processed_for_manifest,
                    downloads_dir,
                    scrape_run_id,
                    manifest_run_id,
                )
                counts["processed"] += 1
                print(f"  processed in {record.elapsed_seconds:.3f}s -> {record.pdf_path}", flush=True)
        except Exception as exc:
            state = handle_process_error(record, config, exc)
            counts[state] = counts.get(state, 0) + 1
            print(f"  {state}: {record.error_message}", flush=True)
        finally:
            # v5.4: always tear the Print Chart modal down so a record that failed inside
            # the modal cannot leave it open over the next patient's chart.
            close_print_chart(page, config)
            save_store(queue_json, store, all_rows)
    if metadata_manifest_path:
        print(f"Metadata manifest: {metadata_manifest_path}", flush=True)
    # Exposed so callers (e.g. cli.run_full_sync_by_date's zip/upload stage) can
    # find the manifest this run produced without re-deriving scrape_run_id --
    # previously only printed to stdout, never returned.
    counts["metadata_manifest_path"] = metadata_manifest_path
    return counts


def default_process_candidates(rows: Sequence[QueueRecord], include_failed: bool = False) -> List[QueueRecord]:
    statuses = {"ready", "review"}
    if include_failed:
        statuses.add("failed")
    return [record for record in rows if record.status in statuses]


def full_sync_on_page(
    page: Page,
    queue_json: str,
    config: SyncConfig,
    downloads_dir: str,
    patients_file: str,
    store: Dict[str, Any],
    rows: List[QueueRecord],
    limit_patients: int = 0,
    max_encounters_per_patient: int = 0,
    dry_run: bool = False,
    rescrape_all: bool = False,
) -> Dict[str, Any]:
    """Discover historical SOAP encounters and process only unprocessed dates.

    One queue row is created per patient/date. Practice Fusion's Notes menu can
    contain more than one SOAP note on the same date; selecting the date captures
    all matching notes in the resulting PDF without creating duplicate PDFs.
    """
    registry = load_patient_registry(patients_file)
    if limit_patients > 0:
        registry = registry[:limit_patients]

    by_row_id = {record.row_id: record for record in rows}
    processed_keys = {
        (record.ehr_patient_guid, record.encounter_date or (
            parse_date(record.appointment_date).isoformat()
            if parse_date(record.appointment_date) else ""
        ))
        for record in rows
        if record.status == "processed"
    }
    counts: Dict[str, int] = {
        "patients_scanned": 0,
        "patients_failed": 0,
        "encounter_dates_discovered": 0,
        "already_processed": 0,
        "processed": 0,
        "validated": 0,
        "review": 0,
        "ready": 0,
        "failed": 0,
    }
    patient_timings: List[Dict[str, Any]] = []

    for patient_index, patient in enumerate(registry, start=1):
        patient_started = time.perf_counter()
        patient_guid = clean(patient.get("ehr_patient_guid"))
        patient_id = clean(patient.get("patient_id"))
        patient_name = clean(patient.get("patient_name"))
        print(
            f"[patient {patient_index}/{len(registry)}] {patient_name} "
            f"({patient_id or patient_guid})",
            flush=True,
        )
        if not patient_guid:
            counts["patients_failed"] += 1
            patient_timings.append(
                {
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "seconds": round(time.perf_counter() - patient_started, 3),
                    "status": "missing_guid",
                }
            )
            continue
        try:
            summary_url = patient_summary_url(patient_guid)
            page.goto(summary_url, wait_until="domcontentloaded")
            try:
                page.locator(PATIENT_NAME_SELECTOR).first.wait_for(
                    state="visible", timeout=10_000
                )
            except Exception:
                pass
            discovered = all_patient_encounters(
                page, config, patient_guid, include_timeline=True
            )
            # One PDF per encounter date; the note picker selects all notes that
            # share that date.
            by_date: Dict[str, DetectedEncounter] = {}
            for encounter in discovered:
                by_date.setdefault(encounter.encounter_date, encounter)
            encounter_dates = list(by_date.values())
            encounter_dates.sort(
                key=lambda item: parse_date(item.encounter_date) or date.min,
                reverse=True,
            )
            if max_encounters_per_patient > 0:
                encounter_dates = encounter_dates[:max_encounters_per_patient]
            counts["patients_scanned"] += 1
            counts["encounter_dates_discovered"] += len(encounter_dates)

            for detected in encounter_dates:
                key = (patient_guid, detected.encounter_date)
                if key in processed_keys and not rescrape_all:
                    counts["already_processed"] += 1
                    continue
                row_id = (
                    f"full-sync|{patient_guid}|{detected.encounter_date}"
                )
                record = by_row_id.get(row_id)
                if record is None:
                    record = QueueRecord(
                        row_id=row_id,
                        practice="",
                        patient_id=patient_id,
                        ehr_patient_guid=patient_guid,
                        patient_name=patient_name,
                        patient_dob=clean(patient.get("dob")),
                        patient_phone=(patient.get("phones") or [""])[0],
                        patient_phone_normalized=(patient.get("phones") or [""])[0],
                        patient_match_status="matched",
                        patient_match_method="patient_registry_full_sync",
                        patient_match_score=1.0,
                        appointment_date=detected.encounter_date,
                        appointment_status="seen",
                        status="ready",
                        status_reason="full_sync_discovered",
                        encounter_key=detected.encounter_key,
                        encounter_date=detected.encounter_date,
                        encounter_type=detected.encounter_type,
                        encounter_code=detected.encounter_code,
                        encounter_chief_complaint=detected.chief_complaint,
                        encounter_source=detected.source,
                        created_at=now_iso(),
                        updated_at=now_iso(),
                        first_ready_at=now_iso(),
                    )
                    rows.append(record)
                    by_row_id[row_id] = record
                elif rescrape_all and record.status == "processed":
                    record.status = "ready"
                    record.status_reason = "full_sync_rescrape"
                    record.pdf_path = ""
                    record.processed_at = ""
                    record.updated_at = now_iso()

                # all_patient_encounters leaves the page on the timeline. Each
                # process call returns to the Summary URL and uses the normal flow.
                result = process_records_on_page(
                    page,
                    queue_json,
                    config,
                    downloads_dir,
                    [record],
                    rows,
                    store,
                    limit=1,
                    dry_run=dry_run,
                    exact_refresh=False,
                )
                for state in ("processed", "validated", "review", "ready", "failed"):
                    counts[state] += result.get(state, 0)
                if record.status == "processed":
                    processed_keys.add(key)
        except Exception as exc:
            counts["patients_failed"] += 1
            print(f"  patient ERROR: {type(exc).__name__}: {exc}", flush=True)
        finally:
            patient_timings.append(
                {
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "seconds": round(time.perf_counter() - patient_started, 3),
                }
            )
            save_store(queue_json, store, rows)

    counts["patient_timings"] = patient_timings
    return counts
