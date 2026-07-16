import os
import ast

import numpy as np
import pyodbc
import requests
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import pandas as pd
from datetime import datetime as dt, datetime, timedelta
import psycopg2
from pandas import DataFrame
import zoho_utils

import string_utils

# Define Key Vault Details
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

# Define Azure Synapse Details
DB_CONFIG = {
    'server': '834analyticsynapse.sql.azuresynapse.net',
    'database': '834_analytics_dev',
    'driver': '{ODBC Driver 17 for SQL Server}'
}

# Define PostgreSQL Details
POSTGRES_CONFIG = {
    "host": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}

run_id = None

def reset_run_id():
    global run_id
    run_id = None

def get_postgres_connection():
    """
    Create a Postgres connection using AAD token auth
    via Service Principal stored in Key Vault.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    sp_credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )

    token = sp_credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        dbname=POSTGRES_CONFIG["database"],
        user=POSTGRES_CONFIG["user"],
        password=token,
        sslmode="require"
    )

# --- Authenticate to Azure Synapse ---
def connect_to_db():
    try:
        secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=DefaultAzureCredential())
        client_id = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
        client_secret = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
        tenant_id = secret_client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

        conn_str = (
            f"DRIVER={DB_CONFIG['driver']};"
            f"SERVER={DB_CONFIG['server']};"
            f"DATABASE={DB_CONFIG['database']};"
            "Authentication=ActiveDirectoryServicePrincipal;"
            f"UID={client_id};"
            f"PWD={client_secret};"
            f"Authority Id={tenant_id};"
        )
        conn = pyodbc.connect(conn_str)
        print("Connected to Azure Synapse")
        return conn
    except Exception as e:
        raise Exception(f"Failed to connect to database: {e}")

def get_contracts_by_carrier(carrier_id, status_str, use_test_npns, batch_size=15, appointment_type_limit=None, agent_type_limit=None):
    status_list = status_str.split(',')
    status_filter = 'AND ('
    for status in status_list:
        if status_list[0] != status:
            status_filter += ' OR '
        status_filter += f"(contract.status = '{status}')"
    status_filter += ')'

    if appointment_type_limit == 'Producer':
        # add producer filter
        print(f"==Limiting appointment type to: {appointment_type_limit}...")
        status_filter = f"{status_filter} AND appointment_type = '{appointment_type_limit}'"

    if agent_type_limit is not None:
        print(f"==Limiting agent type to: {agent_type_limit}...")
        status_filter = f"{status_filter} AND contract.type = '{agent_type_limit}'"

    if use_test_npns == 'Yes':
        print("==Using 'test NPNs only' filter...")
        query_filter = f"""
                WHERE
                (carrier_id = '{carrier_id}') AND (contract.npn = '19712386')
            """
    else:
        age_minimum_time = datetime.now() - timedelta(minutes=30)
        age_minimum_time_str = age_minimum_time.strftime('%Y-%m-%dT%H:%M:%S-05:00')
        print("==Using 'blacklist all test NPNs' filter...")
        query_filter = f"""
                WHERE 
                (
                (carrier_id = '{carrier_id}')
                {status_filter}
                )
                AND (contract.npn != '101110110' AND contract.npn != '101000114'
                AND contract.npn != '101110001' AND contract.npn != '10100011'
                AND contract.npn != '1010001113' AND contract.npn != '1010'
                AND contract.npn != '101000117' AND contract.npn != '0000104272020000'
                AND contract.npn != '000013176850000' AND contract.npn != '0000171263940000'
                AND contract.npn != '000100100' AND contract.npn != '0001001000'
                AND contract.npn != '9999789' AND contract.npn != '123456789'
                AND contract.npn != '1231231' AND contract.npn != '15765851'
                AND contract.npn != '2')\n
            """

    resident_state_fields = 'agent.' + string_utils.resident_state_list[0].replace('_',' ').replace('1','')
    for state in string_utils.resident_state_list[1:]:
        resident_state_fields += ', agent.' + state

    query = f"""SELECT contract_id_crm as contract_id
                        ,carrier_id
                        ,agent_id_crm
                        ,contract.type as agent_type
                        ,contract.npn
                        ,agent.first_name as agent_first_name
                        ,agent.last_name as agent_last_name
                        ,contract.status as contract_status
                        ,agent.status as agent_status
                        ,contract.writing_number
                        ,agent.contracting_email
                        ,agent.email
                        ,agent.secondary_email
                        ,upline
                        ,top_upline
                        ,source_system
                        ,name as id
                        ,field_sales_director
                        ,appointment_type
                        ,status_date
                        ,agent.FEIN as fein
                        ,agent.Phone as phone
                        ,agent.Other_Phone as other_phone
                        ,agent.Mobile as mobile_phone
                        ,parent_contract as parent_id
                        ,requested_state
                        ,contract.pk_id
                        ,{resident_state_fields}
                    FROM wpo.lup_master_agents_contracts contract
                    inner join wpo.lup_agents agent on contract.agent_id_crm = agent.id
                {query_filter}
                LIMIT {batch_size}
                """

    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None
    # Load data into a Pandas DataFrame
    df = pd.read_sql(query, conn)
    if len(df) == 0:
        print("==No rows were found on the CRM.")
        return None
    # Ensure all columns are treated as strings to avoid type issues
    df = df.astype(str)
    df = df.fillna("")  # Replace NaN with empty strings
    df = df.replace('None', None)
    df['process_flag'] = 0
    df['retries'] = 0
    uid_list = generate_unique_id(get_last_inserted_id(), len(df.index))
    df['batch_id'] = generate_unique_id(get_last_inserted_id(), 1)[0]
    for i, contract in df.iterrows():
        df.loc[i, 'txn_id'] = uid_list[i]
    df['responsible_agent'] = None
    df['responsible_agent_contract_status'] = None
    df['email_address'] = None
    df['fail_status'] = None
    df['success_status'] = None
    df['error_message'] = None
    df['note_error'] = None
    df['write_to_crm'] = None
    df['update_status_date'] = None
    df['parent_npn'] = None
    df['parent_wn'] = None
    df['parent_full_name'] = None
    df['parent_first_name'] = None
    df['parent_last_name'] = None
    df['agency_full_name'] = None
    df['agency_npn'] = None
    df['selected_phone'] = None
    df['sibling_contract'] = None
    df['responsible_agent_email'] = None
    df['resident_state'] = None

    now = dt.now()
    df['load_date'] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")
    print("CHECKING FOR REQUEST/RESIDENT")
    print(df.to_string())

    # Requested states Ambetter override, wipe value for all others
    if carrier_id == '2931751000020024159':
        print("==Ambetter detected; Performing requested_state & resident_state reformat...")
        for i, contract in df.iterrows():
            state_str = ''
            if contract['requested_state'] is None:
                print('==No requested states found for this contract, checking Zoho...')
                contract['requested_state'] = zoho_utils.ambetter_requestedstates_fallback(contract)
            if contract['requested_state'] is not None:
                state_intake = contract['requested_state']
                if isinstance(state_intake, list):
                    state_intake = ",".join(state_intake)
                for k, state in enumerate(state_intake.replace('[','').replace(']','').replace("'",'').replace(', ',',').split(',')):
                    if string_utils.state_name_to_state_code(state) in state_str:
                        continue
                    if k > 0:
                        state_str += ','
                    state_str += string_utils.state_name_to_state_code(state)
            df.loc[i, 'requested_state'] = state_str

            full_state_list = string_utils.resident_state_list
            if contract.get('resident_state') is None:
                for field in full_state_list:
                    if contract.get(field.lower()) is not None and contract.get(field.lower()).lower() == 'resident':
                        df.loc[i, 'resident_state'] = string_utils.state_name_to_state_code(field)
            elif contract.get('resident_state').isna():
                contract['resident_state'] = None
    else:
        print("==Carrier is not Ambetter; Clearing requested_state lists...")
        df['requested_state'] = None
    print(f"Contract table after state adjustments:\n{df.to_string()}")

    return df

def upload_contracts_into_queue(contracts_df):
    print("==Uploading given contracts into postgres queue")
    # Generate a unique run_id if it has not been created yet
    global run_id
    print(f"==Current run_id: {run_id}")
    if run_id is None:
        print("====Generating run_id...")
        run_id = generate_unique_id(get_last_inserted_id(), 1)[0]
        print(f"====New run_id: {run_id}")
    # Connect to database
    contracts_df['run_id'] = run_id
    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None
    values = pd.DataFrame()
    try:
        values = contracts_df[master_columns_intake_format].values.tolist()
    except Exception as e:
        values = contracts_df[contract_queue_columns_intake_format].values.tolist()

    insert_query = """
        INSERT INTO wpo.ops_acr_contract_queue (
            run_id,
            batch_id,
            txn_id,
            contract_id,
            carrier_id,
            agent_id,
            agent_type,
            npn,
            agent_first_name,
            agent_last_name,
            contract_status,
            agent_status,
            agent_writing_num,
            contracting_email,
            email,
            secondary_email,
            upline_id,
            top_upline_id,
            contract_source,
            field_sales_director_id,
            appointment_type,
            process_flag,
            retries,
            email_address,
            fail_status,
            error_message,
            note_error,
            load_date,
            id,
            write_to_crm,
            update_status_date,
            success_status,
            responsible_agent,
            responsible_agent_contract_status,
            old_status_date,
            parent_npn,
            parent_wn,
            parent_full_name,
            parent_first_name,
            parent_last_name,
            agency_fein,
            agency_full_name,
            agency_npn,
            selected_phone,
            phone,
            other_phone,
            mobile_phone,
            parent_id,
            requested_states,
            resident_state,
            sibling_contract,
            responsible_agent_email,
            pk_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """

    try:
        cursor = conn.cursor()
        batch_size = 500
        for i in range(0, len(values), batch_size):
            batch = values[i:i + batch_size]
            cursor.executemany(insert_query, batch)
            conn.commit()
        cursor.close()
        print("Insert completed!")

    except Exception as e:
        print(f"Insert failed: {e}")
        raise

def update_contract_batch_in_master_table(df_contracts: DataFrame, pk_name: str = "pk_id", table_name: str = "wpo.lup_master_agents_contracts"):
    # If any fields are added/reorganized in this section, you must address the field_mapping column indices in BulkWriteBody()
    print("==Beginning Master Table contract update...")
    if len(df_contracts) == 0:
        print("==No valid contracts were received. Skipping postgres update...")
        return None
    print(df_contracts.to_string())
    print(df_contracts[['contract_status', 'id', 'fail_status', 'write_to_crm', 'update_status_date', 'success_status',
                        'old_status_date', 'pk_id']])
    print("=====Trimming data=====")
    trimmed_df = df_contracts[
        ['contract_status', 'id', 'fail_status', 'write_to_crm', 'update_status_date', 'success_status',
         'old_status_date', 'pk_id']]
    print("=====write_to_crm filter=====")
    trimmed_df = trimmed_df.loc[trimmed_df['write_to_crm'] == 'Yes']
    trimmed_df = trimmed_df.drop(columns=['write_to_crm'])
    print("=====Success Contract Status logic=====")
    trimmed_df['contract_status'] = np.where(
        (~trimmed_df['success_status'].isnull()) & (trimmed_df['success_status'] != 'None')
        , trimmed_df['success_status'], trimmed_df['contract_status'])
    trimmed_df = trimmed_df.drop(columns=['success_status'])
    print("=====Failed Contract Status logic=====")
    trimmed_df['contract_status'] = np.where(
        (~trimmed_df['fail_status'].isnull()) & (trimmed_df['fail_status'] != 'None')
        , trimmed_df['fail_status'], trimmed_df['contract_status'])
    trimmed_df = trimmed_df.drop(columns=['fail_status'])
    print("=====Status Date logic=====")
    trimmed_df['status_date'] = None
    trimmed_df['status_date'] = np.where(trimmed_df['update_status_date'] == 'Yes', dt.now().date(),
                                         trimmed_df['old_status_date'])
    trimmed_df = trimmed_df.drop(columns=['update_status_date'])
    trimmed_df = trimmed_df.drop(columns=['old_status_date'])
    print("==========")
    print(trimmed_df)
    format_map = {
        "contract_status": "status",
        "id": "name"
    }
    trimmed_df.rename(columns=format_map, inplace=True)

    # upsert dataframe into postgres table
    return bulk_update_table_from_dataframe(trimmed_df, pk_name, table_name)

def bulk_update_table_from_dataframe(df: DataFrame, pk_name: str = 'pk_id', table_name: str = 'wpo.lup_master_agents_contracts'):
    """Unified Bulk Write v2 update wrapper."""
    print("==Bulk updating postgres table...")
    if df.empty:
        print("⚠️ No records to update.")
        return None

    total = len(df)
    print(f"🚀 Bulk update {total} {table_name} records (find_by={pk_name})")

    conn = get_postgres_connection()
    columns_str = normalize_sql_column_name(df.columns[0])
    conflict_updates_str = f"{normalize_sql_column_name(df.columns[0])} = EXCLUDED.{normalize_sql_column_name(df.columns[0])}"
    for col in df.columns[1:]:
        columns_str += f", {normalize_sql_column_name(col)}"
        conflict_updates_str += f", {normalize_sql_column_name(col)} = EXCLUDED.{normalize_sql_column_name(col)}"

    values = f"('{df.iloc[0][0]}'"
    for datum in df.iloc[0][1:]:
        values += f",'{datum}'"
    values += ')'

    for row in df.values[1:]:
        values += f",('{row[0]}'"
        for datum in row[1:]:
            values += f",'{datum}'"
        values += ')'
    query = f"""INSERT INTO {table_name} ({columns_str})
                VALUES {values}
                ON CONFLICT ({pk_name})
                DO UPDATE SET
                    {conflict_updates_str}
            """
    print("==== Bulk Update Query:")
    print(query)
    cur = conn.cursor()
    cur.execute(query)
    affected = cur.rowcount
    conn.commit()
    print(f'Affected rows in module {table_name}: {affected}')

    conn.close()

def update_contract_by_npn(contract):
    # Used to synchronize all versions of a given contract in the current queue batch
    print("==Synchronizing contracts in postgres queue...")
    batch_id = contract['batch_id'].item()
    print(f'batch_id: {batch_id}')
    npn = contract['npn'].item()
    print(f'npn: {npn}')
    contract_series = contract.iloc[0]
    # Connect to database
    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None
    contract_series.drop(index='pk_id',axis=1,inplace=True)
    # Delete & recreate old entry to ensure all fields are up-to-date.
    update_str = f"{contract_series.index[0]} = '{contract_series.get(contract_series.index[0])}'"
    for idx in contract_series.index[1:]:
        update_str += f",\n{idx} = '{contract_series.get(idx)}'"
    update_query = f"""
            UPDATE wpo.ops_acr_contract_queue SET
            {update_str}
            where npn = '{npn}' and batch_id = '{batch_id}'
        """

    try:
        cursor = conn.cursor()
        cursor.execute(update_query)
        conn.commit()
        cursor.close()
        print("==Record updated in postgres successfully.")

    except Exception as e:
        print(f"Insert failed: {e}")
        print(update_query)
        raise
    print("==Finished updating contract in postgres.")

def update_contract_by_txn_id(contract):
    print("==Updating contract in postgres queue...")
    batch_id = contract['batch_id'].item()
    print(f'batch_id: {batch_id}')
    txn_id = contract['txn_id'].item()
    print(f'txn_id: {txn_id}')

    # Connect to database
    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None

    # Delete & recreate old entry to ensure all fields are up-to-date.
    delete_query = f"""
            DELETE FROM wpo.ops_acr_contract_queue
            WHERE batch_id = '{batch_id}'
                AND txn_id = '{txn_id}'
        """

    try:
        cursor = conn.cursor()
        cursor.execute(delete_query)
        conn.commit()
        cursor.close()
        print("==Old record deleted in postgres successfully.")

    except Exception as e:
        print(f"Insert failed: {e}")
        raise

    upload_contracts_into_queue(contract)
    print("==Finished updating contract in postgres.")

def fetch_next_contract_for_processing(batch_id):
    print("==Fetching next contract and updating its process flag...")
    contract = None
    try:
        # Connect to database
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = f"""
        SELECT *
        FROM wpo.ops_acr_contract_queue
        WHERE batch_id = '{batch_id}'
            AND process_flag = '0'
            AND sibling_contract is null
        LIMIT 1
        """

        # Load data into a Pandas DataFrame
        contract = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        contract = contract.astype(str)
        contract = contract.fillna("")  # Replace NaN with empty strings
        contract = contract.replace('None',None)

        print(f"==Fetched next contract to process:\n{contract.to_string()}")

        contract['process_flag'] = '4'
        update_contract_by_txn_id(contract)

        return contract
    except Exception as e:
        print(f"Error fetching next contract for processing: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def contract_to_process_exists(batch_id):
    print("==Fetching next contract and updating its process flag...")
    contract = None
    try:
        # Connect to database
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = f"""
            SELECT *
            FROM wpo.ops_acr_contract_queue
            WHERE batch_id = '{batch_id}'
                AND process_flag = '0'
                AND sibling_contract is null
            LIMIT 1
            """

        # Load data into a Pandas DataFrame
        contract = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        contract = contract.astype(str)
        contract = contract.fillna("")  # Replace NaN with empty strings
    except Exception as e:
        print(f"Error searching for existing contract: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

    if (contract is None) or (len(contract) < 1):
        return False
    return True

def get_contracts_by_batch_id(batch_id):
    print(f"==Fetching all contracts with batch_id: {batch_id}...")
    try:
        # Connect to database
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = f"""
            SELECT *
            FROM wpo.ops_acr_contract_queue
            WHERE batch_id = '{batch_id}'
            """

        # Load data into a Pandas DataFrame
        batch = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        batch = batch.astype(str)
        batch = batch.fillna("")  # Replace NaN with empty strings

        print(f"==Fetched contracts:\n{batch.to_string()}")

        return batch
    except Exception as e:
        print(f"Error searching for contracts by batch_id: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def get_contracts_by_npn_in_batch(npn, batch_id, limit=100):
    print(f"==Fetching all contracts with batch_id: {batch_id} & npn: {npn}...")
    try:
        # Connect to database
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = f"""
            SELECT TOP {limit} *
            FROM wpo.ops_acr_contract_queue
            WHERE batch_id = '{batch_id}'
            AND npn = '{npn}'
            """

        # Load data into a Pandas DataFrame
        batch = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        batch = batch.astype(str)
        batch = batch.fillna("")  # Replace NaN with empty strings

        print(f"==Fetched contracts:\n{batch.to_string()}")

        return batch
    except Exception as e:
        print(f"Error searching for contracts by batch_id: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def get_all_contracts_from_this_run(run_id_override=None):
    global run_id
    if run_id_override is None:
        target_run_id = run_id
    else:
        target_run_id = run_id_override
    print(f"==Fetching all contracts with run_id {target_run_id}...")
    try:
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = f"""
                SELECT *
                FROM wpo.ops_acr_contract_queue
                WHERE run_id = '{target_run_id}'
                """

        # Load data into a Pandas DataFrame
        batch = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        batch = batch.astype(str)
        batch = batch.fillna("")  # Replace NaN with empty strings
        print(f"==Fetched contracts:\n{batch.to_string()}")

        return batch
    except Exception as e:
        print(f"Error searching for contracts by batch_id: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def update_notes(df: DataFrame):
    print("==Updating notes...")


def disable_carrier_active_flag(carrier_id):
    print(f"==Disabling active_flag for carrier '{carrier_id}'")

    # Connect to database
    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None

    # Delete & recreate old entry to ensure all fields are up-to-date.
    update_query = f"""
                UPDATE wpo.ops_acr_process_matrix
                SET active_flag = 'No'
                WHERE carrier_id = '{carrier_id}'
            """
    try:
        cursor = conn.cursor()
        cursor.execute(update_query)
        conn.commit()
        cursor.close()
        print("==Record updated in postgres successfully.")
    except Exception as e:
        print(f"Update failed: {e}")
        raise

    # Handle old ACC carriers that are still part of the ACR process (Update mirrored ACC matrix entry)
    if carrier_id == '2931751000147793570' or carrier_id == '2931751000020024159':
        print("Updating ACC matrix mirror for UHC ACA or Ambetter...")
        # Connect to database
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        # Delete & recreate old entry to ensure all fields are up-to-date.
        update_query = f"""
                        UPDATE wpo.ops_acc_process_matrix
                        SET active_flag = 'No'
                        WHERE carrier_id = '{carrier_id}'
                    """
        try:
            cursor = conn.cursor()
            cursor.execute(update_query)
            conn.commit()
            cursor.close()
            print("==Updated ACC matrix mirror entry successfully.")
        except Exception as e:
            print(f"Update failed: {e}")
            raise

    print("==Finished process matrix in postgres.")

def disable_carrier_automatic_export_flag(carrier_id):
    print(f"==Disabling automatic_export flag for carrier '{carrier_id}'")

    # Connect to database
    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None

    # Delete & recreate old entry to ensure all fields are up-to-date.
    update_query = f"""
                    UPDATE wpo.ops_acr_process_matrix
                    SET automatic_export = 'No'
                    WHERE carrier_id = '{carrier_id}'
                """
    try:
        cursor = conn.cursor()
        cursor.execute(update_query)
        conn.commit()
        cursor.close()
        print("==Record updated in synapse successfully.")

    except Exception as e:
        print(f"Update failed: {e}")
        raise

    print("==Finished process matrix in synapse.")

def advance_carrier_eod_flag(carrier_id, new_eod_flag=-1):
    print(f"==Advancing EOD flag for carrier '{carrier_id}'")

    # Connect to database
    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None

    # Delete & recreate old entry to ensure all fields are up-to-date.
    update_query = f"""
                UPDATE wpo.ops_acr_process_matrix
                SET eod_flag = '{new_eod_flag}'
                WHERE carrier_id = '{carrier_id}'
            """
    try:
        cursor = conn.cursor()
        cursor.execute(update_query)
        conn.commit()
        cursor.close()
        print("==Record updated in synapse successfully.")

    except Exception as e:
        print(f"Update failed: {e}")
        raise

    print("==Finished process matrix in synapse.")

def reset_carrier_eod_flags(date: str):
    print(f"==Resetting EOD Flag for all carriers")

    # Connect to database
    conn = get_postgres_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None

    update_query = f"""
                UPDATE wpo.ops_acr_process_matrix
                SET eod_flag = 0,
                last_eod_refresh_date = '{date}'
                WHERE eod_times is not null
            """
    try:
        cursor = conn.cursor()
        cursor.execute(update_query)
        conn.commit()
        cursor.close()
        print("==Record updated in synapse successfully.")

    except Exception as e:
        print(f"Update failed: {e}")
        raise

    print("==Finished process matrix in synapse.")

def get_last_inserted_id():
    try:
        print("==Fetching last inserted uid...")
        last_inserted_id = None
        # Connect to database
        conn = connect_to_db()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = "SELECT MAX(uid) as recent_uid FROM raw.ops_uid_control"

        # Load data into a Pandas DataFrame
        last_inserted_id = pd.read_sql(query, conn)

        if last_inserted_id is not None:
            last_inserted_id = last_inserted_id['recent_uid'].item()
        #print(f"==Latest uid retrieved: {last_inserted_id}")

    except Exception as e:
        print(f"Error fetching last inserted UID: {e}")
        return 0
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")
    if last_inserted_id is None:
        last_inserted_id = 0
    return last_inserted_id


def generate_unique_id(last_inserted_id, n_rows):
    """
    This function generates a list of unique ids based on the last inserted id, the current date, and the number of rows.

    Args:
        last_inserted_id (int): The last inserted id.
        today (datetime.date): The current date.
        n_rows (int): The number of unique ids to generate.

    Returns:
        list: A list of unique ids.
    """
    today = dt.today().date()
    print(f"==Generating unique id from latest received: {last_inserted_id}...")

    if last_inserted_id:
        last_date, last_sequence_number = str(last_inserted_id)[:5], str(last_inserted_id)[5:]
        last_date = dt.strptime(last_date, "%y%j").date()
        if last_date == today:
            sequence_number = int(last_sequence_number) + 1
        else:
            sequence_number = 1
    else:
        sequence_number = 1

    julian_date = today.strftime("%y%j")
    num_list = list(range(sequence_number, sequence_number + n_rows))
    unique_id = [julian_date + str(num).zfill(8) for num in num_list]
    print(f"==Generated ids: {unique_id}...")

    print("==Uploading new uids to ops_uid_control")
    # Connect to database
    conn = connect_to_db()
    if not conn:
        print("Failed to establish database connection.")
        return None

    values = []

    if len(unique_id) == 1:
        values = [unique_id]
    else:
        for id in unique_id:
            values.append([id])

    insert_query = """
        INSERT INTO raw.ops_uid_control (
            uid,
            process_type,
            table_name
        ) VALUES (?,'ACR','wpo_acr_process_queue')
    """

    try:
        cursor = conn.cursor()
        #cursor.fast_executemany = True
        batch_size = 500
        for i in range(0, len(values), batch_size):
            batch = values[i:i + batch_size]
            cursor.executemany(insert_query, batch)
            conn.commit()
        cursor.close()
        print("Insert completed!")

    except Exception as e:
        print(f"Insert failed: {e}")
        raise

    return unique_id

def get_latest_batch_id():
    try:
        print("==Fetching last used batch id...")
        last_inserted_id = None
        # Connect to database
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = "SELECT MAX(batch_id) as latest_batch_id FROM wpo.ops_acr_contract_queue"

        # Load data into a Pandas DataFrame
        last_inserted_id = pd.read_sql(query, conn)
        if last_inserted_id is not None:
            last_inserted_id = last_inserted_id['latest_batch_id'].item()

    except Exception as e:
        print(f"Error fetching last inserted UID: {e}")
        return 0
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")
    if last_inserted_id is None:
        last_inserted_id = 0
    return last_inserted_id

def get_current_run_id():
    global run_id
    return run_id

def get_lup_carriers():
    print(f"==Fetching carrier information from wpo.lup_carriers...")
    try:
        # Connect to database
        conn = get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = f"""
                SELECT *
                FROM wpo.lup_carriers
                """

        # Load data into a Pandas DataFrame
        df = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        df = df.astype(str)
        df = df.fillna("")  # Replace NaN with empty strings

        return df
    except Exception as e:
        print(f"Error fetching lup_carriers: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def bcbs_mi_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"==Handling firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    for i, contract in firm_contracts.iterrows():
        if (contract['upline'] is None) or (contract['top_upline'] is None):
            print("==Upline or Top Upline data is missing, skipping firm adjustments.")
            continue
        agent_df = None
        contract_df = None
        try:
            agent_df = DataFrame()
            try:
                # Connect to database
                conn = get_postgres_connection()
                if not conn:
                    print("Failed to establish database connection.")
                    return None
                query = f"""
                        SELECT *
                        FROM wpo.lup_agents
                        WHERE responsible_agency = '{contract['agent_id_crm']}'
                        """
                # Load data into a Pandas DataFrame
                agent_df = pd.read_sql(query, conn)
                # Ensure all columns are treated as strings to avoid type issues
                agent_df = agent_df.astype(str)
                agent_df = agent_df.fillna("")  # Replace NaN with empty strings
            except Exception as e:
                print(f"Error fetching lup_carriers: {e}")
                return None
            finally:
                if 'conn' in locals():
                    conn.close()
                    print("Database connection closed.")
            ##
            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            agent_id = agent_df['id'].item()
        except Exception as e:
            print(f"==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue
        query = f"""SELECT contract_id_crm
                        ,carrier_id
                        ,agent_id_crm
                        ,contract.type as agent_type
                        ,contract.npn as npn
                        ,agent.first_name as agent_first_name
                        ,agent.last_name as agent_last_name
                        ,contract.status as contract_status
                        ,agent.status
                        ,contract.writing_number
                        ,agent.contracting_email
                        ,agent.email
                        ,agent.secondary_email
                        ,upline
                        ,top_upline
                        ,source_system
                        ,field_sales_director
                        ,appointment_type
                        ,status_date
                        ,agent.FEIN
                        ,agent.Phone
                        ,agent.Other_Phone
                        ,agent.Mobile as mobile_phone
                        ,parent_contract
                        ,requested_state
                    FROM wpo.lup_master_agents_contracts contract
                        inner join wpo.lup_agents agent on contract.agent_id_crm = agent.id
                    WHERE agent.id = '{agent_id}'
                        and carrier_id = '{carrier_id}'
                    LIMIT 1
        """
        try:
            try:
                # Connect to database
                conn = get_postgres_connection()
                if not conn:
                    print("Failed to establish database connection.")
                    return None
                # Load data into a Pandas DataFrame
                contract_df = pd.read_sql(query, conn)
                # Ensure all columns are treated as strings to avoid type issues
                contract_df = contract_df.astype(str)
                contract_df = contract_df.fillna("")  # Replace NaN with empty strings
            except Exception as e:
                print(f"Error fetching lup_carriers: {e}")
                return None
            finally:
                if 'conn' in locals():
                    conn.close()
                    print("Database connection closed.")
            if len(contract_df) == 0:
                print("==No data in response, setting contract_df to None.")
                contract_df = None
        except Exception as e:
            print(f"==Error during firm adjustments at responsible agent contract fetch level: {e}")

        # If missing, create a new contract.. Done
        if contract_df is None:
            print("==No contract found for responsible agent, creating new contract")
            trimmed_df = agent_df[['id', 'npn']]
            #trimmed_df = trimmed_df.rename(columns={"NPN":"contract.npn"})
            trimmed_df['carrier_id'] = contract['carrier_id']
            trimmed_df['status_date'] = dt.now().date()
            trimmed_df['source_system'] = contract['contract_source']
            trimmed_df['field_sales_director'] = contract['field_sales_director_id']
            trimmed_df['appointment_type'] = 'Producer'
            trimmed_df['requested_states'] = [['Michigan']]
            ####trimmed_df['Schedule'] = '100%'
            ####trimmed_df['Parent_Contract'] = None
            trimmed_df['upline'] = contract['upline']
            trimmed_df['top_upline'] = contract['top_upline']
            trimmed_df['contract_status'] = 'Requested'
            print(trimmed_df.to_string())
            upsert_success = upsert_contract(trimmed_df)
            if upsert_success:
                contract['Status'] = 'Sent to Agent'
                contract['Process_Flag'] = '2'
                contract['responsible_agent'] = trimmed_df['Agent'].item()
                contract['responsible_agent_contract_status'] = trimmed_df['Status'].item()
            else:
                contract['error_message'] = 'Contract creation for Responsible Agent failed'
                contract['Process_Flag'] = '1'
        else:
            print("==Contract found for responsible agent, setting responsible_agent field for firm_contract")
            contract['responsible_agent'] = contract_df['agent_id_crm'].item()
            contract['responsible_agent_contract_status'] = contract_df['contract_status'].item()

        firm_contracts.loc[i] = contract

    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def bcbstx_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"==Handling postgres firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    for i, contract in firm_contracts.iterrows():
        if (contract['upline'] is None) or (contract['top_upline'] is None):
            print("==Upline or Top Upline data is missing, skipping firm adjustments.")
            continue
        agent_df = None
        contract_df = None
        try:
            agent_df = DataFrame()
            try:
                # Connect to database
                conn = get_postgres_connection()
                if not conn:
                    print("Failed to establish database connection.")
                    return None
                query = f"""
                        SELECT *
                        FROM wpo.lup_agents
                        WHERE responsible_agency = '{contract['agent_id_crm']}'
                        """
                # Load data into a Pandas DataFrame
                agent_df = pd.read_sql(query, conn)
                # Ensure all columns are treated as strings to avoid type issues
                agent_df = agent_df.astype(str)
                agent_df = agent_df.fillna("")  # Replace NaN with empty strings
            except Exception as e:
                print(f"Error fetching lup_carriers: {e}")
                return None
            finally:
                if 'conn' in locals():
                    conn.close()
                    print("Database connection closed.")
            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            agent_id = agent_df['id'].item()

            contract['responsible_agent'] = agent_id
            contract['agency_full_name'] = contract['agent_first_name'] + " " + (contract['agent_last_name'] or '')
            contract['agent_first_name'] = agent_df['first_name'].item()
            contract['agent_last_name'] = agent_df['last_name'].item()
            contract['agency_npn'] = contract['npn']
            contract['npn'] = agent_df['npn'].item()
            contract['responsible_agent_email'] = (agent_df['contracting_email'].item()
                                                   or agent_df['email'].item()
                                                   or agent_df['secondary_email'].item())
            contract['phone'] = agent_df['phone'].item() or agent_df['other_phone'].item() or agent_df['mobile_phone'].item()

        except Exception as e:
            print(f"==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue
        firm_contracts.loc[i] = contract
    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def uhc_aca_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"\n==Handling firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    for i, contract in firm_contracts.iterrows():
        agent_df = None
        try:
            try:
                # Connect to database
                conn = get_postgres_connection()
                if not conn:
                    print("Failed to establish database connection.")
                    return None
                query = f"""
                        SELECT *
                        FROM wpo.lup_agents
                        WHERE responsible_agency = '{contract['agent_id_crm']}'
                        """
                # Load data into a Pandas DataFrame
                agent_df = pd.read_sql(query, conn)
                # Ensure all columns are treated as strings to avoid type issues
                agent_df = agent_df.astype(str)
                agent_df = agent_df.fillna("")  # Replace NaN with empty strings
            except Exception as e:
                print(f"Error fetching lup_agents: {e}")
                return None
            finally:
                if 'conn' in locals():
                    conn.close()
                    print("Database connection closed.")

            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            agent_id = agent_df['id'].item()
            contract['responsible_agent'] = agent_id
            if contract['agent_last_name']:
                contract['agency_full_name'] = contract['agent_first_name'] + " " + contract['agent_last_name']
            else:
                contract['agency_full_name'] = contract['agent_first_name']
            contract['agent_first_name'] = agent_df['first_name'].item()
            contract['agent_last_name'] = agent_df['last_name'].item()
            contract['agency_npn'] = contract['npn']
            contract['npn'] = agent_df['npn'].item()

        except Exception as e:
            print(f"==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue

        firm_contracts.loc[i] = contract

    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def uhc_aca_subproducer_adjustments(carrier_id, status_str, subproducer_contracts):
    print(f"==Handling subproducer adjustments...\n==Given data:\n{subproducer_contracts.to_string()}\n")
    if len(subproducer_contracts) == 0:
        print("==No subproducer contracts collected, skipping subproducer adjustments.")
    for i, contract in subproducer_contracts.iterrows():
        contract_df = None
        parent_id = contract['parent_id']
        try:
            try:
                # Connect to database
                conn = get_postgres_connection()
                if not conn:
                    print("Failed to establish database connection.")
                    return None
                query = f"""
                    SELECT name
                        ,carrier_id
                        ,agent_id_crm
                        ,npn
                        ,first_name as agent_first_name
                        ,last_name as agent_last_name
                        ,writing_number
                        ,status
                        ,contract_id_crm
                    FROM wpo.lup_master_agents_contracts
                    WHERE (carrier_id = '{carrier_id}')
                    AND (agent_id_crm = '{parent_id}')
                    LIMIT {1}
                    """
                # Load data into a Pandas DataFrame
                contract_df = pd.read_sql(query, conn)
                # Ensure all columns are treated as strings to avoid type issues
                contract_df = contract_df.astype(str)
                contract_df = contract_df.fillna("")  # Replace NaN with empty strings
            except Exception as e:
                print(f"Error fetching lup_master_agents_contracts: {e}")
                return None
            finally:
                if 'conn' in locals():
                    conn.close()
                    print("Database connection closed.")
        except Exception as e:
            print(f"==Error during firm adjustments at responsible agent contract fetch level: {e}")

        if contract_df is None:
            print("==No parent contract found for parent agent.")
        else:
            print("==Parent contract found for parent agent.")
            contract['parent_npn'] = contract_df['npn'].item()
            contract['parent_wn'] = contract_df['writing_number'].item()
            contract['parent_full_name'] = contract_df['agent_first_name'].item() + " " + contract_df['agent_last_name'].item()
            contract['parent_first_name'] = contract_df['agent_first_name'].item()
            contract['parent_last_name'] = contract_df['agent_last_name'].item()
            contract['responsible_agent'] = contract_df['agent_id_crm'].item()
            contract['responsible_agent_contract_status'] = contract_df['status'].item()

        subproducer_contracts.loc[i] = contract

    print(f"==After subproducer adjustments:\n==Given data:\n{subproducer_contracts.to_string()}")
    return subproducer_contracts

def goldkidney_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"\n==Handling firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    for i, contract in firm_contracts.iterrows():
        agent_df = None
        try:
            try:
                # Connect to database
                conn = get_postgres_connection()
                if not conn:
                    print("Failed to establish database connection.")
                    return None
                query = f"""
                            SELECT *
                            FROM wpo.lup_agents
                            WHERE responsible_agency = '{contract['agent_id_crm']}'
                            """
                # Load data into a Pandas DataFrame
                agent_df = pd.read_sql(query, conn)
                # Ensure all columns are treated as strings to avoid type issues
                agent_df = agent_df.astype(str)
                agent_df = agent_df.fillna("")  # Replace NaN with empty strings
            except Exception as e:
                print(f"Error fetching lup_agents: {e}")
                return None
            finally:
                if 'conn' in locals():
                    conn.close()
                    print("Database connection closed.")

            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            agent_id = agent_df['id'].item()
            contract['responsible_agent'] = agent_id
            contract['responsible_agent_email'] = agent_df['contracting_email'].item() or agent_df['email'].item() or agent_df['secondary_email'].item()
            if contract['agent_last_name']:
                contract['agency_full_name'] = contract['agent_first_name'] + " " + contract['agent_last_name']
            else:
                contract['agency_full_name'] = contract['agent_first_name']
            contract['agent_first_name'] = agent_df['first_name'].item()
            contract['agent_last_name'] = agent_df['last_name'].item()
            contract['agency_npn'] = contract['npn']
            contract['npn'] = agent_df['npn'].item()

        except Exception as e:
            print(f"==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue

        firm_contracts.loc[i] = contract

    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def upsert_contract(contract):
    print(f"==Inserting new contract: {contract}")
    print("==Assembling data...")

    print("==Posting upsert request...")
    contract_df = pd.DataFrame()
    print(contract_df.to_string())
    if len(contract_df) == 0:
        return False
    return True

def normalize_sql_column_name(column: str) -> str:
    if column.count('.') > 0:
        return column.split('.',1)[1]
    return column


master_columns_intake_format = [
    "run_id",
    "batch_id",
    "txn_id",
    "id",
    "carrier_id",
    "agent_id_crm",
    "agent_type",
    "npn",
    "agent_first_name",
    "agent_last_name",
    "contract_status",
    "agent_status",
    "writing_number",
    "contracting_email",
    "email",
    "secondary_email",
    "upline",
    "top_upline",
    "source_system",
    "field_sales_director",
    "appointment_type",
    "process_flag",
    "retries",
    "email_address",
    "fail_status",
    "error_message",
    "note_error",
    "load_date",
    "contract_id",
    "write_to_crm",
    "update_status_date",
    "success_status",
    "responsible_agent",
    "responsible_agent_contract_status",
    "status_date",
    "parent_npn",
    "parent_wn",
    "parent_full_name",
    "parent_first_name",
    "parent_last_name",
    "fein",
    "agency_full_name",
    "agency_npn",
    "selected_phone",
    "phone",
    "other_phone",
    "mobile_phone",
    "parent_id",
    "requested_state",
    "resident_state",
    "sibling_contract",
    "responsible_agent_email",
    "pk_id"
]

contract_queue_columns_intake_format = [
    "run_id",
    "batch_id",
    "txn_id",
    "contract_id",
    "carrier_id",
    "agent_id",
    "agent_type",
    "npn",
    "agent_first_name",
    "agent_last_name",
    "contract_status",
    "agent_status",
    "agent_writing_num",
    "contracting_email",
    "email",
    "secondary_email",
    "upline_id",
    "top_upline_id",
    "contract_source",
    "field_sales_director_id",
    "appointment_type",
    "process_flag",
    "retries",
    "email_address",
    "fail_status",
    "error_message",
    "note_error",
    "load_date",
    "id",
    "write_to_crm",
    "update_status_date",
    "success_status",
    "responsible_agent",
    "responsible_agent_contract_status",
    "old_status_date",
    "parent_npn",
    "parent_wn",
    "parent_full_name",
    "parent_first_name",
    "parent_last_name",
    "agency_fein",
    "agency_full_name",
    "agency_npn",
    "selected_phone",
    "phone",
    "other_phone",
    "mobile_phone",
    "parent_id",
    "requested_states",
    "resident_state",
    "sibling_contract",
    "responsible_agent_email",
    "pk_id"
]



# [ DEBUG COMMAND ]
#get_contracts_by_carrier('2931751000020024159','Requested,Re-Requested','No',batch_size=5)
