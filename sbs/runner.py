import os
from datetime import datetime, timedelta

from utils.db import (
    fetch_unprocessed_states,
    mark_state_success,
    mark_state_failure,
    reset_run_columns,
    insert_rpa_run_log, mark_report_exists,
)

from utils.notifications import (
    send_end_of_process_notification,
    send_invoice_email,
)

from utils.storage import upload_report
from navigation import run_state
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


# Where Playwright downloads SBS reports
DOWNLOAD_DIR = r"C:\Users\myopsadmin\Downloads\sbs"
KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
key_vault_url = os.getenv("KEYVAULT_URL", "")

credential = DefaultAzureCredential()
secret_client = SecretClient(
    vault_url=key_vault_url,
    credential=credential
)

# ==========================================================
# DATE HELPERS
# ==========================================================

def get_week_date_range():
    """
    Returns (from_date, to_date) as strings in MM/DD/YYYY format.
    """
    today = datetime.today().date()
    from_date = today - timedelta(days=6)

    return (
        from_date.strftime("%m/%d/%Y"),
        today.strftime("%m/%d/%Y"),
    )


def format_to_date_for_blob(to_date: str) -> str:
    return to_date.replace("/", "_")


# ==========================================================
# PAYMENT PROFILE (REAL — USED FOR ALL RUNS)
# ==========================================================
card_secret = secret_client.get_secret("sbs-payment").value

PAYMENT_PROFILE = {
    "first_name": "SOFTWARE DEPT. CARD",
    "last_name": "834 LABS",
    "street": "2929 N CENTRAL EXPY",
    "city": "Richardson",
    "state": "Texas",
    "zip": "75080-9998",
    "email": "dataops@834labs.com",
    "phone": "2146758925",
    "card_combined": card_secret,
}




def run_sbs(test_mode: bool = False):
    """
    SBS Agent License Update Runner

    test_mode:
        True  -> run ONE state only
        False -> run ALL unprocessed states

    ALL OTHER BEHAVIOR IS IDENTICAL.
    """

    from_date, to_date = get_week_date_range()
    formatted_to_date = format_to_date_for_blob(to_date)

    print("Starting SBS Agent License Update")
    print(f"Date range: {from_date} → {to_date}")
    print(f"Test mode: {test_mode}")

    states = fetch_unprocessed_states()

    if not states:
        print("ℹNo unprocessed states found.")
        return

    if test_mode:
        states = [states[0]]
        print(f"TEST MODE — running only: {states[0]['jurisdiction']}")

    script_name = "SBS_ALU_Test" if test_mode else "SBS_ALU"

    run_start_time = None
    run_end_time = None
    failure_state = None
    failure_reason = None
    all_states_succeeded = True

    # ======================================================
    # STATE LOOP
    # ======================================================
    for state in states:
        jurisdiction = state["jurisdiction"]
        jur_short = state["jur_short"]

        if run_start_time is None:
            run_start_time = datetime.utcnow()

        print(f"\nProcessing state: {jurisdiction}")

        try:
            # ----------------------------------------------
            # UI FLOW (REAL PAYMENT + REAL DOWNLOAD)
            # ----------------------------------------------
            result = run_state(
                state_row=state,
                from_date=from_date,
                to_date=to_date,
                payment_profile=PAYMENT_PROFILE,
                download_dir=DOWNLOAD_DIR,
            )

            rows_count = result["rows_count"]
            fee_amount = result["fee_amount"]
            pin_number = result.get("pin_number")
            transaction_number = result.get("transaction_number")
            downloaded_file_path = result["downloaded_file_path"]

            # ----------------------------------------------
            # AZURE UPLOAD (REAL FILE)
            # ----------------------------------------------
            if downloaded_file_path == 'NA':
                mark_state_success(
                    jurisdiction=jurisdiction,
                    rows_count=rows_count,
                    fee_amount=fee_amount,
                    pin_number=pin_number,
                    transaction_number=transaction_number,
                    authorization_payment_number=None,
                )
                print(f"Completed state, with no results found: {jurisdiction}")
                continue

            if not os.path.exists(downloaded_file_path):
                raise FileNotFoundError(
                    f"Downloaded report not found: {downloaded_file_path}"
                )

            with open(downloaded_file_path, "rb") as f:
                report_bytes = f.read()

            upload_report(
                jur_short=jur_short,
                formatted_to_date=formatted_to_date,
                report_bytes=report_bytes,
            )

            print(
                f"Uploaded blob: raw/agent_license_update/sbs/"
                f"raw_alu_license_{jur_short}_{formatted_to_date}"
            )

            # ----------------------------------------------
            # MATRIX UPDATE (PER STATE)
            # ----------------------------------------------
            mark_state_success(
                jurisdiction=jurisdiction,
                rows_count=rows_count,
                fee_amount=fee_amount,
                pin_number=pin_number,
                transaction_number=transaction_number,
                authorization_payment_number=None,
            )
            mark_report_exists(jurisdiction)

            print(f"Completed state: {jurisdiction}")

        except Exception as exc:
            all_states_succeeded = False
            failure_state = jurisdiction
            failure_reason = str(exc)

            print(f"Failed state {jurisdiction}: {failure_reason}")

            mark_state_failure(
                jurisdiction=jurisdiction,
                error_message=failure_reason,
                pin_number=pin_number,
                transaction_number=transaction_number,
            )

            if test_mode:
                run_end_time = datetime.utcnow()
                raise

        finally:
            run_end_time = datetime.utcnow()

    # ======================================================
    # RUN-LEVEL LOG (ALWAYS)
    # ======================================================
    if failure_state:
        insert_rpa_run_log(
            script_name=script_name,
            start_datetime=run_start_time,
            end_datetime=run_end_time,
            success_message=None,
            error_message=f"Stopped at {failure_state}: {failure_reason}",
            file_status=None,
            file_path=None,
        )
    else:
        insert_rpa_run_log(
            script_name=script_name,
            start_datetime=run_start_time,
            end_datetime=run_end_time,
            success_message="Process ran successfully",
            error_message=None,
            file_status="Ready",
            file_path="raw/agent_license_update/sbs",
        )

    # ======================================================
    # NOTIFICATIONS (TEST + PROD)
    # ======================================================
    print("Sending end-of-process notification")
    send_end_of_process_notification(from_date, to_date)

    print("Sending invoice email")
    send_invoice_email()

    # ======================================================
    # RESET MATRIX (TEST + PROD)
    # ======================================================
    print("Resetting RPA-managed columns")
    reset_run_columns()

    print("SBS RUN COMPLETE")


# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    run_sbs(test_mode=False)  # set False for full production run
