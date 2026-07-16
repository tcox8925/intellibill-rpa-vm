import pandas as pd
from db_connection import connect_to_db


def read_rpa_matrix():
    """
    Fetches the full RPA matrix from the database and returns it as a DataFrame.

    :return: Pandas DataFrame containing the entire ops_rpa_matrix table.
    """
    try:
        # Connect to database
        conn = connect_to_db()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = """
                SELECT
                    pk_id,
                    process_family,
                    process_type,
                    process_name,
                    automation_type,
                    automated,
                    disabled,
                    disabled_reason,
                    entity_name,
                    entity_type,
                    carrier_id,
                    carrier_name,
                    parent_carrier_id,
                    parent_carrier_name,
                    company_id,
                    company_id_field,
                    product_name,
                    source_mode,
                    source_url,
                    source_port,
                    source_remote_path,
                    source_email,
                    source_sender_email,
                    source_subject_key,
                    source_username,
                    source_password,
                    source_password_secret_name,
                    secret_provider,
                    source_login,
                    otp_required,
                    otp_path,
                    otp_filename,
                    otp_extension,
                    file_type,
                    file_prefix,
                    output_file_name_prefix,
                    expected_extension,
                    requires_extraction,
                    extracted_file_prefix,
                    extracted_file_extension,
                    rename_base,
                    download_path,
                    upload_data,
                    upload_data_table,
                    base_gdrive_path,
                    base_blob_path,
                    blob_container_name,
                    storage_account_name,
                    container_name,
                    script_name,
                    use_profile_path,
                    profile_path,
                    driver,
                    pautomate_url,
                    batch_process_upload,
                    batch_process_upload_set,
                    run_sandbox_only,
                    more_than_one_download,
                    pickup_method,
                    cadence,
                    schedule,
                    target_dates,
                    notification_email,
                    flag_completion
                FROM ops_srv.ops_process_matrix_common opm
                WHERE opm.process_family ilike 'RPA_%';
                """
        # Load data into a Pandas DataFrame
        df = pd.read_sql(query, conn, dtype_backend='numpy_nullable')

        # Ensure all columns are treated as strings to avoid type issues
        df = df.astype(str)
        df = df.fillna("")  # Replace NaN with empty strings

        print(f"Successfully fetched {len(df)} records from ops_rpa_matrix.")
        return df

    except Exception as e:
        print(f"Error reading RPA matrix: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")


def get_blob_config(process_name, company_id, carrier_id):
    """
    Returns a dictionary of blob storage and Key Vault configuration
    from the RPA matrix for the specified process.

    Expected keys in the matrix:
      - storage_account_name
      - keyvault_name
      - client_id
      - client_secret
      - tenant_id

    :param process_name: Process name to filter on.
    :param company_id: Company ID to filter on.
    :param carrier_id: Carrier ID to filter on.
    :return: Dictionary with configuration values.
    """
    df = read_rpa_matrix()
    if df is None:
        raise Exception("RPA matrix could not be loaded.")

    # Ensure all fields are stripped of extra whitespace.
    df = df.astype(str).apply(lambda x: x.str.strip())

    # Filter for the matching row
    row = df[
        (df["process_name"] == process_name) &
        (df["company_id"] == company_id) &
        (df["carrier_id"] == carrier_id)
        ]

    if row.empty:
        raise ValueError(f"No matching config found for {process_name}, {company_id}, {carrier_id}")

    row = row.iloc[0]
    return {
        "storage_account_name": row["storage_account_name"],
        "keyvault_name": row["source_password_secret_name"],
        "client_id": row["client_id"],
        "client_secret": row["client_secret"],
        "tenant_id": row["tenant_id"]
    }

# For testing purposes, you can uncomment the following line:
read_rpa_matrix()

