"""
Daily PDF Processor.

Python port of intellibill-rpa's src/functions/DailyPdfProcessorJob.js. That
Azure Function no longer exists -- this job (and its schedule) now lives
here, called by server.py's /run-daily-pdf-processor endpoint + the
APScheduler cron registered in server.py's lifespan. DailyPdfLoaderJob.js
(still in intellibill-rpa, still on its own app.timer) only triggers the
Tebra scrape now; it no longer chains into a Processor.

Scans the `rcm-attachments` blob container for ZIPs of scraped Tebra
facesheet PDFs (one ZIP per practice/date, dropped by the scrape under
`<folder_structure>/Exchange/Medical Extraction/INBOUND`), uploads each PDF
to the RCM backend's medicalExtraction.uploadMedicalFile mutation, and moves
fully-processed ZIPs into a `processed/` subfolder.

Practice Fusion's group folder is excluded here on purpose -- it's handled
exclusively by pf_facesheet_processor.py (DailyPracticeFusionPdfProcessorJob's
port).
"""

import base64
import io
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

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
RESTRICTED_FOLDERS = {"archived", "processed", "deleted"}
# Practice Fusion group folder -- handled exclusively by pf_facesheet_processor.py
EXCLUDED_FOLDER_STRUCTURES = ["1553326257-1553326257001"]
MAX_RETRIES = 3
BATCH_SIZE = 3

DUPLICATE_FILE = "DUPLICATE_FILE"
EXHAUSTED = "EXHAUSTED"

REQUEST_TIMEOUT = 600


def run_daily_pdf_processor(log, restore_mode=False):
    log("🚀 Daily PDF Processor Started")

    try:
        _validate_config()

        session = _login(log)
        groups = _fetch_practice_groups(log, session)
        container = _get_container(log)

        stats = {"total": 0, "processed": 0, "skipped": 0, "exhausted": 0, "failed": 0,
                 "zips": []}

        if restore_mode:
            log("🔄 Running in RESTORE MODE")
            _restore_zips_from_processed_folder(container, groups, log)
        else:
            log("📋 Running in PROCESS MODE")
            _process_group_inbound_prefixes(container, groups, session, stats, log)

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
    res = session.post(
        f"{BACKEND_API_URL}/api/trpc/auth.login?batch=1",
        json={"0": {"email": RCM_SYSTEM_EMAIL, "password": RCM_SYSTEM_PASSWORD}},
        timeout=REQUEST_TIMEOUT,
    )

    if "TOKEN" not in session.cookies.get_dict():
        raise RuntimeError("Login failed: No TOKEN cookie")

    log("✅ Authenticated")
    return session


def _fetch_practice_groups(log, session):
    url = f"{BACKEND_API_URL}/api/trpc/affiliation.getGroupsAsPracticesForPullingFaceSheets"
    res = session.get(url, timeout=REQUEST_TIMEOUT)
    groups = _extract_trpc_data(res.json())

    if not isinstance(groups, list):
        raise RuntimeError("Groups API returned an unexpected response shape")

    normalized = []
    for g in groups:
        entry = {
            "groupName": str(g.get("groupName") or "").strip(),
            "entity": str(g.get("entity") or "").strip(),
            "subEntity": str(g.get("subEntity") or "").strip(),
            "folderStructure": str(g.get("folderStructure") or "").strip(),
        }
        if entry["groupName"] and entry["entity"] and entry["subEntity"] and entry["folderStructure"]:
            normalized.append(entry)

    if not normalized:
        raise RuntimeError("Groups API returned no valid folder structures")

    filtered = [g for g in normalized if not _is_excluded_folder_structure(g["folderStructure"])]
    excluded_count = len(normalized) - len(filtered)
    if excluded_count:
        log(f"⏭️ Excluded {excluded_count} group folder mapping(s) handled by pf_facesheet_processor")

    if not filtered:
        raise RuntimeError("No group folder structures remain after exclusion")

    log(f"🏥 Retrieved {len(filtered)} active group folder mappings")
    return filtered


def _is_excluded_folder_structure(folder_structure):
    return any(excluded in folder_structure for excluded in EXCLUDED_FOLDER_STRUCTURES)


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
    zip_stats = {"total": 0, "processed": 0, "skipped": 0, "exhausted": 0, "failed": 0}
    failed_rpa_appointment_ids = []
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
        batch_stats = {"total": 0, "processed": 0, "skipped": 0, "exhausted": 0, "failed": 0}
        log(f"📦 Starting batch {start}-{end}")

        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
            results = list(
                pool.map(
                    lambda name: _process_pdf(
                        zf,
                        name,
                        blob_name,
                        session,
                        log,
                        failed_rpa_appointment_ids,
                        pdf_metadata_map.get(name),
                        zip_metadata,
                        (zip_metadata or {}).get("practice"),
                    ),
                    batch,
                )
            )

        for result in results:
            zip_files.append(_file_record(result))
            for bucket in (stats, batch_stats, zip_stats):
                bucket["total"] += 1

            if result.get("success"):
                for bucket in (stats, batch_stats, zip_stats):
                    bucket["processed"] += 1
            elif result.get("errorCode") == DUPLICATE_FILE:
                for bucket in (stats, batch_stats, zip_stats):
                    bucket["skipped"] += 1
            elif result.get("errorCode") == EXHAUSTED:
                for bucket in (stats, batch_stats, zip_stats):
                    bucket["exhausted"] += 1
            else:
                for bucket in (stats, batch_stats, zip_stats):
                    bucket["failed"] += 1

        log(
            f"📊 Batch {start}-{end} (Processed: {batch_stats['processed']}, "
            f"Skipped: {batch_stats['skipped']}, Exhausted: {batch_stats['exhausted']}, "
            f"Failed: {batch_stats['failed']}, Total: {batch_stats['total']})"
        )

    log(
        f"\n📁 ZIP {blob_name} (Processed: {zip_stats['processed']}, Skipped: {zip_stats['skipped']}, "
        f"Exhausted: {zip_stats['exhausted']}, Failed: {zip_stats['failed']}, Total: {zip_stats['total']})"
    )

    # An OK from uploadMedicalFile doesn't prove a claim exists -- the
    # backend only stages the file and creates the claim in the background
    # (intellibill medicalExtractionRouter.ts), and the ZIP used to move on
    # that OK alone. Confirm each PDF's claim from the DB instead
    # ("EDI_Tebra".attachments.associated_claim_id -> claim_header, the same
    # link the backend's own duplicate check uses), waiting a bounded time
    # for background processing to finish, and only move the ZIP once every
    # PDF has a real claim (or is exhausted).
    appt_ids = [f["appt_id"] for f in zip_files if f.get("appt_id")]
    # Only wait when this pass actually uploaded something -- an old ZIP
    # still sitting in INBOUND (re-scanned on every run) shouldn't cost a
    # full wait each time if its attachment is stuck 'pending' backend-side.
    claim_rows, lookup_error = _wait_for_claims(
        appt_ids, log, wait_seconds=CLAIM_CONFIRM_WAIT_SECONDS if zip_stats["processed"] else 0,
    )

    unconfirmed = 0
    for f in zip_files:
        row = claim_rows.get(str(f.get("appt_id")))
        if row and row["claim_id"]:
            f["claim_id"] = row["claim_id"]
            f["dcn"] = row["dcn"]
            f["claim_status"] = row["status"]
            f["attachment_id"] = row["attachment_id"]
            f["claim_confirmed"] = True
            f.pop("error", None)
        else:
            f["claim_confirmed"] = False
            if row:
                f["attachment_id"] = row["attachment_id"]
            if row and row["failure_reason"]:
                f["error"] = f"claim not created: {row['failure_reason']}"
            elif row and row["processing_status"] in ("pending", "processing"):
                f["error"] = f"claim creation still {row['processing_status']} after waiting"
            elif f["status"] in ("uploaded", "duplicate") and not f.get("error"):
                f["error"] = "no claim linked to this facesheet's attachment yet"
            if f["status"] != "exhausted":
                unconfirmed += 1

    claims_confirmed = sum(1 for f in zip_files if f["claim_confirmed"])
    moved, move_note = False, None
    if lookup_error:
        move_note = f"kept in INBOUND: {lookup_error}"
    elif unconfirmed == 0 and len(zip_files) == len(pdf_names):
        log(f"✅ All PDFs in {blob_name} have confirmed claims (or are exhausted) - moving ZIP to processed folder")
        try:
            _move_zip_to_processed(container, blob_name, buffer, log)
            moved = True
        except Exception as err:
            move_note = f"move failed: {err}"
            log(f"❌ Failed to move ZIP to processed folder for {blob_name}: {err}")
    else:
        move_note = f"kept in INBOUND: {unconfirmed} PDF(s) without a confirmed claim (rechecked next run)"
    if move_note:
        log(f"⏸️ {blob_name} {move_note}")

    stats["zips"].append({
        "zip": blob_name,
        "pdfs": len(pdf_names),
        "claims_confirmed": claims_confirmed,
        "uploaded_now": zip_stats["processed"],
        "duplicates": zip_stats["skipped"],
        "exhausted": zip_stats["exhausted"],
        "failed": zip_stats["failed"],
        "unconfirmed": unconfirmed,
        "moved_to_processed": moved,
        "note": move_note,
        "files": zip_files,
    })


def _rcm_connection():
    """The RCM DB (where "EDI_Tebra".claim_header lives) -- same RCM_DB_*
    env vars cron_execution_log.py uses for cron_job_executions."""
    import os
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("RCM_DB_HOST", "").strip(),
        dbname=os.environ.get("RCM_DB_NAME", "").strip(),
        user=os.environ.get("RCM_DB_USER", "").strip(),
        password=os.environ.get("RCM_DB_PASSWORD", "").strip(),
        sslmode="require",
        connect_timeout=10,
    )


# How long to wait for the backend's background claim creation before
# judging a ZIP -- anything still pending after this keeps the ZIP in
# INBOUND and is rechecked on the next processor run.
CLAIM_CONFIRM_WAIT_SECONDS = 600
CLAIM_CONFIRM_POLL_SECONDS = 15


def _wait_for_claims(appt_ids, log, wait_seconds=CLAIM_CONFIRM_WAIT_SECONDS):
    """Poll _lookup_claims until no appointment's attachment is still
    pending/processing (or the wait runs out). Returns (rows, lookup_error)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            rows = _lookup_claims(appt_ids)
        except Exception as err:
            log(f"❌ claim lookup failed: {err}")
            return {}, f"claim lookup failed: {err}"
        in_flight = [a for a, r in rows.items()
                     if not r["claim_id"] and r["processing_status"] in ("pending", "processing")]
        if not in_flight or time.monotonic() >= deadline:
            return rows, None
        log(f"⏳ Waiting on background claim creation for {len(in_flight)} appointment(s)...")
        time.sleep(CLAIM_CONFIRM_POLL_SECONDS)


def _lookup_claims(appt_ids):
    """{rpa_appointment_id: {attachment_id, processing_status, claim_id, dcn,
    status, failure_reason}} from the appointment's latest Facesheet
    attachment (same row/filters as the backend's isDuplicateMedicalFile).
    The claim is attachments.associated_claim_id; claim_creation_response
    carries the claimId too, or the reason no claim was created."""
    appt_ids = sorted({str(a) for a in appt_ids if a})
    if not appt_ids:
        return {}
    conn = _rcm_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT ON (a.rpa_appointment_id)
                   a.rpa_appointment_id, a.id, a.processing_status,
                   ch.claim_id, ch.dcn, ch.status, a.claim_creation_response
            FROM "EDI_Tebra".attachments a
            LEFT JOIN "EDI_Tebra".claim_header ch ON ch.claim_id = COALESCE(
                a.associated_claim_id,
                CASE WHEN a.claim_creation_response->>'claimId'
                          ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                     THEN (a.claim_creation_response->>'claimId')::uuid END)
            WHERE a.attachment_type = 'Facesheet'
              AND a.rpa_appointment_id = ANY(%s)
              AND a.status <> 'TR'
            ORDER BY a.rpa_appointment_id, a.created_at DESC
            """,
            (appt_ids,),
        )
        out = {}
        for appt, att_id, proc_status, claim_id, dcn, claim_status, response in cur.fetchall():
            failure = None
            if not claim_id and isinstance(response, dict):
                detail = response.get("reason") or response.get("error")
                if detail:
                    failure = f"{response.get('stage')}: {detail}"
            out[appt] = {
                "attachment_id": att_id,
                "processing_status": proc_status,
                "claim_id": str(claim_id) if claim_id else None,
                "dcn": dcn,
                "status": claim_status,
                "failure_reason": failure,
            }
        return out
    finally:
        conn.close()


def _file_record(result):
    """One per-PDF line for the cron response: which claim (and DCN) it
    produced, or why it didn't."""
    if result.get("success"):
        status = "uploaded"
    elif result.get("errorCode") == DUPLICATE_FILE:
        status = "duplicate"
    elif result.get("errorCode") == EXHAUSTED:
        status = "exhausted"
    else:
        status = "failed"
    # claim_id / dcn are filled from claim_header afterwards (_lookup_claims).
    rec = {"file": result.get("file"), "appt_id": result.get("appt_id"),
           "appt_date": result.get("appt_date"), "status": status,
           "claim_id": None, "dcn": None}
    if status not in ("uploaded", "duplicate"):
        rec["error"] = result.get("error")
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


def _process_group_inbound_prefixes(container, groups, session, stats, log):
    for prefix in _get_unique_inbound_prefixes(groups):
        log(f"📂 Processing inbound folder for group mapping: {prefix}")
        _process_blob_prefix(container, prefix, session, stats, log)


# ------------------------------------------------------------------------- #
#                              PDF PROCESS                                  #
# ------------------------------------------------------------------------- #


def _process_pdf(zf, file_name, blob_name, session, log, failed_rpa_appointment_ids, manifest_entry, zip_metadata, group):
    sftp_file_path = blob_name
    match = re.match(r"^([0-9]+)_", file_name.strip())
    rpa_appointment_id = (manifest_entry or {}).get("appt_id") or (match.group(1) if match else None)
    retry_status = None

    try:
        buffer = zf.read(file_name)
        if not buffer:
            raise RuntimeError("Empty PDF")

        retry_status = _check_duplicate(log, file_name, session, rpa_appointment_id)
        if retry_status["retry_count"] > MAX_RETRIES:
            raise _CodedError("Max retries exhausted", EXHAUSTED)

        api_data = _call_medical_api(
            log, buffer, file_name, sftp_file_path, session, manifest_entry, zip_metadata, group
        )

        log(f"✅ File uploaded: {file_name} → claimId={api_data.get('claimId')}")
        return {"success": True, "file": file_name, "appt_id": rpa_appointment_id,
                "appt_date": (manifest_entry or {}).get("appt_date")}
    except Exception as err:
        code = getattr(err, "code", None)
        log(f"❌ {file_name} ERROR: {err}")

        if retry_status is not None and code != DUPLICATE_FILE:
            try:
                _update_retry_count(log, rpa_appointment_id, retry_status["retry_count"], session)
                log(f"✅ Retry count for appointment {rpa_appointment_id} updated successfully")
            except Exception as retry_err:
                log(f"⚠️ Failed to update retry count for appointment {rpa_appointment_id}: {retry_err}")

        failed_rpa_appointment_ids.append(rpa_appointment_id)
        return {
            "success": False,
            "file": file_name,
            "appt_id": rpa_appointment_id,
            "appt_date": (manifest_entry or {}).get("appt_date"),
            "errorCode": code,
            "error": str(err),
        }


class _CodedError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------------- #
#                           EXTERNAL CALLS                                  #
# ------------------------------------------------------------------------- #


def _check_duplicate(log, file_name, session, manifest_appointment_id):
    url = f"{BACKEND_API_URL}/api/trpc/medicalDuplicate.isDuplicateMedicalFile"
    input_payload = {}
    if file_name:
        input_payload["fileName"] = file_name
    if manifest_appointment_id:
        input_payload["manifestAppointmentID"] = manifest_appointment_id

    res = session.get(
        url,
        params={"input": json.dumps(input_payload)},
        timeout=REQUEST_TIMEOUT,
    )
    # This used to default isDuplicate to True whenever the call failed or
    # the field was missing (expired session, 5xx, tRPC error body) -- every
    # PDF then counted as a "skipped duplicate", which counts toward the
    # ZIP's move-to-processed check, so the ZIP moved with no claims made.
    # A failed check is a failure (retried next run), never a duplicate.
    if res.status_code != 200:
        raise RuntimeError(f"Duplicate check returned HTTP {res.status_code}: {res.text[:300]}")
    result_data = (res.json().get("result") or {}).get("data") or {}
    if "isDuplicate" not in result_data:
        raise RuntimeError(f"Duplicate check response missing isDuplicate: {res.text[:300]}")
    is_duplicate = result_data["isDuplicate"]

    if is_duplicate:
        raise _CodedError("Duplicate file", DUPLICATE_FILE)

    return {"retry_count": result_data.get("retry_count", 0)}


def _update_retry_count(log, rpa_appointment_id, retry_count, session):
    url = f"{BACKEND_API_URL}/api/trpc/medicalDuplicate.updateRetryCount"
    payload = {"rpa_appointment_id": rpa_appointment_id, "retry_count": (retry_count or 0) + 1}
    log(f"🔄 Updating retry count for appointment {rpa_appointment_id} to {payload['retry_count']}...")
    res = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    return res.json()


def _call_medical_api(log, buffer, file_name, sftp_file_path, session, manifest_entry, zip_metadata, group):
    payload = {
        "fileName": file_name,
        "sftpFilePath": sftp_file_path,
        "fileContent": base64.b64encode(buffer).decode("ascii"),
        "fileType": "application/pdf",
    }
    if manifest_entry:
        payload["manifestEntry"] = _sanitize_nullish(manifest_entry)
    zip_meta_fields = _sanitize_nullish(
        {"entityId": (zip_metadata or {}).get("entity"), "subEntityId": (zip_metadata or {}).get("subEntity")}
    )
    if zip_meta_fields:
        payload.update(zip_meta_fields)
    if group:
        payload["group"] = group

    res = session.post(
        f"{BACKEND_API_URL}/api/trpc/medicalExtraction.uploadMedicalFile",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    # A PDF only counts as processed once the API confirms a claim. Before,
    # a non-200 only logged a warning, and a tRPC error body (no "result")
    # or a response with no claimId still came back as success -- so the
    # PDF counted as processed and the ZIP moved without a claim.
    if res.status_code != 200:
        raise RuntimeError(f"Medical extraction API returned HTTP {res.status_code}: {res.text[:500]}")
    log(f"   ✅ Medical extraction API returned 200 OK for {file_name}")

    data = res.json()
    if not data:
        raise RuntimeError("Empty medical API response")
    if data.get("error"):
        raise RuntimeError(f"Medical extraction API error: {json.dumps(data['error'])[:500]}")

    extracted = (data.get("result") or {}).get("data") or data
    if isinstance(extracted, dict) and "json" in extracted:
        extracted = extracted["json"]
    if extracted.get("claimError"):
        raise RuntimeError(f"Claim creation failed: {extracted['claimError']}")

    # The claim itself is confirmed from claim_header afterwards (see
    # _lookup_claims in _process_zip_blob), not from this response.
    return extracted


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


def _restore_zips_from_processed_folder(container, groups, log):
    log("♻️ Restoring ZIPs from processed folders")
    try:
        for prefix in _get_unique_inbound_prefixes(groups):
            _restore_processed_prefixes(container, prefix, log)
    except Exception as err:
        log(f"❌ Restore operation failed: {err}")
    log("✅ Finished restoring ZIPs from processed folders")


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


def _get_unique_inbound_prefixes(groups):
    seen = []
    for group in groups:
        prefix = _build_inbound_prefix(group["folderStructure"])
        if prefix not in seen:
            seen.append(prefix)
    return seen


def _build_inbound_prefix(folder_structure):
    normalized = _normalize_blob_prefix(folder_structure)
    suffix = _normalize_blob_prefix(INBOUND_PATH_SUFFIX)
    return f"{normalized}/{suffix}" if normalized else suffix


def _sanitize_nullish(value):
    if isinstance(value, list):
        return [_sanitize_nullish(v) for v in value]
    if not isinstance(value, dict):
        return value
    cleaned = {k: _sanitize_nullish(v) for k, v in value.items() if v is not None}
    return cleaned or None


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
            "entity": parsed.get("entity"),
            "subEntity": parsed.get("sub_entity"),
            "ehrName": parsed.get("ehr_name"),
            "practice": parsed.get("practice"),
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
    log(f"Exhausted : {stats['exhausted']}")
    log(f"Failed    : {stats['failed']}")
    rate = (
        ((stats["processed"] + stats["skipped"]) / stats["total"]) * 100 if stats["total"] else 0
    )
    log(f"Success % : {rate:.2f}%")
    log("==============================")
