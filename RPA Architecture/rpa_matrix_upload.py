import os
import pandas as pd
import pyodbc
from db_connection import connect_to_db
from azure_blob_utils import authenticate_blob_storage, download_blob

# Define Azure Blob details
BLOB_CONTAINER_NAME = "834analytics-dev"
BLOB_PATH = "flat_files/ops_rpa_matrix.csv"
LOCAL_CSV_PATH = "C:\\Users\\myopsadmin\\Downloads\\ops_rpa_matrix.csv"

def truncate_rpa_matrix():
    """Truncates the table outside of a transaction to prevent errors."""
    try:
        conn = connect_to_db()
        if not conn:
            print("Failed to establish database connection for TRUNCATE.")
            return

        conn.autocommit = True  # Ensure TRUNCATE runs outside a transaction
        cursor = conn.cursor()

        cursor.execute("TRUNCATE TABLE wpo.ops_rpa_matrix;")
        print("Table truncated successfully.")

    except Exception as e:
        print(f"Error truncating table: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def download_rpa_matrix():
    """Downloads the ops_rpa_matrix.csv file from Azure Blob Storage."""
    try:
        blob_service_client = authenticate_blob_storage()
        if not blob_service_client:
            raise Exception("Failed to authenticate with Azure Blob Storage.")

        download_blob(blob_service_client, BLOB_CONTAINER_NAME, BLOB_PATH, LOCAL_CSV_PATH)
        print(f"Downloaded ops_rpa_matrix.csv from {BLOB_PATH} to {LOCAL_CSV_PATH}")
    except Exception as e:
        print(f"Error downloading RPA matrix: {e}")
        raise

def ensure_table_structure(df):
    """Ensures the ops_rpa_matrix table exists and has the correct columns."""
    try:
        conn = connect_to_db()
        if not conn:
            print("Failed to establish database connection.")
            return False

        cursor = conn.cursor()

        # Retrieve existing columns
        cursor.execute("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'ops_rpa_matrix' AND TABLE_SCHEMA = 'wpo'
        """)
        existing_columns = {row[0] for row in cursor.fetchall()}

        if not existing_columns:
            print("Table exists but has no columns. Check database integrity.")
            return False

        # Compare columns and add missing ones
        csv_columns = set(df.columns)
        missing_columns = csv_columns - existing_columns

        if missing_columns:
            print(f"Missing columns detected: {missing_columns}. Adding now...")

            for column in missing_columns:
                alter_query = f"ALTER TABLE wpo.ops_rpa_matrix ADD [{column}] VARCHAR;"
                conn.autocommit = True  # Ensure ALTER TABLE executes independently
                cursor.execute(alter_query)

            print("Added missing columns to ops_rpa_matrix.")

        return True
    except Exception as e:
        print(f"Error ensuring table structure: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def upload_rpa_matrix():
    """Downloads, validates, uploads the RPA matrix, and deletes the local file."""
    try:
        download_rpa_matrix()
        df = pd.read_csv(LOCAL_CSV_PATH, dtype=str).fillna("")
        # Step 2: Sanitize carrier_id
        df["carrier_id"] = df["carrier_id"].str.replace('"', '').str.strip()

        # Step 3: Validate and convert
        for val in df["carrier_id"]:
            if not val.isdigit():
                raise ValueError(f"Non-numeric carrier_id found: {val}")
            if int(val) > 9223372036854775807:
                raise ValueError(f"carrier_id too large for BIGINT: {val}")
        df["carrier_id"] = df["carrier_id"].astype("int64")

        # Step 2: Sanitize carrier_id
        df["parent_carrier_id"] = df["parent_carrier_id"].str.replace('"', '').str.strip()
        
        # Step 3: Validate and convert
        for val in df["parent_carrier_id"]:
            if val == "":
                continue
            if not val.isdigit():
                raise ValueError(f"Non-numeric carrier_id found: {val}")
            if int(val) > 9223372036854775807:
                raise ValueError(f"parent_carrier_id too large for BIGINT: {val}")

        df["parent_carrier_id"] = df["parent_carrier_id"].replace("", pd.NA)

        # Sanitize column values
        df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)
        print(f"Loaded CSV file with {len(df)} records.")

        if not ensure_table_structure(df):
            return

        conn = connect_to_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM wpo.ops_rpa_matrix;")
        row_count = cursor.fetchone()[0]
        conn.close()

        if row_count > 0:
            print(f"Table already contains {row_count} records. Truncating...")
            truncate_rpa_matrix()

        conn = connect_to_db()
        if not conn:
            print("Failed to establish database connection.")
            return

        cursor = conn.cursor()
        insert_query = f"""
            INSERT INTO wpo.ops_rpa_matrix ({", ".join([f"{col}" for col in df.columns])})
            VALUES ({", ".join(["%s" for _ in df.columns])})
        """

        for _, row in df.iterrows():
            row_values = []
            for col in df.columns:
                val = row[col]
                if col == "carrier_id":
                    val = int(val)# Ensure carrier_id goes in as int, not string
                elif col == "parent_carrier_id":
                    val = None if pd.isna(val) else int(val)
                elif col == "company_id" and val.isdigit():
                    val = int(val)
                row_values.append(val)
            cursor.execute(insert_query, tuple(row_values))

        conn.commit()
        print("Data uploaded successfully to ops_rpa_matrix.")

        if os.path.exists(LOCAL_CSV_PATH):
            os.remove(LOCAL_CSV_PATH)
            print(f"Deleted local CSV file: {LOCAL_CSV_PATH}")
    except Exception as e:
        print(f"Error updating RPA matrix: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")


def update_flag_completion(process_name, company_id, carrier_id, flag_value="1"):
    """
    Updates the flag_completion column in wpo.ops_rpa_matrix.

    :param process_name: The process name to filter.
    :param company_id: The company ID to filter.
    :param carrier_id: The carrier ID to filter.
    :param flag_value: The value to set in flag_completion (default is "1").
    """
    try:
        conn = connect_to_db()
        if not conn:
            print("Failed to establish database connection for flag update.")
            return

        cursor = conn.cursor()
        update_query = """
            UPDATE wpo.ops_rpa_matrix
            SET flag_completion = %s
            WHERE process_name = %s AND carrier_id = %s AND company_id = %s
        """
        cursor.execute(update_query, (flag_value, process_name, int(carrier_id), company_id))
        conn.commit()
        print(f"Updated flag_completion to {flag_value} for {process_name}, {carrier_id}, {company_id}.")

    except Exception as e:
        print(f"Error updating flag_completion: {e}")

    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")


# Run the upload function
#upload_rpa_matrix()
