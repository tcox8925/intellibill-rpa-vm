# ==========================================================
#  intelligence/prompt_templates.py
# ==========================================================
"""
prompt_templates.py — v2
------------------------
Single rich prompt for the full run report.
AI gets the complete picture and writes the analysis.

Schema and identity prompts retained for future use.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


# ==========================================================
#  AI RESULT
# ==========================================================
@dataclass
class AIResult:
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    category: str = ""
    success: bool = True
    raw_prompt: str = ""
    raw_response: str = ""


# ==========================================================
#  SYSTEM MESSAGES
# ==========================================================
SYSTEM_MESSAGES = {
    "schema": (
        "You are a data ops assistant at an insurance operations company. "
        "Analyze column schema changes and suggest canonical mappings. "
        "Be concise but thorough. List each change with its suggested mapping "
        "and confidence (high/med/low). End with recommended actions."
    ),
    "identity": (
        "You are a data ops assistant. Rank identity match candidates by likelihood. "
        "Be concise. Rank candidates with one-line reasoning each. "
        "End with a recommendation."
    ),
    "run_report": (
        "You are a senior data operations analyst at 834 Labs, an insurance services company. "
        "You are writing the analysis section of an ACU (Agent Contract Update) pipeline report.\n\n"

        "CONTEXT:\n"
        "The ACU pipeline processes carrier roster files (Excel/CSV from insurance carriers) "
        "and matches agents in those files to contracts in our internal database (lup_agents_contracts). "
        "Matching is done by NPN (National Producer Number) or writing number, depending on the carrier's "
        "primary_identity_field setting. Each carrier has a carrier_id linking to contracts.\n\n"

        "KEY TERMS:\n"
        "- Agents matched: file rows successfully matched to a contract in our DB\n"
        "- Exceptions: file rows that could NOT be matched\n"
        "- Missing: agents with contracts in our DB who were NOT in the carrier's file (normal — "
        "not every contracted agent appears in every roster pull)\n"
        "- Contract coverage: contracts_loaded / total_rows. Below 50% = major gap. "
        "Above 100% is normal — it means more agents have contracts than appeared in this file.\n"
        "- identity_not_found: agent's NPN or writing number has no matching contract for this carrier_id\n"
        "- identity_multiple_match: agent matched more than one contract — ambiguous\n"
        "- contract_status_excluded: agent matched a contract but contract status is excluded (e.g., On Hold) — cannot be updated\n"
        "- agent_status_excluded: agent matched a contract but agent-level status in lup_agents is excluded (e.g., Quarantined, Suspended)\n"
        "- agent_not_in_registry: agent matched a contract but does not exist in lup_agents at all\n"
        "- blocked_transition: agent's status transition is blocked. Two rules: (1) terminated/cancelled -> active, (2) active -> anything except terminated/inactive. These agents are excluded from results.\n"
        "- parent_not_resolved: agent's parent identifier (WR, name, or NPN) could not be resolved — either not found in contracts or matched multiple contracts. Agent is excluded from results.\n"
        "- status=threshold_exceeded: exception rate exceeded 10% — the carrier still processed, this is a flag not a halt\n"
        "- status=value_change: carrier was BLOCKED from processing because file values didn't match config\n"
        "- status=no_data / no_contracts: no rows remained after filtering, or no contracts exist in DB\n\n"

        "PATTERN RECOGNITION (use these to diagnose root causes):\n"
        "- High exceptions + low coverage (<50%): Root cause is missing contracts in the DB, not a config issue. "
        "Recommendation: populate contracts for this carrier_id.\n"
        "- 0 agents + 0 exceptions + rows > 0: Rows were filtered out BEFORE identity resolution. "
        "This usually means the primary_identity_field is misconfigured (e.g., set to NPN when the carrier "
        "uses writing numbers, or vice versa) — all rows fail the dedup/filter step silently.\n"
        "- High exceptions + high coverage (>80%): The contracts exist but matching is failing — likely "
        "data quality (name mismatches, stale NPNs, duplicate records).\n"
        "- High missing count is usually normal — it means many contracted agents weren't in today's file.\n"
        "- Row variance (previous_rows vs current rows): Large increases may mean a new LOB or file format change. "
        "Large decreases may mean a partial file or filtering issue. Mention notable variances (>20%) in the analysis.\n\n"

        "AUDIENCE: Operations team lead and business lead.\n\n"

        "RULES:\n"
        "- Write in clear, professional prose. No emoji. No markdown. No bullet points or headers.\n"
        "- Use the actual numbers from the data. Do not round aggressively or say 'approximately'.\n"
        "- For each problem carrier, state the root cause based on the patterns above.\n"
        "- Recommendations must be specific and actionable (e.g., 'populate contracts for carrier_id X', "
        "'change primary_identity_field to WR for carrier Y') — not generic advice like 'review and update'.\n"
        "- Do NOT say threshold_exceeded halts processing — it does not.\n"
        "- Keep the analysis to 3-4 focused paragraphs. No filler, no restatement of the summary numbers."
    ),
}


# ==========================================================
#  PROMPT BUILDERS
# ==========================================================

def build_schema_prompt(
        carrier_name: str,
        process_type: str,
        columns_added: List[str],
        columns_removed: List[str],
        canonical_columns: List[str],
        current_columns: List[str],
) -> str:
    return f"""A carrier file has changed its column structure.

CARRIER: {carrier_name} | PROCESS: {process_type}

COLUMNS ADDED: {', '.join(columns_added) if columns_added else '(none)'}
COLUMNS REMOVED: {', '.join(columns_removed) if columns_removed else '(none)'}
CANONICAL DB COLUMNS: {', '.join(canonical_columns)}
CURRENT FILE COLUMNS: {', '.join(current_columns)}

For each ADDED column, suggest which canonical column it maps to (with confidence).
For each REMOVED column, note which mapping is broken.
End with recommended actions."""


def build_identity_prompt(
        carrier_name: str,
        unmatched_row: Dict[str, Any],
        candidates: List[Dict[str, Any]],
) -> str:
    row_str = ", ".join(f"{k}={v}" for k, v in unmatched_row.items() if v)
    cand_lines = []
    for i, c in enumerate(candidates, 1):
        cand_str = ", ".join(f"{k}={v}" for k, v in c.items() if v)
        cand_lines.append(f"  {i}. {cand_str}")

    return f"""Agent identity resolution found multiple matches.

CARRIER: {carrier_name}
UNMATCHED ROW: {row_str}

CANDIDATES:
{chr(10).join(cand_lines)}

Rank candidates from most to least likely. Explain reasoning. End with recommendation."""


def build_run_report_prompt(all_metrics: List[Dict], scan_date: str) -> str:
    """
    Build one comprehensive prompt with per-carrier data.
    The model sees the full picture and writes the analysis.
    """
    total_rows = sum(m["total_rows"] for m in all_metrics)
    total_agents = sum(m["results_count"] for m in all_metrics)
    total_exc = sum(m["exceptions_count"] for m in all_metrics)
    total_missing = sum(m["missing_count"] for m in all_metrics)
    rate = round(total_exc / total_rows * 100, 1) if total_rows > 0 else 0

    # ── Per-carrier table ──
    carrier_lines = []
    for m in sorted(all_metrics, key=lambda x: -x.get("exception_rate", 0)):
        cats = m.get("exception_categories", {})
        cats_str = ", ".join(f"{c}: {n}" for c, n in sorted(cats.items(), key=lambda x: -x[1])) if cats else "none"
        contracts = m.get("contracts_loaded", "unknown")
        coverage = ""
        if isinstance(contracts, int) and contracts > 0 and m["total_rows"] > 0:
            cov_pct = round(contracts / m["total_rows"] * 100, 1)
            coverage = f" ({cov_pct}% coverage)"

        prev = m.get("previous_row_count")
        vpct = m.get("variance_pct")

        carrier_lines.append(
            f"  {m['carrier_name']} [carrier_id={m.get('carrier_id', '?')}]: "
            f"rows={m['total_rows']}, agents={m['results_count']}, "
            f"exceptions={m['exceptions_count']} ({m['exception_rate']}%), "
            f"missing={m['missing_count']}, "
            f"contracts_loaded={contracts}{coverage}, "
            f"status={m['status']}, "
            f"exception_types=[{cats_str}]"
            f"{f', previous_rows={prev}, variance={vpct}%' if prev is not None else ''}"
        )
        if m.get("errors"):
            carrier_lines.append(f"    errors: {'; '.join(m['errors'])}")

    # ── Problem carriers ──
    zero_result = [m for m in all_metrics if m["results_count"] == 0 and m["status"] not in ("value_change", "error")]
    high_exc = [m for m in all_metrics if m["exception_rate"] >= 10 and m["status"] not in ("value_change", "error")]
    halted = [m for m in all_metrics if m["status"] == "value_change"]
    errored = [m for m in all_metrics if m["status"] == "error"]
    no_contracts = [m for m in all_metrics if m["status"] == "no_contracts"]

    problems = []
    if halted:
        problems.append("HALTED (value map mismatch, processing blocked):")
        for m in halted:
            problems.append(f"  {m['carrier_name']}: {'; '.join(m.get('errors', ['unknown']))}")
    if errored:
        problems.append("ERRORS (runtime failures):")
        for m in errored:
            problems.append(f"  {m['carrier_name']}: {'; '.join(m.get('errors', ['unknown']))}")
    if no_contracts:
        problems.append("NO CONTRACTS IN DB:")
        for m in no_contracts:
            problems.append(f"  {m['carrier_name']} (carrier_id: {m.get('carrier_id', '?')})")
    if zero_result:
        problems.append("ZERO AGENTS MATCHED (0 agents AND 0 exceptions = rows filtered before identity resolution):")
        for m in zero_result:
            problems.append(f"  {m['carrier_name']} ({m['total_rows']} rows in file, 0 matched, carrier_id={m.get('carrier_id', '?')})")
    if high_exc:
        problems.append("HIGH EXCEPTION RATE (>=10%):")
        for m in high_exc:
            contracts = m.get("contracts_loaded", "?")
            problems.append(f"  {m['carrier_name']}: {m['exception_rate']}% exceptions, contracts_loaded={contracts}, carrier_id={m.get('carrier_id', '?')}")

    notable_variance = [m for m in all_metrics if m.get("variance_pct") and m["variance_pct"] >= 20]
    if notable_variance:
        problems.append("NOTABLE ROW VARIANCE (>=20% change from previous run):")
        for m in sorted(notable_variance, key=lambda x: -x.get("variance_pct", 0)):
            prev = m.get("previous_row_count", "?")
            problems.append(f"  {m['carrier_name']}: {prev} -> {m['total_rows']} rows ({m['variance_pct']}% change)")

    return f"""Analyze this ACU pipeline run and write the analysis section of the report.

RUN DATE: {scan_date}
TOTALS: {len(all_metrics)} carriers, {total_rows:,} rows processed, {total_agents:,} agents matched, {total_exc:,} exceptions ({rate}%), {total_missing:,} missing

PER-CARRIER DATA (sorted by exception rate, highest first):
{chr(10).join(carrier_lines)}

PROBLEM CARRIERS:
{chr(10).join(problems) if problems else '  None.'}

Write 3-4 paragraphs:

1. Overall assessment: what percentage of carriers ran cleanly (<5% exceptions), and what is the overall health of this run. Do not repeat the summary numbers — the reader already sees them.

2. Problem carrier analysis: For each carrier with high exceptions, zero results, or failures, diagnose the root cause using the pattern rules in your system prompt. Reference specific numbers (contracts_loaded, coverage %, exception counts). Include the carrier_id when recommending contract population.

3. Specific, actionable recommendations: State exactly what needs to happen (e.g., "populate contracts for CHRISTUS-MDC carrier_id 2931751000382772962", "change primary_identity_field to WR for Medica"). Do not give generic advice like "review and update" or "ongoing monitoring"."""