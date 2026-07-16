# ==========================================================
#  intelligence/ai_intelligence.py
# ==========================================================
"""
ai_intelligence.py — v2
-----------------------
Simplified intelligence layer.

One primary function: generate_run_report()
  - Takes all carrier metrics from the pipeline run
  - Makes a single AI call with the full picture
  - Returns AIResult with .summary for the notification email

Schema and identity suggestion retained for future use.
"""

from typing import List, Dict, Any, Optional

from utils.ai_utils import call_ai_model, get_ai_client_config
from intelligence.prompt_templates import (
    AIResult,
    SYSTEM_MESSAGES,
    build_schema_prompt,
    build_identity_prompt,
    build_run_report_prompt,
)


# ==========================================================
#  SAFE AI CALL
# ==========================================================
def _safe_ai_call(
    prompt: str,
    system_message: str,
    category: str,
    fallback_summary: str,
    details: Dict[str, Any],
) -> AIResult:
    """Call the AI model with fallback."""
    config = get_ai_client_config()
    if not config:
        print(f"  [ai] Not configured — using fallback for {category}")
        return AIResult(
            summary=fallback_summary, details=details, category=category,
            success=False, raw_prompt=prompt, raw_response="",
        )

    response = call_ai_model(prompt, system_message=system_message)

    if response and len(response) > 50:
        return AIResult(
            summary=response, details=details, category=category,
            success=True, raw_prompt=prompt, raw_response=response,
        )
    else:
        print(f"  [ai] Response too short ({len(response or '')} chars) — using fallback")
        return AIResult(
            summary=fallback_summary, details=details, category=category,
            success=False, raw_prompt=prompt, raw_response=response or "",
        )


# ==========================================================
#  SCHEMA SUGGESTION (retained)
# ==========================================================
def suggest_schema_mapping(
    carrier_name: str,
    process_type: str,
    columns_added: List[str],
    columns_removed: List[str],
    canonical_columns: List[str],
    current_columns: List[str],
) -> AIResult:
    details = {
        "carrier_name": carrier_name,
        "columns_added": columns_added,
        "columns_removed": columns_removed,
    }

    fallback_lines = [f"Schema drift detected for {carrier_name} ({process_type})."]
    if columns_added:
        fallback_lines.append(f"Columns added: {', '.join(columns_added)}.")
    if columns_removed:
        fallback_lines.append(f"Columns removed: {', '.join(columns_removed)}.")
    fallback_lines.append("Review column changes and update the mapping table.")

    prompt = build_schema_prompt(
        carrier_name, process_type,
        columns_added, columns_removed,
        canonical_columns, current_columns,
    )

    return _safe_ai_call(
        prompt=prompt,
        system_message=SYSTEM_MESSAGES["schema"],
        category="schema",
        fallback_summary=" ".join(fallback_lines),
        details=details,
    )


# ==========================================================
#  IDENTITY SUGGESTION (retained)
# ==========================================================
def suggest_identity_match(
    carrier_name: str,
    unmatched_row: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> AIResult:
    details = {
        "carrier_name": carrier_name,
        "unmatched_row": unmatched_row,
        "candidate_count": len(candidates),
    }

    fallback_lines = [
        f"Identity resolution for {carrier_name}: {len(candidates)} candidates found.",
        "Manual review required.",
    ]

    prompt = build_identity_prompt(carrier_name, unmatched_row, candidates)

    return _safe_ai_call(
        prompt=prompt,
        system_message=SYSTEM_MESSAGES["identity"],
        category="identity",
        fallback_summary=" ".join(fallback_lines),
        details=details,
    )


# ==========================================================
#  RUN REPORT — SINGLE AI CALL WITH FULL CONTEXT
# ==========================================================
def generate_run_report(all_metrics: List[Dict], scan_date: str) -> AIResult:
    """
    Generate a comprehensive analysis for the entire pipeline run.

    One AI call with all per-carrier data. The model sees exception
    rates alongside contract coverage and can connect the dots.

    Parameters
    ----------
    all_metrics : list[dict]
        Per-carrier metrics from the pipeline run.
    scan_date : str
        Run date string.

    Returns
    -------
    AIResult
        .summary contains the analysis text for the notification.
    """
    total_rows = sum(m["total_rows"] for m in all_metrics)
    total_exc = sum(m["exceptions_count"] for m in all_metrics)
    rate = round(total_exc / total_rows * 100, 1) if total_rows > 0 else 0

    details = {
        "carrier_count": len(all_metrics),
        "total_rows": total_rows,
        "total_exceptions": total_exc,
        "exception_rate": rate,
    }

    # ── Build fallback (structured but no AI) ──
    fallback = _build_fallback_report(all_metrics, scan_date)

    # ── Build prompt ──
    prompt = build_run_report_prompt(all_metrics, scan_date)

    print(f"\n  [ai] Generating run report ({len(all_metrics)} carriers, {total_rows:,} rows)...")

    return _safe_ai_call(
        prompt=prompt,
        system_message=SYSTEM_MESSAGES["run_report"],
        category="run_report",
        fallback_summary=fallback,
        details=details,
    )


def _build_fallback_report(all_metrics: List[Dict], scan_date: str) -> str:
    """
    Rule-based fallback when AI is unavailable.
    Produces a structured analysis from the metrics alone.
    """
    total_rows = sum(m["total_rows"] for m in all_metrics)
    total_agents = sum(m["results_count"] for m in all_metrics)
    total_exc = sum(m["exceptions_count"] for m in all_metrics)
    total_missing = sum(m["missing_count"] for m in all_metrics)
    rate = round(total_exc / total_rows * 100, 1) if total_rows > 0 else 0

    lines = []

    # Overall
    clean = [m for m in all_metrics if m["exception_rate"] < 5 and m["status"] == "success"]
    lines.append(
        f"The {scan_date} run processed {len(all_metrics)} carriers with "
        f"{total_rows:,} total rows. {total_agents:,} agents were matched, "
        f"{total_exc:,} exceptions generated ({rate}%), and {total_missing:,} "
        f"agents in the database were not found in the current files. "
        f"{len(clean)} of {len(all_metrics)} carriers completed with exception rates under 5%."
    )

    # High exception carriers
    high_exc = sorted(
        [m for m in all_metrics if m["exception_rate"] >= 10 and m["status"] not in ("value_change", "error")],
        key=lambda x: -x["exception_rate"],
    )
    if high_exc:
        lines.append("")
        lines.append("Carriers with elevated exception rates:")
        for m in high_exc:
            contracts = m.get("contracts_loaded", "unknown")
            coverage_note = ""
            if isinstance(contracts, int) and contracts > 0 and m["total_rows"] > 0:
                cov = round(contracts / m["total_rows"] * 100, 1)
                if cov < 80:
                    coverage_note = (
                        f" Contract coverage is {cov}% ({contracts} contracts "
                        f"for {m['total_rows']} file rows), which likely explains the gap."
                    )
            top_cat = ""
            cats = m.get("exception_categories", {})
            if cats:
                top = max(cats, key=cats.get)
                top_cat = f" Primary exception type: {top} ({cats[top]})."
            lines.append(
                f"  {m['carrier_name']}: {m['exception_rate']}% exception rate "
                f"({m['exceptions_count']} of {m['total_rows']} rows).{top_cat}{coverage_note}"
            )

    # Halted / errored
    halted = [m for m in all_metrics if m["status"] == "value_change"]
    errored = [m for m in all_metrics if m["status"] == "error"]
    no_contracts = [m for m in all_metrics if m["status"] == "no_contracts"]

    if halted or errored or no_contracts:
        lines.append("")
        lines.append("Carriers requiring attention:")
        for m in halted:
            lines.append(
                f"  {m['carrier_name']}: Processing halted due to value map mismatch. "
                f"{'; '.join(m.get('errors', []))}"
            )
        for m in errored:
            lines.append(
                f"  {m['carrier_name']}: Runtime error. {'; '.join(m.get('errors', []))}"
            )
        for m in no_contracts:
            lines.append(
                f"  {m['carrier_name']}: No contracts found in database for "
                f"carrier_id {m.get('carrier_id', 'unknown')}."
            )

    # Zero-result
    zero = [m for m in all_metrics if m["results_count"] == 0 and m["status"] not in ("value_change", "error")]
    if zero:
        lines.append("")
        lines.append("Carriers with zero matched agents:")
        for m in zero:
            if m["exceptions_count"] == 0 and m["total_rows"] > 0:
                lines.append(
                    f"  {m['carrier_name']}: {m['total_rows']} rows in file, 0 agents matched, "
                    f"0 exceptions. Rows were filtered out before identity resolution, which "
                    f"typically indicates the primary_identity_field is misconfigured "
                    f"(e.g., set to NPN when the carrier uses writing numbers)."
                )
            elif m["total_rows"] == 0:
                lines.append(
                    f"  {m['carrier_name']}: No rows after filtering. Check file format, "
                    f"sheet name, and filter configuration."
                )
            else:
                lines.append(
                    f"  {m['carrier_name']}: {m['total_rows']} rows, {m['exceptions_count']} exceptions, "
                    f"0 matched. Review contract data for carrier_id {m.get('carrier_id', 'unknown')}."
                )

    # Notable variance
    notable_var = [m for m in all_metrics if m.get("variance_pct") and m["variance_pct"] >= 20]
    if notable_var:
        lines.append("")
        lines.append("Notable row count changes from previous run:")
        for m in sorted(notable_var, key=lambda x: -x.get("variance_pct", 0)):
            prev = m.get("previous_row_count", "?")
            direction = "increase" if m["total_rows"] > (prev or 0) else "decrease"
            lines.append(
                f"  {m['carrier_name']}: {prev} -> {m['total_rows']} rows "
                f"({m['variance_pct']}% {direction})."
            )

    return "\n".join(lines)
