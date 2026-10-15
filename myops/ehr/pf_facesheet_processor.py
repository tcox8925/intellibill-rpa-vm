"""
Daily Practice Fusion PDF Processor.

Python port of intellibill-rpa's
src/functions/DailyPracticeFusionPdfProcessorJob.js. That Azure Function no
longer exists -- this job (and its schedule) now lives here, called by
server.py's /run-daily-practice-fusion-pdf-processor endpoint + the
APScheduler cron registered in server.py's lifespan.
DailyPracticeFusionPDFLoaderJob.js (still in intellibill-rpa, still on its
own app.timer) only triggers the Practice Fusion scrape now; it no longer
chains into a Processor.

Forwards every Practice Fusion facesheet PDF sitting in the one fixed blob
folder below to the RCM backend's pfFacesheetProcessing.processFacesheet
mutation -- NOT medicalExtraction.uploadMedicalFile (that's pdf_processor.py,
which explicitly excludes this same folder). See
PF_FACESHEET_PROCESSING_README.md in the backend repo for what that mutation
does: OCR + LLM extraction into patient_header/patient_coverages/
patient_visits, dedup'd server-side, no claim/ICD involvement at all.
"""

import base64
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from azure.storage.blob import BlobPrefix, BlobServiceClient

from .config import (
    AZURE_STORAGE_CONNECTION_STRING,
    BACKEND_API_URL,
    RCM_ATTACHMENTS_CONTAINER,
    RCM_SYSTEM_EMAIL,
    RCM_SYSTEM_PASSWORD,
)

INBOUND_PATH_SUFFIX = "Exchange/Medical Extraction/INBOUND"
# The one fixed blob folder this job is scoped to - mirrors the
# `${clientTaxid}-${entityId}` convention pdf_processor.py's groups use.
PRACTICE_FUSION_FOLDER_STRUCTURE = "1553326257-1553326257001"
RESTRICTED_FOLDERS = {"archived", "processed", "deleted"}
BATCH_SIZE = 3

ALREADY_PROCESSED = "ALREADY_PROCESSED"
SKIPPED = "SKIPPED"
NO_MANIFEST_ENTRY = "NO_MANIFEST_ENTRY"
FAILED = "FAILED"

REQUEST_TIMEOUT = 600


def run_daily_practice_fusion_pdf_processor(log, restore_mode=False):
    log("🚀 Daily Practice Fusion PDF Processor Started")

    try:
        _validate_config()

        session = _login(log)
        container = _get_container(log)
        inbound_prefix = _build_pf_inbound_prefix()

        stats = {"total": 0, "processed": 0, "skipped": 0, "failed": 0, "zips": []}

        if restore_mode:
            log("🔄 Running in RESTORE MODE")
            _restore_processed_prefixes(container, inbound_prefix, log)
        else:
            log("📋 Running in PROCESS MODE")
            log(f"📂 Processing Practice Fusion inbound folder: {inbound_prefix}")
            _process_blob_prefix(container, inbound_prefix, session, stats, log)

        _print_summary(stats, log)
        return stats
    except Exception as err:
        log("❌ CRITICAL ERROR")
        log(repr(err))
        raise


# ------------------------------------------------------------------------- #
#                                  AUTH                                     #
# ------------------------------------------------------------------------- #


def _login(log):
    log("🔐 Logging in...")
    session = requests.Session()
    session.post(
        f"{BACKEND_API_URL}/api/trpc/auth.login?batch=1",
        json={"0": {"email": RCM_SYSTEM_EMAIL, "password": RCM_SYSTEM_PASSWORD}},
        timeout=REQUEST_TIMEOUT,
    )
    if "TOKEN" not in session.cookies.get_dict():
        raise RuntimeError("Login failed: No TOKEN cookie")
    log("✅ Authenticated")
    return session


def _extract_trpc_data(response_data):
    payload = response_data[0] if isinstance(response_data, list) else response_data
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict) and "data" in result:
        data = result["data"]
        if isinstance(data, dict) and "json" in data:
            return data["json"]
        return data
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        if isinstance(data, dict) and "json" in data:
            return data["json"]
        return data
    return payload


# ------------------------------------------------------------------------- #
#                                STORAGE                                    #
# ------------------------------------------------------------------------- #


def _get_container(log):
    log(f"📁 Connecting to blob storage {RCM_ATTACHMENTS_CONTAINER}")
    client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container = client.get_container_client(RCM_ATTACHMENTS_CONTAINER)
    log(f"✅ Connected to {container.container_name}")
    return container


# ------------------------------------------------------------------------- #
#                              ZIP PROCESS                                  #
# ------------------------------------------------------------------------- #


def _process_zip_blob(container, blob_name, session, stats, log):
    zip_stats = {"total": 0, "processed": 0, "skipped": 0, "failed": 0}
    log(f"\n📁 Processing ZIP: {blob_name}")

    buffer = container.download_blob(blob_name).readall()
    zf = zipfile.ZipFile(io.BytesIO(buffer))
    zip_metadata = _extract_zip_metadata(zf, blob_name, log)
    pdf_metadata_map = {}
    if zip_metadata:
        for appt in zip_metadata.get("appointments", []):
            if appt.get("pdf_file"):
                pdf_metadata_map[appt["pdf_file"]] = appt
        log(f"📋 ZIP metadata loaded for {blob_name} (appointments: {len(pdf_metadata_map)})")

    pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
    log(f"📄 Found {len(pdf_names)} PDFs")
    zip_files = []

    for i in range(0, len(pdf_names), BATCH_SIZE):
        batch = pdf_names[i : i + BATCH_SIZE]
        start, end = i + 1, min(i + BATCH_SIZE, len(pdf_names))
        batch_stats = {"total": 0, "processed": 0, "skipped": 0, "failed": 0}
        log(f"📦 Starting batch {start}-{end}")

        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
            results = list(
                pool.map(lambda name: _process_pdf(zf, name, session, log, pdf_metadata_map.get(name)), batch)
            )

        for name, result in zip(batch, results):
            zip_files.append(_file_record(name, result, pdf_metadata_map.get(name)))
            for bucket in (stats, batch_stats, zip_stats):
                bucket["total"] += 1

            if result.get("success"):
                for bucket in (stats, batch_stats, zip_stats):
                    bucket["processed"] += 1
            elif result.get("errorCode") in (ALREADY_PROCESSED, NO_MANIFEST_ENTRY):
                for bucket in (stats, batch_stats, zip_stats):
                    bucket["skipped"] += 1
            else:
                for bucket in (stats, batch_stats, zip_stats):
                    bucket["failed"] += 1

        log(
            f"📊 Batch {start}-{end} (Processed: {batch_stats['processed']}, "
            f"Skipped: {batch_stats['skipped']}, Failed: {batch_stats['failed']}, Total: {batch_stats['total']})"
        )

    log(
        f"\n📁 ZIP {blob_name} (Processed: {zip_stats['processed']}, Skipped: {zip_stats['skipped']}, "
        f"Failed: {zip_stats['failed']}, Total: {zip_stats['total']})"
    )

    # "Processed" = the extracted data is actually stored: the facesheet's
    # attachments row is completed AND its patient_visits row exists
    # (intellibill's processPfFacesheet writes both in one transaction).
    # An OK from processFacesheet alone isn't proof, so confirm from the DB
    # and only move the ZIP once every PDF is stored. A PDF with no manifest
    # entry is never sent to the backend, so it can't hold the ZIP back.
    lookup_error = None
    try:
        stored = _lookup_stored_facesheets([f["attachment_id"] for f in zip_files if f.get("attachment_id")])
    except Exception as err:
        stored, lookup_error = {}, f"attachments/patient_visits lookup failed: {err}"
        log(f"❌ {blob_name} {lookup_error}")

    not_stored = 0
    for f in zip_files:
        row = stored.get(f.get("attachment_id"))
        if row and row["completed"] and row["patient_visit_id"]:
            f["stored"] = True
            f["patient_visit_id"] = row["patient_visit_id"]
            f["diagnosis_count"] = row["diagnosis_count"]
            f.pop("reason", None)
            continue
        f["stored"] = False
        if row and row["failure"]:
            f["reason"] = row["failure"]
        elif row and row["completed"]:
            f["reason"] = "attachment completed but no patient_visits row for this patient/appt_date"
        elif row:
            f["reason"] = f"attachment processing_status={row['processing_status']}"
        elif f["status"] == "no_manifest_entry":
            f["reason"] = "no manifest entry / appt_date in ZIP metadata (never sent to backend)"
        elif not f.get("reason"):
            f["reason"] = "no attachments row for this facesheet"
        if f["status"] != "no_manifest_entry":
            not_stored += 1

    moved, move_note = False, None
    if lookup_error:
        move_note = f"kept in INBOUND: {lookup_error}"
    elif not_stored == 0 and len(zip_files) == len(pdf_names):
        log(f"✅ All PDFs in {blob_name} stored in patient_visits - moving ZIP to processed folder")
        try:
            _move_zip_to_processed(container, blob_name, buffer, log)
            moved = True
        except Exception as err:
            move_note = f"move failed: {err}"
            log(f"❌ Failed to move ZIP to processed folder for {blob_name}: {err}")
    else:
        move_note = f"kept in INBOUND: {not_stored} PDF(s) not stored in patient_visits (rechecked next run)"
    if move_note:
        log(f"⏸️ {blob_name} {move_note}")

    # Per-ZIP breakdown for the PRACTICE_FUSION_FACESHEET_PULL cron response.
    # Keys deliberately avoid "failed"/"error" so pf_sync_v5_6/server.py's
    # _find_stage_failures keeps judging the run exactly as before.
    stats["zips"].append({
        "zip": blob_name,
        "pdfs": len(pdf_names),
        "processed": sum(1 for f in zip_files if f["stored"]),
        "already_processed": sum(1 for f in zip_files if f["status"] == "already_processed" and f["stored"]),
        "no_manifest_entry": sum(1 for f in zip_files if f["status"] == "no_manifest_entry"),
        "not_processed": not_stored,
        "moved_to_processed": moved,
        "note": move_note,
        "files": zip_files,
    })


def _parse_appt_date(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _lookup_stored_facesheets(attachment_ids):
    """{attachment_id: {completed, processing_status, failure, patient_visit_id,
    diagnosis_count}} from "EDI_Tebra".attachments + patient_visits, matched the
    same way intellibill does (backfillMissingPfFacesheetPatientVisits.ts):
    patient_visits.patient_header_id = attachments.associated_patient_header_id
    AND patient_visits.appt_date = the manifest's appt_date."""
    from .pdf_processor import _rcm_connection

    ids = sorted({int(a) for a in attachment_ids if a})
    if not ids:
        return {}
    conn = _rcm_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, processing_status, processed, associated_patient_header_id,
                   json_manifest_entry->>'appt_date', claim_creation_response
            FROM "EDI_Tebra".attachments
            WHERE id = ANY(%s)
              AND attachment_type = 'Facesheet' AND upload_source = 'practice_fusion'
            """,
            (ids,),
        )
        attachments = cur.fetchall()
        header_ids = sorted({str(r[3]) for r in attachments if r[3]})
        visits = {}
        if header_ids:
            cur.execute(
                """
                SELECT patient_header_id, appt_date, id, COALESCE(jsonb_array_length(visit_diagnosis), 0)
                FROM "EDI_Tebra".patient_visits
                WHERE patient_header_id = ANY(%s::uuid[])
                ORDER BY created_at DESC NULLS LAST
                """,
                (header_ids,),
            )
            for header_id, appt_date, visit_id, dx_count in cur.fetchall():
                visits.setdefault((str(header_id), appt_date), (str(visit_id), dx_count))
    finally:
        conn.close()

    out = {}
    for att_id, status, processed, header_id, appt_date, response in attachments:
        visit = visits.get((str(header_id), _parse_appt_date(appt_date))) if header_id else None
        failure = None
        if status == "failed" and isinstance(response, dict):
            failure = f"{response.get('stage')}: {response.get('error')}"
        out[att_id] = {
            "completed": status == "completed" or processed is True,
            "processing_status": status,
            "failure": failure,
            "patient_visit_id": visit[0] if visit else None,
            "diagnosis_count": visit[1] if visit else None,
        }
    return out


def _file_record(file_name, result, manifest_entry):
    code = result.get("errorCode")
    if result.get("success"):
        status = "processed"
    elif code == ALREADY_PROCESSED:
        status = "already_processed"
    elif code == NO_MANIFEST_ENTRY:
        status = "no_manifest_entry"
    elif code == SKIPPED:
        status = "skipped_by_backend"
    else:
        status = "not_processed"
    rec = {
        "file": file_name,
        "patient_name": (manifest_entry or {}).get("patient_name"),
        "appt_date": (manifest_entry or {}).get("appt_date"),
        "status": status,
        "attachment_id": result.get("attachmentId"),
    }
    reason = result.get("reason") or result.get("error")
    if reason:
        rec["reason"] = reason
    return rec


def _process_blob_prefix(container, prefix, session, stats, log):
    normalized_prefix = _normalize_blob_prefix(prefix)
    log(f"📂 Scanning folder: {normalized_prefix or '/'}")

    for item in container.walk_blobs(name_starts_with=normalized_prefix, delimiter="/"):
        is_prefix = isinstance(item, BlobPrefix)
        name = item.name

        if is_prefix:
            if _is_restricted_blob(name):
                log(f"⏭️ Skipping restricted folder: {name}")
                continue
            _process_blob_prefix(container, name, session, stats, log)
            continue

        if not name.endswith(".zip"):
            continue
        _process_zip_blob(container, name, session, stats, log)


# ------------------------------------------------------------------------- #
#                              PDF PROCESS                                  #
# ------------------------------------------------------------------------- #


def _process_pdf(zf, file_name, session, log, manifest_entry):
    try:
        buffer = zf.read(file_name)
        if not buffer:
            raise RuntimeError("Empty PDF")

        if not manifest_entry:
            log(f"⏭️ {file_name} SKIPPED: no manifest entry found in ZIP metadata")
            return {"success": False, "errorCode": NO_MANIFEST_ENTRY}

        # appt_date is required (non-nullable) on the backend's
        # practiceFusionManifestEntrySchema - skip locally instead of
        # round-tripping a guaranteed 400.
        if not manifest_entry.get("appt_date"):
            log(f"⏭️ {file_name} SKIPPED: manifest entry missing appt_date")
            return {"success": False, "errorCode": NO_MANIFEST_ENTRY}

        result = _call_facesheet_processing_api(log, buffer, file_name, session, manifest_entry)

        status = result.get("status")
        # attachmentId: the "EDI_Tebra".attachments row the backend staged for
        # this facesheet -- used afterwards to confirm the data was stored.
        attachment_id = result.get("attachmentId")
        if status == "applied":
            log(f"✅ File applied: {file_name} (patient_id: {manifest_entry.get('patient_id')})")
            return {"success": True, "attachmentId": attachment_id}
        if status == "already_processed":
            log(f"⏭️ Already processed: {file_name}")
            return {"success": False, "errorCode": ALREADY_PROCESSED, "attachmentId": attachment_id}
        if status == "skipped":
            log(f"⏭️ Skipped by backend: {file_name} (reason: {result.get('reason')})")
            return {"success": False, "errorCode": SKIPPED, "reason": result.get("reason"),
                    "attachmentId": attachment_id}

        raise RuntimeError(f"Unexpected facesheet processing API response: {result}")
    except Exception as err:
        log(f"❌ {file_name} ERROR: {err}")
        return {"success": False, "errorCode": FAILED, "error": str(err)}


# ------------------------------------------------------------------------- #
#                           EXTERNAL CALLS                                  #
# ------------------------------------------------------------------------- #


def _call_facesheet_processing_api(
    log, buffer, file_name, session, manifest_entry, special_historical_diagnosis_run=False
):
    # Build the manifestEntry explicitly rather than passing the raw parsed
    # object through -- the backend's pfFacesheetManifestEntrySchema declares
    # patient_id as nullable-but-required (must be present even when null),
    # so a generic "strip null values" helper would break exactly the
    # appointments that legitimately have no patient_id yet.
    payload = {
        "fileName": file_name,
        "fileContent": base64.b64encode(buffer).decode("ascii"),
        "fileType": "application/pdf",
        "manifestEntry": {
            "appt_date": manifest_entry.get("appt_date"),
            "appt_time": manifest_entry.get("appt_time"),
            "pdf_file": manifest_entry.get("pdf_file"),
            "patient_name": manifest_entry.get("patient_name"),
            "dob": manifest_entry.get("dob"),
            "provider_name": manifest_entry.get("provider_name"),
            "service_location": manifest_entry.get("service_location"),
            "patient_id": manifest_entry.get("patient_id"),
        },
        # Lets the backend tell a one-off historical diagnosis backfill call
        # (see historical_diagnosis_backfill.py) apart from the normal
        # nightly/refresh delivery path -- e.g. to re-apply over an existing
        # already_processed row instead of skipping it. False on every
        # existing caller in this file, so normal delivery is unaffected.
        "specialHistoricalDiagnosisRun": special_historical_diagnosis_run,
    }

    res = session.post(
        f"{BACKEND_API_URL}/api/trpc/pfFacesheetProcessing.processFacesheet",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if res.status_code == 200:
        log(f"   ✅ Facesheet processing API returned 200 OK for {file_name}")
    else:
        log(f"   ⚠️ Facesheet processing API returned status {res.status_code}")

    data = res.json()
    if not data:
        raise RuntimeError("Empty facesheet processing API response")

    return _extract_trpc_data(data)


# ------------------------------------------------------------------------- #
#                                HELPERS                                    #
# ------------------------------------------------------------------------- #


def _move_zip_to_processed(container, blob_name, buffer, log):
    last_slash = blob_name.rfind("/")
    folder_path = blob_name[:last_slash] if last_slash >= 0 else ""
    file_name = blob_name[last_slash + 1 :] if last_slash >= 0 else blob_name
    destination = f"{folder_path}/processed/{file_name}" if folder_path else f"processed/{file_name}"

    log(f"📦 Moving ZIP to processed folder: {blob_name} -> {destination}")
    container.upload_blob(destination, buffer, overwrite=True)
    container.delete_blob(blob_name)
    log(f"✅ Moved ZIP to processed folder: {destination}")


def _restore_processed_prefixes(container, prefix, log):
    normalized_prefix = _normalize_blob_prefix(prefix)
    for item in container.walk_blobs(name_starts_with=normalized_prefix, delimiter="/"):
        is_prefix = isinstance(item, BlobPrefix)
        if not is_prefix:
            continue
        name = item.name

        if _is_processed_folder(name):
            log(f"📂 Restoring ZIPs from processed folder: {name}")
            try:
                _restore_zips_under_prefix(container, name, log)
            except Exception as err:
                log(f"❌ Failed to restore ZIPs from processed folder {name}: {err}")
            continue

        _restore_processed_prefixes(container, name, log)


def _restore_zips_under_prefix(container, prefix, log):
    for blob in container.list_blobs(name_starts_with=prefix):
        if not blob.name.endswith(".zip"):
            continue
        try:
            _move_zip_out_of_processed_folder(container, blob.name, log)
        except Exception as err:
            log(f"❌ Failed to restore ZIP {blob.name}: {err}")


def _move_zip_out_of_processed_folder(container, blob_name, log):
    destination = _remove_processed_segment(blob_name)
    if destination == blob_name:
        log(f"⚠️ Could not derive restore path for ZIP: {blob_name}")
        return

    buffer = container.download_blob(blob_name).readall()
    log(f"📦 Restoring ZIP: {blob_name} -> {destination}")
    container.upload_blob(destination, buffer, overwrite=True)
    container.delete_blob(blob_name)
    log(f"✅ Restored ZIP: {destination}")


def _remove_processed_segment(blob_name):
    parts = [p for p in blob_name.split("/") if p]
    try:
        parts.remove(next(p for p in parts if p.lower() == "processed"))
    except StopIteration:
        return blob_name
    return "/".join(parts)


def _normalize_blob_prefix(prefix):
    trimmed = str(prefix or "").strip()
    if not trimmed or trimmed == "/":
        return ""
    return trimmed.lstrip("/")


def _build_pf_inbound_prefix():
    normalized = _normalize_blob_prefix(PRACTICE_FUSION_FOLDER_STRUCTURE)
    suffix = _normalize_blob_prefix(INBOUND_PATH_SUFFIX)
    return f"{normalized}/{suffix}" if normalized else suffix


def _is_processed_folder(prefix):
    parts = [p for p in prefix.lower().split("/") if p]
    return bool(parts) and parts[-1] == "processed"


def _is_restricted_blob(blob_name):
    parts = [p for p in blob_name.lower().split("/") if p]
    return any(p in RESTRICTED_FOLDERS for p in parts)


def _extract_zip_metadata(zf, blob_name, log):
    try:
        json_entries = [n for n in zf.namelist() if n.lower().endswith(".json")]
        if not json_entries:
            raise RuntimeError(f"ZIP {blob_name} does not contain a metadata JSON file")

        parsed = json.loads(zf.read(json_entries[0]).decode("utf-8")) or {}
        return {
            "generatedOn": parsed.get("generated_on"),
            # PF manifest entries: {pdf_file, appt_date, appt_time,
            # patient_name, dob, provider_name, service_location, patient_id}
            # -- no appt_id/facesheet_id/entity/sub_entity/ehr_name, unlike
            # the generic Tebra-style manifest pdf_processor.py parses.
            "appointments": parsed.get("appointments") or [],
            "raw": parsed,
        }
    except Exception as err:
        log(f"⚠️ Failed to extract ZIP metadata for {blob_name}: {err}")
        return None


def _validate_config():
    required = {
        "RCM_SYSTEM_EMAIL": RCM_SYSTEM_EMAIL,
        "RCM_SYSTEM_PASSWORD": RCM_SYSTEM_PASSWORD,
        "BACKEND_API_URL": BACKEND_API_URL,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing env: {', '.join(missing)}")


def _print_summary(stats, log):
    log("\n==============================")
    log("📊 SUMMARY")
    log(f"Total     : {stats['total']}")
    log(f"Processed : {stats['processed']}")
    log(f"Skipped   : {stats['skipped']}")
    log(f"Failed    : {stats['failed']}")
    rate = ((stats["processed"] + stats["skipped"]) / stats["total"]) * 100 if stats["total"] else 0
    log(f"Success % : {rate:.2f}%")
    log("==============================")
