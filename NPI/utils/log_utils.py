from contextlib import closing
from datetime import datetime, timezone
from typing import Optional

# ⚠️ After migration, import your Postgres connection instead
# from utils.db_utils import get_postgres_connection
from utils.db_utils import get_postgres_connection

TABLE = "wpo.ops_pch_logs"

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _next_log_id(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(log_id), 0) + 1 FROM wpo.ops_pch_logs;")
        return cur.fetchone()[0]


def log_start(
    *,
    txn_id: str,
    script_name: str,
    process_type: str,
    file_path: Optional[str] = None,
    company_id: Optional[str] = None,
    carrier_id: Optional[str] = None,
) -> None:
    """
    Insert a STARTED log row with next log_id.
    """
    sql = f"""
    INSERT INTO {TABLE}
      (log_id, txn_id, script_name, process_type, status, error,
       company_id, carrier_id, file_path, started_at, created_at)
    VALUES
      (%s, %s, %s, %s, 'STARTED', NULL, %s, %s, %s, %s, %s);
    """

    now = _utcnow()
    with closing(get_postgres_connection()) as conn, closing(conn.cursor()) as cur:
        log_id = _next_log_id(conn)  # ✅ fetch next log_id

        cur.execute(sql, (
            log_id,
            txn_id,
            script_name,
            process_type,
            company_id,
            carrier_id,
            file_path,
            now,
            now
        ))
        conn.commit()


def log_end(*, txn_id: str, success: bool, error: Optional[str] = None, is_caqh: bool = False) -> None:
    """
    Mark most recent STARTED row for txn_id as SUCCESS/FAILED.
    Only update wpo.pch_provider_info.caqh_status when is_caqh=True (i.e. the job
    was an actual CAQH lookup). Other modules (NPI/BOARD/OIG) must not touch it.
    If no log row exists, insert a terminal row.
    """
    status = "SUCCESS" if success else "FAILED"
    caqh_status = True if success else False

    err = (error or "")
    if len(err) > 3800:
        err = err[:3800] + "\n..."

    now = _utcnow()
    with closing(get_postgres_connection()) as conn, closing(conn.cursor()) as cur:

        # ----------------------------
        # 1) Update logs
        # ----------------------------
        sql_update = f"""
        UPDATE {TABLE}
           SET status = %s,
               ended_at = %s,
               error = CASE WHEN %s = '' THEN error ELSE %s END
        WHERE log_id = (
            SELECT log_id FROM {TABLE}
            WHERE txn_id = %s
            ORDER BY started_at DESC, log_id DESC
            LIMIT 1
        );
        """
        cur.execute(sql_update, (status, now, err, err, txn_id))
        updated = cur.rowcount

        if updated == 0:
            sql_insert = f"""
            INSERT INTO {TABLE}
              (txn_id, script_name, process_type, status, error,
               company_id, carrier_id, file_path, started_at, ended_at, created_at)
            VALUES
              (%s, %s, %s, %s, %s, NULL, NULL, NULL, %s, %s, %s);
            """
            cur.execute(sql_insert, (
                txn_id, "run_npi_scrape", "NPI Scrape",
                status, err,
                now, now, now
            ))

        # ----------------------------
        # 2) Update provider caqh_status (CAQH jobs only)
        # ----------------------------
        if is_caqh:
            sql_provider = """
            UPDATE wpo.pch_provider_info
               SET caqh_status = %s,
                   updated_on = %s
             WHERE txn_id = %s;
            """
            cur.execute(sql_provider, (caqh_status, now, txn_id))

        conn.commit()