import datetime
import pandas as pd
import psycopg2
import pytz
from db_connection import connect_to_db
import pyodbc

# Global variables for logging
start_time = None  # Will be a datetime object
start_times = {}
#start_time_str = None  # The string version used as the unique key
end_time = None  # Will be a datetime object
last_success = None
last_error = None

# Global aggregator variables for carrier results
carrier_successes = []
carrier_errors = []

# Define CST timezone
cst = pytz.timezone("America/Chicago")

# Error codes for different types of errors
ERROR_CODES = {
    "login_error": "E001",
    "download_button_not_found": "E002",
    "process_interrupted": "E003",
    "download_error": "E004",
    "upload_error": "E005",
    "db_connection_error": "E006",
    "sql_upload_error": "E007",
    "file_deletion_error": "E008",
    "general_error": "E009",
    "link_not_found":"E010",
    "navigation_error":"E011",
    "OTP_error":"E012",
    "download_failed":"E013",
    "target_file_not_found":"E014",
    "filter_error":"E015"
}


def setup_logger(script_name):
    global start_times
    # Get current time in CST as a datetime object.
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    start_time = utc_now.astimezone(cst)
    # Create a string representation in a consistent format.
    # For example: "2025-01-31 23:26:49.801"
    start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    start_times[script_name] = start_time_str
    # Reset success/errors from previous runs
    global last_success
    last_success = None
    global last_error
    last_error = None
    print(f"Script {script_name} started at {start_time_str} CST.")
    return script_name


def init_log_entry(script_name):
    """
    Insert an initial log row using the script_name and the saved start_time_str.
    This row is uniquely identified by (script_name, start_time_str).
    """
    try:
        conn = connect_to_db()
        if conn is None:
            return False

        cursor = conn.cursor()
        script_name_val = str(script_name)[:50]
        # Insert initial row using start_time_str.
        cursor.execute(
            """
            INSERT INTO wpo.ops_rpa_script_logs (script_name, start_datetime, end_datetime, error, success)
            VALUES (%s, %s, NULL, NULL, NULL)
            """,
            script_name_val, start_times.get(script_name)
        )
        conn.commit()
        print("Initial log entry inserted in SQL.")
        return True
    except psycopg2.Error as sql_error:
        print(f"SQL Error on init: {sql_error}")
        return False
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def update_log_extra_fields(
    script_name,
    file_status=None,
    file_path=None,
    process_type=None,
    file_report_month=None,
    file_com_month=None,
    company_id=None,
    carrier_id=None,
    product_name=None,
    flow_id=None,
    sub_entity_id=None
):
    """
    Updates the extra columns in ops_rpa_script_logs identified by (script_name, start_datetime).
    """
    try:
        # Fetch the start time from the global start_times dict
        start_time_str = start_times.get(script_name)
        if not start_time_str:
            print(f"== Cannot update log. No start_time found for script: {script_name}")
            return

        conn = connect_to_db()
        if conn is None:
            print("== Unable to connect to DB for updating extra fields.")
            return

        cursor = conn.cursor()

        # Build dynamic SET clauses
        set_clauses = []
        values = []

        if file_status is not None:
            set_clauses.append("file_status = %s")
            values.append(file_status)
        if file_path is not None:
            set_clauses.append("file_path = %s")
            values.append(file_path)
        if process_type is not None:
            set_clauses.append("process_type = %s")
            values.append(process_type)
        if file_report_month is not None:
            set_clauses.append("file_report_month = %s")
            values.append(file_report_month)
        if file_com_month is not None:
            set_clauses.append("file_com_month = %s")
            values.append(file_com_month)
        if company_id is not None:
            set_clauses.append("company_id = %s")
            values.append(company_id)
        if carrier_id is not None:
            set_clauses.append("carrier_id = %s")
            values.append(carrier_id)
        if product_name is not None:
            set_clauses.append("product_name = %s")
            values.append(product_name)
        if flow_id is not None:
            set_clauses.append("flow_id = %s")
            values.append(flow_id)
        if sub_entity_id is not None:
            set_clauses.append("sub_entity_id = %s")
            values.append(sub_entity_id)

        if not set_clauses:
            print("== No extra fields to update.")
            return

        set_clause_str = ", ".join(set_clauses)
        update_query = f"""
            UPDATE wpo.ops_rpa_script_logs
            SET {set_clause_str}
            WHERE script_name = %s AND start_datetime = %s
        """

        values.append(str(script_name)[:50])
        values.append(start_time_str)

        cursor.execute(update_query, tuple(values))
        conn.commit()
        print(f"== Extra log fields updated for {script_name}.")
    except Exception as e:
        print(f"== Error updating extra fields in ops_rpa_script_logs: {e}")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()




def log_success():
    global last_success
    last_success = "Process ran successfully."
    print(f"== Success: {last_success}")


def log_error(error_code, description, script_name):
    global last_error
    last_error = f"Error {error_code}: {description}"
    print(f"Logged error: {last_error}")


def record_carrier_result(carrier, success, message):
    """
    Record the outcome of a carrier's processing.
    """
    global carrier_successes, carrier_errors
    if success:
        carrier_successes.append(f"{carrier}: {message}")
    else:
        carrier_errors.append(f"{carrier}: {message}")


def update_log_to_postgres(script_name, start_datetime_str, end_datetime_str, error, success):
    """
    Update (or insert) the log entry identified by the unique key (script_name, start_datetime_str).
    We pass start_datetime_str and end_datetime_str as strings.
    """
    try:
        conn = connect_to_db()
        if conn is None:
            return False

        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM wpo.ops_rpa_script_logs WHERE script_name = %s AND start_datetime = %s",
            script_name, start_datetime_str
        )
        exists = cursor.fetchone()[0]

        script_name_val = str(script_name)[:50]
        error_val = str(error)[:500] if error else None
        success_val = str(success)[:500] if success else None

        if exists:
            if success_val:
                error_val = None  # Clear error if success is present
            cursor.execute(
                """
                UPDATE wpo.ops_rpa_script_logs
                SET end_datetime = %s,
                    error = %s,
                    success = %s
                WHERE script_name = %s AND start_datetime = %s
                """,
                end_datetime_str, error_val, success_val, script_name_val, start_datetime_str
            )
        else:
            cursor.execute(
                """
                INSERT INTO wpo.ops_rpa_script_logs (script_name, start_datetime, end_datetime, error, success)
                VALUES (%s, %s, %s, %s, %s)
                """,
                script_name_val, start_datetime_str, end_datetime_str, error_val, success_val
            )
        conn.commit()
        print("Log entry successfully updated in SQL.")
        return True
    except pyodbc.Error as sql_error:
        print(f"SQL Error: {sql_error}")
        return False
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


def log_final_entry(script_name):
    """
    Updates the log entry with final values using the unique key (script_name, start_time_str).
    """
    global end_time
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    end_time = utc_now.astimezone(cst)
    end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    update_log_to_postgres(
        script_name,
        start_datetime_str=start_times.get(script_name),
        end_datetime_str=end_time_str,
        error=last_error if last_error else None,
        success=last_success if last_success else None
    )


def log_overall_result(script_name):
    """
    Updates the log entry with a summary of all results using the unique key (script_name, start_time_str).
    This function combines any global errors (last_error) with carrier-specific errors.
    """
    global carrier_successes, carrier_errors, last_error
    # Compute current end time as a string
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    end_time = utc_now.astimezone(cst)
    end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    # Combine errors: first any global error, then any carrier errors
    overall_error = ""
    if last_error:
        overall_error = last_error
    if carrier_errors:
        # Append a separator if needed
        if overall_error:
            overall_error += "\n"
        overall_error += "Carriers failed:\n" + "\n".join(carrier_errors)

    # Combine successes from carriers (if any)
    overall_success = ""
    if carrier_successes:
        overall_success = "Carriers uploaded successfully:\n" + "\n".join(carrier_successes)

    # If no error was recorded at all, then consider the process successful.
    if not overall_error and not overall_success:
        overall_success = "Process ran successfully."

    # Update the log entry (using the global start_time_str from setup_logger)
    update_log_to_postgres(
        script_name,
        start_datetime_str=start_times.get(script_name),
        end_datetime_str=end_time_str,
        error=overall_error if overall_error else None,
        success=overall_success if overall_success else None
    )
