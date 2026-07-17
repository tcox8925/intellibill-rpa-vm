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
import string
import zipfile

from azure.storage.blob import BlobServiceClient

from .config import (
    TABLE_NAME, DOWNLOAD_DIR, STORAGE_ACCOUNT_NAME, AZURE_STORAGE_CONNECTION_STRING,
)
from .db import get_ehr_connection
from .query import scope_clause
from .matching import normalize_text
from .session import now_cst


def get_practice_abbr(practice_name):
    # Treat +, -, /, & as word separators (in addition to spaces) so spacing
    # around them doesn't change the abbreviation: "PrePost+ Tennessee" and
    # "PrePost+Tennessee" both -> ["PrePost","Tennessee"] -> "PT".
    words = re.split(r"[\s+\-/&]+", practice_name)
    return "".join(w[0].upper() for w in words if w and w[0].isalpha())


def generate_random_suffix(length=4):
    return "".join(random.choices(string.digits, k=length))


def upload_zip_to_rcm_sftp(local_zip_path, zip_name, practice_name):
    SFTP_CONTAINER = "834labs-sftp"
    print(f"[SFTP] Uploading zip to {STORAGE_ACCOUNT_NAME}/{SFTP_CONTAINER}")

    service = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container = service.get_container_client(SFTP_CONTAINER)

    normalized_practice = normalize_text(practice_name)
    print(f"[SFTP] Looking for folder matching: {practice_name} ({normalized_practice})")

    folders = set()
    for blob in container.list_blobs():
        parts = blob.name.split("/")
        if len(parts) > 1:
            folders.add(parts[0])
    print(f"[SFTP] Found folders: {folders}")

    matched_folder = None
    for folder in folders:
        if normalized_practice in normalize_text(folder):
            matched_folder = folder
            print(f"[SFTP] Matched via normalized match: {matched_folder}")
            break
    if not matched_folder and practice_name in folders:
        matched_folder = practice_name
        print(f"[SFTP] Matched via exact name: {matched_folder}")
    if not matched_folder:
        matched_folder = practice_name.strip()
        print(f"[SFTP] No match found. Creating new folder: {matched_folder}")
        container.upload_blob(f"{matched_folder}/.init", b"", overwrite=True)

    blob_path = f"{matched_folder}/{zip_name}"
    with open(local_zip_path, "rb") as f:
        container.upload_blob(blob_path, f, overwrite=True)
    print(f"[SFTP] Uploaded {zip_name} to {matched_folder}/")
    return blob_path


def pass_zip(sel, practice_name, no_upload=False):
    """
    Build ONE ZIP for the selection's processed signed-note appointments and
    deliver it to the practice folder in 834labs-sftp. ZIP name uses the
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
        # daily/backfill: only rows NOT already delivered (file_path IS NULL),
        # so the ZIP contains exactly what this run produced and never re-ships
        # history (which caused the "Missing local PDF" flood — old delivered
        # rows whose local PDFs were long since cleaned up). target mode is an
        # on-demand re-delivery, so it zips the requested rows regardless.
        if sel.mode != "target":
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
            print("[ZIP] No processed signed-note appointments. Skipping zip.")
            return

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
        for facesheet_id, pdf_filename in list(needed_pdfs.items()):
            if not os.path.exists(os.path.join(DOWNLOAD_DIR, pdf_filename)):
                print(f"[ZIP] Missing local PDF {pdf_filename}")
                keep = [i for i, r in enumerate(records) if r["facesheet_id"] != facesheet_id]
                before = len(records)
                records = [records[i] for i in keep]
                included_db_ids = [included_db_ids[i] for i in keep]
                print(f"[ZIP] Dropped {before - len(records)} records")

        if not records:
            print("[ZIP] No records left after local-PDF checks. Skipping zip.")
            return

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
                  f"Local ZIP kept for inspection.")
            return

        zip_blob_path = None
        try:
            zip_blob_path = upload_zip_to_rcm_sftp(zip_path, zip_name, practice_name)
        except Exception as e:
            print(f"[ZIP SFTP ERROR] {e}")

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
    finally:
        cur.close()
        conn.close()
