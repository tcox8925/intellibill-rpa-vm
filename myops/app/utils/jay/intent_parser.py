"""
JAI Intent Parser - Response synthesis (kept) + legacy intent parsing (deprecated).

The new pipeline uses intent_detector.py for intent parsing.
This module now only provides synthesize_response() and legacy compatibility.
"""

import json
import logging
import re
import time
from typing import List, Dict, Any, Optional

from app.utils.jay.llm_client import get_llm_client
from app.utils.jay.prompts import build_synthesis_prompt

logger = logging.getLogger(__name__)


class IntentParseError(Exception):
    """Legacy exception kept for backward compatibility."""
    pass


def _format_number(val) -> str:
    """Format a numeric value with commas for readability."""
    if isinstance(val, float):
        if val == int(val):
            return f"{int(val):,}"
        return f"{val:,.2f}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)


def _try_fast_synthesis(
    raw_data: List[Dict[str, Any]],
    format_type: str,
    user_query: str,
) -> Optional[str]:
    """Attempt to generate a response without an LLM call.

    Returns a string response if the result is simple enough to handle
    programmatically, or None if LLM synthesis is needed.
    """
    if not raw_data:
        return "No results found for your query."

    num_rows = len(raw_data)
    num_cols = len(raw_data[0]) if raw_data else 0

    # KPI: single scalar value
    if num_rows == 1 and num_cols == 1:
        col_name = list(raw_data[0].keys())[0]
        val = list(raw_data[0].values())[0]
        label = col_name.replace("_", " ").title()
        return f"The {label} is **{_format_number(val)}**."

    # Single row with multiple columns (profile/detail view)
    if num_rows == 1 and num_cols <= 6:
        row = raw_data[0]
        parts = []
        for col, val in row.items():
            label = col.replace("_", " ").title()
            parts.append(f"**{label}**: {_format_number(val)}")
        return "Here are the results:\n\n" + " | ".join(parts)

    # Chart results — provide richer summaries for chart format types
    # (checked before grouped-result path so line_chart gets trend analysis)
    if format_type in ("bar_chart", "pie_chart", "line_chart") and num_rows > 1:
        return _synthesize_chart(raw_data, format_type, num_rows)

    # Small grouped result (e.g., count by category) — table with 2 cols
    if 2 <= num_rows <= 10 and num_cols == 2:
        cols = list(raw_data[0].keys())
        dim_col, val_col = cols[0], cols[1]
        # Skip if the value column looks like an ID
        _ID_KW = ("_id", "id_", "pk", "key", "npn", "npi", "ssn", "ein", "uuid", "guid")
        val_lower = val_col.lower()
        if any(kw in val_lower for kw in _ID_KW) or val_lower in ("id", "npn", "npi", "ssn"):
            return f"Found **{num_rows}** results."
        # Try to compute numeric totals
        numeric_vals = []
        for r in raw_data:
            try:
                v = r[val_col]
                if v is not None:
                    numeric_vals.append((r[dim_col], float(v)))
            except (ValueError, TypeError):
                pass
        if numeric_vals:
            numeric_vals.sort(key=lambda x: x[1], reverse=True)
            total = sum(v for _, v in numeric_vals)
            top_name, top_val = numeric_vals[0]
            top_pct = (top_val / total * 100) if total else 0
            parts = [f"**{top_name}** leads with **{_format_number(top_val)}** ({top_pct:.0f}%)"]
            if len(numeric_vals) >= 2:
                second_name, second_val = numeric_vals[1]
                second_pct = (second_val / total * 100) if total else 0
                parts.append(f"followed by **{second_name}** at **{_format_number(second_val)}** ({second_pct:.0f}%)")
            summary = ", ".join(parts) + "."
            summary += f" {num_rows} entries total **{_format_number(total)}**."
            return summary
        else:
            return f"Found **{num_rows}** results."

    # Table results — compute actual statistics instead of just count
    if format_type == "table" and num_rows >= 2:
        return _synthesize_table(raw_data, num_rows)

    if num_rows > 200:
        return f"Found **{_format_number(num_rows)}** results (showing first 200)."

    # Fall through to LLM synthesis
    return None


def _find_numeric_columns(raw_data: List[Dict[str, Any]]) -> List[str]:
    """Identify columns that contain numeric values."""
    if not raw_data:
        return []
    numeric_cols = []
    for col in raw_data[0].keys():
        count = 0
        for r in raw_data:
            v = r.get(col)
            if v is None:
                continue
            if isinstance(v, (int, float)):
                count += 1
            else:
                try:
                    float(v)
                    count += 1
                except (ValueError, TypeError):
                    break
        else:
            if count > 0:
                numeric_cols.append(col)
    return numeric_cols


def _find_label_column(raw_data: List[Dict[str, Any]], numeric_cols: List[str]) -> Optional[str]:
    """Find the best non-numeric column to use as a label (name/identifier)."""
    non_numeric = [c for c in raw_data[0].keys() if c not in numeric_cols]
    if not non_numeric:
        return None
    # Prefer columns with "name" in the key
    for c in non_numeric:
        if "name" in c.lower():
            return c
    return non_numeric[0]


def _safe_float(val) -> Optional[float]:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _synthesize_table(raw_data: List[Dict[str, Any]], num_rows: int) -> str:
    """Generate a data-driven summary for table results."""
    numeric_cols = _find_numeric_columns(raw_data)
    label_col = _find_label_column(raw_data, numeric_cols)
    cap = " (showing first 200)" if num_rows > 200 else ""

    if not numeric_cols:
        return f"Showing **{_format_number(num_rows)}** results{cap}."

    # Pick the most interesting numeric column (highest total, skip counts/IDs)
    best_col = None
    best_total = 0
    for col in numeric_cols:
        vals = [_safe_float(r.get(col)) for r in raw_data]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        col_total = sum(vals)
        # Prefer columns that look like amounts/values over IDs/identifiers
        col_lower = col.lower()
        _ID_KEYWORDS = ("_id", "id_", "pk", "key", "npn", "npi", "ssn", "ein", "fein", "tin", "uuid", "guid")
        is_id = any(kw in col_lower for kw in _ID_KEYWORDS) or col_lower in ("id", "npn", "npi", "ssn")
        if is_id:
            continue
        if col_total > best_total or best_col is None:
            best_total = col_total
            best_col = col

    if best_col is None:
        return f"Showing **{_format_number(num_rows)}** results{cap}."

    vals = [_safe_float(r.get(best_col)) for r in raw_data]
    vals_clean = [v for v in vals if v is not None]
    total = sum(vals_clean)
    avg = total / len(vals_clean) if vals_clean else 0
    col_label = best_col.replace("_", " ").title()

    parts = [f"Showing **{_format_number(num_rows)}** results{cap}."]
    parts.append(f"Total {col_label}: **{_format_number(total)}**, average: **{_format_number(avg)}**.")

    # Find the top entry by name if we have a label column
    if label_col and vals_clean:
        max_idx = None
        max_val = float("-inf")
        for i, r in enumerate(raw_data):
            v = _safe_float(r.get(best_col))
            if v is not None and v > max_val:
                max_val = v
                max_idx = i
        if max_idx is not None:
            top_name = raw_data[max_idx].get(label_col, "N/A")
            top_pct = (max_val / total * 100) if total else 0
            parts.append(
                f"Top entry: **{top_name}** with **{_format_number(max_val)}** ({top_pct:.1f}%)."
            )

    return " ".join(parts)


def _synthesize_chart(
    raw_data: List[Dict[str, Any]], format_type: str, num_rows: int
) -> str:
    """Generate a data-driven summary for chart results."""
    cols = list(raw_data[0].keys())
    # Identify dimension and value columns
    numeric_cols = _find_numeric_columns(raw_data)
    non_numeric = [c for c in cols if c not in numeric_cols]
    dim_col = non_numeric[0] if non_numeric else cols[0]
    val_col = numeric_cols[0] if numeric_cols else None

    if val_col is None:
        return f"Showing **{num_rows}** data points."

    entries = []
    for r in raw_data:
        v = _safe_float(r.get(val_col))
        if v is not None:
            entries.append((r.get(dim_col, "N/A"), v))

    if not entries:
        return f"Showing **{num_rows}** data points."

    total = sum(v for _, v in entries)
    entries_sorted = sorted(entries, key=lambda x: x[1], reverse=True)
    top_name, top_val = entries_sorted[0]
    bottom_name, bottom_val = entries_sorted[-1]

    parts = []

    if format_type == "line_chart" and len(entries) >= 3:
        # Detect trend: compare first half avg to second half avg
        half = len(entries) // 2
        first_half_avg = sum(v for _, v in entries[:half]) / half if half else 0
        second_half_avg = sum(v for _, v in entries[half:]) / (len(entries) - half) if (len(entries) - half) else 0
        if second_half_avg > first_half_avg * 1.05:
            trend = "upward"
        elif second_half_avg < first_half_avg * 0.95:
            trend = "downward"
        else:
            trend = "stable"
        parts.append(
            f"Peaks at **{_format_number(top_val)}** ({top_name}) and dips to "
            f"**{_format_number(bottom_val)}** ({bottom_name}). "
            f"Overall trend is **{trend}**."
        )
    else:
        # Bar/pie: highlight top and runner-up
        top_pct = (top_val / total * 100) if total else 0
        parts.append(
            f"**{top_name}** leads with **{_format_number(top_val)}** ({top_pct:.0f}%)"
        )
        if len(entries_sorted) >= 2:
            second_name, second_val = entries_sorted[1]
            second_pct = (second_val / total * 100) if total else 0
            parts[0] += f", followed by **{second_name}** at **{_format_number(second_val)}** ({second_pct:.0f}%)"
        parts[0] += "."

    parts.append(f"Total: **{_format_number(total)}**.")
    return " ".join(parts)


DASHBOARD_SYNTHESIS_PROMPT = """You are JAI, a friendly AI data assistant for the MyOps platform.

You are writing a short summary paragraph for a DASHBOARD response. The dashboard already displays
KPI cards, charts, and a data table — the user can see all of those. Your job is to provide a
brief narrative that ties the data together and highlights the most important insights.

Rules:
- 2-4 sentences maximum. Be concise and insightful.
- Start by explaining WHAT the data shows in plain language (e.g. "Your provider network spans 33 specialties...")
- Highlight the most notable finding (top category, concentration, trend).
- If relevant, mention something actionable or notable the user should pay attention to.
- Use markdown bold for key numbers.
- NEVER mention column names, SQL, table names, or schema details.
- NEVER say "records found" or "rows returned" — describe what the data MEANS.
- Translate technical column names to plain business terms.
- Do NOT repeat numbers that are already shown as KPI cards — reference them naturally.
- Talk like a helpful colleague — brief, direct, no corporate filler.

{terminology_context}
"""


def synthesize_dashboard_summary(
    user_query: str,
    raw_data: List[Dict[str, Any]],
    dashboard_sections: List[Dict[str, Any]],
    current_module: str = None,
    pipeline_context: str = "",
    assumption: str = None,
) -> str:
    """Generate a rich LLM-powered summary for dashboard responses.

    Unlike synthesize_response (which skips LLM for simple data),
    this always calls the LLM because dashboard summaries need
    contextual narrative that programmatic code can't produce well.
    """
    from app.utils.jay.prompts import get_terminology_context

    t0 = time.time()

    terminology = get_terminology_context(current_module)
    system_prompt = DASHBOARD_SYNTHESIS_PROMPT.replace("{terminology_context}", terminology)
    if pipeline_context:
        system_prompt += (
            "\n\n--- Pipeline Context ---\n"
            f"{pipeline_context}\n"
            "---\n"
        )

    # Build a description of what sections the dashboard shows
    section_descriptions = []
    for s in dashboard_sections:
        fmt = s.get("format", "")
        title = s.get("title", "")
        desc = s.get("description", "")
        if fmt == "kpi":
            kpi_data = s.get("data", {})
            section_descriptions.append(
                f"- KPI card: {kpi_data.get('kpi_label', title)} = {kpi_data.get('kpi_value', 'N/A')} ({desc})"
            )
        elif fmt in ("bar_chart", "pie_chart", "line_chart"):
            chart_data = s.get("data", {})
            top_labels = chart_data.get("labels", [])[:5]
            section_descriptions.append(
                f"- {fmt.replace('_', ' ').title()}: \"{title}\" showing {', '.join(str(l) for l in top_labels)}..."
            )
        elif fmt == "table":
            table_data = s.get("data", {})
            section_descriptions.append(
                f"- Table: \"{title}\" with {table_data.get('total_rows', 0)} rows, columns: {', '.join(table_data.get('columns', []))}"
            )

    sections_text = "\n".join(section_descriptions)

    # Truncate raw data for context
    data_sample = raw_data[:15] if len(raw_data) > 15 else raw_data
    data_str = json.dumps(data_sample, default=str, indent=2)
    if len(raw_data) > 15:
        data_str += f"\n... and {len(raw_data) - 15} more rows (total: {len(raw_data)})"

    user_content = (
        f"User question: {user_query}\n\n"
        f"Dashboard sections being displayed:\n{sections_text}\n\n"
        f"Raw data ({len(raw_data)} rows):\n{data_str}"
    )
    if assumption:
        user_content += f"\n\nAssumption made: {assumption}"

    try:
        client, deployment = get_llm_client()
        resp = client.chat.completions.create(
            model=deployment,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        elapsed = (time.time() - t0) * 1000
        logger.info(f"Dashboard synthesis completed ({elapsed:.0f}ms)")
        return (resp.choices[0].message.content or "").strip()

    except Exception as e:
        logger.error(f"Dashboard synthesis LLM call failed: {e}")
        # Fallback to programmatic summary
        return _build_fallback_dashboard_summary(raw_data, dashboard_sections)


def _build_fallback_dashboard_summary(
    raw_data: List[Dict[str, Any]],
    sections: List[Dict[str, Any]],
) -> str:
    """Programmatic fallback if LLM dashboard synthesis fails."""
    parts = []
    for s in sections:
        if s.get("format") == "kpi":
            kpi = s.get("data", {})
            parts.append(f"**{kpi.get('kpi_label', '')}**: {kpi.get('kpi_value', '')}")
    if parts:
        return " | ".join(parts)
    return f"Showing **{len(raw_data)}** results across multiple views."


MULTI_INSIGHT_SYNTHESIS_PROMPT = """You are JAI, a friendly AI data assistant for the MyOps platform.

You are writing a short summary paragraph for a MULTI-INSIGHT DASHBOARD response. The dashboard
displays multiple panels — each showing a different aspect of the data (KPIs, charts, tables).
Your job is to provide a brief narrative that ties ALL the insights together and highlights
the most important findings across them.

Rules:
- 2-4 sentences maximum. Be concise and insightful.
- Tie the insights together — don't just list them. Find the story across panels.
- Highlight the most notable finding across ALL insights (top performer, trend, outlier).
- Use markdown bold for key numbers.
- NEVER mention column names, SQL, table names, or schema details.
- NEVER say "records found" or "rows returned" — describe what the data MEANS.
- Translate technical column names to plain business terms.
- Talk like a helpful colleague — brief, direct, no corporate filler.

{terminology_context}
"""


def synthesize_multi_insight_summary(
    user_query: str,
    insights: List[Dict[str, Any]],
    current_module: str = None,
    assumption: str = None,
) -> str:
    """Generate a narrative summary that covers ALL insights in a multi-insight dashboard.

    Calls the LLM to synthesize a 2-4 sentence summary tying together multiple
    independent data panels. Falls back to a bullet-point summary on failure.

    Args:
        user_query: The original user question.
        insights: List of insight dicts with title, description, format_hint, raw_data.
        current_module: Module name for terminology context.
        assumption: Any assumption made during query planning.

    Returns:
        A markdown string summary.
    """
    from app.utils.jay.prompts import get_terminology_context

    t0 = time.time()

    # Build context describing each insight's key data points
    insight_descriptions: List[str] = []
    for idx, insight in enumerate(insights, 1):
        title = insight.get("title", f"Insight {idx}")
        description = insight.get("description", "")
        format_hint = insight.get("format_hint", "table")
        raw_data = insight.get("raw_data") or []

        if not raw_data:
            continue

        # Extract key data points for context
        num_rows = len(raw_data)
        columns = list(raw_data[0].keys())

        data_summary_parts = [f"Panel \"{title}\" ({format_hint}, {num_rows} rows)"]
        if description:
            data_summary_parts.append(f"  Description: {description}")

        # Include a small data sample (first 5 rows, truncated)
        sample = raw_data[:5]
        data_summary_parts.append(f"  Sample data: {json.dumps(sample, default=str)}")
        if num_rows > 5:
            data_summary_parts.append(f"  ... and {num_rows - 5} more rows")

        insight_descriptions.append("\n".join(data_summary_parts))

    if not insight_descriptions:
        return "No data available for this query."

    # Build prompt
    terminology = get_terminology_context(current_module)
    system_prompt = MULTI_INSIGHT_SYNTHESIS_PROMPT.replace("{terminology_context}", terminology)

    insights_text = "\n\n".join(insight_descriptions)
    user_content = (
        f"User question: {user_query}\n\n"
        f"Dashboard panels:\n{insights_text}"
    )
    if assumption:
        user_content += f"\n\nAssumption made: {assumption}"

    try:
        client, deployment = get_llm_client()
        resp = client.chat.completions.create(
            model=deployment,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        elapsed = (time.time() - t0) * 1000
        logger.info(f"Multi-insight synthesis completed ({elapsed:.0f}ms)")
        return (resp.choices[0].message.content or "").strip()

    except Exception as e:
        logger.error(f"Multi-insight synthesis LLM call failed: {e}")
        # Fallback: bullet-point summary
        return _build_fallback_multi_insight_summary(insights)


def _build_fallback_multi_insight_summary(
    insights: List[Dict[str, Any]],
) -> str:
    """Programmatic fallback if LLM multi-insight synthesis fails."""
    bullets: List[str] = []
    for insight in insights:
        title = insight.get("title", "Insight")
        raw_data = insight.get("raw_data") or []
        if not raw_data:
            continue

        # Try to extract a key number
        row = raw_data[0]
        for val in row.values():
            if val is not None:
                try:
                    num = float(val)
                    bullets.append(f"- **{title}**: {_format_number(num)}")
                    break
                except (ValueError, TypeError):
                    continue
        else:
            bullets.append(f"- **{title}**: {len(raw_data)} results")

    if not bullets:
        return "Dashboard with multiple insights."
    return "Here's an overview of the key findings:\n\n" + "\n".join(bullets)


def synthesize_response(
    user_query: str,
    raw_data: List[Dict[str, Any]],
    format_type: str,
    current_module: str = None,
    pipeline_context: str = "",
) -> str:
    """Generate a natural language summary of query results.

    Uses fast programmatic synthesis for simple results (KPI, small tables),
    falling back to LLM synthesis only for complex results that benefit
    from natural language explanation.
    """
    t0 = time.time()

    # Try fast synthesis first (no LLM call)
    fast_result = _try_fast_synthesis(raw_data, format_type, user_query)
    if fast_result is not None:
        elapsed = (time.time() - t0) * 1000
        logger.info(f"Fast synthesis used ({elapsed:.0f}ms), skipped LLM call")
        return fast_result

    # Fall back to LLM synthesis for complex results
    client, deployment = get_llm_client()

    system_prompt = build_synthesis_prompt(current_module, pipeline_context=pipeline_context)

    # Truncate data for LLM context (max 20 rows summary)
    data_summary = raw_data[:20] if len(raw_data) > 20 else raw_data
    data_str = json.dumps(data_summary, default=str, indent=2)

    if len(raw_data) > 20:
        data_str += f"\n... and {len(raw_data) - 20} more rows (total: {len(raw_data)})"

    user_content = (
        f"User question: {user_query}\n\n"
        f"Display format: {format_type}\n\n"
        f"Query results ({len(raw_data)} rows):\n{data_str}"
    )

    try:
        resp = client.chat.completions.create(
            model=deployment,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        elapsed = (time.time() - t0) * 1000
        logger.info(f"LLM synthesis completed ({elapsed:.0f}ms)")
        return (resp.choices[0].message.content or "").strip()

    except Exception as e:
        logger.error(f"Synthesis LLM call failed: {e}")
        # Fallback: generate basic summary
        if not raw_data:
            return "No results found for your query."
        if len(raw_data) == 1 and len(raw_data[0]) == 1:
            val = list(raw_data[0].values())[0]
            return f"The result is **{val}**."
        return f"Found {len(raw_data)} results."


# =====================================================
# Dashboard Insight Planner
# =====================================================

_VALID_FORMAT_HINTS = {"kpi", "bar_chart", "pie_chart", "line_chart", "table"}
_REQUIRED_SPEC_FIELDS = {"title", "sql_template", "format_hint"}


def plan_dashboard_insights(
    user_query: str,
    module: str,
    schema_context: str,
    current_module: str = None,
) -> List[Dict[str, Any]]:
    """Use an LLM to decompose a broad query into 3-4 complementary insight specs.

    Each spec contains a title, description, sql_template, format_hint, and
    column metadata that the downstream dashboard builder uses to generate
    individual panels.

    Args:
        user_query: The user's broad natural language query.
        module: Module name (e.g., "agents", "commission_totals").
        schema_context: Pre-formatted schema/DDL with available tables and columns.
        current_module: Optional current module context (unused, reserved for future).

    Returns:
        List of insight spec dicts.  Empty list on any failure (caller should
        fall back to template-based insights).
    """
    from app.utils.jay.prompts_v2 import build_dashboard_planner_prompt

    t0 = time.time()

    system_prompt, user_message = build_dashboard_planner_prompt(
        module=module,
        user_query=user_query,
        schema_context=schema_context,
    )

    try:
        client, deployment = get_llm_client()

        # Use _get_llm_kwargs pattern for reasoning model compatibility
        from app.utils.jay.pipeline_v2 import _get_llm_kwargs

        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            **_get_llm_kwargs(deployment, temperature=0.0),
        )

        raw_content = (resp.choices[0].message.content or "").strip()
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "Dashboard planner LLM call completed (%dms), response length: %d",
            int(elapsed),
            len(raw_content),
        )

        # Strip markdown code fences if present
        if raw_content.startswith("```"):
            raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
            raw_content = re.sub(r"\s*```$", "", raw_content)

        specs = json.loads(raw_content)

        if not isinstance(specs, list):
            logger.warning("Dashboard planner returned non-list: %s", type(specs))
            return []

        # Validate each spec
        validated = []
        for i, spec in enumerate(specs):
            if not isinstance(spec, dict):
                logger.warning("Dashboard planner spec %d is not a dict, skipping", i)
                continue

            missing = _REQUIRED_SPEC_FIELDS - set(spec.keys())
            if missing:
                logger.warning(
                    "Dashboard planner spec %d missing fields %s, skipping", i, missing
                )
                continue

            # Normalise format_hint
            fmt = spec.get("format_hint", "").lower().strip()
            if fmt not in _VALID_FORMAT_HINTS:
                logger.warning(
                    "Dashboard planner spec %d has invalid format_hint '%s', defaulting to 'table'",
                    i,
                    fmt,
                )
                spec["format_hint"] = "table"

            # Ensure list fields default to empty lists
            spec.setdefault("metric_columns", [])
            spec.setdefault("dimension_columns", [])
            spec.setdefault("description", "")

            validated.append(spec)

        logger.info(
            "Dashboard planner produced %d valid specs (out of %d returned)",
            len(validated),
            len(specs),
        )
        return validated

    except json.JSONDecodeError as e:
        elapsed = (time.time() - t0) * 1000
        logger.error("Dashboard planner JSON parse error (%dms): %s", int(elapsed), e)
        return []

    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        logger.error("Dashboard planner failed (%dms): %s", int(elapsed), e)
        return []
