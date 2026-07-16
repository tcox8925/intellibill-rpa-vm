"""
Jay Semantic Registry - Module definitions for safe parameterized SQL generation.

Each module defines:
- table: fully qualified table name
- grain: what each row represents
- metrics: aggregate expressions
- dimensions: available columns for grouping/selection
- filters: available filter types
- scope_columns: maps entity_id/sub_entity_id to actual column names for permission injection
- entity_links: dynamic entity resolution mappings
- searchable_fields: columns available for text search
"""

from typing import Dict

from app.utils.jay.business_knowledge import (
    SYNONYMS,
    BUSINESS_GLOSSARY,
    get_synonym_context,
    get_business_glossary_context,
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
FORCE_LIMIT = True

ALLOWED_KEYWORDS = [
    "SELECT", "DISTINCT", "COUNT", "SUM", "AVG", "MIN", "MAX",
    "FROM", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT", "OFFSET",
    "JOIN", "LEFT JOIN", "LEFT OUTER JOIN", "INNER JOIN", "RIGHT JOIN", "ON",
    "CROSS JOIN LATERAL", "UNNEST", "AS", "AND", "OR", "IN", "NOT",
    "LIKE", "ILIKE", "IS NULL", "IS NOT NULL", "BETWEEN", "CASE", "WHEN",
    "THEN", "ELSE", "END", "COALESCE", "NULLIF", "TRIM", "LOWER",
    "CAST", "INTERVAL", "CONCAT", "EXTRACT", "CEIL", "CEILING",
    "UNION ALL",
]

BLOCKED_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER",
    "COPY", "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
    "UNION",  # NOTE: UNION is blocked but UNION ALL is allowed — handled in sql_validator.py
]

GLOBAL_ENTITIES = {
    "agent": {
        "table": "wpo.lup_agents",
        "primary_key": "npn",
        "display_column": "full_name",
    },
    "carrier_name": {
        "table": "wpo.jay_carrier_lookup",
        "primary_key": "carrier_name",
        "display_column": "carrier_name",
    },
    "commission_member": {
        "table": "wpo.vw_com_items_ai",
        "primary_key": "account_number",
        "display_column": "insured_name",
    },
    "pch_provider": {
        "table": "wpo.pch_provider_info",
        "primary_key": "npi",
        "display_column": "first_name || ' ' || last_name",
        "display_template": "{display_value} (NPI: {resolve_value})",
    },
    "pch_member": {
        "table": "wpo.pch_member_roster",
        "primary_key": "amisys_number",
        "display_column": "first_name || ' ' || last_name",
        "display_template": "{display_value} ({resolve_value})",
    },
}


MODULES: Dict[str, Dict] = {

    # =====================================================
    # BOOK OF BUSINESS (Synapse)
    # =====================================================
    "bob": {
        "table": "analytic_vault.bob_carrier_memberships_vw",
        "grain": "member_snapshot",
        "db_type": "synapse",
        "domain": "bob",
        "scope_columns": {"entity_id": "entity_id"},  # scoped by entity_id to restrict agent book-of-business per organization
        "description": "Carrier membership snapshots: agent production, member counts, carrier enrollment. Monthly snapshots by report_date.",
        "ddl_summary": "agent_npn VARCHAR, agent_full_name VARCHAR, agent_recruiter VARCHAR, direct_upline_name VARCHAR, top_upline_name VARCHAR, carrier_short_name VARCHAR, mem_name VARCHAR, mem_state VARCHAR(2), mem_age INT, AgeCategory VARCHAR, product_type_mapped VARCHAR (MAPD/PDP/Med Supp), carrier_status VARCHAR, payment_status VARCHAR, contract_count INT, mem_count INT, report_date DATE, report_mon_year VARCHAR",
        "schema_ddl": (
            "-- Table: analytic_vault.bob_carrier_memberships_vw\n"
            "-- Description: Carrier membership snapshots: agent production, member counts, carrier enrollment. Monthly snapshots.\n"
            "-- Database: Synapse (T-SQL syntax: use TOP instead of LIMIT, YEAR()/MONTH() instead of EXTRACT)\n"
            "\n"
            "CREATE TABLE analytic_vault.bob_carrier_memberships_vw (\n"
            "    agent_npn VARCHAR,                -- Agent National Producer Number\n"
            "    agent_full_name VARCHAR,           -- Agent full name\n"
            "    agent_recruiter VARCHAR,           -- Recruiter who recruited this agent\n"
            "    direct_upline_name VARCHAR,        -- Direct upline agent name\n"
            "    top_upline_name VARCHAR,           -- Top-level upline agent name\n"
            "    carrier_short_name VARCHAR,        -- Insurance carrier short name (e.g. 'Aetna', 'Humana')\n"
            "    mem_name VARCHAR,                  -- Member full name\n"
            "    mem_state VARCHAR(2),              -- Member US state code (e.g. 'TX', 'FL')\n"
            "    mem_age INT,                       -- Member age in years\n"
            "    AgeCategory VARCHAR,               -- Age bucket (e.g. '65-69', '70-74')\n"
            "    product_type_mapped VARCHAR,       -- Product type: MAPD, PDP, or Med Supp\n"
            "    carrier_status VARCHAR,            -- Carrier enrollment status\n"
            "    payment_status VARCHAR,            -- Payment status\n"
            "    contract_count INT,                -- Number of contracts\n"
            "    mem_count INT,                     -- Number of members\n"
            "    report_date DATE,                  -- Snapshot report date\n"
            "    report_mon_year VARCHAR            -- Report month-year label\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Time column: report_date. Latest data: WHERE report_date = (SELECT MAX(report_date) FROM analytic_vault.bob_carrier_memberships_vw)\n"
            "-- Use SUM(contract_count) for contract totals, SUM(mem_count) for member totals\n"
            "-- Synapse syntax: use TOP N instead of LIMIT, YEAR(col)/MONTH(col) instead of EXTRACT\n"
        ),
        "sample_queries": ["How many total members?", "Member count by carrier", "Break down members by age category", "Show agents with most members"],

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
            "quarter": {
                "expression": "CONCAT('Q', CEILING(MONTH({alias}.report_date) / 3.0))",
                "temporal": True,
                "description": "Quarter (Q1-Q4) derived from report_date",
            },
            "year": {
                "expression": "CAST(YEAR({alias}.report_date) AS VARCHAR)",
                "temporal": True,
                "description": "Year derived from report_date",
            },
            "year_quarter": {
                "expression": "CAST(YEAR({alias}.report_date) AS VARCHAR) + '-Q' + CAST(CEILING(MONTH({alias}.report_date) / 3.0) AS VARCHAR)",
                "temporal": True,
                "description": "Year-Quarter (e.g. 2025-Q1) derived from report_date",
            },
        },

        "entity_links": {
            "agent": {"local_key": "agent_npn"},
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
            "agent_npn", "agent_full_name", "mem_name", "carrier_short_name",
        ],
    },

    # =====================================================
    # COMMISSION ITEMS
    # =====================================================
    "commission_items": {
        "table": "wpo.vw_com_items_ai",
        "grain": "commission_line",
        "db_type": "postgres",
        "domain": "commission_items",
        "scope_columns": {"entity_id": "company_id"},  # scoped by company_id (maps logical entity_id to actual company_id column); commission records are tied to the managing entity
        "description": "Individual commission line items: payments, premiums, agent earnings per policy per carrier per period.",
        "ddl_summary": "pk_id UUID PK, company_id VARCHAR, company_name VARCHAR, carrier_id VARCHAR, carrier_name VARCHAR, npn VARCHAR, agent_name VARCHAR, writing_agent VARCHAR, upline_name VARCHAR, top_upline_name VARCHAR, account_number VARCHAR, insured_name VARCHAR, payment NUMERIC ($), premium NUMERIC ($), split VARCHAR, policy_state VARCHAR(2), coverage_month VARCHAR (date string YYYY-MM-DD, filter with LIKE 'YYYY-MM%'), market VARCHAR, report_date DATE, statement_month VARCHAR",
        "schema_ddl": (
            "-- Table: wpo.vw_com_items_ai\n"
            "-- Description: Individual commission line items per policy per agent per carrier per period\n"
            "-- Database: PostgreSQL\n"
            "\n"
            "CREATE TABLE wpo.vw_com_items_ai (\n"
            "    pk_id UUID PRIMARY KEY,\n"
            "    company_id VARCHAR,               -- Company identifier\n"
            "    company_name VARCHAR,              -- Company name\n"
            "    carrier_id VARCHAR,                -- Carrier identifier\n"
            "    carrier_name VARCHAR,              -- Insurance carrier (e.g., 'Aetna', 'Ambetter')\n"
            "    npn VARCHAR,                       -- Agent National Producer Number\n"
            "    agent_name VARCHAR,                -- Agent full name\n"
            "    writing_agent VARCHAR,             -- Writing agent name\n"
            "    upline_name VARCHAR,               -- Direct upline agent name\n"
            "    top_upline_name VARCHAR,           -- Top-level upline agent name\n"
            "    account_number VARCHAR,            -- Policy account number\n"
            "    insured_name VARCHAR,              -- Policy holder name\n"
            "    payment NUMERIC,                   -- Commission payment amount in USD\n"
            "    premium NUMERIC,                   -- Policy premium amount in USD\n"
            "    split VARCHAR,                     -- Commission split info\n"
            "    policy_state VARCHAR(2),           -- US state code (e.g., 'TX', 'FL')\n"
            "    coverage_month VARCHAR,            -- Date string 'YYYY-MM-DD' (e.g., '2025-06-01'). Cast directly: coverage_month::DATE. Filter with LIKE: WHERE coverage_month LIKE '2025-06%%'\n"
            "    market VARCHAR,                    -- Market segment/product category. Categories: ACA, Medicare, Supplemental, Dental, Vision, Life, Group, Individual (also contains agent names in some rows)\n"
            "    payment_type VARCHAR,              -- Commission payment type: 'Commission', 'Override', 'Bonus', 'Assignment'\n"
            "    report_date DATE,                  -- Report date\n"
            "    statement_month VARCHAR            -- Statement month\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Time column: coverage_month (VARCHAR 'YYYY-MM-DD', already includes day). Cast directly: coverage_month::DATE. Do NOT append '-01'.\n"
            "-- Filter by month: WHERE coverage_month LIKE '2025-06%%'\n"
            "-- Latest: WHERE coverage_month = (SELECT MAX(coverage_month) FROM wpo.vw_com_items_ai)\n"
            "-- Cast text to NUMERIC for aggregation: SUM(payment::NUMERIC)\n"
            "-- For monthly grouping: GROUP BY TO_CHAR(coverage_month::DATE, 'YYYY-MM')\n"
        ),
        "sample_queries": ["Total commissions this month", "Commission breakdown by carrier", "Top 10 agents by commission", "How much did agent X earn?"],

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
            "month": "coverage_month",  # alias for natural language queries
            "market": "market",
            "payment_type": "payment_type",
            "insured_name": "insured_name",
            "account_number": "account_number",
            "premium": "premium",
            "payment": "payment",
            "report_date": "report_date",
            "upline_name": "upline_name",
            "top_upline_name": "top_upline_name",
            "quarter": {
                "expression": "CONCAT('Q', CEIL(EXTRACT(MONTH FROM {alias}.coverage_month::DATE) / 3.0)::INT)",
                "temporal": True,
                "description": "Quarter (Q1-Q4) derived from coverage_month",
            },
            "year": {
                "expression": "EXTRACT(YEAR FROM {alias}.coverage_month::DATE)::TEXT",
                "temporal": True,
                "description": "Year derived from coverage_month",
            },
            "year_quarter": {
                "expression": "EXTRACT(YEAR FROM {alias}.coverage_month::DATE)::TEXT || '-Q' || CEIL(EXTRACT(MONTH FROM {alias}.coverage_month::DATE) / 3.0)::INT",
                "temporal": True,
                "description": "Year-Quarter (e.g. 2025-Q1) derived from coverage_month",
            },
        },

        "entity_links": {
            "agent": {"local_key": "npn"},
        },

        "filters": {
            "agent": {"column": None, "type": "dynamic_agent"},
            "member": {"column": None, "type": "dynamic_member"},
            "carrier_name": {"column": "carrier_name", "type": "categorical_token"},
            "account_number": {"column": "account_number", "type": "exact_text"},
            "coverage_month": {"column": "coverage_month", "type": "categorical_strict"},
            "report_date": {"column": "report_date", "type": "date"},
            "payment_type": {"column": "payment_type", "type": "categorical_strict", "valid_values": ["Assignment", "Bonus", "Commission", "Override"]},
            "relative_time": {"column": None, "type": "logical_time"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "npn", "agent_name", "carrier_name", "insured_name", "account_number",
        ],
    },

    # =====================================================
    # COMMISSION TOTALS
    # =====================================================
    "commission_totals": {
        "table": "wpo.com_totals",
        "grain": "statement_summary",
        "db_type": "postgres",
        "domain": "commission_totals",
        "scope_columns": {"entity_id": "company_id", "sub_entity_id": "sub_entity_id"},
        "description": "Commission statement summaries: total payouts per carrier per statement period. Includes total_policies, total_agents, total_commissions by carrier. High-level commission reporting.",
        "ddl_summary": "pk_id UUID PK, job_id TEXT, company_id TEXT, company_name TEXT, carrier_id TEXT, carrier_name TEXT, statement_month TEXT (timestamp string, filter with LIKE 'YYYY-MM%'), npn TEXT, agent_name TEXT, payment_type TEXT (Commission/Commissions/Override/Bonus/Assignment/Other), statement_total TEXT ($, castable to NUMERIC), status TEXT (Paid/On Hold/ACH Returned/Applied to Debit Balance/etc.), report_date TEXT, sub_entity_id VARCHAR",
        "schema_ddl": (
            "-- Table: wpo.com_totals\n"
            "-- Description: Commission statement summaries per carrier per statement period\n"
            "-- Database: PostgreSQL\n"
            "\n"
            "CREATE TABLE wpo.com_totals (\n"
            "    pk_id UUID PRIMARY KEY,\n"
            "    job_id TEXT,                       -- Import job identifier\n"
            "    company_id TEXT,                   -- Company identifier\n"
            "    company_name TEXT,                 -- Company name\n"
            "    carrier_id TEXT,                   -- Carrier identifier\n"
            "    carrier_name TEXT,                 -- Insurance carrier name (e.g., 'Aetna', 'Humana')\n"
            "    statement_month TEXT,              -- Statement period as full timestamp string 'YYYY-MM-DD HH:MM:SS'. Filter with: statement_month LIKE '2025-11%%' for Nov 2025\n"
            "    npn TEXT,                          -- Agent National Producer Number\n"
            "    agent_name TEXT,                   -- Agent full name\n"
            "    payment_type TEXT,                 -- Commission payment type: 'Commission' (base agent pay), 'Commissions', 'Override' (upline), 'Bonus', 'Assignment', 'Other'\n"
            "    statement_total TEXT,              -- Total payment amount as text (cast to NUMERIC for math: statement_total::NUMERIC)\n"
            "    status TEXT,                       -- Statement status: 'Paid', 'On Hold', 'ACH Returned', 'Applied to Debit Balance', 'Debit Balance Collected', 'Debit Balance Owed', 'Direct Deposit Needed', 'Archive', 'Tech Upload'\n"
            "    report_date TEXT                   -- Report date\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Time column: statement_month is TEXT stored as full timestamp 'YYYY-MM-DD HH:MM:SS.NNNNNNN'.\n"
            "-- To filter by month: WHERE statement_month LIKE '2025-11%' (for November 2025)\n"
            "-- To filter by year: WHERE statement_month LIKE '2025%'\n"
            "-- Latest: WHERE statement_month = (SELECT MAX(statement_month) FROM wpo.com_totals)\n"
            "-- statement_total is TEXT; cast for aggregation: SUM(statement_total::NUMERIC)\n"
            "-- NEVER use statement_month = 'YYYY-MM' — it won't match. Always use LIKE 'YYYY-MM%'.\n"
        ),
        "sample_queries": ["Total commission by carrier", "Show statements for January 2025", "Compare commission this year vs last year", "Top 5 carriers by total payment"],

        "metrics": {
            "sum_statement_total": {"expression": "SUM(statement_total)"},
            "count_statements": {"expression": "COUNT(*)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "carrier_name": "carrier_name",
            "statement_month": "statement_month",
            "month": "statement_month",  # alias for natural language queries
            "npn": "npn",
            "agent_name": "agent_name",
            "status": "status",
            "payment_type": "payment_type",
            "statement_total": "statement_total",
            "quarter": {
                "expression": "CONCAT('Q', CEIL(EXTRACT(MONTH FROM {alias}.statement_month::TIMESTAMP) / 3.0)::INT)",
                "temporal": True,
                "description": "Quarter (Q1-Q4) derived from statement_month",
            },
            "year": {
                "expression": "EXTRACT(YEAR FROM {alias}.statement_month::TIMESTAMP)::TEXT",
                "temporal": True,
                "description": "Year derived from statement_month",
            },
            "year_quarter": {
                "expression": "EXTRACT(YEAR FROM {alias}.statement_month::TIMESTAMP)::TEXT || '-Q' || CEIL(EXTRACT(MONTH FROM {alias}.statement_month::TIMESTAMP) / 3.0)::INT",
                "temporal": True,
                "description": "Year-Quarter (e.g. 2025-Q1) derived from statement_month",
            },
        },

        "entity_links": {
            "agent": {"local_key": "npn"},
        },

        "filters": {
            "agent": {"column": None, "type": "dynamic_agent"},
            "carrier_name": {"column": "carrier_name", "type": "categorical_token"},
            "statement_month": {"column": "statement_month", "type": "month_year"},
            "statement_date": {"column": "statement_date", "type": "date"},  # TODO: verify — 'statement_date' column not found in com_totals DDL; actual time column is statement_month (TEXT). This filter may produce invalid SQL.
            "payment_type": {"column": "payment_type", "type": "categorical_strict", "valid_values": ["Assignment", "Bonus", "Commission", "Commissions", "Other", "Override"]},
            "commission_status": {"column": "status", "type": "categorical_strict", "valid_values": ["Paid", "On Hold", "ACH Returned", "Applied to Debit Balance", "Debit Balance Collected", "Debit Balance Owed", "Direct Deposit Needed", "Archive", "Tech Upload"]},
            "relative_time": {"column": None, "type": "logical_time"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "carrier_name", "npn", "agent_name", "statement_month",
        ],
    },

    # =====================================================
    # AGENTS CONTRACTS
    # =====================================================
    "agents_contracts": {
        "table": "wpo.agents_contracts_view",
        "grain": "agent_contract",
        "db_type": "postgres",
        "domain": "agent_contracts",
        "scope_columns": {"entity_id": "company_id"},  # scoped by company_id; each entity manages its own agent contracts
        "resolve_key": "npn",
        "display_field": "full_name",
        "description": "Agent-carrier contract records: appointment status, writing numbers, upline hierarchy, state appointments. Also covers: operations data for carrier contracting, agent onboarding status, appointment management, downline agent hierarchy.",
        "ddl_summary": "pk_id UUID PK, company_id VARCHAR, company_name VARCHAR, npn VARCHAR, first_name VARCHAR, last_name VARCHAR, full_name VARCHAR, carrier VARCHAR, status VARCHAR (58 values: Active/Appointed/Effective/Inactive/Pending/Terminated/etc.), agent_status VARCHAR (29 values: Active/Inactive/Lead/Prospect/Terminated/etc.), appointment_type VARCHAR (AG4/LMO/Producer/Reporting Only/Subproducer), affiliation VARCHAR, writing_number VARCHAR, upline_npn VARCHAR, upline_full_name VARCHAR, top_upline_npn VARCHAR, top_upline_full_name VARCHAR, appointed_states TEXT (semicolon-separated state codes e.g. 'AK;AZ;CA;FL'), requested_states TEXT (semicolon-separated state codes requested but not yet appointed), assignee VARCHAR, field_sales_director VARCHAR, marketer VARCHAR, email VARCHAR, phone VARCHAR, preferred_language VARCHAR (Chinese/Creole/English/French/Hindi/Mandarin/Portuguese/Spanish/Vietnamese), created_time TIMESTAMP, modified_time TIMESTAMP",
        "schema_ddl": (
            "-- Table: wpo.agents_contracts_view\n"
            "-- Description: Agent-carrier contract records with appointment status, upline hierarchy\n"
            "-- Database: PostgreSQL\n"
            "\n"
            "CREATE TABLE wpo.agents_contracts_view (\n"
            "    pk_id UUID PRIMARY KEY,\n"
            "    company_id VARCHAR,               -- Company identifier\n"
            "    company_name VARCHAR,              -- Company name\n"
            "    npn VARCHAR,                       -- Agent National Producer Number (unique per agent)\n"
            "    first_name VARCHAR,                -- Agent first name\n"
            "    last_name VARCHAR,                 -- Agent last name\n"
            "    full_name VARCHAR,                 -- Agent full name\n"
            "    carrier VARCHAR,                   -- Carrier name\n"
            "    status VARCHAR,                    -- Contract status. 58 total values. Key values: 'Active', 'Active - Re-Certification Needed', 'Active - Reporting Only', 'Appointed', 'Effective', 'Inactive', 'Pending', 'Pending - Certification Required', 'Pending - FFM Required', 'Pending - First Business', 'Terminated', 'Termed by Carrier', 'Termed - Carrier Exit'\n"
            "    agent_status VARCHAR,              -- Agent status. 29 total values. Key values: 'Active', 'Active Captive', 'Active - Principal', 'Inactive', 'Lead', 'Prospect', 'Terminated', 'Suspended', 'No Longer Licensed'\n"
            "    appointment_type VARCHAR,           -- Type of appointment. Values: 'AG4', 'LMO', 'Producer', 'Reporting Only', 'Subproducer'\n"
            "    affiliation VARCHAR,               -- Agent affiliation\n"
            "    writing_number VARCHAR,            -- Writing number\n"
            "    upline_npn VARCHAR,                -- Direct upline NPN\n"
            "    upline_full_name VARCHAR,          -- Direct upline name\n"
            "    top_upline_npn VARCHAR,            -- Top-level upline NPN\n"
            "    top_upline_full_name VARCHAR,      -- Top-level upline name\n"
            "    appointed_states TEXT,             -- Semicolon-separated state codes (e.g. 'AK;AZ;CA;FL')\n"
            "    requested_states TEXT,             -- Semicolon-separated state codes requested but not yet appointed\n"
            "    assignee VARCHAR,                  -- Assigned staff\n"
            "    field_sales_director VARCHAR,      -- Field sales director\n"
            "    marketer VARCHAR,                  -- Assigned marketer\n"
            "    email VARCHAR,                     -- Agent email\n"
            "    phone VARCHAR,                     -- Agent phone\n"
            "    preferred_language VARCHAR,        -- Preferred language: Chinese, Creole, English, French, Hindi, Mandarin, Portuguese, Spanish, Vietnamese\n"
            "    commission_schedule_status VARCHAR, -- Commission schedule status\n"
            "    current_or_sch_status VARCHAR,     -- Current or scheduled status\n"
            "    status_date VARCHAR,               -- Status date\n"
            "    created_time TIMESTAMP,            -- Record creation time\n"
            "    modified_time TIMESTAMP            -- Last modification time\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Use COUNT(DISTINCT npn) for unique agent counts\n"
            "-- Filter active agents: WHERE t.status = 'Active' (use exact casing from column comment)\n"
        ),
        "sample_queries": ["How many active agents?", "Agent count by carrier", "List agents with pending contracts", "Top uplines by agent count"],

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
            "agent": {"local_key": "npn"},
        },

        "filters": {
            "carrier": {"column": None, "type": "dynamic_carrier"},
            "upline": {"column": None, "type": "dynamic_upline"},
            "top_upline": {"column": None, "type": "dynamic_top_upline"},
            "carrier_id": {"column": "carrier", "type": "categorical_strict"},
            "carrier_name": {"column": "carrier_name", "type": "categorical_token"},  # TODO: verify — DDL shows column name is 'carrier' not 'carrier_name'; this filter may reference a non-existent column
            "upline_npn": {"column": "upline_npn", "type": "exact_text"},
            "upline_full_name": {"column": "upline_full_name", "type": "categorical_token"},
            "top_upline_npn": {"column": "top_upline_npn", "type": "exact_text"},
            "top_upline_full_name": {"column": "top_upline_full_name", "type": "categorical_token"},
            "agent_status": {"column": "agent_status", "type": "categorical_token", "valid_values": ["Active", "Active Captive", "Active - Principal", "Active - Released", "Inactive", "Lead", "Lead - Principal", "Prospect", "Prospect Captive", "Prospect - Principal", "Suspended", "Terminated", "No Longer Licensed"]},
            "contract_status": {"column": "status", "type": "categorical_strict", "valid_values": ["Active", "Active - Re-Certification Needed", "Active - Reporting Only", "Appointed", "Effective", "Inactive", "Interested", "Needs Attention", "Not Appointed", "Not Contracted", "Pending", "Pending - Agency Appointment", "Pending - Certification Required", "Pending - FFM Required", "Pending - First Business", "Pending - Need E&O", "Pending - Need W-9", "Pending - State License Required", "Ready to Submit", "Rejected", "Release Required", "Requested", "Request Sent to Carrier", "Sent to Agent", "Sent to Carrier", "Submitted", "Suspended", "Termed by Carrier", "Termed - Carrier Exit", "Terminated"]},
            "npn": {"column": "npn", "type": "exact_text"},
            "writing_number": {"column": "writing_number", "type": "exact_text"},
            "appointment_type": {"column": "appointment_type", "type": "categorical_strict", "valid_values": ["AG4", "LMO", "Producer", "Reporting Only", "Subproducer"]},
            "preferred_language": {"column": "preferred_language", "type": "categorical_strict", "valid_values": ["Chinese", "Creole", "English", "French", "Hindi", "Mandarin", "Portuguese", "Spanish", "Vietnamese"]},
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
            "npn", "writing_number", "company_name", "upline_full_name",
            "top_upline_full_name", "carrier", "first_name", "last_name", "assignee",
        ],
    },

    # =====================================================
    # AGENTS MASTER
    # =====================================================
    "agents": {
        "table": "wpo.lup_agents",
        "grain": "agent_master",
        "db_type": "postgres",
        "domain": "agent_profile",
        "scope_columns": {"entity_id": "company_id"},  # scoped by company_id; agents are managed per organization (actual DB column is company_id)
        "resolve_key": "npn",
        "display_field": "full_name",
        "description": "Master agent directory: contact info, communication preferences, compliance. One row per agent. Also covers: CRM agent profiles, agent contact management, do-not-call/opt-out tracking.",
        "ddl_summary": "pk_id UUID PK, npn VARCHAR (unique agent ID), full_name VARCHAR, first_name VARCHAR, last_name VARCHAR, email VARCHAR, phone VARCHAR, mobile VARCHAR, status VARCHAR (29 values: Active/Active Captive/Active - Principal/Inactive/Lead/Prospect/Terminated/Suspended/No Longer Licensed/etc.), preferred_language VARCHAR (Chinese/Creole/English/French/Hindi/Mandarin/Portuguese/Spanish/Vietnamese), permission_to_text VARCHAR (Yes/No), do_not_call VARCHAR (Yes/No), email_opt_out VARCHAR (Yes/No)",
        "schema_ddl": (
            "-- Table: wpo.lup_agents\n"
            "-- Description: Master agent directory with contact info and preferences. One row per agent.\n"
            "-- Database: PostgreSQL\n"
            "\n"
            "CREATE TABLE wpo.lup_agents (\n"
            "    pk_id UUID PRIMARY KEY,\n"
            "    npn VARCHAR,                       -- National Producer Number (unique agent identifier)\n"
            "    full_name VARCHAR,                 -- Agent full name\n"
            "    first_name VARCHAR,                -- Agent first name\n"
            "    last_name VARCHAR,                 -- Agent last name\n"
            "    email VARCHAR,                     -- Agent email address\n"
            "    phone VARCHAR,                     -- Agent phone number\n"
            "    mobile VARCHAR,                    -- Agent mobile number\n"
            "    status VARCHAR,                    -- Agent status. Key values: 'Active', 'Active Captive', 'Active - Principal', 'Inactive', 'Lead', 'Prospect', 'Terminated', 'Suspended', 'No Longer Licensed' (29 total statuses)\n"
            "    preferred_language VARCHAR,        -- Preferred language: Chinese, Creole, English, French, Hindi, Mandarin, Portuguese, Spanish, Vietnamese\n"
            "    permission_to_text VARCHAR,        -- Yes or No\n"
            "    do_not_call VARCHAR,               -- Yes or No\n"
            "    email_opt_out VARCHAR              -- Yes or No\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Use COUNT(*) for total agent count\n"
            "-- Filter active: WHERE t.status = 'Active' (use exact casing from column comment)\n"
        ),
        "sample_queries": ["How many active agents?", "Find agent by NPN", "List agents who opted out of email", "Agents by preferred language"],

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
        },

        "entity_links": {
            "agent": {"local_key": "npn", "direct": True},
        },

        "filters": {
            "npn": {"column": "npn", "type": "exact_text"},
            "agent_status": {"column": "status", "type": "categorical_token", "valid_values": ["Active", "Active Captive", "Active - Principal", "Active - Released", "Inactive", "Lead", "Lead - Principal", "Prospect", "Prospect Captive", "Prospect - Principal", "Suspended", "Terminated", "No Longer Licensed"]},
            "preferred_language": {"column": "preferred_language", "type": "categorical_strict", "valid_values": ["Chinese", "Creole", "English", "French", "Hindi", "Mandarin", "Portuguese", "Spanish", "Vietnamese"]},
            "permission_to_text": {"column": "permission_to_text", "type": "boolean_flag"},
            "do_not_call": {"column": "do_not_call", "type": "boolean_flag"},
            "email_opt_out": {"column": "email_opt_out", "type": "boolean_flag"},
            "agent": {"column": None, "type": "dynamic_agent"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "npn", "full_name", "first_name", "last_name", "email", "phone", "mobile",
        ],
    },

    # =====================================================
    # LICENSES
    # =====================================================
    "licenses": {
        "table": "wpo.lup_agent_licenses",
        "grain": "agent_license",
        "db_type": "postgres",
        "domain": "agent_licenses",
        "scope_columns": {},  # intentionally unscoped: state licensing data is regulatory/global; no entity_id column exists on this table
        "resolve_key": "agent_npn",
        "display_field": "license_number",
        "description": "Agent state licensing records. Use this module for questions about agents licensed in a state, license counts by state, license type/status, issue/expiry dates, and compliance tracking.",
        "ddl_summary": "transaction_id UUID PK, agent_npn VARCHAR (FK to agents), lic_id VARCHAR, type VARCHAR (lic=state license / cert=certification / sbe=state-based exchange), status VARCHAR (for licenses: Resident/Non-Resident/Non-R; for certs: AHIP/FFM), state VARCHAR(2), issue_date DATE, expiry_date DATE, license_number VARCHAR, license_owner VARCHAR, license_market VARCHAR (Health/Life), certification_year VARCHAR, npn_valid VARCHAR",
        "schema_ddl": (
            "-- Table: wpo.lup_agent_licenses\n"
            "-- Description: Agent state licensing records for compliance tracking\n"
            "-- Database: PostgreSQL\n"
            "\n"
            "CREATE TABLE wpo.lup_agent_licenses (\n"
            "    transaction_id UUID PRIMARY KEY,\n"
            "    agent_npn VARCHAR,                 -- Agent NPN (FK to agents table)\n"
            "    lic_id VARCHAR,                    -- License identifier\n"
            "    type VARCHAR,                      -- Record type: 'lic' (state license), 'cert' (certification like FFM/AHIP), 'sbe' (state-based exchange). To find licenses only: WHERE type = 'lic'\n"
            "    status VARCHAR,                    -- For licenses (type='lic'): 'Resident' or 'Non-Resident' or 'Non-R'. For certs (type='cert'): 'AHIP', 'FFM'. This is the license residency type, NOT active/expired status.\n"
            "    state VARCHAR(2),                  -- US state code (e.g., 'TX', 'FL')\n"
            "    issue_date DATE,                   -- License issue date\n"
            "    expiry_date DATE,                  -- License expiry date\n"
            "    license_number VARCHAR,            -- License number\n"
            "    license_owner VARCHAR,             -- License owner name\n"
            "    license_market VARCHAR,            -- Market: Health or Life\n"
            "    certification_year VARCHAR,        -- Certification year\n"
            "    npn_valid VARCHAR                  -- NPN validation status\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Use COUNT(*) for license counts, COUNT(DISTINCT agent_npn) for unique agents\n"
            "-- IMPORTANT: The 'status' column stores the license RESIDENCY TYPE (Resident/Non-Resident), NOT active/expired status.\n"
            "-- To find active licenses, check expiry_date >= CURRENT_DATE, not the status column.\n"
            "-- 'type' distinguishes licenses (lic) from certifications (cert, sbe).\n"
            "-- To find resident agents in a state: WHERE t.type = 'lic' AND t.state = 'TX' AND t.status = 'Resident'\n"
            "-- To find non-resident agents in a state: WHERE t.type = 'lic' AND t.state = 'TX' AND (t.status = 'Non-Resident' OR t.status = 'Non-R')\n"
            "-- Check expiry: WHERE expiry_date < CURRENT_DATE for expired licenses\n"
        ),
        "sample_queries": ["How many agents are licensed in Texas?", "How many active licenses?", "Agents licensed by state", "Expired licenses", "Licenses expiring this month"],

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
            "agent": {"local_key": "agent_npn"},
        },

        "filters": {
            "agent_npn": {"column": "agent_npn", "type": "exact_text"},
            "license_type": {"column": "type", "type": "categorical_strict", "valid_values": ["lic", "cert", "sbe"]},
            "license_status": {"column": "status", "type": "categorical_token", "valid_values": ["Resident", "Non-Resident", "Non-R", "AHIP", "FFM"]},
            "license_state": {"column": "state", "type": "categorical_strict"},
            "license_number": {"column": "license_number", "type": "exact_text"},
            "npn_valid": {"column": "npn_valid", "type": "boolean_flag"},
            "certification_year": {"column": "certification_year", "type": "numeric"},
            "agent": {"column": None, "type": "dynamic_agent"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "agent_npn", "license_number", "license_owner",
        ],
    },

    # =====================================================
    # PCH PROVIDERS
    # =====================================================
    "pch_providers": {
        "table": "wpo.pch_provider_info",
        "grain": "provider_record",
        "domain": "provider_core",
        "db_type": "postgres",
        "scope_columns": {
            "entity_id": "company_id",
            "sub_entity_id": "group_id",
        },
        "description": "Provider credentialing records for PCH health plan. NPI, specialties, credentialing status. Scoped by company/group. Also covers: provider credentialing management, provider network, provider directory, CAQH tracking, specialty distribution. CRITICAL: ~99.99% of rows are status='Lead' (bulk NPI imports). Real providers have status IN ('Active','Prospect','Compliance','Deactivated'). Always add WHERE status != 'Lead' unless user explicitly asks about leads.",
        "ddl_summary": "pk_id UUID PK, txn_id VARCHAR, npi VARCHAR (unique provider ID), first_name VARCHAR, last_name VARCHAR, company_id VARCHAR (entity scope), company_name VARCHAR, group_id VARCHAR (sub-entity scope), group_name VARCHAR, status VARCHAR (CRITICAL: 'Lead' ~6.8M bulk imports | 'Active' ~33 | 'Prospect' ~106 | 'Compliance' ~1 | 'Deactivated' ~1. DEFAULT FILTER: status != 'Lead'), type VARCHAR (Individual/Organization), state VARCHAR, city VARCHAR, zip VARCHAR, gender VARCHAR, primary_speciality VARCHAR, secondary_speciality VARCHAR, professional_degree VARCHAR (MD/DO/NP), board_cert VARCHAR, email VARCHAR, source VARCHAR, job_owner_name VARCHAR (credentialing specialist), caqh_number VARCHAR",
        "schema_ddl": (
            "-- Table: wpo.pch_provider_info\n"
            "-- Description: Provider credentialing records for PCH health plan\n"
            "-- Database: PostgreSQL\n"
            "-- SCOPE: Filtered by company_id and group_id (injected automatically)\n"
            "\n"
            "CREATE TABLE wpo.pch_provider_info (\n"
            "    pk_id UUID PRIMARY KEY,\n"
            "    txn_id VARCHAR,                    -- Transaction identifier\n"
            "    npi VARCHAR,                       -- National Provider Identifier (unique per provider)\n"
            "    first_name VARCHAR,                -- Provider first name\n"
            "    last_name VARCHAR,                 -- Provider last name\n"
            "    company_id VARCHAR,                -- Company ID (scope filter - auto-injected)\n"
            "    company_name VARCHAR,              -- Company name\n"
            "    group_id VARCHAR,                  -- Group ID (scope filter - auto-injected)\n"
            "    group_name VARCHAR,                -- Group name\n"
            "    status VARCHAR,                    -- CRITICAL: 'Lead' (~6.8M bulk NPI imports, NOT real providers), 'Active' (33), 'Prospect' (106), 'Compliance' (1), 'Deactivated' (1). DEFAULT: WHERE status != 'Lead'\n"
            "    type VARCHAR,                      -- Provider type: Individual or Organization\n"
            "    state VARCHAR,                     -- US state code\n"
            "    city VARCHAR,                      -- City\n"
            "    zip VARCHAR,                       -- ZIP code\n"
            "    gender VARCHAR,                    -- Gender\n"
            "    primary_speciality VARCHAR,        -- Primary medical specialty\n"
            "    secondary_speciality VARCHAR,      -- Secondary specialty\n"
            "    professional_degree VARCHAR,       -- Degree: MD, DO, NP, etc.\n"
            "    board_cert VARCHAR,                -- Board certification status\n"
            "    email VARCHAR,                     -- Provider email\n"
            "    source VARCHAR,                    -- Data source\n"
            "    job_owner_name VARCHAR,            -- Credentialing specialist assigned\n"
            "    caqh_number VARCHAR                -- CAQH number\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- ALWAYS add WHERE status != 'Lead' unless user explicitly asks about leads\n"
            "-- 'Lead' rows are bulk NPI registry imports (~6.8M), NOT actual providers\n"
            "-- Real providers: status IN ('Active','Prospect','Compliance','Deactivated') (~141 total)\n"
            "-- Use COUNT(*) for provider counts, COUNT(DISTINCT npi) for unique providers\n"
            "-- company_id and group_id filters are injected automatically for security\n"
        ),
        "sample_queries": ["How many active providers?", "Providers by specialty", "Providers pending credentialing", "Find provider by NPI"],

        "metrics": {
            "count_providers": {"expression": "COUNT(*)"},
            "count_distinct_npi": {"expression": "COUNT(DISTINCT npi)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "npi": "npi",
            "txn_id": "txn_id",
            "first_name": "first_name",
            "last_name": "last_name",
            "company_id": "company_id",
            "company_name": "company_name",
            "group_id": "group_id",
            "group_name": "group_name",
            "status": "status",
            "type": "type",
            "state": "state",
            "city": "city",
            "zip": "zip",
            "gender": "gender",
            "primary_speciality": "primary_speciality",
            "secondary_speciality": "secondary_speciality",
            "professional_degree": "professional_degree",
            "email": "email",
            "job_owner_name": "job_owner_name",
            "source": "source",
            "board_cert": "board_cert",
        },

        "entity_links": {
            "pch_provider": {"local_key": "npi"},
        },

        "filters": {
            "npi": {"column": "npi", "type": "exact_text"},
            "status": {"column": "status", "type": "categorical_token", "valid_values": ["Active", "Prospect", "Compliance", "Deactivated"]},  # "Lead" excluded — ~6.8M bulk NPI import rows, never a real provider status
            "type": {"column": "type", "type": "categorical_strict", "valid_values": ["Individual", "Organization"]},
            "state": {"column": "state", "type": "categorical_strict"},
            "city": {"column": "city", "type": "categorical_token"},
            "primary_speciality": {"column": "primary_speciality", "type": "categorical_token"},
            "gender": {"column": "gender", "type": "categorical_strict", "valid_values": ["Male", "Female"]},  # TODO: verify — pch_provider_info gender values not documented in DDL; actual DB values may differ (e.g. 'M'/'F' codes)
            "job_owner_name": {"column": "job_owner_name", "type": "categorical_token"},
            "source": {"column": "source", "type": "categorical_strict"},
            "provider": {"column": None, "type": "dynamic_pch_provider"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "npi", "first_name", "last_name", "txn_id", "email", "job_owner_name",
        ],
    },

    # =====================================================
    # PCH MEMBERS
    # =====================================================
    "pch_members": {
        "table": "wpo.pch_member_roster",
        "grain": "member_record",
        "db_type": "postgres",
        "domain": "member_roster",
        "scope_columns": {
            "entity_id": "company_id",
        },
        "description": "Health plan member roster for PCH. Demographics, clinical utilization, risk scores, PCP assignments. Scoped by company. Also covers: member clinical data, utilization metrics (ED visits, admissions, Rx), risk stratification, population health management.",
        "ddl_summary": "pk_id UUID PK, amisys_number VARCHAR (unique member ID), medicaid_number VARCHAR, first_name VARCHAR, last_name VARCHAR, member_dob VARCHAR, gender VARCHAR, member_state VARCHAR, member_city VARCHAR, member_zip VARCHAR, pcp_npi VARCHAR (assigned PCP), line_of_business VARCHAR (Medicaid/Medicare), product VARCHAR, population_health_category VARCHAR, primary_risk_category VARCHAR, risk_score VARCHAR, member_type VARCHAR, member_status VARCHAR, total_pcp_claims VARCHAR, all_admits VARCHAR, acute_admits VARCHAR, ed_visits VARCHAR, snf_admits VARCHAR, total_brand_rx_count VARCHAR, generic_rx_count VARCHAR, company_id VARCHAR (entity scope), report_date VARCHAR",
        "schema_ddl": (
            "-- Table: wpo.pch_member_roster\n"
            "-- Description: Health plan member roster with demographics and clinical utilization\n"
            "-- Database: PostgreSQL\n"
            "-- SCOPE: Filtered by company_id (injected automatically)\n"
            "\n"
            "CREATE TABLE wpo.pch_member_roster (\n"
            "    pk_id UUID PRIMARY KEY,\n"
            "    amisys_number VARCHAR,             -- Unique member identifier (Amisys number)\n"
            "    medicaid_number VARCHAR,           -- Medicaid number\n"
            "    first_name VARCHAR,                -- Member first name\n"
            "    last_name VARCHAR,                 -- Member last name\n"
            "    member_dob VARCHAR,                -- Date of birth\n"
            "    gender VARCHAR,                    -- Gender: 'F' (Female) or 'M' (Male)\n"
            "    member_state VARCHAR,              -- US state code\n"
            "    member_city VARCHAR,               -- City\n"
            "    member_zip VARCHAR,                -- ZIP code\n"
            "    pcp_npi VARCHAR,                   -- Assigned PCP's NPI\n"
            "    line_of_business VARCHAR,          -- Line of business: 'Medicaid' or 'Medicare'\n"
            "    product VARCHAR,                   -- Health plan product: 'PREMIER' or 'VALUE'\n"
            "    population_health_category VARCHAR, -- Population health category\n"
            "    primary_risk_category VARCHAR,     -- Primary risk category\n"
            "    risk_score VARCHAR,                -- Risk score\n"
            "    member_type VARCHAR,               -- Member type\n"
            "    member_status VARCHAR,             -- Member status: 'Active'\n"
            "    total_pcp_claims VARCHAR,          -- Total PCP claims\n"
            "    all_admits VARCHAR,                -- All hospital admissions\n"
            "    acute_admits VARCHAR,              -- Acute admissions\n"
            "    ed_visits VARCHAR,                 -- Emergency department visits\n"
            "    snf_admits VARCHAR,                -- Skilled nursing facility admissions\n"
            "    total_brand_rx_count VARCHAR,      -- Brand prescription count\n"
            "    generic_rx_count VARCHAR,          -- Generic prescription count\n"
            "    company_id VARCHAR,                -- Company ID (scope filter - auto-injected)\n"
            "    carrier_id VARCHAR,                -- Carrier identifier\n"
            "    report_date VARCHAR                -- Report date\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Use COUNT(*) for member counts\n"
            "-- company_id filter is injected automatically for security\n"
            "-- Clinical columns are VARCHAR; cast to NUMERIC for aggregation if needed\n"
        ),
        "sample_queries": ["How many active members?", "Members by risk category", "Members with high ED visits", "Find member by Amisys number"],

        "metrics": {
            "count_members": {"expression": "COUNT(*)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "amisys_number": "amisys_number",
            "medicaid_number": "medicaid_number",
            "first_name": "first_name",
            "last_name": "last_name",
            "member_dob": "member_dob",
            "gender": "gender",
            "member_state": "member_state",
            "member_city": "member_city",
            "member_zip": "member_zip",
            "pcp_npi": "pcp_npi",
            "line_of_business": "line_of_business",
            "product": "product",
            "population_health_category": "population_health_category",
            "primary_risk_category": "primary_risk_category",
            "risk_score": "risk_score",
            "member_type": "member_type",
            "member_status": "member_status",
            "carrier_id": "carrier_id",
            "report_date": "report_date",
            "company_id": "company_id",
        },

        "entity_links": {
            "pch_member": {"local_key": "amisys_number"},
        },

        "filters": {
            "amisys_number": {"column": "amisys_number", "type": "exact_text"},
            "pcp_npi": {"column": "pcp_npi", "type": "exact_text"},
            "member_state": {"column": "member_state", "type": "categorical_strict"},
            "member_city": {"column": "member_city", "type": "categorical_token"},
            "line_of_business": {"column": "line_of_business", "type": "categorical_strict", "valid_values": ["Medicaid", "Medicare"]},
            "product": {"column": "product", "type": "categorical_strict", "valid_values": ["PREMIER", "VALUE"]},
            "population_health_category": {"column": "population_health_category", "type": "categorical_strict"},
            "primary_risk_category": {"column": "primary_risk_category", "type": "categorical_strict"},
            "member_status": {"column": "member_status", "type": "categorical_strict", "valid_values": ["Active", "Inactive"]},  # TODO: verify — DDL comment only documents 'Active'; 'Inactive' not confirmed in DB data
            "gender": {"column": "gender", "type": "categorical_strict", "valid_values": ["F", "M"]},
            "member": {"column": None, "type": "dynamic_pch_member"},
            "report_date": {"column": "report_date", "type": "date"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "amisys_number", "medicaid_number", "first_name", "last_name", "pcp_npi",
        ],
    },
    # =====================================================
    # MEMBERCARE CALL ASSESSMENTS
    # =====================================================
    "membercare_assessments": {
        "table": "wpo.membercare_agent_assessment_recordings",
        "grain": "call_recording",
        "domain": "assessments",
        "db_type": "postgres",
        "scope_columns": {
            "entity_id": "entity_id",
            "sub_entity_id": "sub_entity_id",
        },
        "description": (
            "Membercare call recording QA assessments. Each row is one recorded call with AI-generated "
            "transcription and QA score. Campaigns: OEP_2024 (Open Enrollment 2024), OEP_2025 (Open "
            "Enrollment 2025), SEP_2026 (Special Enrollment 2026). Scoring: total_score is 0-100, "
            "call_status is 'pass' (score >= 84) or 'fail' (score < 84). Supervisors can override "
            "via total_edited_score and edited_compliance_score."
        ),
        "ddl_summary": (
            "id UUID PK, agent_login VARCHAR (agent email/username), recorded_at DATETIME, "
            "phone_number VARCHAR, campaign TEXT (OEP_2024 / OEP_2025 / SEP_2026), "
            "file_name VARCHAR, file_size BIGINT (bytes), created_at DATETIME, updated_at DATETIME, "
            "uploaded_by VARCHAR, total_score BIGINT (AI QA score 0-100, pass >= 84), "
            "total_edited_score BIGINT (supervisor-revised score), edited_at DATETIME, "
            "edited_compliance_score VARCHAR, call_status VARCHAR (pass/fail), "
            "transcription TEXT, edited_by VARCHAR, entity_id VARCHAR, sub_entity_id VARCHAR, "
            "sales_scorecard TEXT (JSON sale/not-sale assessment)"
        ),
        "schema_ddl": (
            "-- Table: wpo.membercare_agent_assessment_recordings\n"
            "-- Description: Membercare call recording QA assessments with AI scores\n"
            "-- Database: PostgreSQL\n"
            "-- SCOPE: Filtered by entity_id and sub_entity_id (injected automatically)\n"
            "\n"
            "CREATE TABLE wpo.membercare_agent_assessment_recordings (\n"
            "    id UUID PRIMARY KEY,\n"
            "    agent_login VARCHAR,               -- Agent email or username\n"
            "    recorded_at TIMESTAMP,             -- Call recording timestamp\n"
            "    phone_number VARCHAR,              -- Phone number called\n"
            "    campaign TEXT,                     -- Campaign: OEP_2024, OEP_2025, or SEP_2026\n"
            "    file_name VARCHAR,                 -- Recording file name\n"
            "    file_size BIGINT,                  -- File size in bytes\n"
            "    created_at TIMESTAMP,              -- Record creation time\n"
            "    updated_at TIMESTAMP,              -- Record update time\n"
            "    uploaded_by VARCHAR,               -- Who uploaded the recording\n"
            "    total_score BIGINT,                -- AI QA score 0-100 (pass >= 84, fail < 84)\n"
            "    total_edited_score BIGINT,         -- Supervisor-revised QA score\n"
            "    edited_at TIMESTAMP,               -- When score was edited\n"
            "    edited_compliance_score VARCHAR,   -- Edited compliance score\n"
            "    call_status VARCHAR,               -- 'pass' or 'fail'\n"
            "    transcription TEXT,                -- Call transcription\n"
            "    edited_by VARCHAR,                 -- Who edited the score\n"
            "    entity_id VARCHAR,                 -- Entity ID (scope - auto-injected)\n"
            "    sub_entity_id VARCHAR,             -- Sub-entity ID (scope - auto-injected)\n"
            "    sales_scorecard TEXT               -- JSON sales assessment\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Time column: recorded_at (TIMESTAMP). For month filtering: EXTRACT(MONTH FROM recorded_at)\n"
            "-- Scoring: total_score 0-100, pass >= 84, fail < 84\n"
            "-- For pass/fail counts: SUM(CASE WHEN call_status = 'pass' THEN 1 ELSE 0 END)\n"
            "-- entity_id and sub_entity_id filters are auto-injected\n"
        ),
        "sample_queries": [
            "How many call recordings do we have?",
            "How many calls for OEP_2025?",
            "Call recordings by campaign",
            "How many calls passed vs failed?",
            "Average score by agent",
            "Average score by campaign",
            "Which agent has the most recordings?",
            "Show recordings where score is below 84",
            "Total count and compliance by campaign",
        ],

        "metrics": {
            "count_recordings": {"expression": "COUNT(*)"},
            "avg_score": {"expression": "AVG(total_score)"},
            "avg_edited_score": {"expression": "AVG(total_edited_score)"},
            "sum_score": {"expression": "SUM(total_score)"},
            "min_score": {"expression": "MIN(total_score)"},
            "max_score": {"expression": "MAX(total_score)"},
            "count_passed": {"expression": "SUM(CASE WHEN call_status = 'pass' THEN 1 ELSE 0 END)"},
            "count_failed": {"expression": "SUM(CASE WHEN call_status = 'fail' THEN 1 ELSE 0 END)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "agent_login": "agent_login",
            "phone_number": "phone_number",
            "campaign": "campaign",
            "call_status": "call_status",
            "edited_compliance_score": "edited_compliance_score",
            "total_score": "total_score",
            "total_edited_score": "total_edited_score",
            "uploaded_by": "uploaded_by",
            "edited_by": "edited_by",
            "recorded_at": "recorded_at",
            "created_at": "created_at",
            "entity_id": "entity_id",
            "sub_entity_id": "sub_entity_id",
            "quarter": {
                "expression": "CONCAT('Q', CEIL(EXTRACT(MONTH FROM {alias}.recorded_at) / 3.0)::INT)",
                "temporal": True,
                "description": "Quarter (Q1-Q4) derived from recorded_at",
            },
            "year": {
                "expression": "EXTRACT(YEAR FROM {alias}.recorded_at)::TEXT",
                "temporal": True,
                "description": "Year derived from recorded_at",
            },
            "year_quarter": {
                "expression": "EXTRACT(YEAR FROM {alias}.recorded_at)::TEXT || '-Q' || CEIL(EXTRACT(MONTH FROM {alias}.recorded_at) / 3.0)::INT",
                "temporal": True,
                "description": "Year-Quarter (e.g. 2025-Q1) derived from recorded_at",
            },
        },

        "entity_links": {},

        "filters": {
            "agent_login": {"column": "agent_login", "type": "categorical_token"},
            "call_status": {"column": "call_status", "type": "categorical_strict", "valid_values": ["pass", "fail"]},
            "campaign": {"column": "campaign", "type": "categorical_strict", "valid_values": ["OEP_2024", "OEP_2025", "SEP_2026"]},
            "uploaded_by": {"column": "uploaded_by", "type": "categorical_token"},
            "recorded_at": {"column": "recorded_at", "type": "month_year"},
            "relative_time": {"column": None, "type": "logical_time"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "agent_login", "phone_number", "campaign", "uploaded_by",
        ],
    },

    # =====================================================
    # AGILITY CALL ASSESSMENTS
    # =====================================================
    "agility_assessments": {
        "table": "wpo.agility_agent_assessment_recordings",
        "domain": "assessments",
        "grain": "call_recording",
        "db_type": "postgres",
        "scope_columns": {},  # intentionally unscoped: agility_agent_assessment_recordings has no entity_id/sub_entity_id column; access control is handled at the application layer via entity membership
        "description": (
            "Agility agent call recording QA assessments. Each row is one recorded call with "
            "AI-generated transcription and QA score. Campaigns: OEP_2024 / OEP_2025 / SEP_2026. "
            "Scoring: total_score is 0-100, call_status is 'pass' (>= 84) or 'fail' (< 84). "
            "Supervisors can override via total_edited_score."
        ),
        "ddl_summary": (
            "id UUID PK, agent_login VARCHAR (agent email/username), recorded_at DATETIME, "
            "phone_number VARCHAR, campaign TEXT (OEP_2024 / OEP_2025 / SEP_2026), "
            "file_name VARCHAR, file_size BIGINT (bytes), created_at DATETIME, updated_at DATETIME, "
            "uploaded_by VARCHAR, total_score BIGINT (AI QA score 0-100, pass >= 84), "
            "total_edited_score BIGINT (supervisor-revised score), edited_at DATETIME, "
            "call_status VARCHAR (pass/fail), transcription TEXT, edited_by VARCHAR"
        ),
        "schema_ddl": (
            "-- Table: wpo.agility_agent_assessment_recordings\n"
            "-- Description: Agility agent call recording QA assessments with AI scores\n"
            "-- Database: PostgreSQL\n"
            "\n"
            "CREATE TABLE wpo.agility_agent_assessment_recordings (\n"
            "    id UUID PRIMARY KEY,\n"
            "    agent_login VARCHAR,               -- Agent email or username\n"
            "    recorded_at TIMESTAMP,             -- Call recording timestamp\n"
            "    phone_number VARCHAR,              -- Phone number called\n"
            "    campaign TEXT,                     -- Campaign: OEP_2024, OEP_2025, or SEP_2026\n"
            "    file_name VARCHAR,                 -- Recording file name\n"
            "    file_size BIGINT,                  -- File size in bytes\n"
            "    created_at TIMESTAMP,              -- Record creation time\n"
            "    updated_at TIMESTAMP,              -- Record update time\n"
            "    uploaded_by VARCHAR,               -- Who uploaded the recording\n"
            "    total_score BIGINT,                -- AI QA score 0-100 (pass >= 84, fail < 84)\n"
            "    total_edited_score BIGINT,         -- Supervisor-revised QA score\n"
            "    edited_at TIMESTAMP,               -- When score was edited\n"
            "    call_status VARCHAR,               -- 'pass' or 'fail'\n"
            "    transcription TEXT,                -- Call transcription\n"
            "    edited_by VARCHAR                  -- Who edited the score\n"
            ");\n"
            "\n"
            "-- NOTES:\n"
            "-- Time column: recorded_at (TIMESTAMP). For month filtering: EXTRACT(MONTH FROM recorded_at)\n"
            "-- Scoring: total_score 0-100, pass >= 84, fail < 84\n"
            "-- For pass/fail counts: SUM(CASE WHEN call_status = 'pass' THEN 1 ELSE 0 END)\n"
        ),
        "sample_queries": [
            "How many agility call recordings?",
            "Agility recordings by campaign",
            "How many agility calls passed?",
            "Average agility score by agent",
            "Average score by campaign",
        ],

        "metrics": {
            "count_recordings": {"expression": "COUNT(*)"},
            "avg_score": {"expression": "AVG(total_score)"},
            "avg_edited_score": {"expression": "AVG(total_edited_score)"},
            "sum_score": {"expression": "SUM(total_score)"},
            "min_score": {"expression": "MIN(total_score)"},
            "max_score": {"expression": "MAX(total_score)"},
            "count_passed": {"expression": "SUM(CASE WHEN call_status = 'pass' THEN 1 ELSE 0 END)"},
            "count_failed": {"expression": "SUM(CASE WHEN call_status = 'fail' THEN 1 ELSE 0 END)"},
            "list_records": {"expression": "*", "is_list": True},
        },

        "dimensions": {
            "agent_login": "agent_login",
            "phone_number": "phone_number",
            "campaign": "campaign",
            "call_status": "call_status",
            "total_score": "total_score",
            "total_edited_score": "total_edited_score",
            "uploaded_by": "uploaded_by",
            "edited_by": "edited_by",
            "recorded_at": "recorded_at",
            "created_at": "created_at",
            "quarter": {
                "expression": "CONCAT('Q', CEIL(EXTRACT(MONTH FROM {alias}.recorded_at) / 3.0)::INT)",
                "temporal": True,
                "description": "Quarter (Q1-Q4) derived from recorded_at",
            },
            "year": {
                "expression": "EXTRACT(YEAR FROM {alias}.recorded_at)::TEXT",
                "temporal": True,
                "description": "Year derived from recorded_at",
            },
            "year_quarter": {
                "expression": "EXTRACT(YEAR FROM {alias}.recorded_at)::TEXT || '-Q' || CEIL(EXTRACT(MONTH FROM {alias}.recorded_at) / 3.0)::INT",
                "temporal": True,
                "description": "Year-Quarter (e.g. 2025-Q1) derived from recorded_at",
            },
        },

        "entity_links": {},

        "filters": {
            "agent_login": {"column": "agent_login", "type": "categorical_token"},
            "call_status": {"column": "call_status", "type": "categorical_strict", "valid_values": ["pass", "fail"]},
            "campaign": {"column": "campaign", "type": "categorical_strict", "valid_values": ["OEP_2024", "OEP_2025", "SEP_2026", "Medicare Fall"]},
            "uploaded_by": {"column": "uploaded_by", "type": "categorical_token"},
            "recorded_at": {"column": "recorded_at", "type": "month_year"},
            "relative_time": {"column": None, "type": "logical_time"},
        },

        "state_expansion": {},
        "searchable_fields": [
            "agent_login", "phone_number", "campaign", "uploaded_by",
        ],
    },
}


def get_module_ddl(module_name: str) -> str:
    """Get the CREATE TABLE DDL for a specific module.

    Returns the schema_ddl string that LLMs can use to generate SQL.
    Falls back to ddl_summary if schema_ddl is not defined.
    """
    cfg = MODULES.get(module_name)
    if not cfg:
        return ""
    return cfg.get("schema_ddl", cfg.get("ddl_summary", ""))


MODULE_KEY_COLUMNS = {
    "commission_items": [
        "carrier_name", "npn", "agent_name", "coverage_month (DATE)",
        "market (Health/Life/Senior/P&C/Group/Benefits/Supplemental)",
        "payment_type", "premium (NUMERIC)", "payment (NUMERIC)",
        "insured_name", "account_number", "policy_state",
    ],
    "commission_totals": [
        "carrier_name", "statement_month (DATE)", "npn", "agent_name",
        "status", "payment_type", "statement_total (NUMERIC)",
        "total_policies (INT)", "active_policies (INT)",
    ],
    "com_carrier_summary": [
        "carrier_name", "statement_month (DATE)", "npn", "agent_name",
        "statement_total (NUMERIC)", "total_policies (INT)",
    ],
    "agents_contracts": [
        "appointment_type (Producer/Subproducer/AG4/LMO/Reporting Only)",
        "carrier", "status (Active/Appointed/Terminated/Pending/...)",
        "agent_status (Active/Inactive/Terminated/...)",
        "npn", "full_name", "upline_npn", "top_upline_npn",
        "writing_number", "affiliation",
    ],
    "agents": [
        "status (Active/Inactive/Terminated/Lead/Prospect/Suspended/...)",
        "type (internal agent classification — NOT appointment_type)",
        "npn", "full_name", "email", "phone",
        "preferred_language (English/Spanish/Chinese/...)",
        "gender (Male/Female/Non-Binary/Other)",
    ],
    "licenses": [
        "type (lic=state license/cert=certification/sbe=state-based exchange)",
        "status (Resident/Non-Resident for licenses; AHIP/FFM for certs)",
        "state (2-letter US state code)", "expiry_date (DATE)",
        "license_market (Health/Life)", "agent_npn",
    ],
    "pch_providers": [
        "credentialing_status (Credentialed/Pending/Expired/...)",
        "npi", "name", "speciality", "taxonomy_desc",
        "board_certified (Yes/No)", "city", "state",
    ],
    "pch_members": [
        "status", "npi", "mem_id", "mem_name",
        "mem_dob (DATE)", "carrier_name", "plan_name",
    ],
    "bob": [
        "carrier_name", "total_members (INT)", "state",
        "product_type", "plan_year", "enrollment_type",
    ],
    "membercare_assessments": [
        "assessment_status", "agent_full_name", "npn",
        "assessment_date (DATE)", "call_recording_url",
    ],
    "agility_assessments": [
        "assessment_status", "agent_full_name", "npn",
        "assessment_date (DATE)", "call_recording_url",
    ],
}


def get_module_summary_catalog() -> dict:
    """Generate a lightweight module catalog for intent detection.

    Contains: name, description, sample_queries, db_type, table, key_columns.
    The key_columns field lists the most business-relevant columns per module,
    with value hints for categorical columns, to help the intent LLM distinguish
    modules from each other.
    Does NOT include full DDL or field mappings (those go to SQL generation step only).
    """
    catalog = {}
    for name, cfg in MODULES.items():
        catalog[name] = {
            "description": cfg.get("description", ""),
            "sample_queries": cfg.get("sample_queries", []),
            "db_type": cfg.get("db_type", "postgres"),
            "table": cfg["table"],
            "key_columns": MODULE_KEY_COLUMNS.get(name, []),
        }
    return catalog


def get_module_catalog() -> dict:
    """Generate a catalog of modules for LLM context.

    Includes table name, db_type, column mappings, and metric expressions
    so the LLM can make more informed decisions.
    """
    catalog = {}
    for name, cfg in MODULES.items():
        # Build dimension detail: semantic_name -> actual_column or COMPUTED description
        dim_detail = {}
        for dim_name, col_or_cfg in cfg["dimensions"].items():
            if isinstance(col_or_cfg, dict):
                dim_detail[dim_name] = f"COMPUTED: {col_or_cfg.get('description', dim_name)}"
            else:
                dim_detail[dim_name] = col_or_cfg

        # Build metric detail: metric_name -> expression
        metric_detail = {}
        for metric_name, metric_cfg in cfg["metrics"].items():
            metric_detail[metric_name] = {
                "expression": metric_cfg["expression"],
                "is_list": metric_cfg.get("is_list", False),
            }

        # Build filter detail: filter_name -> type
        filter_detail = {}
        for filter_name, filter_cfg in cfg["filters"].items():
            filter_detail[filter_name] = filter_cfg["type"]

        catalog[name] = {
            "table": cfg["table"],
            "db_type": cfg.get("db_type", "postgres"),
            "grain": cfg["grain"],
            "description": cfg.get("description", ""),
            "ddl_summary": cfg.get("ddl_summary", ""),
            "metrics": metric_detail,
            "dimensions": dim_detail,
            "filters": filter_detail,
            "sample_queries": cfg.get("sample_queries", []),
        }
    return catalog


def get_all_allowed_tables() -> set:
    """Get all allowed table names from both the semantic registry and table catalog.

    Returns uppercase schema-qualified table names for SQL validator whitelist.
    """
    from app.utils.jay.table_catalog import get_all_registered_tables

    registry_tables = {m["table"].upper() for m in MODULES.values()}
    entity_tables = {e["table"].upper() for e in GLOBAL_ENTITIES.values()}
    catalog_tables = get_all_registered_tables()

    return registry_tables | entity_tables | catalog_tables


def get_categorical_value_map(module: str) -> dict:
    """Build column -> {lowercase_value: db_value} mapping for a module's categorical filters.

    Used by the SQL value normalizer to correct case mismatches in LLM-generated SQL
    while keeping exact = comparisons (index-friendly).

    Returns e.g.: {"status": {"active": "Active", "pending": "Pending", ...}}
    """
    from app.utils.jay.knowledge_cache import get_categorical_value_map as _cached
    return _cached(module)


def get_all_categorical_value_maps() -> dict:
    """Build column -> {lowercase_value: db_value} mapping across ALL modules.

    Merges valid_values from every module so the normalizer works for
    multi-table queries spanning multiple domains.
    """
    from app.utils.jay.knowledge_cache import get_all_categorical_value_maps as _cached
    return _cached()
