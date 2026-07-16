"""
JAI Format Engine - Decides how to present query results.

Rules:
- Single scalar value -> kpi
- Grouped data <= 10 groups -> bar_chart or pie_chart (based on LLM hint)
- Grouped data > 10 groups -> table
- List of records -> table
- 2+ dimensions -> table
- > 200 rows -> truncated table with total_rows count
"""

import hashlib
import json
import logging
import re
from datetime import datetime as dt
from typing import Dict, Any, List, Optional

from app.utils.jay.pipeline_models import DashboardSection, PipelineContext

logger = logging.getLogger(__name__)

MAX_CHART_GROUPS = 10
MAX_CHART_GROUPS_TEMPORAL = 60  # Line charts handle many more data points
MAX_INLINE_ROWS = 200

# Dimension names that represent time-series data
_TIME_DIMENSION_NAMES = {
    "statement_month", "statement_date", "coverage_month",
    "report_date", "report_mon_year", "recorded_at",
    "month", "quarter", "year", "year_quarter",
}


def compute_sql_fingerprint(sql: str, params: List[Any], module: str) -> str:
    """Generate a stable fingerprint for a SQL query.

    Used for favorites re-execution. The fingerprint is based on
    the SQL template and module (not param values, since those
    change with permissions).
    """
    # Normalize: strip whitespace, lowercase
    normalized = " ".join(sql.lower().split())
    content = f"{module}:{normalized}"
    return hashlib.sha256(content.encode()).hexdigest()[:64]


def decide_format(
    raw_data: List[Dict[str, Any]],
    format_hint: str = "auto",
    dimensions: List[str] = None,
    metric: str = None,
    dimension_configs: Dict[str, Any] = None,
    context: PipelineContext = None,
) -> Dict[str, Any]:
    """Decide the display format and shape the data accordingly.

    Args:
        raw_data: Query result rows
        format_hint: LLM suggested format (auto, bar_chart, pie_chart, line_chart, table, text, kpi)
        dimensions: Dimensions used in the query
        metric: Metric used in the query
        dimension_configs: Module dimension configurations (for detecting temporal dimensions)

    Returns:
        {
            "format": "text" | "bar_chart" | "pie_chart" | "line_chart" | "table" | "kpi" | "error",
            "data": { ... shaped data ... },
        }
    """
    if not raw_data:
        # For existential queries with no results, provide a clear "not found" message
        query_type = _get_query_type(context)
        if query_type == "existential":
            if context is not None:
                context.add("format", "format_decision", "existential query — no results")
            return {
                "format": "text",
                "data": {},
                "existential_message": "No matching results found for your search.",
            }
        return {
            "format": "text",
            "data": None,
        }

    dimensions = dimensions or []
    dimension_configs = dimension_configs or {}
    is_list = metric and metric == "list_records"
    num_rows = len(raw_data)
    num_columns = len(raw_data[0]) if raw_data else 0

    result = None
    format_reason = ""

    # Check if this is an existential query (yes/no existence check)
    query_type = _get_query_type(context)
    is_existential = query_type == "existential"

    # Derive a meaningful KPI label from context when the SQL alias is generic
    _GENERIC_KPI_ALIASES = {"value", "count", "result", "total", "cnt", "sum"}
    _kpi_label_from_ctx = (
        context.user_summary if context and getattr(context, "user_summary", None) else None
    )

    # Existential query with small result set -> list format
    if is_existential and num_rows <= 10 and num_columns <= 3:
        format_reason = f"Existential query with {num_rows} match(es) -> list table"
        match_word = "match" if num_rows == 1 else "matches"
        result = _format_as_table(raw_data, num_rows)
        result["existential_message"] = f"Yes, I found {num_rows} {match_word}:"
        # Log and return early — existential queries always show matches as table
        if context is not None:
            context.add("format", "format_decision", format_reason)
        return result

    # Single scalar value -> KPI
    if num_rows == 1 and num_columns == 1:
        key = list(raw_data[0].keys())[0]
        value = raw_data[0][key]
        format_reason = "Single scalar value -> kpi"
        label = (
            _kpi_label_from_ctx
            if key.lower() in _GENERIC_KPI_ALIASES and _kpi_label_from_ctx
            else _humanize_column(key)
        )
        result = {
            "format": "kpi",
            "data": {
                "kpi_value": _format_number(value),
                "kpi_label": label,
            },
        }

    # Single row with "value" column and dimension -> KPI
    elif num_rows == 1 and "value" in raw_data[0] and len(dimensions) == 0:
        format_reason = "Single row with value column -> kpi"
        result = {
            "format": "kpi",
            "data": {
                "kpi_value": _format_number(raw_data[0]["value"]),
                "kpi_label": _kpi_label_from_ctx or "Result",
            },
        }

    # List records -> table
    elif is_list:
        format_reason = "List records -> table"
        result = _format_as_table(raw_data, num_rows)

    # Grouped data with dimension + value
    elif len(dimensions) == 1 and "value" in raw_data[0]:
        dim_col = list(raw_data[0].keys())[0]  # First column is dimension

        # Use higher threshold for temporal dimensions (line charts handle many points)
        is_temporal = _is_temporal_dimension(dimensions[0], dimension_configs, raw_data)
        max_groups = MAX_CHART_GROUPS_TEMPORAL if is_temporal else MAX_CHART_GROUPS

        if num_rows <= max_groups:
            # Chart format (temporal data auto-selects line_chart)
            chart_format = _pick_chart_format(format_hint, dimensions, dimension_configs, raw_data)
            # Use quarter labels if all data points are quarter-start dates
            use_quarter = _is_quarter_series(raw_data, dim_col)
            fmt_fn = _format_as_quarter if use_quarter else _clean_datetime
            labels = [fmt_fn(str(row.get(dim_col, ""))) for row in raw_data]
            values = [_to_number(row.get("value", 0)) for row in raw_data]

            is_quarter_label = "Quarter labels" if use_quarter else "Standard labels"
            format_reason = f"{num_rows} rows with {dimensions[0]} dimension -> {chart_format} ({is_quarter_label})"
            result = {
                "format": chart_format,
                "data": {
                    "labels": labels,
                    "values": values,
                    "series_name": _humanize_column("value"),
                },
            }
        else:
            format_reason = f"{num_rows} groups exceeds chart threshold ({max_groups}) -> table"
            result = _format_as_table(raw_data, num_rows)

    # 2+ dimensions -> always table
    elif len(dimensions) >= 2:
        format_reason = f"{len(dimensions)} dimensions -> table"
        result = _format_as_table(raw_data, num_rows)

    else:
        # Fallback: table
        format_reason = "Fallback -> table"
        result = _format_as_table(raw_data, num_rows)

    # Log format decision to pipeline context
    if context is not None:
        context.add("format", "format_decision", format_reason)

    return result


# ---------------------------------------------------------------------------
# Module -> navigation route mapping
# ---------------------------------------------------------------------------
_MODULE_ROUTES: Dict[str, Dict[str, str]] = {
    "agents": {"route": "/agility-crm/agent-management", "label": "Agent Management"},
    "commission_items": {"route": "/agility-crm/commissions", "label": "Commissions"},
    "commission_totals": {"route": "/agility-crm/commissions", "label": "Commission Totals"},
    "pch_providers": {"route": "/pch-crm/providers", "label": "Providers"},
    "pch_members": {"route": "/pch-crm/members", "label": "Members"},
    "bob": {"route": "/agility-crm/data-analytics", "label": "Data Analytics"},
    "licenses": {"route": "/agility-crm/agent-management", "label": "Agent Licenses"},
    "membercare_assessments": {"route": "/service-performance", "label": "Service Performance"},
}


def build_dashboard_sections(
    raw_data: List[Dict[str, Any]],
    format_hint: str = "auto",
    dimensions: List[str] = None,
    dimension_configs: Dict[str, Any] = None,
    context: PipelineContext = None,
    module: str = None,
    user_query: str = None,
    assumption: str = None,
    resolved_entities: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a multi-section dashboard from query results.

    Creates multiple complementary views of the same data:
    1. KPI cards for key metrics (totals, counts, averages)
    2. A chart visualization (auto-selected based on data shape)
    3. A summary table (top entries)
    4. Navigation links to relevant pages

    Returns:
        {
            "format": "dashboard",
            "data": {
                "summary": "Rich markdown summary...",
                "sections": [
                    {"title": "...", "format": "kpi", "data": {...}, "description": "..."},
                    {"title": "...", "format": "bar_chart", "data": {...}, "description": "..."},
                    {"title": "...", "format": "table", "data": {...}, "description": "..."},
                ],
                "navigation_links": [
                    {"label": "View All Agents", "route": "/agility-crm/agent-management", "description": "See full agent list"},
                ]
            }
        }
    """
    dimensions = dimensions or []
    dimension_configs = dimension_configs or {}

    # --- Edge case: empty or None data ---
    if not raw_data:
        return {
            "format": "dashboard",
            "data": {
                "summary": "No data available for this query.",
                "sections": [],
                "navigation_links": _build_navigation_links(module),
            },
        }

    columns = list(raw_data[0].keys())

    # Classify columns into numeric (metrics) and text (dimensions)
    # Skip ID-like columns from metrics to avoid meaningless KPIs
    _ID_KEYWORDS = ("_id", "id_", "pk", "key", "npn", "npi", "ssn", "ein", "fein", "tin", "uuid", "guid")
    numeric_cols: List[str] = []
    text_cols: List[str] = []
    for col in columns:
        col_lower = col.lower()
        is_id = any(kw in col_lower for kw in _ID_KEYWORDS) or col_lower in ("id", "npn", "npi", "ssn")
        if _column_is_numeric(raw_data, col) and not is_id:
            numeric_cols.append(col)
        else:
            text_cols.append(col)

    sections: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 1. KPI sections — individual cards per numeric column, max 4
    #    Each uses {kpi_value, kpi_label} to match JayKpiData on frontend
    # ------------------------------------------------------------------
    for col in numeric_cols[:4]:
        values = [_to_number(row.get(col)) for row in raw_data if row.get(col) is not None]
        if not values:
            continue
        total = sum(values)
        count = len(values)
        avg = total / count if count else 0
        col_label = _humanize_column(col)
        # Avoid "Total Total Premium" when column already starts with "total"
        kpi_label = col_label if col_label.lower().startswith("total") else f"Total {col_label}"
        sections.append({
            "title": col_label,
            "format": "kpi",
            "data": {
                "kpi_value": _format_number(total),
                "kpi_label": kpi_label,
            },
            "description": (
                f"Avg: {_format_number(avg)} per {_naturalize_column(text_cols[0])}"
                if text_cols
                else f"Avg: {_format_number(avg)} across {_format_number(count)} records"
            ),
        })

    # ------------------------------------------------------------------
    # 2. Chart section — delegate to decide_format for best chart type,
    #    falling back to extracting a chartable subset if result is table
    # ------------------------------------------------------------------
    chart_section = _build_chart_section(
        raw_data, format_hint, dimensions, dimension_configs, context,
        numeric_cols, text_cols,
    )
    if chart_section is not None:
        sections.append(chart_section)

    # ------------------------------------------------------------------
    # 3. Table section — top 10 rows as a preview
    # ------------------------------------------------------------------
    preview_rows = raw_data[:10]
    table_result = _format_as_table(preview_rows, len(raw_data))
    sections.append({
        "title": "Data Preview",
        "format": "table",
        "data": table_result["data"],
        "description": f"Showing top {len(preview_rows)} of {_format_number(len(raw_data))} rows.",
    })

    # ------------------------------------------------------------------
    # 4. Summary — rich text with key insights
    # ------------------------------------------------------------------
    summary = _build_dashboard_summary(raw_data, numeric_cols, text_cols, user_query, assumption)

    # ------------------------------------------------------------------
    # 5. Navigation links
    # ------------------------------------------------------------------
    navigation_links = _build_navigation_links(module, resolved_entities)

    # Log to pipeline context
    if context is not None:
        context.add("format", "format_decision", f"dashboard with {len(sections)} sections")

    return {
        "format": "dashboard",
        "data": {
            "summary": summary,
            "sections": sections,
            "navigation_links": navigation_links,
        },
    }


def _column_is_numeric(raw_data: List[Dict[str, Any]], col: str) -> bool:
    """Check whether a column contains predominantly numeric values."""
    sample = raw_data[:20]  # Check first 20 rows
    numeric_count = 0
    non_null_count = 0
    for row in sample:
        val = row.get(col)
        if val is None:
            continue
        non_null_count += 1
        try:
            float(val)
            numeric_count += 1
        except (ValueError, TypeError):
            pass
    if non_null_count == 0:
        return False
    return numeric_count / non_null_count >= 0.8


def _build_chart_section(
    raw_data: List[Dict[str, Any]],
    format_hint: str,
    dimensions: List[str],
    dimension_configs: Dict[str, Any],
    context: PipelineContext,
    numeric_cols: List[str],
    text_cols: List[str],
) -> Optional[Dict[str, Any]]:
    """Build the chart section for a dashboard.

    First tries decide_format(); if the result is a chart, uses it directly.
    If decide_format() yields a table (too many groups), extracts the top 10
    rows by the first numeric column and builds a bar chart.
    """
    # Try decide_format with existing dimensions
    fmt_result = decide_format(
        raw_data, format_hint, dimensions,
        dimension_configs=dimension_configs, context=context,
    )

    chart_fmt = fmt_result.get("format", "")

    if chart_fmt in ("bar_chart", "pie_chart", "line_chart"):
        return {
            "title": "Visualization",
            "format": chart_fmt,
            "data": fmt_result["data"],
            "description": "Chart generated from query results.",
        }

    # decide_format returned table or kpi — try to extract a chartable subset
    if not numeric_cols or not text_cols:
        return None

    dim_col = text_cols[0]
    val_col = numeric_cols[0]

    # Sort by the first numeric column descending and take top 10
    try:
        sorted_data = sorted(
            raw_data,
            key=lambda r: _to_number(r.get(val_col, 0)),
            reverse=True,
        )
    except Exception:
        sorted_data = raw_data

    top_data = sorted_data[:10]
    if len(top_data) < 2:
        return None

    labels = [str(row.get(dim_col, "")) for row in top_data]
    values = [_to_number(row.get(val_col, 0)) for row in top_data]

    chart_format = _pick_chart_format(format_hint, [dim_col], dimension_configs, top_data)

    return {
        "title": f"Top {len(top_data)} by {_humanize_column(val_col)}",
        "format": chart_format,
        "data": {
            "labels": labels,
            "values": values,
            "series_name": _humanize_column(val_col),
        },
        "description": f"Top entries ranked by {_humanize_column(val_col).lower()}.",
    }


def _build_dashboard_summary(
    raw_data: List[Dict[str, Any]],
    numeric_cols: List[str],
    text_cols: List[str],
    user_query: str = None,
    assumption: str = None,
) -> str:
    """Build a rich, context-aware markdown summary of the data."""
    lines: List[str] = []
    total_rows = len(raw_data)

    # Determine if data is grouped (dimension + metric) vs flat list
    is_grouped = bool(text_cols and numeric_cols and len(raw_data[0]) <= 3)

    if is_grouped and numeric_cols:
        # Grouped data: "141 providers across 33 specialties"
        dim_natural = _naturalize_column(text_cols[0])  # e.g. "speciality"
        val_col = numeric_cols[0]
        val_natural = _naturalize_column(val_col)  # e.g. "providers" or "premium"
        values = [_to_number(row.get(val_col)) for row in raw_data if row.get(val_col) is not None]
        grand_total = sum(values) if values else 0

        # When the metric is a "count" column, lead with the total
        val_lower = val_col.lower()
        is_count = "count" in val_lower or "cnt" in val_lower or "num" in val_lower
        if is_count:
            lines.append(
                f"**{_format_number(grand_total)}** records across "
                f"**{_format_number(total_rows)}** {dim_natural} groups."
            )
        else:
            val_label = _humanize_column(val_col).lower()
            lines.append(
                f"**{_format_number(total_rows)}** {dim_natural} groups "
                f"with a combined {val_label} of **{_format_number(grand_total)}**."
            )

        # Top and bottom entries
        if values and len(values) >= 2:
            sorted_rows = sorted(
                [(row.get(text_cols[0], "N/A"), _to_number(row.get(val_col, 0))) for row in raw_data],
                key=lambda x: x[1], reverse=True,
            )
            top_name, top_val = sorted_rows[0]
            top_pct = (top_val / grand_total * 100) if grand_total else 0
            second_name, second_val = sorted_rows[1]
            second_pct = (second_val / grand_total * 100) if grand_total else 0
            lines.append(
                f"**{top_name}** leads with **{_format_number(top_val)}** "
                f"({top_pct:.0f}%), followed by **{second_name}** "
                f"with **{_format_number(second_val)}** ({second_pct:.0f}%)."
            )
    else:
        # Flat data or multi-column
        lines.append(f"**{_format_number(total_rows)}** records found.")

        # Summarize each numeric column with context
        for col in numeric_cols[:4]:
            values = [_to_number(row.get(col)) for row in raw_data if row.get(col) is not None]
            if not values:
                continue
            total = sum(values)
            avg = total / len(values)
            max_val = max(values)
            label = _humanize_column(col)
            lines.append(
                f"- **{label}**: Total {_format_number(total)}, "
                f"Avg {_format_number(avg)}, Max {_format_number(max_val)}"
            )

        # Top entry by first numeric column
        if numeric_cols and text_cols:
            val_col = numeric_cols[0]
            dim_col = text_cols[0]
            try:
                top_row = max(raw_data, key=lambda r: _to_number(r.get(val_col, 0)))
                top_name = top_row.get(dim_col, "N/A")
                top_val = _format_number(top_row.get(val_col, 0))
                dim_natural = _naturalize_column(dim_col)
                val_natural = _naturalize_column(val_col)
                lines.append(
                    f"\nHighest {dim_natural}: **{top_name}** "
                    f"with {val_natural} of {top_val}."
                )
            except Exception:
                pass

    # Add assumption context if available
    if assumption:
        lines.append(f"\n*{assumption}*")

    return "\n".join(lines)


def _build_navigation_links(
    module: Optional[str],
    resolved_entities: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    """Build navigation links based on the data module.

    When resolved_entities are provided, encodes them as query-param-style
    ``filters`` on the link so the frontend can append them to the URL.
    """
    if not module:
        return []
    route_info = _MODULE_ROUTES.get(module)
    if not route_info:
        return []

    # Map resolved entity types to URL param names per module
    filters: Dict[str, str] = {}
    if resolved_entities:
        _entity_param_map: Dict[str, str] = {
            "npn": "npn",
            "carrier_name": "carrier",
            "npi": "npi",
            "account_number": "account",
        }
        for entity_type, param_name in _entity_param_map.items():
            if entity_type in resolved_entities:
                filters[param_name] = resolved_entities[entity_type]

    link: Dict[str, Any] = {
        "label": f"View {route_info['label']}",
        "route": route_info["route"],
        "description": f"Go to the {route_info['label']} page for full details.",
    }
    if filters:
        link["filters"] = filters
    return [link]


# Maps DB column name patterns to navigation targets.
# Each entry: column_pattern → (route, url_param_name)
_LINKABLE_COLUMNS: Dict[str, Dict[str, str]] = {
    "npn":              {"route": "/agility-crm/agent-management", "param": "npn"},
    "agent_npn":        {"route": "/agility-crm/agent-management", "param": "npn"},
    "npi":              {"route": "/pch-crm/providers", "param": "npi"},
    "pcp_npi":          {"route": "/pch-crm/providers", "param": "npi"},
    "carrier_name":     {"route": "/agility-crm/commissions", "param": "carrier"},
    "account_number":   {"route": "/pch-crm/members", "param": "account"},
    "amisys_number":    {"route": "/pch-crm/members", "param": "account"},
}


def _detect_column_links(column_keys: List[str]) -> Dict[str, Dict[str, str]]:
    """Return a mapping of column index (as string) → {route, param} for linkable columns."""
    links: Dict[str, Dict[str, str]] = {}
    for idx, col_key in enumerate(column_keys):
        col_lower = col_key.lower().strip()
        if col_lower in _LINKABLE_COLUMNS:
            links[str(idx)] = _LINKABLE_COLUMNS[col_lower]
    return links


def _format_as_table(
    raw_data: List[Dict[str, Any]], total_rows: int
) -> Dict[str, Any]:
    """Format data as a table response."""
    if not raw_data:
        return {"format": "table", "data": {"columns": [], "rows": [], "total_rows": 0}}

    columns = [_humanize_column(k) for k in raw_data[0].keys()]
    column_keys = list(raw_data[0].keys())

    # Truncate if too many rows
    display_data = raw_data[:MAX_INLINE_ROWS]
    rows = [
        [_format_cell(row.get(k)) for k in column_keys]
        for row in display_data
    ]

    # Detect linkable entity columns
    column_links = _detect_column_links(column_keys)

    data: Dict[str, Any] = {
        "columns": columns,
        "rows": rows,
        "total_rows": total_rows,
    }
    if column_links:
        data["column_links"] = column_links

    return {
        "format": "table",
        "data": data,
    }


def _is_temporal_dimension(
    dim_name: str,
    dimension_configs: Dict[str, Any] = None,
    raw_data: List[Dict[str, Any]] = None,
) -> bool:
    """Check if a dimension represents time-series data."""
    dim_lower = dim_name.lower().replace(" ", "_")
    # Check by name — case-insensitive (covers both computed and raw time columns)
    if dim_lower in _TIME_DIMENSION_NAMES:
        return True
    # Fuzzy: any column containing temporal keywords
    _TEMPORAL_KEYWORDS = {"month", "quarter", "year", "date", "period", "week"}
    if any(kw in dim_lower for kw in _TEMPORAL_KEYWORDS):
        return True
    # Check computed temporal flag in config
    if dimension_configs:
        cfg = dimension_configs.get(dim_name)
        if isinstance(cfg, dict) and cfg.get("temporal"):
            return True
    # Heuristic: if actual data values look like datetime strings, treat as temporal
    if raw_data and len(raw_data) > 0:
        sample = str(raw_data[0].get(dim_name, ""))
        if _DATETIME_RE.match(sample.strip()):
            return True
    return False


def _pick_chart_format(
    format_hint: str,
    dimensions: List[str] = None,
    dimension_configs: Dict[str, Any] = None,
    raw_data: List[Dict[str, Any]] = None,
) -> str:
    """Pick chart type based on LLM hint and dimension metadata."""
    if format_hint in ("bar_chart", "pie_chart", "line_chart"):
        return format_hint
    # Auto-detect: if first dimension is temporal, prefer line_chart
    if dimensions:
        if _is_temporal_dimension(dimensions[0], dimension_configs, raw_data):
            return "line_chart"
    # Default: bar chart for most breakdowns
    return "bar_chart"


def _format_number(value) -> str:
    """Format a number for display."""
    if value is None:
        return "0"
    try:
        num = float(value)
        if num == int(num):
            return f"{int(num):,}"
        return f"{num:,.2f}"
    except (ValueError, TypeError):
        return str(value)


def _to_number(value) -> float:
    """Convert a value to float for chart data."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _format_cell(value) -> str:
    """Format a cell value for table display."""
    if value is None:
        return ""
    s = str(value)
    # Clean up datetime-like strings (e.g. "2021-01-01 00:00:00.0000000" → "Jan 2021")
    s = _clean_datetime(s)
    return s


# Regex for datetime strings like "2021-01-01 00:00:00", "2021-01-01 00:00:00.0000000",
# with timezone offset "2021-01-01 00:00:00+00:00", or plain date "2021-01-01"
_DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[\sT]+\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2})?)?$"
)

# Quarter start months
_QUARTER_MONTHS = {1: "Q1", 4: "Q2", 7: "Q3", 10: "Q4"}


def _is_quarter_series(raw_data: List[Dict[str, Any]], dim_col: str) -> bool:
    """Check if all values in a dimension column are quarter-start dates (Jan/Apr/Jul/Oct 1st)."""
    if not raw_data or len(raw_data) < 2:
        return False
    for row in raw_data:
        val = str(row.get(dim_col, "")).strip()
        m = _DATETIME_RE.match(val)
        if not m:
            return False
        month_int, day = int(m.group(2)), m.group(3)
        if day != "01" or month_int not in _QUARTER_MONTHS:
            return False
    return True


def _format_as_quarter(value: str) -> str:
    """Format a quarter-start datetime as 'Q1 2021'."""
    m = _DATETIME_RE.match(value.strip())
    if m:
        year, month_int = m.group(1), int(m.group(2))
        q = _QUARTER_MONTHS.get(month_int)
        if q:
            return f"{q} {year}"
    return _clean_datetime(value)


def _clean_datetime(value: str) -> str:
    """Convert datetime strings to human-readable month labels.

    '2021-01-01 00:00:00.0000000' → 'Jan 2021'
    '2021-01-01 00:00:00+00:00'  → 'Jan 2021'
    '2021-01' → 'Jan 2021'
    """
    m = _DATETIME_RE.match(value.strip())
    if m:
        year, month_str, day = m.group(1), m.group(2), m.group(3)
        month_int = int(month_str)
        if day == "01":
            # Month-level date → format as "Jan 2021"
            try:
                d = dt(int(year), month_int, 1)
                return d.strftime("%b %Y")
            except ValueError:
                pass
        else:
            # Specific date → format as "Jan 15, 2021"
            try:
                d = dt(int(year), month_int, int(day))
                return d.strftime("%b %d, %Y")
            except ValueError:
                pass
    # Also handle YYYY-MM strings
    if re.match(r"^\d{4}-\d{2}$", value.strip()):
        try:
            d = dt.strptime(value.strip(), "%Y-%m")
            return d.strftime("%b %Y")
        except ValueError:
            pass
    return value


def _humanize_column(col: str) -> str:
    """Convert snake_case column name to human-readable."""
    return col.replace("_", " ").title()


# Prefixes to strip for more natural summaries
_STRIP_PREFIXES = (
    "primary_", "secondary_", "agent_", "provider_", "member_",
    "total_", "sum_", "avg_", "max_", "min_",
)


def _naturalize_column(col: str) -> str:
    """Convert a DB column name to natural English for summaries.

    Examples:
        primary_speciality -> speciality
        provider_count -> count
        agent_name -> agent name  (keeps compound after stripping)
        total_premium -> premium
        status -> status
    """
    name = col.lower()
    for prefix in _STRIP_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            stripped = name[len(prefix):]
            # Don't strip if result is too short (e.g. agent_name -> "name" is ok,
            # but keep the full column if result is just 1-2 chars)
            if len(stripped) > 2:
                name = stripped
                break
    # Clean up and make readable
    name = name.replace("_", " ").strip()
    if not name:
        name = col.replace("_", " ").strip()
    return name


def build_multi_insight_dashboard(
    insights: List[Dict[str, Any]],
    module: str = None,
    user_query: str = None,
    assumption: str = None,
    resolved_entities: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a dashboard from multiple independent insight datasets.

    Unlike build_dashboard_sections() which creates multiple views of ONE dataset,
    this composes sections from MULTIPLE different SQL results — each insight
    is a separate query result with its own format hint.

    Args:
        insights: List of insight dicts, each with:
            - title: str — section heading
            - description: str — section description
            - format_hint: str — "kpi", "bar_chart", "pie_chart", "line_chart", or "table"
            - raw_data: List[Dict] — query result rows
            - metric_columns: List[str] — numeric columns
            - dimension_columns: List[str] — text/category columns
        module: Data module name (for navigation links).
        user_query: Original user question (for context).
        assumption: Any assumption made during query planning.

    Returns:
        {"format": "dashboard", "data": {"summary": "", "sections": [...], "navigation_links": [...]}}
    """
    sections: List[Dict[str, Any]] = []

    for insight in insights:
        title = insight.get("title", "Insight")
        description = insight.get("description", "")
        format_hint = (insight.get("format_hint") or "table").lower().strip()
        raw_data = insight.get("raw_data") or []
        metric_columns = insight.get("metric_columns") or []
        dimension_columns = insight.get("dimension_columns") or []

        # Skip empty insights
        if not raw_data:
            continue

        columns = list(raw_data[0].keys())

        # Resolve metric and dimension columns from data if not provided
        if not metric_columns:
            metric_columns = [c for c in columns if _column_is_numeric(raw_data, c)]
        if not dimension_columns:
            dimension_columns = [c for c in columns if c not in metric_columns]

        # ----- KPI -----
        if format_hint == "kpi":
            section = _build_multi_kpi_section(raw_data, title, description, metric_columns)
            if section:
                sections.append(section)

        # ----- Charts -----
        elif format_hint in ("bar_chart", "pie_chart", "line_chart"):
            section = _build_multi_chart_section(
                raw_data, title, description, format_hint,
                metric_columns, dimension_columns,
            )
            if section:
                sections.append(section)

        # ----- Table (default) -----
        else:
            section = _build_multi_table_section(
                raw_data, title, description,
            )
            if section:
                sections.append(section)

    navigation_links = _build_navigation_links(module, resolved_entities)

    return {
        "format": "dashboard",
        "data": {
            "summary": "",  # Caller sets this via synthesize_multi_insight_summary
            "sections": sections,
            "navigation_links": navigation_links,
        },
    }


def _build_multi_kpi_section(
    raw_data: List[Dict[str, Any]],
    title: str,
    description: str,
    metric_columns: List[str],
) -> Optional[Dict[str, Any]]:
    """Build a KPI section from the first numeric value in the data."""
    if not raw_data:
        return None

    # Try metric_columns first, then fall back to any numeric-looking column
    row = raw_data[0]
    kpi_col = None
    kpi_val = None

    for col in metric_columns:
        val = row.get(col)
        if val is not None:
            kpi_col = col
            kpi_val = val
            break

    if kpi_col is None:
        # Fall back: pick first numeric value from the row
        for col, val in row.items():
            if val is not None:
                try:
                    float(val)
                    kpi_col = col
                    kpi_val = val
                    break
                except (ValueError, TypeError):
                    continue

    if kpi_col is None:
        return None

    return {
        "title": title,
        "format": "kpi",
        "data": {
            "kpi_value": _format_number(kpi_val),
            "kpi_label": title,
        },
        "description": description,
    }


def _build_multi_chart_section(
    raw_data: List[Dict[str, Any]],
    title: str,
    description: str,
    format_hint: str,
    metric_columns: List[str],
    dimension_columns: List[str],
) -> Optional[Dict[str, Any]]:
    """Build a chart section using the first text column as labels and first numeric as values."""
    if not raw_data or len(raw_data) < 1:
        return None

    columns = list(raw_data[0].keys())

    # Pick label column (first dimension or first text column)
    label_col = dimension_columns[0] if dimension_columns else None
    if label_col is None:
        for c in columns:
            if not _column_is_numeric(raw_data, c):
                label_col = c
                break
    if label_col is None and columns:
        label_col = columns[0]

    # Pick value column (first metric or first numeric column)
    value_col = metric_columns[0] if metric_columns else None
    if value_col is None:
        for c in columns:
            if _column_is_numeric(raw_data, c):
                value_col = c
                break

    if label_col is None or value_col is None:
        return None

    labels = [str(row.get(label_col, "")) for row in raw_data]
    values = [_to_number(row.get(value_col, 0)) for row in raw_data]

    return {
        "title": title,
        "format": format_hint,
        "data": {
            "labels": labels,
            "values": values,
            "series_name": _naturalize_column(value_col),
        },
        "description": description,
    }


def _build_multi_table_section(
    raw_data: List[Dict[str, Any]],
    title: str,
    description: str,
    max_rows: int = 20,
) -> Optional[Dict[str, Any]]:
    """Build a table section with humanized column names, limited to max_rows."""
    if not raw_data:
        return None

    columns = [_naturalize_column(k) for k in raw_data[0].keys()]
    column_keys = list(raw_data[0].keys())
    display_data = raw_data[:max_rows]
    rows = [
        [_format_cell(row.get(k)) for k in column_keys]
        for row in display_data
    ]

    return {
        "title": title,
        "format": "table",
        "data": {
            "columns": columns,
            "rows": rows,
            "total_rows": len(raw_data),
        },
        "description": description,
    }


def _get_query_type(context: Optional[PipelineContext]) -> Optional[str]:
    """Extract query_type from pipeline context (set by technical spec).

    Returns "data", "existential", "navigation", "ambiguous", or None.
    """
    if context is None:
        return None
    return context.get("query_type")
