"""
JAI Dashboard Insight Templates — predefined queries for module-level dashboards.

When the LLM planner fails or the user asks for generic "insights" on a module,
these templates provide 3-4 diverse metrics (KPI, distribution, ranking, recent
activity) so the frontend can render a meaningful dashboard.

Each template defines:
- title / description: human-readable label
- sql_template: ready-to-execute SQL with {SCOPE_FILTER} placeholder
- format_hint: display format (bar_chart, pie_chart, table, kpi)
- metric_columns / dimension_columns: column roles for the format engine

NOTE on BOB (Synapse/T-SQL): uses TOP N instead of LIMIT, and {SCOPE_FILTER}
is injected differently by the executor.
"""

from typing import Any, Dict, List


# ============================================================================
# INSIGHT TEMPLATES — keyed by module name
# ============================================================================

INSIGHT_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {

    # -----------------------------------------------------------------------
    # AGENTS  (wpo.lup_agents)
    # -----------------------------------------------------------------------
    "agents": [
        {
            "title": "Agent Status Distribution",
            "description": "Breakdown of agents by current status.",
            "sql_template": (
                "SELECT t.status, COUNT(*) AS agent_count "
                "FROM wpo.lup_agents t "
                "WHERE {SCOPE_FILTER} "
                "GROUP BY t.status "
                "ORDER BY agent_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "pie_chart",
            "metric_columns": ["agent_count"],
            "dimension_columns": ["status"],
        },
        {
            "title": "Top States by Agent Count",
            "description": "States with the most licensed agents.",
            "sql_template": (
                "SELECT l.state, COUNT(DISTINCT t.npn) AS agent_count "
                "FROM wpo.lup_agents t "
                "JOIN wpo.lup_agent_licenses l ON t.npn = l.agent_npn "
                "WHERE {SCOPE_FILTER} AND l.state IS NOT NULL AND l.type = 'lic' "
                "GROUP BY l.state "
                "ORDER BY agent_count DESC "
                "LIMIT 15"
            ),
            "format_hint": "bar_chart",
            "metric_columns": ["agent_count"],
            "dimension_columns": ["state"],
        },
        {
            "title": "Recent Agent Activity",
            "description": "Most recently created agent records.",
            "sql_template": (
                "SELECT t.first_name, t.last_name, t.status, t.created_time "
                "FROM wpo.lup_agents t "
                "WHERE {SCOPE_FILTER} "
                "ORDER BY t.created_time DESC "
                "LIMIT 20"
            ),
            "format_hint": "table",
            "metric_columns": [],
            "dimension_columns": ["first_name", "last_name", "status", "created_time"],
        },
        {
            "title": "Total Agents",
            "description": "Overall agent count.",
            "sql_template": (
                "SELECT COUNT(*) AS total_agents "
                "FROM wpo.lup_agents t "
                "WHERE {SCOPE_FILTER}"
            ),
            "format_hint": "kpi",
            "metric_columns": ["total_agents"],
            "dimension_columns": [],
        },
    ],

    # -----------------------------------------------------------------------
    # COMMISSION ITEMS  (wpo.vw_com_items_ai)
    # -----------------------------------------------------------------------
    "commission_items": [
        {
            "title": "Revenue by Carrier",
            "description": "Top 10 carriers by total commission payment.",
            "sql_template": (
                "SELECT t.carrier_name, SUM(t.payment::NUMERIC) AS total_payment "
                "FROM wpo.vw_com_items_ai t "
                "WHERE {SCOPE_FILTER} AND t.carrier_name IS NOT NULL "
                "GROUP BY t.carrier_name "
                "ORDER BY total_payment DESC "
                "LIMIT 10"
            ),
            "format_hint": "bar_chart",
            "metric_columns": ["total_payment"],
            "dimension_columns": ["carrier_name"],
        },
        {
            "title": "Payment Type Breakdown",
            "description": "Commission vs Override vs Bonus distribution.",
            "sql_template": (
                "SELECT t.payment_type, SUM(t.payment::NUMERIC) AS total_payment "
                "FROM wpo.vw_com_items_ai t "
                "WHERE {SCOPE_FILTER} AND t.payment_type IS NOT NULL "
                "GROUP BY t.payment_type "
                "ORDER BY total_payment DESC "
                "LIMIT 50"
            ),
            "format_hint": "pie_chart",
            "metric_columns": ["total_payment"],
            "dimension_columns": ["payment_type"],
        },
        {
            "title": "Top Performing Agents",
            "description": "Top 10 agents by total commission earned.",
            "sql_template": (
                "SELECT t.agent_name, t.npn, SUM(t.payment::NUMERIC) AS total_commission "
                "FROM wpo.vw_com_items_ai t "
                "WHERE {SCOPE_FILTER} AND t.agent_name IS NOT NULL "
                "GROUP BY t.agent_name, t.npn "
                "ORDER BY total_commission DESC "
                "LIMIT 10"
            ),
            "format_hint": "table",
            "metric_columns": ["total_commission"],
            "dimension_columns": ["agent_name", "npn"],
        },
        {
            "title": "Total Revenue",
            "description": "Overall commission revenue and line item count.",
            "sql_template": (
                "SELECT SUM(t.payment::NUMERIC) AS total_revenue, COUNT(*) AS total_items "
                "FROM wpo.vw_com_items_ai t "
                "WHERE {SCOPE_FILTER}"
            ),
            "format_hint": "kpi",
            "metric_columns": ["total_revenue", "total_items"],
            "dimension_columns": [],
        },
    ],

    # -----------------------------------------------------------------------
    # COMMISSION TOTALS  (wpo.com_totals)
    # -----------------------------------------------------------------------
    "commission_totals": [
        {
            "title": "Payment Status Distribution",
            "description": "Statement count by payment status (Paid, On Hold, etc.).",
            "sql_template": (
                "SELECT t.status, COUNT(*) AS statement_count "
                "FROM wpo.com_totals t "
                "WHERE {SCOPE_FILTER} AND t.status IS NOT NULL "
                "GROUP BY t.status "
                "ORDER BY statement_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "pie_chart",
            "metric_columns": ["statement_count"],
            "dimension_columns": ["status"],
        },
        {
            "title": "Top Carriers by Payout",
            "description": "Top 10 carriers by total statement payout.",
            "sql_template": (
                "SELECT t.carrier_name, SUM(t.statement_total::NUMERIC) AS total_payout "
                "FROM wpo.com_totals t "
                "WHERE {SCOPE_FILTER} AND t.carrier_name IS NOT NULL "
                "GROUP BY t.carrier_name "
                "ORDER BY total_payout DESC "
                "LIMIT 10"
            ),
            "format_hint": "bar_chart",
            "metric_columns": ["total_payout"],
            "dimension_columns": ["carrier_name"],
        },
        {
            "title": "Recent Statements",
            "description": "Latest 20 commission statements.",
            "sql_template": (
                "SELECT t.carrier_name, t.agent_name, t.statement_total, t.status, t.statement_month "
                "FROM wpo.com_totals t "
                "WHERE {SCOPE_FILTER} "
                "ORDER BY t.report_date DESC "
                "LIMIT 20"
            ),
            "format_hint": "table",
            "metric_columns": ["statement_total"],
            "dimension_columns": ["carrier_name", "agent_name", "status", "statement_month"],
        },
        {
            "title": "Total Payout",
            "description": "Overall commission payout total.",
            "sql_template": (
                "SELECT SUM(t.statement_total::NUMERIC) AS total_payout "
                "FROM wpo.com_totals t "
                "WHERE {SCOPE_FILTER}"
            ),
            "format_hint": "kpi",
            "metric_columns": ["total_payout"],
            "dimension_columns": [],
        },
    ],

    # -----------------------------------------------------------------------
    # PCH PROVIDERS  (wpo.pch_provider_info)
    # Default filter: status != 'Lead' (leads are bulk NPI imports, ~6.8M rows)
    # -----------------------------------------------------------------------
    "pch_providers": [
        {
            "title": "Specialty Distribution",
            "description": "Provider count by primary specialty (excluding leads).",
            "sql_template": (
                "SELECT t.primary_speciality, COUNT(*) AS provider_count "
                "FROM wpo.pch_provider_info t "
                "WHERE {SCOPE_FILTER} AND t.status != 'Lead' AND t.primary_speciality IS NOT NULL "
                "GROUP BY t.primary_speciality "
                "ORDER BY provider_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "bar_chart",
            "metric_columns": ["provider_count"],
            "dimension_columns": ["primary_speciality"],
        },
        {
            "title": "Provider Status Breakdown",
            "description": "Provider count by status (excluding leads).",
            "sql_template": (
                "SELECT t.status, COUNT(*) AS provider_count "
                "FROM wpo.pch_provider_info t "
                "WHERE {SCOPE_FILTER} AND t.status != 'Lead' "
                "GROUP BY t.status "
                "ORDER BY provider_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "pie_chart",
            "metric_columns": ["provider_count"],
            "dimension_columns": ["status"],
        },
        {
            "title": "Geographic Coverage",
            "description": "Provider count by state (excluding leads).",
            "sql_template": (
                "SELECT t.state, COUNT(*) AS provider_count "
                "FROM wpo.pch_provider_info t "
                "WHERE {SCOPE_FILTER} AND t.status != 'Lead' AND t.state IS NOT NULL "
                "GROUP BY t.state "
                "ORDER BY provider_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "bar_chart",
            "metric_columns": ["provider_count"],
            "dimension_columns": ["state"],
        },
        {
            "title": "Total Providers",
            "description": "Overall provider count (excluding leads).",
            "sql_template": (
                "SELECT COUNT(*) AS total_providers "
                "FROM wpo.pch_provider_info t "
                "WHERE {SCOPE_FILTER} AND t.status != 'Lead'"
            ),
            "format_hint": "kpi",
            "metric_columns": ["total_providers"],
            "dimension_columns": [],
        },
    ],

    # -----------------------------------------------------------------------
    # PCH MEMBERS  (wpo.pch_member_roster)
    # -----------------------------------------------------------------------
    "pch_members": [
        {
            "title": "Product Enrollment",
            "description": "Member count by health plan product.",
            "sql_template": (
                "SELECT t.product, COUNT(*) AS member_count "
                "FROM wpo.pch_member_roster t "
                "WHERE {SCOPE_FILTER} AND t.product IS NOT NULL "
                "GROUP BY t.product "
                "ORDER BY member_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "pie_chart",
            "metric_columns": ["member_count"],
            "dimension_columns": ["product"],
        },
        {
            "title": "Health Risk Profile",
            "description": "Member count by population health category.",
            "sql_template": (
                "SELECT t.population_health_category, COUNT(*) AS member_count "
                "FROM wpo.pch_member_roster t "
                "WHERE {SCOPE_FILTER} AND t.population_health_category IS NOT NULL "
                "GROUP BY t.population_health_category "
                "ORDER BY member_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "bar_chart",
            "metric_columns": ["member_count"],
            "dimension_columns": ["population_health_category"],
        },
        {
            "title": "Member Demographics",
            "description": "Gender breakdown and recent enrollments.",
            "sql_template": (
                "SELECT t.gender, t.line_of_business, COUNT(*) AS member_count "
                "FROM wpo.pch_member_roster t "
                "WHERE {SCOPE_FILTER} AND t.gender IS NOT NULL "
                "GROUP BY t.gender, t.line_of_business "
                "ORDER BY member_count DESC "
                "LIMIT 50"
            ),
            "format_hint": "table",
            "metric_columns": ["member_count"],
            "dimension_columns": ["gender", "line_of_business"],
        },
        {
            "title": "Total Members",
            "description": "Overall member count.",
            "sql_template": (
                "SELECT COUNT(*) AS total_members "
                "FROM wpo.pch_member_roster t "
                "WHERE {SCOPE_FILTER}"
            ),
            "format_hint": "kpi",
            "metric_columns": ["total_members"],
            "dimension_columns": [],
        },
    ],

    # -----------------------------------------------------------------------
    # BOB — Book of Business  (analytic_vault.bob_carrier_memberships_vw)
    # Synapse DB: uses TOP N instead of LIMIT
    # -----------------------------------------------------------------------
    "bob": [
        {
            "title": "Members by Carrier",
            "description": "Total member count by carrier.",
            "sql_template": (
                "SELECT TOP 15 t.carrier_short_name, SUM(t.mem_count) AS total_members "
                "FROM analytic_vault.bob_carrier_memberships_vw t "
                "WHERE {SCOPE_FILTER} "
                "GROUP BY t.carrier_short_name "
                "ORDER BY total_members DESC"
            ),
            "format_hint": "bar_chart",
            "metric_columns": ["total_members"],
            "dimension_columns": ["carrier_short_name"],
        },
        {
            "title": "Product Type Distribution",
            "description": "Member count by product type (MAPD, PDP, Med Supp).",
            "sql_template": (
                "SELECT TOP 50 t.product_type_mapped, SUM(t.mem_count) AS total_members "
                "FROM analytic_vault.bob_carrier_memberships_vw t "
                "WHERE {SCOPE_FILTER} AND t.product_type_mapped IS NOT NULL "
                "GROUP BY t.product_type_mapped "
                "ORDER BY total_members DESC"
            ),
            "format_hint": "pie_chart",
            "metric_columns": ["total_members"],
            "dimension_columns": ["product_type_mapped"],
        },
        {
            "title": "Top Agents by Members",
            "description": "Top 10 agents by total member count.",
            "sql_template": (
                "SELECT TOP 10 t.agent_full_name, t.agent_npn, SUM(t.mem_count) AS total_members "
                "FROM analytic_vault.bob_carrier_memberships_vw t "
                "WHERE {SCOPE_FILTER} AND t.agent_full_name IS NOT NULL "
                "GROUP BY t.agent_full_name, t.agent_npn "
                "ORDER BY total_members DESC"
            ),
            "format_hint": "table",
            "metric_columns": ["total_members"],
            "dimension_columns": ["agent_full_name", "agent_npn"],
        },
        {
            "title": "Total Book of Business",
            "description": "Overall member count in book of business.",
            "sql_template": (
                "SELECT SUM(t.mem_count) AS total_members "
                "FROM analytic_vault.bob_carrier_memberships_vw t "
                "WHERE {SCOPE_FILTER}"
            ),
            "format_hint": "kpi",
            "metric_columns": ["total_members"],
            "dimension_columns": [],
        },
    ],
}


# Module -> database type mapping
_MODULE_DB_TYPES: Dict[str, str] = {
    "agents": "postgres",
    "commission_items": "postgres",
    "commission_totals": "postgres",
    "pch_providers": "postgres",
    "pch_members": "postgres",
    "bob": "synapse",
}


def get_insight_templates(module: str) -> List[Dict[str, Any]]:
    """Return insight templates for a module, or empty list if none defined."""
    return INSIGHT_TEMPLATES.get(module, [])


def get_db_type_for_module(module: str) -> str:
    """Return 'synapse' for BOB module, 'postgres' for everything else."""
    return _MODULE_DB_TYPES.get(module, "postgres")
