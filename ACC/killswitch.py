# ==========================================================
#  killswitch.py
# ==========================================================
"""
killswitch.py
-------------
Standalone script to trigger a controlled shutdown for the ACC RPA process.

⚙️  Behavior:
    • Sets all active carriers (active_flag = '1') in [raw].[ops_acc_process_matrix] to 0.
    • Optionally restricts by carrier_id list.
    • Uses db_utils.get_postgres_connection() for DB access.
    • Designed to be called manually or by Power Automate.


✅  Result:
    The runner will detect all carriers inactive and perform a graceful shutdown.

✅    Usage :
    • certain carrier kill : python killswitch.py CIGNA AMBETTER
    • global kill : python killswitch.py

"""

import sys
from utils import db_utils


# ==========================================================
#  MAIN EXECUTION
# ==========================================================
def deactivate_all_carriers(conn, carrier_ids=None):
    """
    Set active_flag = '0' in the process matrix.
    If carrier_ids list is provided, only those carriers are deactivated.
    """
    cursor = conn.cursor()

    if carrier_ids:
        placeholders = ", ".join(["%s"] * len(carrier_ids))
        query = f"""
            UPDATE wpo.ops_acc_process_matrix
            SET active_flag = '0',
                last_error = 'System kill triggered manually',
                notes = 'Killswitch executed'
            WHERE carrier_id IN ({placeholders}) AND active_flag = '1'
        """
        cursor.execute(query, carrier_ids)
    else:
        query = """
            UPDATE wpo.ops_acc_process_matrix
            SET active_flag = '0',
                last_error = 'System kill triggered manually',
                notes = 'Killswitch executed'
            WHERE active_flag = '1'
        """
        cursor.execute(query)

    conn.commit()
    print("🔴 Killswitch executed — all active carriers disabled.")


def main():
    """
    Entry point for command line or Power Automate execution.
    Example:
        python killswitch.py
        python killswitch.py CIGNA CARESOURCE
    """
    try:
        carrier_ids = sys.argv[1:] if len(sys.argv) > 1 else None

        conn = db_utils.get_postgres_connection()
        deactivate_all_carriers(conn, carrier_ids)
        conn.close()

        print("✅ Database updated successfully.")
    except Exception as e:
        print(f"❌ Failed to execute killswitch: {e}")


# ==========================================================
#  ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    main()
