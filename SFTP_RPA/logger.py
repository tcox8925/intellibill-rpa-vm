import datetime
import pytz
from db_connection import connect_to_db
import psycopg2

# ===============================
# GLOBAL VARIABLES
# ===============================

start_times = {}
end_time = None
last_success = None
last_error = None

carrier_successes = []
carrier_errors = []

cst = pytz.timezone("America/Chicago")

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
    "link_not_found": "E010",
    "navigation_error": "E011",
    "OTP_error": "E012",
    "download_failed": "E013",
    "target_file_not_found": "E014",
    "filter_error": "E015"
}


# ===============================
# LOGGER SETUP
# ===============================

def setup_logger(script_name):
    global start_times, last_success, last_error

    utc_now = datetime.datetime.now(datetime.timezone.utc)
    start_time = utc_now.astimezone(cst)
    start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    start_times[script_name] = start_time_str
    last_success = None
    last_error = None

    print(f"Script {script_name} started at {start_time_str} CST.")
    return script_name


# ===============================
# INITIAL LOG INSERT
# ===============================

def init_log_entry(script_name):
    try:
        conn = connect_to_db()
        if conn is None:
            return False

        cursor = conn.cursor()
        script_name_val = str(script_name)[:50]

        cursor.execute(
            """
            INSERT INTO wpo.ops_rpa_script_logs 
            (script_name, start_datetime, end_datetime, error, success)
            VALUES (%s, %s, NULL, NULL, NULL)
            """,
            (
                script_name_val,
                start_times.get(script_name)
            )
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


# ===============================
# UPDATE EXTRA FIELDS
# ===============================

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
    flow_id=None
):
    try:
        start_time_str = start_times.get(script_name)
        if not start_time_str:
            print(f"== Cannot update log. No start_time found for script: {script_name}")
            return

        conn = connect_to_db()
        if conn is None:
            print("== Unable to connect to DB for updating extra fields.")
            return

        cursor = conn.cursor()

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
        print(f"== Error updating extra fields: {e}")

    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


# ===============================
# SUCCESS / ERROR TRACKING
# ===============================

def log_success():
    global last_success
    last_success = "Process ran successfully."
    print(f"== Success: {last_success}")


def log_error(error_code, description, script_name):
    global last_error
    last_error = f"Error {error_code}: {description}"
    print(f"Logged error: {last_error}")


def record_carrier_result(carrier, success, message):
    global carrier_successes, carrier_errors
    if success:
        carrier_successes.append(f"{carrier}: {message}")
    else:
        carrier_errors.append(f"{carrier}: {message}")


# ===============================
# FINAL LOG UPDATE
# ===============================

def update_log_to_postgres(script_name, start_datetime_str, end_datetime_str, error, success):
    try:
        conn = connect_to_db()
        if conn is None:
            return False

        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM wpo.ops_rpa_script_logs WHERE script_name = %s AND start_datetime = %s",
            (script_name, start_datetime_str)
        )

        exists = cursor.fetchone()[0]

        script_name_val = str(script_name)[:50]
        error_val = str(error)[:500] if error else None
        success_val = str(success)[:500] if success else None

        if exists:
            if success_val:
                error_val = None

            cursor.execute(
                """
                UPDATE wpo.ops_rpa_script_logs
                SET end_datetime = %s,
                    error = %s,
                    success = %s
                WHERE script_name = %s AND start_datetime = %s
                """,
                (
                    end_datetime_str,
                    error_val,
                    success_val,
                    script_name_val,
                    start_datetime_str
                )
            )
        else:
            cursor.execute(
                """
                INSERT INTO wpo.ops_rpa_script_logs 
                (script_name, start_datetime, end_datetime, error, success)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    script_name_val,
                    start_datetime_str,
                    end_datetime_str,
                    error_val,
                    success_val
                )
            )

        conn.commit()
        print("Log entry successfully updated in SQL.")
        return True

    except psycopg2.Error as sql_error:
        print(f"SQL Error: {sql_error}")
        return False

    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


def log_final_entry(script_name):
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