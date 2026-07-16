from utils.db_utils import get_postgres_connection
from utils import upload_utils
from uuid import uuid4

def check_deactivation_status(npi: str, txn_id_provider: str, dry_run: bool = False):
    """
    Query PostgreSQL wpo.npi_registry table for the given NPI,
    apply deactivation logic, and insert validation + fail detail.
    """

    conn = get_postgres_connection()
    cur = conn.cursor()

    sql = """
        SELECT 
            "NPI Deactivation Date",
            "NPI Reactivation Date"
        FROM wpo.npi_registry
        WHERE "NPI" = %s
    """

    cur.execute(sql, (npi,))
    row = cur.fetchone()

    status = "Pass"
    deact_date = None

    if row:
        deact_date, react = row
        if deact_date:
            status = "Fail"

    validation_result = {
        "source": "NPI_DEACTIVATION",
        "status": status
    }

    if dry_run:
        print(f"[DRY RUN] Deactivation check for NPI {npi}: {validation_result}")
        return validation_result

    # Insert into regulatory validation
    reg_txn_ids = upload_utils.upload_regulatory_validation(
        txn_id_provider, 
        [validation_result]
    )
    print(f"[UPLOAD] NPI {npi}: {validation_result}")

    # Insert fail detail if needed
    if status == "Fail":
        txn_id_reg = reg_txn_ids.get("NPI_DEACTIVATION")
        if txn_id_reg:
            fail_row = [{
                "txn_id_reg": txn_id_reg,
                "check_type": "NPI_DEACTIVATION",
                "description": "NPI has been deactivated in NPI Registry.",
                "action_date": deact_date.isoformat() if hasattr(deact_date, "isoformat") else deact_date,
                "source": "NPI Registry"
            }]
            upload_utils.upload_regulatory_fail_details(txn_id_provider, fail_row)
            print(f"[DETAILS] NPI {npi}: Deactivation details inserted")

    return validation_result