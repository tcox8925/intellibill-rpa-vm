"""
Unified ZIP builder + SFTP delivery.

Collapses create_daily_zip_with_json + create_backfill_zip_with_json into one
pass_zip driven by the selector's scope. PDFs deduped by tebra_facesheet_id;
one JSON record per signed-note appointment. The JSON now also carries
charge_data (the VIEW CHARGE scrape) when present. Writes file_path back on
every appointment included in the uploaded ZIP.
"""

import os
import json
import random
import re
import string
import zipfile

from azure.storage.blob import BlobServiceClient

from .config import (
    TABLE_NAME, DOWNLOAD_DIR, STORAGE_ACCOUNT_NAME, AZURE_STORAGE_CONNECTION_STRING,
    RCM_ATTACHMENTS_CONTAINER,
)
from .db import get_ehr_connection
from .query import scope_clause
from .session import now_cst


def get_practice_abbr(practice_name):
    # Treat +, -, /, & as word separators (in addition to spaces) so spacing
    # around them doesn't change the abbreviation: "PrePost+ Tennessee" and
    # "PrePost+Tennessee" both -> ["PrePost","Tennessee"] -> "PT".
    words = re.split(r"[\s+\-/&]+", practice_name)
    return "".join(w[0].upper() for w in words if w and w[0].isalpha())


def generate_random_suffix(length=4):
    return "".join(random.choices(string.digits, k=length))


def _safe_segment(value):
    """Sanitize blob path segments to avoid accidental nested paths."""
    text = str(value or "").strip()
    text = text.replace("/", "-").replace("\\", "-")
    return text


def _resolve_inbound_folder_from_structure(folder_structure):
    folder_root = _safe_segment(folder_structure)
    if not folder_root:
        raise RuntimeError("folder_structure is required for upload path resolution")
    return f"{folder_root}/Exchange/Medical Extraction/INBOUND"


def upload_zip_to_rcm_sftp(local_zip_path, zip_name, folder_structure):
    sftp_container = RCM_ATTACHMENTS_CONTAINER
    print(
        f"[SFTP] Upload start account={STORAGE_ACCOUNT_NAME} "
        f"container={sftp_container}",
        flush=True,
    )

    service = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container = service.get_container_client(sftp_container)
    try:
        container.create_container()
        print(f"[SFTP] Created container {sftp_container}", flush=True)
    except Exception:
        pass

    inbound_folder = _resolve_inbound_folder_from_structure(folder_structure)

    blob_path = f"{inbound_folder}/{zip_name}"
    with open(local_zip_path, "rb") as f:
        container.upload_blob(blob_path, f, overwrite=True)
    print(
        f"[SFTP] Upload success zip={zip_name} "
        f"path={blob_path}",
        flush=True,
    )
    return blob_path


def pass_zip(sel, practice_name, no_upload=False):
    """
    Build ONE ZIP for the selection's processed signed-note appointments and
    deliver it to the mapped client folder inside the configured container. ZIP name uses the
    window end date (backfill) / today (daily). JSON carries charge_data when
    captured.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        where, params = scope_clause(sel, alias="")
        # zip-specific gate: delivered-ready rows
        where += [
            "appt_note IS NOT NULL",
            "process_status = 'Processed'",
            "tebra_facesheet_id IS NOT NULL",
        ]
        # daily: only rows NOT already delivered (file_path IS NULL), so the
        # unbounded nightly sweep never re-ships the entire historical backlog
        # (which caused the "Missing local PDF" flood — old delivered rows
        # whose local PDFs were long since cleaned up). backfill/target are
        # explicit, scoped requests — re-zip and re-upload regardless of
        # file_path, same as the facesheet re-pull.
        if sel.mode == "daily":
            where.append("file_path IS NULL")
        cur.execute(
            f"""
            SELECT id, appt_id, tebra_facesheet_id, appt_date, appt_time,
                   patient_name, dob, provider_name, service_location,
                   appt_reason, appt_status, patient_id, appt_note, file_path,
                   charge_status, charge_data
            FROM {TABLE_NAME}
            WHERE {' AND '.join(where)}
            ORDER BY appt_date, appt_time
            """,
            tuple(params),
        )
        appt_rows = cur.fetchall()
        if not appt_rows:
            print("[ZIP] No processed signed-note appointments. Skipping zip.", flush=True)
            return {
                "attempted_count": 0,
                "uploaded_count": 0,
                "failed_count": 0,
                "container": RCM_ATTACHMENTS_CONTAINER,
            }

        # folder date: end of window (backfill) else today
        folder_dt = sel.end_date or now_cst().date()
        folder_date = folder_dt.strftime("%Y-%m-%d")

        temp_dir = os.path.join(DOWNLOAD_DIR, "zip_tmp")
        os.makedirs(temp_dir, exist_ok=True)

        records, included_db_ids, needed_pdfs = [], [], {}
        for row in appt_rows:
            (db_id, appt_id, facesheet_id, appt_date, appt_time, patient_name, dob,
             provider_name, service_location, appt_reason, appt_status,
             patient_id, appt_note, existing_file_path, charge_status, charge_data) = row

            last_name = patient_name.split(",")[0].strip().replace(" ", "_")
            pdf_filename = f"{facesheet_id}_{last_name}.pdf"
            needed_pdfs[facesheet_id] = pdf_filename

            records.append({
                "pdf_file": pdf_filename,
                "appt_id": appt_id,
                "facesheet_id": facesheet_id,
                "appt_date": str(appt_date),
                "appt_time": str(appt_time),
                "patient_name": patient_name,
                "dob": str(dob),
                "provider_name": provider_name,
                "service_location": service_location,
                "appt_reason": appt_reason,
                "appt_status": appt_status,
                "patient_id": patient_id,
                "appt_note": appt_note,
                "charge_status": charge_status,
                "charge_data": charge_data,          # None or captured charge JSON
                "charge_in_facesheet": charge_data is None,  # False => scraped separately
                "previously_delivered": existing_file_path is not None,
            })
            included_db_ids.append(db_id)

        print(f"[ZIP] {len(records)} appointments, {len(needed_pdfs)} unique PDFs")

        # Drop records whose local PDF is missing this run.
        missing_pdf_files = []
        missing_pdf_db_ids = []
        for facesheet_id, pdf_filename in list(needed_pdfs.items()):
            if not os.path.exists(os.path.join(DOWNLOAD_DIR, pdf_filename)):
                print(f"[ZIP] Missing local PDF {pdf_filename}", flush=True)
                missing_pdf_files.append(pdf_filename)
                dropped_ids = [
                    included_db_ids[i]
                    for i, r in enumerate(records)
                    if r["facesheet_id"] == facesheet_id
                ]
                missing_pdf_db_ids.extend(dropped_ids)
                keep = [i for i, r in enumerate(records) if r["facesheet_id"] != facesheet_id]
                before = len(records)
                records = [records[i] for i in keep]
                included_db_ids = [included_db_ids[i] for i in keep]
                print(f"[ZIP] Dropped {before - len(records)} records", flush=True)

        if missing_pdf_db_ids:
            cur.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET process_status='Error',
                    process_error_stage='ZIP',
                    process_error_message=%s,
                    updated_date=now()
                WHERE id = ANY(%s)
                """,
                (
                    "Missing local PDF during ZIP build; facesheet will be re-downloaded on next run",
                    missing_pdf_db_ids,
                ),
            )
            conn.commit()

        if not records:
            print("[ZIP] No records left after local-PDF checks. Skipping zip.", flush=True)
            return {
                "attempted_count": 0,
                "uploaded_count": 0,
                "failed_count": 1,
                "container": RCM_ATTACHMENTS_CONTAINER,
                "upload_error": (
                    "All ZIP candidates were missing local PDFs. "
                    f"missing={len(missing_pdf_files)}"
                ),
            }

        practice_abbr = get_practice_abbr(practice_name)
        suffix = generate_random_suffix()
        json_name = f"tebra_facesheets_{practice_abbr}_{folder_date}_{suffix}.json"
        zip_name = f"tebra_facesheets_{practice_abbr}_{folder_date}_{suffix}.zip"
        json_path = os.path.join(temp_dir, json_name)
        zip_path = os.path.join(temp_dir, zip_name)

        metadata = {
            "generated_on": now_cst().isoformat(),
            "entity": sel.entity,
            "sub_entity": sel.sub_entity,
            "ehr_name": sel.ehr_name,
            "practice": practice_name,
            "appointments": records,
        }
        with open(json_path, "w") as f:
            json.dump(metadata, f, indent=4, default=str)

        unique_pdfs = {r["pdf_file"] for r in records}
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for pdf_filename in unique_pdfs:
                local_pdf = os.path.join(DOWNLOAD_DIR, pdf_filename)
                if os.path.exists(local_pdf):
                    z.write(local_pdf, pdf_filename)
            z.write(json_path, json_name)

        if no_upload:
            print(f"[ZIP][DRY-RUN] Built {zip_name} ({len(unique_pdfs)} PDFs) at "
                  f"{zip_path} — skipping SFTP upload and file_path write-back. "
                  f"Local ZIP kept for inspection.", flush=True)
            return {
                "attempted_count": 0,
                "uploaded_count": 0,
                "failed_count": 0,
                "container": RCM_ATTACHMENTS_CONTAINER,
            }

        zip_blob_path = None
        attempted_count = 1
        uploaded_count = 0
        failed_count = 0
        upload_error = None
        try:
            zip_blob_path = upload_zip_to_rcm_sftp(
                zip_path,
                zip_name,
                sel.folder_structure,
            )
            uploaded_count = 1
        except Exception as e:
            failed_count = 1
            upload_error = str(e)
            print(
                f"[ZIP SFTP ERROR] practice={practice_name} zip={zip_name} error={e}",
                flush=True,
            )

        print(
            f"[ZIP] Upload summary practice={practice_name} "
            f"container={RCM_ATTACHMENTS_CONTAINER} attempted={attempted_count} "
            f"uploaded={uploaded_count} failed={failed_count}",
            flush=True,
        )

        if included_db_ids and zip_blob_path:
            cur.execute(
                f"UPDATE {TABLE_NAME} SET file_path=%s, updated_date=now() WHERE id = ANY(%s)",
                (zip_blob_path, included_db_ids),
            )
            conn.commit()

        try:
            os.remove(zip_path)
        except Exception:
            pass
        for pdf_filename in unique_pdfs:
            try:
                os.remove(os.path.join(DOWNLOAD_DIR, pdf_filename))
            except OSError:
                pass
        return {
            "attempted_count": attempted_count,
            "uploaded_count": uploaded_count,
            "failed_count": failed_count,
            "container": RCM_ATTACHMENTS_CONTAINER,
            "blob_path": zip_blob_path,
            "zip_name": zip_name,
            "upload_error": upload_error,
        }
    finally:
        cur.close()
        conn.close()
