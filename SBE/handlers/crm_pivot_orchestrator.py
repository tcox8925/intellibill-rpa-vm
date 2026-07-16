# ==========================================================
# handlers/crm_pivot_orchestrator.py
# Creates CRM-pivoted agent rows and uploads CSVs to Blob.
# Follows SBE modular structure & logging conventions.
# ==========================================================

import csv
import os
from math import ceil
from datetime import datetime

from utils import db_utils
from utils.azure_blob_utils import authenticate_blob_storage, upload_file_to_blob


# ===================================================================
# CONFIG
# ===================================================================
LOCAL_BASE = "C://Users//poorn//Microsoft//Downloads//acc//"
BLOB_FOLDER = "raw/agent_data_source/crm_upload/"
BATCH_SIZE = 25000


# ===================================================================
# MAIN ORCHESTRATOR ENTRY POINT
# ===================================================================
def run_crm_pivot_export():
    print("\n[CRM] Starting CRM pivot export job...")

    conn = db_utils.get_postgres_connection()
    cur = conn.cursor()

    # --------------------------------------------------------------
    # 1) Load CRM fields (column names)
    # --------------------------------------------------------------
    cur.execute("""
        SELECT DISTINCT crm_field
        FROM raw.sbe_certs
        WHERE crm_field IS NOT NULL
        ORDER BY crm_field;
    """)
    crm_fields = [row[0] for row in cur.fetchall()]

    if not crm_fields:
        print("[CRM] No CRM fields found in sbe_certs.")
        return False

    print(f"[CRM] Found {len(crm_fields)} CRM fields")

    # --------------------------------------------------------------
    # 2) Load valid NPNs (in both sbe_certs & lup_agents)
    # --------------------------------------------------------------
    cur.execute("""
        SELECT DISTINCT s.nipr_npn, a.id
        FROM raw.sbe_certs s
        JOIN raw.lup_agents a ON a.npn = s.nipr_npn
        WHERE s.status = 'NIPR'
        ORDER BY s.nipr_npn;
    """)
    rows = cur.fetchall()

    if not rows:
        print("[CRM] No valid NPNs found that exist in lup_agents.")
        return False

    # Build mapping: npn → zoho_id
    npn_to_zoho_id = {row[0]: row[1] for row in rows}
    valid_npns = list(npn_to_zoho_id.keys())

    print(f"[CRM] Valid NPNs: {len(valid_npns)}")

    # --------------------------------------------------------------
    # 3) Load all SBE certs (once)
    # --------------------------------------------------------------
    cur.execute("""
        SELECT nipr_npn, crm_field
        FROM raw.sbe_certs
        WHERE status = 'NIPR'
        AND crm_field IS NOT NULL;
    """)
    all_rows = cur.fetchall()

    conn.close()

    # --------------------------------------------------------------
    # Group CRM fields by NPN
    # --------------------------------------------------------------
    npn_to_fields = {npn: set() for npn in valid_npns}

    for npn, crm_field in all_rows:
        if npn in npn_to_fields:
            npn_to_fields[npn].add(crm_field)

    # --------------------------------------------------------------
    # 4) Create pivoted rows  (FIXED INDENTATION)
    # --------------------------------------------------------------
    pivot_rows = []
    for npn in valid_npns:
        row = {
            "id": npn_to_zoho_id[npn],  # Zoho Contact ID
            "npn": npn                  # NPN for reference
        }
        for field in crm_fields:
            row[field] = "Yes" if field in npn_to_fields[npn] else "No"
        pivot_rows.append(row)

    total = len(pivot_rows)
    print(f"[CRM] Pivot complete. Total pivot rows = {total}")

    # --------------------------------------------------------------
    # 5) Write CSV batches & upload
    # --------------------------------------------------------------
    num_batches = ceil(total / BATCH_SIZE)
    print(f"[CRM] Exporting in {num_batches} batch(es)")

    bsc = authenticate_blob_storage()

    for batch_idx in range(num_batches):
        start = batch_idx * BATCH_SIZE
        end = min(start + BATCH_SIZE, total)
        batch = pivot_rows[start:end]

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"crm_export_batch_{batch_idx + 1}_of_{num_batches}_{ts}.csv"
        local_path = os.path.join(LOCAL_BASE, filename)
        blob_path = BLOB_FOLDER + filename

        # write CSV
        with open(local_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "npn"] + crm_fields)
            writer.writeheader()
            writer.writerows(batch)

        print(f"[CRM] Wrote batch {batch_idx+1}/{num_batches} → {local_path}")

        # upload
        upload_file_to_blob(bsc, local_path, blob_path, overwrite=True)
        print(f"[CRM] Uploaded → {blob_path}")

        # delete local file
        try:
            os.remove(local_path)
            print(f"[CRM] Deleted local file {local_path}")
        except Exception as e:
            print(f"[CRM] Failed removing local file: {e}")

    print("\n[CRM] CRM pivot export job completed.\n")
    return True


# ===================================================================
# STANDALONE EXECUTION
# ===================================================================
#if __name__ == "__main__":
#    run_crm_pivot_export()
