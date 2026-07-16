# executor.py

from typing import Dict, Any, List
from sql_validator import validate_sql
from db_executor import (
    get_postgres_connection,
    get_synapse_connection,
)


class QueryExecutor:

    def __init__(self, statement_timeout_ms: int = 15000):
        self.statement_timeout_ms = statement_timeout_ms

    def execute(
        self,
        module: str,
        sql: str,
        params: List[Any]
    ) -> List[Dict[str, Any]]:

        # -------------------------------------------------
        # 1️⃣ Validate SQL
        # -------------------------------------------------
        validate_sql(sql)

        # -------------------------------------------------
        # 2️⃣ Route by module
        # -------------------------------------------------
        if module == "bob":
            return self._execute_synapse(sql, params)

        return self._execute_postgres(sql, params)

    # -------------------------------------------------
    # POSTGRES EXECUTION
    # -------------------------------------------------

    def _execute_postgres(
        self,
        sql: str,
        params: List[Any]
    ) -> List[Dict[str, Any]]:

        conn = get_postgres_connection()

        try:
            with conn.cursor() as cur:
                # enforce timeout + read-only
                cur.execute("SET LOCAL statement_timeout = %s;", (self.statement_timeout_ms,))
                cur.execute("SET LOCAL TRANSACTION READ ONLY;")

                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

                return [dict(zip(columns, row)) for row in rows]

        finally:
            conn.close()

    # -------------------------------------------------
    # SYNAPSE EXECUTION
    # -------------------------------------------------

    def _execute_synapse(
            self,
            sql: str,
            params: List[Any]
    ) -> List[Dict[str, Any]]:

        conn = get_synapse_connection()

        try:
            cursor = conn.cursor()

            if params:
                # 🔥 Convert Postgres-style %s to pyodbc ? placeholders
                placeholder_count = sql.count("%s")

                if placeholder_count != len(params):
                    raise ValueError(
                        f"Parameter mismatch: SQL expects {placeholder_count}, "
                        f"but {len(params)} parameters were supplied."
                    )

                sql = sql.replace("%s", "?")

                cursor.execute(sql, params)
            else:
                cursor.execute(sql)

            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()

            return [dict(zip(columns, row)) for row in rows]

        finally:
            conn.close()