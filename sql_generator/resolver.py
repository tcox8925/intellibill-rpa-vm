from typing import Dict, Any, List
import psycopg2
from psycopg2.extras import RealDictCursor
from semantic_registry import MODULES, GLOBAL_ENTITIES


class Resolver:

    def __init__(self, connection):
        self.conn = connection
        self.is_postgres = isinstance(connection, psycopg2.extensions.connection)

    # =======================================================
    # 🔹 INTERNAL QUERY EXECUTOR (DUAL ENGINE SAFE)
    # =======================================================

    def _execute(self, sql: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:

        if self.is_postgres:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params or {})
                return cur.fetchall()

        # ---- Synapse / pyodbc ----
        # convert %(name)s → ?
        if params:
            ordered_values = []
            for key in params.keys():
                sql = sql.replace(f"%({key})s", "?")
                ordered_values.append(params[key])
        else:
            ordered_values = []

        with self.conn.cursor() as cur:
            cur.execute(sql, ordered_values)
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    # =======================================================
    # 🔹 LIMIT / TOP HANDLER
    # =======================================================

    def _limit_clause(self, limit: int) -> str:
        if self.is_postgres:
            return f"LIMIT {limit}"
        return f"TOP {limit}"

    # =======================================================
    # 🔹 ILIKE vs LIKE HANDLER
    # =======================================================

    def _like_operator(self) -> str:
        return "ILIKE" if self.is_postgres else "LIKE"

    # =======================================================
    # 🔹 DISPLAY TEMPLATE
    # =======================================================

    def _apply_display_template(self, rows, entity_cfg):
        template = entity_cfg.get("display_template")

        for r in rows:
            if template:
                try:
                    r["display_value"] = template.format(**r)
                except Exception:
                    r["display_value"] = f"{r.get('display_value')} ({r.get('resolve_value')})"
            else:
                r["display_value"] = f"{r.get('display_value')} ({r.get('resolve_value')})"

        return rows

    # =======================================================
    # GLOBAL ENTITY RESOLUTION
    # =======================================================

    def resolve_entity(self, entity_type: str, value: str, limit: int = 10):

        entity_cfg = GLOBAL_ENTITIES.get(entity_type)
        if not entity_cfg:
            return {"resolved": False, "candidates": []}

        table = entity_cfg["table"]
        resolve_key = entity_cfg["primary_key"]
        display_column = entity_cfg["display_column"]

        top_clause = self._limit_clause(limit)
        like_op = self._like_operator()

        # 1️⃣ Exact primary key
        sql = f"""
            SELECT {top_clause}
                {resolve_key} AS resolve_value,
                {display_column} AS display_value
            FROM {table}
            WHERE {resolve_key} = %(value)s
        """ if not self.is_postgres else f"""
            SELECT
                {resolve_key} AS resolve_value,
                {display_column} AS display_value
            FROM {table}
            WHERE {resolve_key} = %(value)s
            LIMIT %(limit)s
        """

        params = {"value": value}
        if self.is_postgres:
            params["limit"] = limit

        rows = self._execute(sql, params)

        if rows:
            rows = self._apply_display_template(rows, entity_cfg)
            if len(rows) == 1:
                return {"resolved": True, "match": rows[0]}
            return {"resolved": False, "candidates": rows}

        # 2️⃣ Partial match
        sql = f"""
            SELECT {top_clause}
                {resolve_key} AS resolve_value,
                {display_column} AS display_value
            FROM {table}
            WHERE {display_column} {like_op} %(value)s
        """ if not self.is_postgres else f"""
            SELECT
                {resolve_key} AS resolve_value,
                {display_column} AS display_value
            FROM {table}
            WHERE {display_column} {like_op} %(value)s
            LIMIT %(limit)s
        """

        params = {"value": f"%{value}%"}
        if self.is_postgres:
            params["limit"] = limit

        rows = self._execute(sql, params)

        if rows:
            rows = self._apply_display_template(rows, entity_cfg)
            if len(rows) == 1:
                return {"resolved": True, "match": rows[0]}
            return {"resolved": False, "candidates": rows}

        return {"resolved": False, "candidates": []}

    # =======================================================
    # CATEGORICAL DISTINCT RESOLUTION
    # =======================================================

    def resolve_categorical_value(
            self,
            table: str,
            column: str,
            value: str,
            limit: int = 100
    ) -> Dict[str, Any]:

        if not value or not isinstance(value, str):
            return {"resolved": False, "candidates": []}

        value_clean = value.strip()

        # Build engine-safe DISTINCT query
        if self.is_postgres:
            distinct_query = f"""
                SELECT DISTINCT {column} AS display_value
                FROM {table}
                WHERE {column} IS NOT NULL
                  AND TRIM({column}) <> ''
                LIMIT %(limit)s
            """
            rows = self._execute(distinct_query, {"limit": limit})
        else:
            distinct_query = f"""
                SELECT DISTINCT TOP {limit} {column} AS display_value
                FROM {table}
                WHERE {column} IS NOT NULL
                  AND LTRIM(RTRIM({column})) <> ''
            """
            rows = self._execute(distinct_query)

        distinct_values = [r["display_value"] for r in rows if r.get("display_value")]

        if not distinct_values:
            return {"resolved": False, "candidates": []}

        # 1️⃣ Exact match
        exact_matches = [
            v for v in distinct_values
            if v.strip().lower() == value_clean.lower()
        ]

        if len(exact_matches) == 1:
            return {"resolved": True, "match": {"resolve_value": exact_matches[0]}}

        if len(exact_matches) > 1:
            return {
                "resolved": False,
                "candidates": [
                    {"resolve_value": v, "display_value": v}
                    for v in exact_matches
                ]
            }

        # 2️⃣ Partial match
        partial_matches = [
            v for v in distinct_values
            if value_clean.lower() in v.lower()
        ]

        if len(partial_matches) == 1:
            return {"resolved": True, "match": {"resolve_value": partial_matches[0]}}

        if len(partial_matches) > 1:
            return {
                "resolved": False,
                "candidates": [
                    {"resolve_value": v, "display_value": v}
                    for v in partial_matches
                ]
            }

        # 3️⃣ No match → show top suggestions
        return {
            "resolved": False,
            "candidates": [
                {"resolve_value": v, "display_value": v}
                for v in distinct_values[:10]
            ]
        }