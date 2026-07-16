# semantic_registry.py

from typing import Dict

DEFAULT_LIMIT = 50
FORCE_LIMIT = True

ALLOWED_KEYWORDS = [
    "SELECT",
    "DISTINCT",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "FROM",
    "WHERE",
    "GROUP BY",
    "HAVING",
    "ORDER BY",
    "LIMIT",
    "OFFSET",
    "CROSS JOIN LATERAL",
    "UNNEST",
]

BLOCKED_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "COPY",
    "CREATE",
    "UNION",
    ";",
]

# Filter types:
# numeric
# exact_text
# categorical_strict
# categorical_token
# boolean_flag
# date
# state_flag
# dynamic_carrier
# dynamic_upline
# dynamic_top_upline
# dynamic_agent
# dynamic_member  ← NEW

GLOBAL_ENTITIES = {
    "agent": {
        "table": "wpo.lup_agents",
        "primary_key": "npn",
        "display_column": "full_name",
    },

    # NEW: Carrier resolution
    "carrier_name": {
        "table": "wpo.vw_com_items_ai",
        "primary_key": "carrier_name",
        "display_column": "carrier_name",
    },

    # NEW: Commission Member resolution
    "commission_member": {
        "table": "wpo.vw_com_items_ai",
        "primary_key": "account_number",
        "display_column": "insured_name",
    },
}

MODULES: Dict[str, Dict] = {
    "bob": {
        "table": "analytic_vault.bob_carrier_memberships_vw",
        "grain": "member_snapshot",

        "metrics": {
        "count_records": {"expression": "COUNT(*)"},
        "sum_contracts": {"expression": "SUM(contract_count)"},
        "sum_members": {"expression": "SUM(mem_count)"},
        "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "agent_npn": "agent_npn",
            "agent_full_name": "agent_full_name",
            "agent_recruiter": "agent_recruiter",
            "direct_upline_name": "direct_upline_name",
            "top_upline_name": "top_upline_name",
            "carrier_short_name": "carrier_short_name",
            "mem_name": "mem_name",
            "mem_state": "mem_state",
            "mem_age": "mem_age",
            "AgeCategory": "AgeCategory",
            "product_type_mapped": "product_type_mapped",
            "carrier_status": "carrier_status",
            "payment_status": "payment_status",
            "report_date": "report_date",
            "report_mon_year": "report_mon_year",
        },

        "entity_links": {
            "agent": {"local_key": "agent_npn"}
        },

        "filters": {
            "agent": {"column": None, "type": "dynamic_agent"},
            "member": {"column": None, "type": "dynamic_member"},
            "carrier_name": {"column": "carrier_short_name", "type": "categorical_token"},
            "report_date": {"column": "report_date", "type": "month_year"},
            "relative_time": {"column": None, "type": "logical_time"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "agent_npn",
            "agent_full_name",
            "mem_name",
            "carrier_short_name",
        ],
    },

    # =====================================================
    # COMMISSION ITEMS
    # =====================================================
    "commission_items": {
        "table": "wpo.vw_com_items_ai",
        "grain": "commission_line",

        "metrics": {
            "sum_commission": {"expression": "SUM(payment)"},
            "count_records": {"expression": "COUNT(*)"},
            "count_agents": {"expression": "COUNT(DISTINCT npn)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "carrier_name": "carrier_name",
            "npn": "npn",
            "agent_name": "agent_name",
            "writing_agent": "writing_agent",
            "policy_state": "policy_state",
            "coverage_month": "coverage_month",
            "market": "market",
            "insured_name": "insured_name",
            "account_number": "account_number",
            "premium": "premium",
            "payment": "payment",
            "report_date": "report_date",
            "upline_name": "upline_name",
            "top_upline_name": "top_upline_name",
        },

        "entity_links": {
            "agent": {"local_key": "npn"}
        },

        "filters": {
            "agent": {"column": None, "type": "dynamic_agent"},
            "member": {"column": None, "type": "dynamic_member"},
            "carrier_name": {"column": "carrier_name", "type": "categorical_token"},
            "account_number": {"column": "account_number", "type": "exact_text"},
            "coverage_month": {"column": "coverage_month", "type": "categorical_strict"},
            "report_date": {"column": "report_date", "type": "date"},
            "relative_time": {"column": None, "type": "logical_time"},
        },
        

        "state_expansion": {},
        "searchable_fields": [
            "npn",
            "agent_name",
            "carrier_name",
            "insured_name",
            "account_number",
        ],
    },

    # =====================================================
    # COMMISSION TOTALS
    # =====================================================
    "commission_totals": {
        "table": "wpo.com_totals",
        "grain": "statement_summary",

        "metrics": {
            "sum_statement_total": {"expression": "SUM(statement_total)"},
            "count_statements": {"expression": "COUNT(*)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "carrier_name": "carrier_name",
            "statement_month": "statement_month",
            "statement_date": "statement_date",
            "agent_npn": "agent_npn",
            "agent_name": "agent_name",
            "status": "status",
            "statement_type": "statement_type",
            "statement_total": "statement_total",
        },

        "entity_links": {
            "agent": {"local_key": "agent_npn"}
        },

        "filters": {
            "agent": {"column": None, "type": "dynamic_agent"},
            "carrier_name": {"column": "carrier_name", "type": "categorical_token"},
            "statement_month": {"column": "statement_month", "type": "month_year"},
            "statement_date": {"column": "statement_date", "type": "date"},
            "relative_time": {"column": None, "type": "logical_time"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "carrier_name",
            "agent_npn",
            "agent_name",
            "statement_month",
        ],
    },

    # =====================================================
    # AGENTS CONTRACTS
    # =====================================================
    "agents_contracts": {
        "table": "wpo.agents_contracts_view",
        "grain": "agent_contract",
        "resolve_key": "npn",
        "display_field": "full_name",

        "metrics": {
            "count_agents": {"expression": "COUNT(DISTINCT npn)"},
            "count_contracts": {"expression": "COUNT(*)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "carrier": "carrier",
            "agent_status": "agent_status",
            "contract_status": "status",
            "upline": "upline_npn",
            "top_upline": "top_upline_npn",
            "company_name": "company_name",
            "appointment_type": "appointment_type",
            "affiliation": "affiliation",
            "assignee": "assignee",
            "writing_number": "writing_number",
            "npn": "npn",
            "preferred_language": "preferred_language",
            "field_sales_director": "field_sales_director",
            "marketer": "marketer",
            "commission_schedule_status": "commission_schedule_status",
            "current_or_sch_status": "current_or_sch_status",
            "status_date": "status_date",
            "created_time": "created_time",
            "modified_time": "modified_time",
        },
        "entity_links": {
            "agent": {
                "local_key": "npn"  # or agent_npn
            }
        },

        "filters": {

            # -----------------------------
            # LOGICAL (LLM) ROUTING FIELDS
            # -----------------------------
            "carrier": {"column": None, "type": "dynamic_carrier"},
            "upline": {"column": None, "type": "dynamic_upline"},
            "top_upline": {"column": None, "type": "dynamic_top_upline"},

            # -----------------------------
            # TECHNICAL FILTERS (SQL)
            # -----------------------------
            "carrier_id": {"column": "carrier", "type": "categorical_strict"},
            "carrier_name": {"column": "carrier_name", "type": "categorical_token"},

            "upline_npn": {"column": "upline_npn", "type": "exact_text"},
            "upline_full_name": {"column": "upline_full_name", "type": "categorical_token"},

            "top_upline_npn": {"column": "top_upline_npn", "type": "exact_text"},
            "top_upline_full_name": {"column": "top_upline_full_name", "type": "categorical_token"},

            "agent_status": {"column": "agent_status", "type": "categorical_token"},
            "contract_status": {"column": "status", "type": "categorical_strict"},
            "npn": {"column": "npn", "type": "exact_text"},
            "writing_number": {"column": "writing_number", "type": "exact_text"},
            "appointment_type": {"column": "appointment_type", "type": "categorical_strict"},
            "affiliation": {"column": "affiliation", "type": "categorical_token"},
            "assignee": {"column": "assignee", "type": "categorical_token", "resolve": True},
            "created_time": {"column": "created_time", "type": "date"},
            "status_date": {"column": "status_date", "type": "date"},
            "modified_time": {"column": "modified_time", "type": "date"},
            "agent": {"column": None, "type": "dynamic_agent"},

        },

        "state_expansion": {
            "appointed_states": {"column": "appointed_states", "requires_unnest": True},
            "requested_states": {"column": "requested_states", "requires_unnest": True},
        },

        "searchable_fields": [
            "npn",
            "writing_number",
            "company_name",
            "upline_full_name",
            "top_upline_full_name",
            "carrier",
            "first_name",
            "last_name",
            "assignee",
        ],
    },

    # =====================================================
    # AGENTS MASTER
    # =====================================================
    "agents": {
        "table": "wpo.lup_agents",
        "grain": "agent_master",
        "resolve_key": "npn",
        "display_field": "full_name",

        "metrics": {
            "count_agents": {"expression": "COUNT(*)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "npn": "npn",
            "full_name": "full_name",
            "first_name": "first_name",
            "last_name": "last_name",
            "email": "email",
            "phone": "phone",
            "mobile": "mobile",
            "agent_status": "status",
            "preferred_language": "preferred_language",
            "permission_to_text": "permission_to_text",
            "do_not_call": "do_not_call",
            "email_opt_out": "email_opt_out",

            "alabama": "alabama",
            "alaska": "alaska",
            "arizona": "arizona",
            "arkansas": "arkansas",
            "california": "california",
            "colorado": "colorado",
            "connecticut": "connecticut",
            "delaware": "delaware",
            "district_of_columbia": "district_of_columbia",
            "florida": "florida",
            "georgia": "georgia",
            "hawaii": "hawaii",
            "idaho": "idaho",
            "illinois": "illinois",
            "indiana": "indiana",
            "iowa": "iowa",
            "kansas": "kansas",
            "kentucky": "kentucky",
            "maine": "maine",
            "maryland": "maryland",
            "massachusetts": "massachusetts",
            "michigan": "michigan",
            "minnesota": "minnesota",
            "mississippi": "mississippi",
            "missouri": "missouri",
            "montana": "montana",
            "nebraska": "nebraska",
            "nevada": "nevada",
            "new_hampshire": "new_hampshire",
            "new_jersey": "new_jersey",
            "new_mexico": "new_mexico",
            "new_york": "new_york",
            "north_carolina": "north_carolina",
            "north_dakota": "north_dakota",
            "ohio": "ohio",
            "oklahoma": "oklahoma",
            "oregon": "oregon",
            "pennsylvania": "pennsylvania",
            "rhode_island": "rhode_island",
            "south_carolina": "south_carolina",
            "south_dakota": "south_dakota",
            "tennessee": "tennessee",
            "texas": "texas",
            "utah": "utah",
            "vermont": "vermont",
            "virginia": "virginia",
            "washington": "washington",
            "west_virginia": "west_virginia",
            "wisconsin": "wisconsin",
            "wyoming": "wyoming",
        },
        "entity_links": {
            "agent": {
                "local_key": "npn",
                "direct": True
            }
        },

        "filters": {
            "npn": {"column": "npn", "type": "exact_text"},
            "agent_status": {"column": "status", "type": "categorical_token"},
            "preferred_language": {"column": "preferred_language", "type": "categorical_strict"},
            "permission_to_text": {"column": "permission_to_text", "type": "boolean_flag"},
            "do_not_call": {"column": "do_not_call", "type": "boolean_flag"},
            "email_opt_out": {"column": "email_opt_out", "type": "boolean_flag"},
            "agent": {"column": None, "type": "dynamic_agent"},

            **{
                state: {"column": state, "type": "state_flag"}
                for state in [
                    "alabama","alaska","arizona","arkansas","california","colorado",
                    "connecticut","delaware","district_of_columbia","florida",
                    "georgia","hawaii","idaho","illinois","indiana","iowa",
                    "kansas","kentucky","maine","maryland","massachusetts",
                    "michigan","minnesota","mississippi","missouri","montana",
                    "nebraska","nevada","new_hampshire","new_jersey","new_mexico",
                    "new_york","north_carolina","north_dakota","ohio","oklahoma",
                    "oregon","pennsylvania","rhode_island","south_carolina",
                    "south_dakota","tennessee","texas","utah","vermont",
                    "virginia","washington","west_virginia","wisconsin","wyoming"
                ]
            },
        },

        "state_expansion": {},
        "searchable_fields": [
            "npn",
            "full_name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "mobile",
        ],
    },

    # =====================================================
    # LICENSES
    # =====================================================
    "licenses": {
        "table": "wpo.lup_agent_licenses",
        "grain": "agent_license",
        "resolve_key": "agent_npn",
        "display_field": "license_number",

        "metrics": {
            "count_licenses": {"expression": "COUNT(*)"},
            "count_distinct_agents": {"expression": "COUNT(DISTINCT agent_npn)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "transaction_id": "transaction_id",
            "agent_npn": "agent_npn",
            "lic_id": "lic_id",
            "license_type": "type",
            "license_status": "status",
            "license_state": "state",
            "issue_date": "issue_date",
            "expiry_date": "expiry_date",
            "license_number": "license_number",
            "license_owner": "license_owner",
            "license_market": "license_market",
            "certification_year": "certification_year",
            "npn_valid": "npn_valid",
        },
        "entity_links": {
            "agent": {
                "local_key": "agent_npn"  # or agent_npn
            }
        },

        "filters": {
            "agent_npn": {"column": "agent_npn", "type": "exact_text"},
            "license_type": {"column": "type", "type": "categorical_strict"},
            "license_status": {"column": "status", "type": "categorical_token"},
            "license_state": {"column": "state", "type": "categorical_strict"},
            "license_number": {"column": "license_number", "type": "exact_text"},
            "npn_valid": {"column": "npn_valid", "type": "boolean_flag"},
            "certification_year": {"column": "certification_year", "type": "numeric"},
            "agent": {"column": None, "type": "dynamic_agent"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "agent_npn",
            "license_number",
            "license_owner",
        ],
    },
}