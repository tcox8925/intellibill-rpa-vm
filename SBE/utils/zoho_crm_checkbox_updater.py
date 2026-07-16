# ==========================================================
# handlers/zoho_crm_checkbox_updater.py
# Bulk update Zoho CRM Contacts based on CRM pivot file (NPN-based)
# ==========================================================

import os
import csv
from datetime import datetime
import pandas as pd

from utils.azure_blob_utils import (
    authenticate_blob_storage,
    download_file_from_blob, upload_file_to_blob, delete_blob, read_filenames_from_blob,
)
from utils.zoho_utils import bulk_update_crm

# ==========================================================
# CONFIGURATION
# ==========================================================
LOCAL_DIR = "C://Users//actua//Microsoft//Downloads//acc//"
BLOB_BASE_FOLDER = "raw/agent_data_source/"
BLOB_FOLDER = "crm_upload/"
BLOB_ARCHIVE = "archive/"
MODULE_NAME = "Contacts"
FIND_BY = "NPN"            # <--- SWITCHED TO NPN
BATCH_LIMIT = 25000


# ==========================================================
# HELPER: Yes/No → bool
# ==========================================================
def yesno_to_bool(value):
    if not value:
        return ''
    ret = str(value).strip().lower() in ("yes", "true", "1")
    if not ret:
        ret = ''
    return ret


# ==========================================================
# MAIN
# ==========================================================
def run_zoho_crm_checkbox_update(blob_filename_in = None):

    print("\n[ZOHO_CRM] Starting CRM checkbox update...")
    blob_folder_path = f"{BLOB_BASE_FOLDER}{BLOB_FOLDER}"
    bsc = authenticate_blob_storage()

    print(read_filenames_from_blob(bsc, blob_folder_path))

    blob_filepaths = []
    # If a filename is passed, handle only that file
    # Else, gather each file in the folder, and process all of them
    if blob_filename_in is None:
        # retrieve list of filenames in crm upload folder
        blob_filepaths = read_filenames_from_blob(bsc, blob_folder_path)
        pass
    else:
        blob_filepaths = [blob_filename_in]

    for blob_filename in blob_filepaths:
        blob_filename = os.path.basename(blob_filename)
        # ------------------------------------------------------
        # 1) Download CSV
        # ------------------------------------------------------
        blob_path = f"{BLOB_BASE_FOLDER}{BLOB_FOLDER}{blob_filename}"
        local_csv_path = os.path.join(LOCAL_DIR, blob_filename)

        print(f"[ZOHO_CRM] Downloading {blob_path} → {local_csv_path}")
        ok = download_file_from_blob(bsc, blob_path, local_csv_path)
        if not ok:
            print(f"[ZOHO_CRM] Failed to download blob: {blob_path}")
            return False

        # ------------------------------------------------------
        # 2) Read CSV
        # ------------------------------------------------------
        print(f"[ZOHO_CRM] Reading CSV...")
        with open(local_csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            records = list(reader)

        if not records:
            print("[ZOHO_CRM] CSV empty.")
            return False

        print(f"[ZOHO_CRM] Loaded {len(records)} rows.")

        # ------------------------------------------------------
        # 3) Convert rows into Zoho payload
        # ------------------------------------------------------
        prepared = []
        for rec in records:

            npn = rec.get("npn")
            if not npn:
                print(f"[ZOHO_CRM] Missing NPN for row: {rec}")
                continue

            out = {"NPN": npn}      # <---- NPN used as lookup key

            for field, value in rec.items():

                # Metadata columns excluded
                if field in ("id", "npn"):
                    continue

                # Convert Yes/No → True/False
                out[field] = yesno_to_bool(value)

            prepared.append(out)

        print(f"[ZOHO_CRM] Prepared {len(prepared)} Zoho update rows.")

        # ------------------------------------------------------
        # 4) Bulk Write
        # ------------------------------------------------------
        download_path = LOCAL_DIR + "zoho_bulk_out_" + datetime.now().strftime("%Y%m%d")
        print("+++++++++++++++++++")
        print(prepared)
        print(f"[ZOHO_CRM] Sending updates to Zoho (module={MODULE_NAME}, find_by={FIND_BY})...")
        summary = bulk_update_crm(
            module=MODULE_NAME,
            records=prepared,
            carrier_id=None,
            download_path=download_path,
            find_by=FIND_BY
        )
    
        print(f"[ZOHO_CRM] Summary: {summary}")


        # ------------------------------------------------------
        # 5) Cleanup
        # ------------------------------------------------------
        try:
            upload_file_to_blob(bsc, local_csv_path, f"{BLOB_BASE_FOLDER}{BLOB_ARCHIVE}{blob_filename}")
            delete_blob(bsc, f"{BLOB_BASE_FOLDER}{BLOB_FOLDER}{blob_filename}")
        except:
            pass

        try:
            os.remove(local_csv_path)
            print(f"[ZOHO_CRM] Deleted temp file: {local_csv_path}")
        except:
            pass


    #return summary


if __name__ == "__main__":
    #run_zoho_crm_checkbox_update("crm_export_batch_1_of_1_20251202_201712.csv")
    run_zoho_crm_checkbox_update()
