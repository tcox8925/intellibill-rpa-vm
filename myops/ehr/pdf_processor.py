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

        stats = {"total": 0, "processed": 0, "skipped": 0, "exhausted": 0, "failed": 0}

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

    if zip_stats["processed"] + zip_stats["exhausted"] + zip_stats["skipped"] == len(pdf_names):
        log(f"✅ All PDFs in {blob_name} processed or exhausted - moving ZIP to processed folder")
        try:
            _move_zip_to_processed(container, blob_name, buffer, log)
        except Exception as err:
            log(f"❌ Failed to move ZIP to processed folder for {blob_name}: {err}")


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

        log(f"✅ File processed and claim created: {file_name} → {api_data.get('claimId')}")
        return {"success": True, "claimId": api_data.get("claimId")}
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
        return {"success": False, "errorCode": code, "error": str(err)}


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
    result_data = (res.json().get("result") or {}).get("data") or {}
    is_duplicate = result_data.get("isDuplicate", True)

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

    if res.status_code == 200:
        log(f"   ✅ Medical extraction API returned 200 OK for {file_name}")
    else:
        log(f"   ⚠️ Medical extraction API returned status {res.status_code}: {res.text}")

    data = res.json()
    if not data:
        raise RuntimeError("Empty medical API response")

    extracted = (data.get("result") or {}).get("data") or data
    if extracted.get("claimError"):
        raise RuntimeError(f"Claim creation failed: {extracted['claimError']}")

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
