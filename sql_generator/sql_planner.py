from typing import Tuple, Dict, Any, List
import re
from semantic_registry import MODULES, DEFAULT_LIMIT, FORCE_LIMIT, GLOBAL_ENTITIES
from intent_models import QueryIntent
from datetime import datetime
from dateutil.relativedelta import relativedelta

def _qualify_metric_expression(expr: str, table_alias: str, db_type: str) -> str:
    e = expr.strip()

    # -------------------------------------------------
    # COUNT(*)
    # -------------------------------------------------
    if re.fullmatch(r"COUNT\s*\(\s*\*\s*\)", e, flags=re.IGNORECASE):
        return e

    # -------------------------------------------------
    # COUNT(DISTINCT col)
    # -------------------------------------------------
    m = re.fullmatch(
        r"COUNT\s*\(\s*DISTINCT\s+([A-Za-z_][A-Za-z0-9_]*)\s*\)",
        e,
        flags=re.IGNORECASE
    )
    if m:
        col = m.group(1)
        return f"COUNT(DISTINCT {table_alias}.{col})"

    # -------------------------------------------------
    # SUM / AVG / MIN / MAX
    # -------------------------------------------------
    m = re.fullmatch(
        r"(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
        e,
        flags=re.IGNORECASE
    )
    if m:
        func = m.group(1).upper()
        col = m.group(2)

        # 🔥 Cast for numeric aggregations
        if func in ("SUM", "AVG"):

            if db_type == "postgres":
                return (
                    f"{func}("
                    f"COALESCE(NULLIF({table_alias}.{col}, '')::NUMERIC, 0)"
                    f")"
                )

            else:  # synapse
                return (
                    f"{func}("
                    f"COALESCE(NULLIF({table_alias}.{col}, ''), 0)"
                    f")"
                )

        # MIN/MAX usually safe as-is
        return f"{func}({table_alias}.{col})"

    return e


# -------------------------------------------------
# WHERE CLAUSE BUILDER (DB AWARE)
# -------------------------------------------------
def _build_where_clause(
    filters: Dict[str, Any],
    filters_map: Dict[str, Dict],
    table_alias: str,
    db_type: str
) -> Tuple[str, List[Any]]:

    clauses: List[str] = []
    ordered_params: List[Any] = []

    # 🔥 Decide placeholder once
    placeholder = "%s" if db_type == "postgres" else "?"

    for key, value in filters.items():

        if key not in filters_map:
            raise ValueError(f"Invalid filter: {key}")

        filter_cfg = filters_map[key]
        column = filter_cfg.get("column")
        filter_type = filter_cfg.get("type")

        # Skip logical/dynamic filters
        if filter_type.startswith("dynamic_") or filter_type == "logical_time":
            continue

        if not column:
            continue

        # =====================================================
        # MULTI SELECT
        # =====================================================
        if isinstance(value, list):

            if not value:
                continue

            placeholders = ", ".join([placeholder] * len(value))

            clauses.append(
                f"LOWER(TRIM({table_alias}.{column})) IN ({placeholders})"
            )

            ordered_params.extend([str(v).lower() for v in value])
            continue

        # =====================================================
        # MONTH YEAR (YYYY-MM)
        # =====================================================
        if filter_type == "month_year":
            year, month = value.split("-")

            if db_type == "postgres":
                first_day = f"{year}-{month}-01"

                clauses.append(
                    f"{table_alias}.{column}::date >= {placeholder} "
                    f"AND {table_alias}.{column}::date < ({placeholder}::date + INTERVAL '1 month')"
                )

            else:
                from datetime import date
                first_day = date(int(year), int(month), 1)

                clauses.append(
                    f"{table_alias}.{column} >= {placeholder} "
                    f"AND {table_alias}.{column} < DATEADD(month, 1, {placeholder})"
                )

            ordered_params.append(first_day)
            ordered_params.append(first_day)
            continue

        # =====================================================
        # EXACT MATCH TYPES
        # =====================================================
        if filter_type in ("exact_text", "categorical_strict", "boolean_flag", "state_flag"):

            clauses.append(
                f"LOWER(TRIM({table_alias}.{column})) = LOWER(TRIM({placeholder}))"
            )

            ordered_params.append(value)
            continue

        # =====================================================
        # TOKEN / LIKE
        # =====================================================
        if filter_type == "categorical_token":

            clauses.append(
                f"LOWER(TRIM({table_alias}.{column})) LIKE {placeholder}"
            )

            ordered_params.append(f"%{str(value).lower()}%")
            continue

        # =====================================================
        # NUMERIC
        # =====================================================
        if filter_type == "numeric":

            clauses.append(
                f"{table_alias}.{column} = {placeholder}"
            )

            ordered_params.append(value)
            continue

        # =====================================================
        # DATE
        # =====================================================
        if filter_type == "date":

            clauses.append(
                f"{table_alias}.{column} = {placeholder}"
            )

            ordered_params.append(value)
            continue

        raise ValueError(f"Unsupported filter type: {filter_type}")

    if not clauses:
        return "", []

    return " AND ".join(clauses), ordered_params


# -------------------------------------------------
# MAIN QUERY BUILDER
# -------------------------------------------------

def build_query(intent: QueryIntent) -> Tuple[str, List[Any]]:

    if intent.module not in MODULES:
        raise ValueError(f"Unsupported module: {intent.module}")

    module_cfg = MODULES[intent.module]
    base_table = module_cfg["table"]
    table_alias = "t"

    db_type = "synapse" if intent.module == "bob" else "postgres"

    metrics = module_cfg["metrics"]
    dimensions = module_cfg["dimensions"]
    filters_cfg = module_cfg["filters"]

    metric_cfg = metrics[intent.metric]
    is_list = bool(metric_cfg.get("is_list", False))

    select_parts: List[str] = []
    group_parts: List[str] = []

    # -------------------------------------------------
    # SELECT
    # -------------------------------------------------

    if db_type == "synapse" and FORCE_LIMIT:
        limit = intent.limit or DEFAULT_LIMIT
        select_prefix = f"SELECT TOP {int(limit)} "
    else:
        select_prefix = "SELECT "

    if is_list:
        if intent.explicit_dimensions and intent.dimensions:
            for dim in intent.dimensions:
                col = dimensions[dim]
                select_parts.append(f"{table_alias}.{col}")
        else:
            select_parts.append(f"{table_alias}.*")
    else:
        expr = _qualify_metric_expression(metric_cfg["expression"], table_alias, db_type)
        select_parts.append(expr + " AS value")

        for dim in intent.dimensions:
            col = dimensions[dim]
            select_parts.insert(0, f"{table_alias}.{col}")
            group_parts.append(f"{table_alias}.{col}")

    # -------------------------------------------------
    # RELATIVE TIME RESOLUTION
    # -------------------------------------------------

    if "relative_time" in intent.filters:

        today = datetime.today()

        relative_value = intent.filters["relative_time"]

        if relative_value == "this_month":
            resolved_month = today.strftime("%Y-%m")

        elif relative_value == "last_month":
            resolved_month = (today - relativedelta(months=1)).strftime("%Y-%m")

        elif relative_value == "this_year":
            resolved_month = today.strftime("%Y")

        elif relative_value == "last_year":
            resolved_month = (today - relativedelta(years=1)).strftime("%Y")

        else:
            resolved_month = None

        # Apply to correct column based on module
        if resolved_month:

            if intent.module == "bob":
                intent.filters["report_date"] = resolved_month

            elif intent.module == "commission_items":
                intent.filters["coverage_month"] = resolved_month

            elif intent.module == "commission_totals":
                intent.filters["statement_month"] = resolved_month

        # Remove logical filter
        del intent.filters["relative_time"]

    # -------------------------------------------------
    # AUTO LATEST TIME LOGIC
    # -------------------------------------------------

    auto_time_clause = ""

    if intent.module == "bob":
        if  "report_date" not in intent.filters:
            auto_time_clause = (
                f"{table_alias}.report_date = "
                f"(SELECT MAX(report_date) FROM {base_table})"
            )
            intent.auto_time_applied = True

    elif intent.module == "commission_items":
        if "coverage_month" not in intent.filters and "report_date" not in intent.filters:
            auto_time_clause = (
                f"{table_alias}.coverage_month = "
                f"(SELECT MAX(coverage_month) FROM {base_table})"
            )
            intent.auto_time_applied = True

    elif intent.module == "commission_totals":
        if "statement_month" not in intent.filters and "statement_date" not in intent.filters:
            auto_time_clause = (
                f"{table_alias}.statement_month = "
                f"(SELECT MAX(statement_month) FROM {base_table})"
            )
            intent.auto_time_applied = True

    # -------------------------------------------------
    # WHERE
    # -------------------------------------------------
    print("==== FILTERS BEFORE WHERE BUILD ====")
    print(intent.filters)
    print("==== FILTER TYPES ====")
    print(filters_cfg)

    where_sql, ordered_params = _build_where_clause(
        intent.filters,
        filters_cfg,
        table_alias,
        db_type
    )

    # Merge auto time clause
    if auto_time_clause:
        if where_sql:
            where_sql = f"{where_sql} AND {auto_time_clause}"
        else:
            where_sql = auto_time_clause

    # -------------------------------------------------
    # FINAL SQL
    # -------------------------------------------------

    sql = f"{select_prefix}{', '.join(select_parts)} FROM {base_table} {table_alias} "

    if where_sql:
        sql += f"WHERE {where_sql} "

    if group_parts and not is_list:
        sql += f"GROUP BY {', '.join(group_parts)} "

    if db_type == "postgres" and FORCE_LIMIT:
        limit = intent.limit or DEFAULT_LIMIT
        sql += f"LIMIT {int(limit)}"

    if db_type == "postgres":
        return sql.strip(), ordered_params

    return sql.strip(), ordered_params
