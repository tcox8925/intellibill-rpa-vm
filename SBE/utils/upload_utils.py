# ======================================================
# utils/upload_utils.py  (FINAL — MATCHES AZURE_BLOB_UTILS)
# ======================================================

import os
import csv
import uuid
import datetime
from datetime import date
from utils.nipr_pull import today_cst_str
from utils import db_utils
from utils.azure_blob_utils import (
    authenticate_blob_storage,
    upload_file_to_blob,
    download_file_from_blob,
)



# ------------------------------------------------------
# Address Parser
# ------------------------------------------------------
def parse_address(addr_text: str):
    try:
        parts = [p.strip() for p in addr_text.split(",")]

        state_zip = parts[-1].split()
        st = state_zip[0]
        zipcode = state_zip[-1]

        city = parts[-2]
        street = ", ".join(parts[:-2])
        return street, city, st, zipcode

    except Exception:
        return None, None, None, None


# ------------------------------------------------------
# Paths for temp CSVs
# ------------------------------------------------------
def _get_sbe_csv_paths(state_code, base_local_dir="/tmp/sbe_scratch"):
    today = date.today().strftime("%Y%m%d")
    filename = f"sbe_certs_{state_code}_{today}.csv"

    os.makedirs(base_local_dir, exist_ok=True)
    local_path = os.path.join(base_local_dir, filename)

    blob_path = f"raw/agent_data_source/sbe_temp/{filename}"

    return local_path, blob_path


# ------------------------------------------------------
# Load dedupe values for scraping (CSV + DB)
# ------------------------------------------------------
def load_sbe_existing_dedupe_values(state_cfg, base_local_dir="/tmp/sbe_scratch"):
    state_code = state_cfg.get("state_code")
    dedupe_field = (state_cfg.get("nipr_pull") or "").strip()

    if not state_code or not dedupe_field:
        print(f"[DEDUPE] Missing state or nipr_pull → no dedupe.")
        return dedupe_field, set()

    allowed = {"license_number", "full_name", "email", "broker_uid"}
    if dedupe_field not in allowed:
        print(f"[DEDUPE] Field '{dedupe_field}' not allowed → skipping")
        return dedupe_field, set()

    existing = set()

    # ----------- CSV dedupe -----------
    local_path, _ = _get_sbe_csv_paths(state_code, base_local_dir)
    if os.path.exists(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if dedupe_field in reader.fieldnames:
                    for row in reader:
                        val = (row.get(dedupe_field) or "").strip()
                        if val:
                            existing.add(val)
            print(f"[DEDUPE] Seeded {len(existing)} from CSV for {state_code}.{dedupe_field}")
        except Exception as e:
            print(f"[DEDUPE] Failed reading CSV for {state_code}: {e}")

    # ----------- DB dedupe -----------
    try:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()

        cur.execute(
            f"""
            SELECT DISTINCT {dedupe_field}
            FROM raw.sbe_certs
            WHERE state_code = %s AND {dedupe_field} IS NOT NULL
            """,
            (state_code,)
        )

        count = 0
        for (v,) in cur.fetchall():
            if v:
                existing.add(str(v).strip())
                count += 1

        conn.close()
        print(f"[DEDUPE] Seeded {count} from DB for {state_code}.{dedupe_field}")

    except Exception as e:
        print(f"[DEDUPE] Failed DB read for {state_code}: {e}")

    print(f"[DEDUPE] Total dedupe = {len(existing)} for {state_code}.{dedupe_field}")
    return dedupe_field, existing


# ------------------------------------------------------
# BUFFER → CSV → BLOB
# ------------------------------------------------------
def buffer_sbe_batch_to_csv(state_cfg, batch_records,
                            base_local_dir="C://Users//poorn//Microsoft//Downloads//acc/",
                            upload_to_blob=True):

    if not batch_records:
        return None, None

    state_code = state_cfg["state_code"]
    local_path, blob_path = _get_sbe_csv_paths(state_code, base_local_dir)

    write_header = not os.path.exists(local_path)
    now = datetime.datetime.utcnow().isoformat()

    columns = [
        "id", "state_code", "full_name", "email", "phone",
        "street", "city", "state", "zipcode",
        "broker_uid", "profile_url",
        "product_expertise", "languages",
        "distance", "license_number",
        "company_id", "crm_field",
        "status", "created_at", "updated_at"
    ]

    with open(local_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)

        if write_header:
            writer.writeheader()

        for rec in batch_records:
            writer.writerow({
                "id": rec.get("id") or str(uuid.uuid4()),
                "state_code": rec.get("state_code"),
                "full_name": rec.get("full_name"),
                "email": rec.get("email"),
                "phone": rec.get("phone"),
                "street": rec.get("street"),
                "city": rec.get("city"),
                "state": rec.get("state"),
                "zipcode": rec.get("zipcode"),
                "broker_uid": rec.get("broker_uid"),
                "profile_url": rec.get("profile_url"),
                "product_expertise": rec.get("product_expertise"),
                "languages": rec.get("languages"),
                "distance": rec.get("distance"),
                "license_number": rec.get("license_number"),
                "company_id": rec.get("company_id"),
                "crm_field": rec.get("crm_field"),
                "status": rec.get("status") or "Pending",
                "created_at": now,
                "updated_at": now,
            })

    print(f"[BUFFER] Wrote {len(batch_records)} rows → {local_path}")

    # Upload CSV to blob
    if upload_to_blob:
        try:
            bsc = authenticate_blob_storage()
            upload_file_to_blob(bsc, local_path, blob_path, overwrite=True)
        except Exception as e:
            print(f"[BUFFER] Blob upload failed for {state_code}: {e}")

    return local_path, blob_path



def load_sbe_csv_into_table(state_cfg):
    """
    Loads the scraped CSV (from Azure Blob) into raw.sbe_certs.

    Return Value Controls Downstream Logic:
    ----------------------------------------------------
    False → No new data (blob missing, empty, failed download,
             OR all rows were duplicates)
    True  → New rows inserted; run NIPR next.

    Blob Handling:
    ----------------------------------------------------
    - Blob is ALWAYS deleted if it existed (success, empty, or duplicates)
    """

    import os
    import csv
    from datetime import datetime
    from utils.azure_blob_utils import (
        download_file_from_blob,
        authenticate_blob_storage,
        delete_blob,
        blob_exists
    )
    from utils import db_utils

    state_code = state_cfg["state_code"]
    nipr_field = (state_cfg.get("nipr_pull") or "").strip()

    today = datetime.now().strftime("%Y%m%d")
    blob_path = f"raw/agent_data_source/sbe_temp/sbe_certs_{state_code}_{today}.csv"
    local_tmp = f"C://Users//poorn//Microsoft//Downloads//acc//sbe_load_{state_code}_{today}.csv"

    print(f"[LOAD] Loading scraped CSV into DB for {state_code}…")
    print(f"[LOAD] Blob source: {blob_path}")

    # --------------------------------------------------------
    # Check if blob exists first
    # --------------------------------------------------------
    bsc = authenticate_blob_storage()
    if not blob_exists(bsc, blob_path):
        print(f"[LOAD] No CSV found in blob for {state_code} (all duplicates or no scrape)")
        return False

    # --------------------------------------------------------
    # Download CSV
    # --------------------------------------------------------
    success = download_file_from_blob(bsc, blob_path, local_tmp)
    if not success or not os.path.exists(local_tmp):
        print(f"[LOAD] Blob exists but failed to download for {state_code}")
        # delete broken blob
        try:
            delete_blob(bsc, blob_path)
        except:
            pass
        return False

    # --------------------------------------------------------
    # Read rows
    # --------------------------------------------------------
    rows = []
    with open(local_tmp, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    print(f"[LOAD] Found {len(rows)} rows for {state_code}")

    if not rows:
        print(f"[LOAD] CSV empty → deleting blob for {state_code}")
        delete_blob(bsc, blob_path)
        return False

    # --------------------------------------------------------
    # Fetch existing dedupe values
    # --------------------------------------------------------
    existing = set()
    if nipr_field:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT DISTINCT {nipr_field}
            FROM raw.sbe_certs
            WHERE state_code = %s AND {nipr_field} IS NOT NULL
            """,
            (state_code,)
        )
        for (v,) in cur.fetchall():
            if v:
                existing.add(str(v).strip())
        conn.close()

    print(f"[LOAD] DB existing {state_code}.{nipr_field} = {len(existing)}")

    # --------------------------------------------------------
    # Insert rows
    # --------------------------------------------------------
    conn = db_utils.get_postgres_connection()
    cur = conn.cursor()
    now = datetime.utcnow()

    insert_sql = """
        INSERT INTO raw.sbe_certs (
            id,
            state_code,
            full_name,
            email,
            phone,
            street,
            city,
            state,
            zipcode,
            broker_uid,
            profile_url,
            product_expertise,
            languages,
            distance,
            license_number,
            company_id,
            crm_field,
            status,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    inserted = 0
    for r in rows:

        dval = (r.get(nipr_field) or "").strip() if nipr_field else None
        if dval and dval in existing:
            # duplicate row → skip
            continue

        params = [
            r.get("id"),
            r.get("state_code"),
            r.get("full_name"),
            r.get("email"),
            r.get("phone"),
            r.get("street"),
            r.get("city"),
            r.get("state"),
            r.get("zipcode"),
            r.get("broker_uid"),
            r.get("profile_url"),
            r.get("product_expertise"),
            r.get("languages"),
            r.get("distance"),
            r.get("license_number"),
            r.get("company_id"),
            r.get("crm_field"),
            "Pending",
            now,
            now,
        ]

        try:
            cur.execute(insert_sql, params)
            inserted += 1
        except Exception as e:
            print(f"[LOAD] ERROR inserting row for {state_code}: {e}")

    conn.commit()
    conn.close()

    print(f"[LOAD] Inserted {inserted} rows for {state_code}")


    try:
        delete_blob(bsc, blob_path)
        print(f"[LOAD] Deleted blob for {state_code}: {blob_path}")
    except Exception as e:
        print(f"[LOAD] Blob delete failed for {state_code}: {e}")

    # Delete local tmp file
    try:
        orig_local, _ = _get_sbe_csv_paths(state_code, base_local_dir="C://Users//poorn//Microsoft//Downloads//acc/")
        if os.path.exists(orig_local):
            os.remove(orig_local)
            print(f"[LOAD] Deleted local scrape CSV for {state_code}: {orig_local}")
    except Exception as e:
        print(f"[LOAD] Failed to delete local scrape CSV for {state_code}: {e}")
    return inserted > 0

# ===========================================================
# BUFFER BULK NIPR RESULTS TO CSV
# ===========================================================

def buffer_nipr_results_to_csv(state_cfg, results, base_local_dir="C:\\Users\\poorn\\Microsoft\\Downloads\\acc"):
    """
    Writes *all* NIPR results (NIPR + License Not Found)
    to nipr_temp/nipr_bulk_{STATE}_{DATE}.csv
    """

    if not results:
        return False

    state = state_cfg["state_code"]
    today = today_cst_str()

    filename = f"nipr_bulk_{state}_{today}.csv"
    local_path = os.path.join(base_local_dir, filename)

    # BLOB PATH MUST MATCH LOADER
    blob_path = f"raw/agent_data_source/nipr_temp/{filename}"

    columns = [
        "id", "state_code", "full_name", "license_number",
        "nipr_name", "nipr_dob", "nipr_npn",
        "nipr_demographics_updated", "nipr_resident_states",
        "nipr_producer_licensing_updated",
        "nipr_appointments_updated", "nipr_report_price",
        "status"
    ]

    write_header = not os.path.exists(local_path)

    with open(local_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)

        if write_header:
            writer.writeheader()

        for rec in results:
            writer.writerow({col: rec.get(col) for col in columns})

    print(f"[NIPR_BUFFER] Wrote {len(results)} rows → {local_path}")

    # Upload to blob
    try:
        bsc = authenticate_blob_storage()
        upload_file_to_blob(bsc, local_path, blob_path,overwrite=True)
        print(f"[NIPR_BUFFER] Uploaded → {blob_path}")
    except Exception as e:
        print(f"[NIPR_BUFFER_ERR] Blob upload failed: {e}")

    return True


def load_nipr_csv_into_sbe_certs(state_cfg):
    """
    Load the bulk NIPR CSV for a state and update raw.sbe_certs in bulk.
    This uses: download_file_from_blob, delete_blob, db_utils.get_synapse_connection.

    CSV format expected (from buffer_nipr_results_to_csv):
        id, license_number, full_name, state_code,
        nipr_name, nipr_dob, nipr_npn,
        nipr_demographics_updated, nipr_producer_licensing_updated,
        nipr_appointments_updated, nipr_resident_states, nipr_report_price,
        status
    """
    import csv
    import os
    from datetime import datetime
    from utils.azure_blob_utils import (
        authenticate_blob_storage,
        download_file_from_blob,
        delete_blob,
        blob_exists
    )
    from utils import db_utils

    state = state_cfg["state_code"]
    today = today_cst_str()

    # Blob + local temp paths
    blob_path = f"raw/agent_data_source/nipr_temp/nipr_bulk_{state}_{today}.csv"
    local_tmp = f"C://Users//poorn//Microsoft//Downloads//acc//nipr_bulk_load_{state}_{today}.csv"

    print(f"[NIPR_LOAD] Loading NIPR CSV for {state}")
    print(f"[NIPR_LOAD] Blob path: {blob_path}")

    bsc = authenticate_blob_storage()

    # ------------------------------------------------------
    # Check if blob exists
    # ------------------------------------------------------
    if not blob_exists(bsc, blob_path):
        print(f"[NIPR_LOAD] No NIPR CSV found for {state}")
        return False

    # ------------------------------------------------------
    # Download CSV
    # ------------------------------------------------------
    ok = download_file_from_blob(bsc, blob_path, local_tmp)
    if not ok or not os.path.exists(local_tmp):
        print(f"[NIPR_LOAD] Failed to download blob for {state}")
        try: delete_blob(bsc, blob_path)
        except: pass
        return False

    # ------------------------------------------------------
    # Read CSV rows
    # ------------------------------------------------------
    rows = []
    with open(local_tmp, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    print(f"[NIPR_LOAD] {len(rows)} rows found for {state}")

    if not rows:
        print(f"[NIPR_LOAD] CSV empty — deleting blob.")
        delete_blob(bsc, blob_path)
        return False

    conn = db_utils.get_postgres_connection()
    cur = conn.cursor()

    # ------------------------------------------------------
    # BULK UPDATE STATEMENT
    # ------------------------------------------------------
    update_sql = """
        UPDATE raw.sbe_certs
        SET
            nipr_name                       = %s,
            nipr_dob                        = %s,
            nipr_npn                        = %s,
            nipr_resident_states            = %s,
            nipr_demographics_updated       = %s,
            nipr_producer_licensing_updated = %s,
            nipr_appointments_updated       = %s,
            nipr_report_price               = %s,
            status                          = %s,
            nipr_enriched                   = 1,
            updated_at                      = now() at time zone 'utc'
        WHERE id = %s
    """

    updated = 0

    for r in rows:
        try:
            cur.execute(update_sql, (
                r.get("nipr_name"),
                r.get("nipr_dob"),
                r.get("nipr_npn"),
                r.get("nipr_resident_states"),
                r.get("nipr_demographics_updated"),
                r.get("nipr_producer_licensing_updated"),
                r.get("nipr_appointments_updated"),
                r.get("nipr_report_price"),
                r.get("status") or "NIPR",
                r.get("id"),
            ))
            updated += 1
        except Exception as e:
            print(f"[NIPR_LOAD_ERR] state={state}, id={r.get('id')} : {e}")

    conn.commit()
    conn.close()

    print(f"[NIPR_LOAD] Updated {updated} rows for {state}")

    # Cleanup blob + local temp
    try:
        delete_blob(bsc, blob_path)
        print(f"[NIPR_LOAD] Deleted blob for {state}")
    except:
        pass

    try:
        orig_local = f"C://Users//poorn//Microsoft//Downloads//acc//nipr_bulk_{state}_{today}.csv"
        if os.path.exists(orig_local):
            os.remove(orig_local)
            print(f"[NIPR_LOAD] Deleted local NIPR CSV for {state}")
    except Exception as e:
        print(f"[NIPR_LOAD] Failed to delete original NIPR CSV for {state}: {e}")

    return updated > 0
