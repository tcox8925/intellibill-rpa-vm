"""
sbe_matrix_reader.py
--------------------
Refreshes [raw].[ops_acc_process_matrix] (or _queue) from Azure Blob Storage.

Steps:
1️⃣ Auth via utils.azure_blob_utils
2️⃣ Download static CSV from blob
3️⃣ Truncate target table (TRUNCATE + DELETE fallback)
4️⃣ Bulk insert new data
"""

import os
import io
import pandas as pd
from utils import azure_blob_utils, db_utils

# ==========================================================
# CONFIGURATION
# ==========================================================
TARGET_TABLE = "wpo.ops_sbe_process_matrix"   # ✅ or process_matrix
BLOB_PATH = "flat_files/ops_sbe_process_matrix.csv"
CONTAINER = "834analytics-dev"
#LOCAL_BACKUP = os.path.join(os.getcwd(), "ops_acc_rpa_matrix.csv")

# ==========================================================
# MAIN
# ==========================================================
def main():
    print("\n🏁 Starting SBE Matrix Refresh\n")

    try:
        # 1️⃣ Authenticate via helper (this already logs Key Vault + success)
        blob_service = azure_blob_utils.authenticate_blob_storage()

        # 2️⃣ Download matrix CSV
        blob_client = blob_service.get_blob_client(container=CONTAINER, blob=BLOB_PATH)
        print(f"📥 Downloading {BLOB_PATH} from container '{CONTAINER}'...")
        csv_data = blob_client.download_blob().readall().decode("utf-8")

        # Local backup
        #with open(LOCAL_BACKUP, "w", encoding="utf-8") as f:
        #    f.write(csv_data)
        #print(f"💾 Saved matrix locally → {LOCAL_BACKUP}")

        # 3️⃣ Read into DataFrame
        df = pd.read_csv(io.StringIO(csv_data))
        print(f"✅ Loaded {len(df)} rows with {len(df.columns)} columns.")

        # 4️⃣ Connect to Synapse
        conn = db_utils.get_postgres_connection()
        print("🔗 Connected to Postgres")

        # 5️⃣ Truncate safely (Synapse limitation-aware)
        try:
            print(f"🧹 Truncating table → {TARGET_TABLE}")
            conn_autocommit = db_utils.get_postgres_connection()
            conn_autocommit.autocommit = True
            cur = conn_autocommit.cursor()
            cur.execute(f"TRUNCATE TABLE {TARGET_TABLE}")
            cur.close()
            conn_autocommit.close()
            print(f"✅ Table truncated: {TARGET_TABLE}")
        except Exception as e:
            print(f"⚠️ TRUNCATE failed ({e}) — falling back to DELETE...")
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {TARGET_TABLE}")
            conn.commit()
            print(f"✅ Deleted all existing rows from {TARGET_TABLE}")

        # 6️⃣ Bulk insert DataFrame
        cursor = conn.cursor()
        cols = ", ".join(f"{c}" for c in df.columns)
        placeholders = ", ".join("%s" for _ in df.columns)

        for _, row in df.iterrows():
            values = tuple(None if pd.isna(v) else v for v in row.values)
            cursor.execute(f"INSERT INTO {TARGET_TABLE} ({cols}) VALUES ({placeholders})", values)

        conn.commit()
        cursor.close()
        conn.close()
        print(f"📤 Uploaded {len(df)} row(s) to {TARGET_TABLE}")
        print("\n✅ Matrix refresh completed successfully.\n")

    except Exception as e:
        print(f"\n❌ Matrix refresh failed: {e}\n")


# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    main()
