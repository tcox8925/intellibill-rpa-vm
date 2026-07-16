"""
Jay Table Catalog - Comprehensive metadata catalog for multi-table SQL generation.

Organizes ~45 tables into 15 semantic domains with:
- DDL definitions (focused on query-relevant columns)
- JOIN relationships (within and across domains)
- Scope column mappings
- Real query examples from the codebase

Architecture: Two-stage funnel
  Stage 1 (Intent) sees lightweight domain catalog (~750 tokens)
  Stage 2 (SQL Gen) sees full DDL for selected domains + JOIN context
"""

from typing import Dict, List, Set


# =============================================================================
# CROSS-DOMAIN JOIN MAP
# =============================================================================

CROSS_DOMAIN_JOINS = {
    "agent_profile <-> commission_items": {
        "left": "wpo.lup_agents",
        "right": "wpo.vw_com_items_ai",
        "on": "lup_agents.npn = vw_com_items_ai.npn",
        "type": "LEFT JOIN",
    },
    "agent_profile <-> commission_totals": {
        "left": "wpo.lup_agents",
        "right": "wpo.com_totals",
        "on": "lup_agents.npn = com_totals.npn",
        "type": "LEFT JOIN",
    },
    "agent_profile <-> agent_licenses": {
        "left": "wpo.lup_agents",
        "right": "wpo.lup_agent_licenses",
        "on": "lup_agents.npn = lup_agent_licenses.agent_npn",
        "type": "LEFT JOIN",
    },
    "agent_profile <-> agent_contracts": {
        "left": "wpo.lup_agents",
        "right": "wpo.agents_contracts_view",
        "on": "lup_agents.npn = agents_contracts_view.npn",
        "type": "LEFT JOIN",
    },
    "agent_profile <-> agent_address": {
        "left": "wpo.lup_agents",
        "right": "wpo.agent_address",
        "on": "lup_agents.pk_id = agent_address.agent_id",
        "type": "LEFT JOIN",
    },
    "agent_profile <-> agent_communication": {
        "left": "wpo.lup_agents",
        "right": "wpo.agent_communication",
        "on": "lup_agents.pk_id = agent_communication.agent_id",
        "type": "LEFT JOIN",
    },
    "provider_core <-> member_roster": {
        "left": "wpo.pch_provider_info",
        "right": "wpo.pch_member_roster",
        "on": "pch_provider_info.npi = pch_member_roster.pcp_npi",
        "type": "LEFT JOIN",
    },
    "provider_core <-> provider_credentialing (affiliations)": {
        "left": "wpo.pch_provider_info",
        "right": "wpo.pch_affiliations",
        "on": "pch_provider_info.pk_id::TEXT = pch_affiliations.txn_id_provider",
        "type": "LEFT JOIN",
    },
    "provider_core <-> provider_credentialing (carriers)": {
        "left": "wpo.pch_provider_info",
        "right": "wpo.pch_carriers",
        "on": "pch_provider_info.pk_id::TEXT = pch_carriers.txn_id_provider",
        "type": "LEFT JOIN",
    },
    "provider_core <-> provider_credentialing (regulatory)": {
        "left": "wpo.pch_provider_info",
        "right": "wpo.pch_regulatory_validation",
        "on": "pch_provider_info.pk_id::TEXT = pch_regulatory_validation.txn_id_provider",
        "type": "LEFT JOIN",
    },
    "member_roster <-> member_clinical (med_claims)": {
        "left": "wpo.pch_member_roster",
        "right": "wpo.pch_med_claims",
        "on": "pch_member_roster.amisys_number = pch_med_claims.member_amisys_nbr",
        "type": "LEFT JOIN",
    },
    "member_roster <-> member_clinical (rx_claims)": {
        "left": "wpo.pch_member_roster",
        "right": "wpo.pch_rx_claim",
        "on": "pch_member_roster.amisys_number = pch_rx_claim.member_amisys_nbr",
        "type": "LEFT JOIN",
    },
    "member_roster <-> member_clinical (care_gaps)": {
        "left": "wpo.pch_member_roster",
        "right": "wpo.pch_care_gap_detail",
        "on": "pch_member_roster.amisys_number = pch_care_gap_detail.mem_id",
        "type": "LEFT JOIN",
    },
    "commission_items <-> lookup (carrier_short)": {
        "left": "wpo.vw_com_items_ai",
        "right": "wpo.lup_carriers_short",
        "on": "vw_com_items_ai.carrier_name = lup_carriers_short.vendor_name",
        "type": "LEFT JOIN",
    },
    "commission_totals <-> lookup (carrier_short)": {
        "left": "wpo.com_totals",
        "right": "wpo.lup_carriers_short",
        "on": "com_totals.carrier_id = lup_carriers_short.id",
        "type": "LEFT JOIN",
    },
    "crm <-> entity (users)": {
        "left": "wpo.crm_tickets",
        "right": "ops_sec.users",
        "on": "crm_tickets.owner = users.email",
        "type": "LEFT JOIN",
    },
    "any <-> entity (entity_name)": {
        "left": "*.company_id",
        "right": "ops_sec.entity",
        "on": "<table>.company_id = entity.entity_id",
        "type": "LEFT JOIN",
        "note": "Replace <table> with actual table alias",
    },
    "any <-> entity (sub_entity)": {
        "left": "*.sub_entity_id",
        "right": "ops_sec.sub_entity",
        "on": "<table>.sub_entity_id = sub_entity.sub_entity_id",
        "type": "LEFT JOIN",
        "note": "Replace <table> with actual table alias",
    },
    "provider_address <-> lookup (addr_type)": {
        "left": "wpo.pch_provider_address",
        "right": "wpo.lup_addrtype",
        "on": "pch_provider_address.address_type_id = lup_addrtype.pk_id",
        "type": "LEFT JOIN",
    },
    "agent_address <-> lookup (addr_type)": {
        "left": "wpo.agent_address",
        "right": "wpo.lup_addrtype",
        "on": "agent_address.address_type_id = lup_addrtype.pk_id",
        "type": "LEFT JOIN",
    },
    "commission_summary <-> entity": {
        "left": "wpo.com_carrier_statement_totals",
        "right": "ops_sec.entity",
        "on": "com_carrier_statement_totals.company_id = entity.entity_id",
        "type": "LEFT JOIN",
    },
    "commission_items <-> commission_totals": {
        "left": "wpo.vw_com_items_ai",
        "right": "wpo.com_totals",
        "on": "vw_com_items_ai.npn = com_totals.npn AND vw_com_items_ai.carrier_name = com_totals.carrier_name",
        "type": "INNER JOIN",
        "note": "Join on agent NPN + carrier; statement_month may also be needed for period alignment",
    },
    "member_roster <-> provider_core": {
        "left": "wpo.pch_member_roster",
        "right": "wpo.pch_provider_info",
        "on": "pch_member_roster.pcp_npi = pch_provider_info.npi",
        "type": "LEFT JOIN",
        "note": "Links members to their assigned PCP via NPI",
    },
}


# =============================================================================
# TABLE DOMAINS
# =============================================================================

TABLE_DOMAINS: Dict[str, Dict] = {

    # -----------------------------------------------------------------
    # DOMAIN 1: AGENT PROFILE
    # -----------------------------------------------------------------
    "agent_profile": {
        "description": "Agent master data: names, contact info, status, NPN, company assignment. The primary agent table with 200+ columns. WARNING: The agents table does NOT have a 'state' column — state data is ONLY in wpo.lup_agent_licenses (agent_licenses domain).",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.lup_agents",
                "description": "Agent master table — one row per agent with profile, contact, status info",
                "ddl": (
                    "CREATE TABLE wpo.lup_agents (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    company_id VARCHAR,               -- Company/entity identifier (for scoping)\n"
                    "    company_name VARCHAR,              -- Company name\n"
                    "    npn VARCHAR,                       -- National Producer Number (unique agent ID)\n"
                    "    first_name VARCHAR,\n"
                    "    last_name VARCHAR,\n"
                    "    full_name VARCHAR,                 -- Agent full name\n"
                    "    email VARCHAR,\n"
                    "    phone VARCHAR,\n"
                    "    mobile VARCHAR,\n"
                    "    status VARCHAR,                    -- Agent status. Key values: 'Active', 'Active Captive', 'Active - Principal', 'Inactive', 'Lead', 'Prospect', 'Terminated', 'Suspended', 'No Longer Licensed' (29 total statuses)\n"
                    "    preferred_language VARCHAR,        -- Preferred language: Chinese, Creole, English, French, Hindi, Mandarin, Portuguese, Spanish, Vietnamese\n"
                    "    type VARCHAR,                      -- Agent classification type (NOT appointment_type; for Producer/Subproducer filtering use agents_contracts_view.appointment_type instead)\n"
                    "    gender VARCHAR,\n"
                    "    owner VARCHAR,                     -- Account owner\n"
                    "    sub_entity_id VARCHAR,             -- Sub-entity for scoping\n"
                    "    agency_dba VARCHAR,                  -- Agency/firm DBA name\n"
                    "    recruiter VARCHAR,                   -- Recruiter name\n"
                    "    responsible_agent VARCHAR,           -- Responsible agent name\n"
                    "    responsible_agency VARCHAR,          -- Responsible agency name\n"
                    "    reports_to VARCHAR,                  -- Direct upline / reports-to name\n"
                    "    sales_director VARCHAR,              -- Top-level upline (sales director)\n"
                    "    created_time TIMESTAMP,\n"
                    "    modified_time TIMESTAMP\n"
                    ");\n"
                    "-- NOTE: This table does NOT have state columns. For state/license queries,\n"
                    "-- always JOIN to wpo.lup_agent_licenses:\n"
                    "-- SELECT a.first_name, a.last_name, l.state\n"
                    "-- FROM wpo.lup_agents a JOIN wpo.lup_agent_licenses l ON a.npn = l.agent_npn\n"
                    "-- WHERE l.state = 'FL'\n"
                ),
                "join_keys": {
                    "pk_id": "agent_address.agent_id, agent_communication.agent_id, agent_delegate.agent_id, crm_notes.agent_id",
                    "npn": "vw_com_items_ai.npn, com_totals.npn, lup_agent_licenses.agent_npn, agents_contracts_view.npn",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
            {
                "name": "wpo.agent_address",
                "description": "Agent mailing/business addresses",
                "ddl": (
                    "CREATE TABLE wpo.agent_address (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    agent_id UUID NOT NULL,            -- FK: wpo.lup_agents.pk_id\n"
                    "    address_type_id UUID NOT NULL,     -- FK: wpo.lup_addrtype.pk_id\n"
                    "    line1 VARCHAR(500) NOT NULL,\n"
                    "    line2 VARCHAR(500),\n"
                    "    street VARCHAR(255),\n"
                    "    city VARCHAR(100) NOT NULL,\n"
                    "    state VARCHAR(100) NOT NULL,\n"
                    "    county VARCHAR(150),\n"
                    "    zip VARCHAR(20),\n"
                    "    \"primary\" BOOLEAN DEFAULT FALSE\n"
                    ");\n"
                ),
                "join_keys": {
                    "agent_id": "lup_agents.pk_id",
                    "address_type_id": "lup_addrtype.pk_id",
                },
                "scope_column": None,
            },
            {
                "name": "wpo.agent_communication",
                "description": "Agent phone numbers and email contact records",
                "ddl": (
                    "CREATE TABLE wpo.agent_communication (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    agent_id UUID NOT NULL,            -- FK: wpo.lup_agents.pk_id\n"
                    "    communication_id UUID NOT NULL,    -- FK: wpo.lup_communication_medium.pk_id\n"
                    "    value VARCHAR(255) NOT NULL,       -- Phone number or email\n"
                    "    extension VARCHAR(10),\n"
                    "    text_opt BOOLEAN DEFAULT FALSE,\n"
                    "    dnd VARCHAR(20),                   -- Do not disturb\n"
                    "    \"primary\" BOOLEAN DEFAULT FALSE,\n"
                    "    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"agent_id": "lup_agents.pk_id"},
                "scope_column": None,
            },
            {
                "name": "wpo.agent_delegate",
                "description": "Agent delegates (authorized contacts)",
                "ddl": (
                    "CREATE TABLE wpo.agent_delegate (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    agent_id UUID NOT NULL,            -- FK: wpo.lup_agents.pk_id\n"
                    "    name NVARCHAR(100) NOT NULL,\n"
                    "    email NVARCHAR(100) NOT NULL,\n"
                    "    phone NVARCHAR(15),\n"
                    "    position NVARCHAR(255),\n"
                    "    permissions NVARCHAR(500)\n"
                    ");\n"
                ),
                "join_keys": {"agent_id": "lup_agents.pk_id"},
                "scope_column": None,
            },
            {
                "name": "wpo.lup_agent_compliance",
                "description": "Agent compliance inquiry records",
                "ddl": (
                    "CREATE TABLE wpo.lup_agent_compliance (\n"
                    "    pk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n"
                    "    inquiry_id VARCHAR,\n"
                    "    npn VARCHAR(50),                   -- Agent NPN\n"
                    "    agent_name VARCHAR(255),\n"
                    "    agent_email VARCHAR(255),\n"
                    "    agent_status VARCHAR(255),\n"
                    "    inquiry_type VARCHAR(255),         -- Type of compliance inquiry\n"
                    "    inquiry_status VARCHAR(255),       -- Open, Closed, Pending\n"
                    "    inquiry_description VARCHAR(2000),\n"
                    "    related_carrier VARCHAR(255),\n"
                    "    received_from VARCHAR(255),\n"
                    "    comp_owner VARCHAR(255),\n"
                    "    dir_upline VARCHAR(255),\n"
                    "    date_received TIMESTAMP,\n"
                    "    due_date TIMESTAMP,\n"
                    "    date_resolved TIMESTAMP,\n"
                    "    created_time TIMESTAMP,\n"
                    "    modified_time TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"npn": "lup_agents.npn"},
                "scope_column": None,
            },
        ],
        "internal_joins": [
            "lup_agents.pk_id = agent_address.agent_id",
            "lup_agents.pk_id = agent_communication.agent_id",
            "lup_agents.pk_id = agent_delegate.agent_id",
            "lup_agents.npn = lup_agent_compliance.npn",
        ],
        "sample_queries": [
            "How many active agents?",
            "List agents by state",
            "Show agent contact details for John Smith",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 2: AGENT CONTRACTS
    # -----------------------------------------------------------------
    "agent_contracts": {
        "description": "Agent-carrier contract records: appointment status, writing numbers, upline hierarchy, state appointments.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.agents_contracts_view",
                "description": "Agent-carrier contract view with appointment status and upline hierarchy",
                "ddl": (
                    "CREATE TABLE wpo.agents_contracts_view (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    company_id VARCHAR,               -- Company identifier\n"
                    "    company_name VARCHAR,\n"
                    "    npn VARCHAR,                       -- Agent NPN\n"
                    "    first_name VARCHAR,\n"
                    "    last_name VARCHAR,\n"
                    "    full_name VARCHAR,\n"
                    "    carrier VARCHAR,                   -- Carrier/insurance company name (column is 'carrier', NOT 'carrier_name')\n"
                    "    status VARCHAR,                    -- Contract status. 58 total values. Key values: 'Active', 'Active - Re-Certification Needed', 'Active - Reporting Only', 'Appointed', 'Effective', 'Inactive', 'Pending', 'Pending - Certification Required', 'Pending - FFM Required', 'Pending - First Business', 'Terminated', 'Termed by Carrier', 'Termed - Carrier Exit'\n"
                    "    agent_status VARCHAR,              -- Agent status. 29 total values. Key values: 'Active', 'Active Captive', 'Active - Principal', 'Inactive', 'Lead', 'Prospect', 'Terminated', 'Suspended', 'No Longer Licensed'\n"
                    "    appointment_type VARCHAR,           -- Type of appointment. Values: 'AG4', 'LMO', 'Producer', 'Reporting Only', 'Subproducer'\n"
                    "    affiliation VARCHAR,\n"
                    "    writing_number VARCHAR,\n"
                    "    upline_npn VARCHAR,\n"
                    "    upline_full_name VARCHAR,\n"
                    "    top_upline_npn VARCHAR,\n"
                    "    top_upline_full_name VARCHAR,\n"
                    "    appointed_states TEXT,             -- Semicolon-separated state codes (e.g. 'AK;AZ;CA;FL')\n"
                    "    requested_states TEXT,             -- Semicolon-separated state codes requested but not yet appointed (e.g. 'TX;FL;CA')\n"
                    "    assignee VARCHAR,\n"
                    "    email VARCHAR,\n"
                    "    phone VARCHAR,\n"
                    "    preferred_language VARCHAR,        -- Preferred language: Chinese, Creole, English, French, Hindi, Mandarin, Portuguese, Spanish, Vietnamese\n"
                    "    created_time TIMESTAMP,\n"
                    "    modified_time TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {
                    "npn": "lup_agents.npn",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "How many active contracts?",
            "Show agents with Aetna contracts",
            "Contract status breakdown by carrier",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 3: AGENT LICENSES
    # -----------------------------------------------------------------
    "agent_licenses": {
        "description": "Agent license records: state licensing, status, expiration dates, license types.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.lup_agent_licenses",
                "description": "Agent license records per state",
                "ddl": (
                    "CREATE TABLE wpo.lup_agent_licenses (\n"
                    "    transaction_id UUID PRIMARY KEY,\n"
                    "    agent_npn VARCHAR,                 -- Agent NPN\n"
                    "    lic_id VARCHAR,\n"
                    "    type VARCHAR,                      -- Record type: 'lic' (state license), 'cert' (certification like FFM/AHIP), 'sbe' (state-based exchange). To find licenses only: WHERE type = 'lic'\n"
                    "    status VARCHAR,                    -- For licenses (type='lic'): 'Resident' or 'Non-Resident' or 'Non-R'. For certs (type='cert'): 'AHIP', 'FFM'. This is the license residency type, NOT active/expired status.\n"
                    "    state VARCHAR(2),                  -- US state code (e.g., 'TX', 'FL')\n"
                    "    issue_date DATE,\n"
                    "    expiry_date DATE,\n"
                    "    license_number VARCHAR,\n"
                    "    license_owner VARCHAR,\n"
                    "    license_market VARCHAR,             -- Market: 'Health', 'Life'\n"
                    "    certification_year VARCHAR,\n"
                    "    npn_valid VARCHAR\n"
                    ");\n"
                    "-- NOTES:\n"
                    "-- IMPORTANT: The 'status' column stores the license RESIDENCY TYPE (Resident/Non-Resident), NOT active/expired status.\n"
                    "-- To find active licenses, check expiry_date >= CURRENT_DATE, not the status column.\n"
                    "-- 'type' distinguishes licenses (lic) from certifications (cert, sbe).\n"
                    "-- To find resident agents in a state: WHERE t.type = 'lic' AND t.state = 'TX' AND t.status = 'Resident'\n"
                    "-- To find non-resident agents in a state: WHERE t.type = 'lic' AND t.state = 'TX' AND (t.status = 'Non-Resident' OR t.status = 'Non-R')\n"
                    "-- Check expiry: WHERE expiry_date < CURRENT_DATE for expired licenses\n"
                ),
                "join_keys": {"agent_npn": "lup_agents.npn"},
                "scope_column": None,
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "How many agents have active licenses in Texas?",
            "Show license expirations this month",
            "License count by state",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 4: COMMISSION ITEMS
    # -----------------------------------------------------------------
    "commission_items": {
        "description": "Individual commission line items: payments, premiums, agent earnings per policy per carrier per period.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.vw_com_items_ai",
                "description": "Commission line items per policy per agent per carrier per period",
                "ddl": (
                    "CREATE TABLE wpo.vw_com_items_ai (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    company_id VARCHAR,               -- Company identifier\n"
                    "    company_name VARCHAR,\n"
                    "    carrier_id VARCHAR,\n"
                    "    carrier_name VARCHAR,              -- Insurance carrier name\n"
                    "    npn VARCHAR,                       -- Agent NPN\n"
                    "    agent_name VARCHAR,\n"
                    "    writing_agent VARCHAR,\n"
                    "    upline_name VARCHAR,\n"
                    "    top_upline_name VARCHAR,\n"
                    "    account_number VARCHAR,            -- Policy account number\n"
                    "    insured_name VARCHAR,              -- Policy holder name\n"
                    "    payment NUMERIC,                   -- Commission payment in USD\n"
                    "    premium NUMERIC,                   -- Policy premium in USD\n"
                    "    split VARCHAR,\n"
                    "    policy_state VARCHAR(2),           -- US state code\n"
                    "    coverage_month VARCHAR,            -- Date string 'YYYY-MM-DD' (already includes day). Cast directly: coverage_month::DATE. Do NOT append '-01'. Filter with LIKE: WHERE coverage_month LIKE '2025-11%%'\n"
                    "    market VARCHAR,                    -- Market segment: ACA, Medicare, Supplemental, Dental, Vision, Life, Group, Individual (data quality varies)\n"
                    "    payment_type VARCHAR,              -- Commission payment type: 'Commission', 'Override', 'Bonus', 'Assignment'\n"
                    "    report_date DATE,\n"
                    "    statement_month VARCHAR\n"
                    ");\n"
                    "-- Time column: coverage_month (VARCHAR 'YYYY-MM-DD', already has day). Cast directly: coverage_month::DATE. Do NOT append '-01'.\n"
                    "-- Filter by month: WHERE coverage_month LIKE '2025-11%%'\n"
                    "-- Cast payment/premium to NUMERIC for aggregation: SUM(payment::NUMERIC)\n"
                ),
                "join_keys": {
                    "npn": "lup_agents.npn",
                    "carrier_name": "lup_carriers_short.vendor_name",
                    "company_id": "entity.entity_id",
                },
                "scope_column": None,
            },
            {
                "name": "wpo.com_header",
                "description": "Raw commission file data from carrier statements",
                "ddl": (
                    "CREATE TABLE wpo.com_header (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    job_id VARCHAR,\n"
                    "    txn_id VARCHAR,\n"
                    "    company_id VARCHAR,\n"
                    "    company_name VARCHAR,\n"
                    "    carrier_id VARCHAR,\n"
                    "    carrier_name VARCHAR,\n"
                    "    car_prod_npn VARCHAR,              -- Agent NPN on carrier file\n"
                    "    car_prod_name VARCHAR,             -- Agent name on carrier file\n"
                    "    car_com_amt VARCHAR,               -- Commission amount (cast to NUMERIC)\n"
                    "    car_policy_prem_amt VARCHAR,       -- Premium amount (cast to NUMERIC)\n"
                    "    car_insured_name VARCHAR,          -- Policy holder name\n"
                    "    car_policy_id VARCHAR,             -- Policy ID\n"
                    "    car_state VARCHAR,                 -- US state code\n"
                    "    car_market VARCHAR,\n"
                    "    report_date VARCHAR,\n"
                    "    load_date VARCHAR\n"
                    ");\n"
                ),
                "join_keys": {
                    "company_id": "entity.entity_id",
                    "carrier_id": "lup_carriers_short.id",
                },
                "scope_column": "company_id",
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "Total commissions this month",
            "Commission breakdown by carrier",
            "Top 10 agents by commission",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 5: COMMISSION TOTALS
    # -----------------------------------------------------------------
    "commission_totals": {
        "description": "Commission statement summaries: total payouts per carrier per statement period. Includes total_policies, total_agents, total_commissions by carrier. Carrier-level aggregates.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.com_totals",
                "description": "Commission statement summaries per carrier per period",
                "ddl": (
                    "CREATE TABLE wpo.com_totals (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    job_id TEXT,\n"
                    "    company_id TEXT,                   -- Company identifier (for scoping)\n"
                    "    company_name TEXT,\n"
                    "    carrier_id TEXT,\n"
                    "    carrier_name TEXT,                 -- Insurance carrier name\n"
                    "    statement_month TEXT,              -- Timestamp string 'YYYY-MM-DD HH:MM:SS'. Filter with LIKE: WHERE statement_month LIKE '2025-11%%'\n"
                    "    npn TEXT,                          -- Agent NPN\n"
                    "    agent_name TEXT,\n"
                    "    payment_type TEXT,                 -- Commission payment type: 'Commission', 'Commissions', 'Override', 'Bonus', 'Assignment', 'Other'\n"
                    "    statement_total TEXT,              -- Total payment amount as text (castable to NUMERIC for math: SUM(statement_total::NUMERIC))\n"
                    "    status TEXT,                       -- Statement status: 'Paid', 'On Hold', 'ACH Returned', 'Applied to Debit Balance', 'Debit Balance Collected', 'Debit Balance Owed', 'Direct Deposit Needed', 'Archive', 'Tech Upload'\n"
                    "    report_date TEXT,\n"
                    "    sub_entity_id VARCHAR              -- Sub-entity for scoping\n"
                    ");\n"
                    "-- Time column: statement_month is TEXT stored as full timestamp 'YYYY-MM-DD HH:MM:SS.NNNNNNN'.\n"
                    "-- NEVER use statement_month = 'YYYY-MM' — it won't match. Always use LIKE 'YYYY-MM%%'.\n"
                    "-- Example: WHERE statement_month LIKE '2025-11%%' for November 2025\n"
                ),
                "join_keys": {
                    "npn": "lup_agents.npn",
                    "carrier_id": "lup_carriers_short.id",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
            {
                "name": "wpo.com_carrier_statement_totals",
                "description": "Carrier-level commission summary with agent/policy counts",
                "ddl": (
                    "CREATE TABLE wpo.com_carrier_statement_totals (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    job_id VARCHAR,\n"
                    "    company_id VARCHAR,\n"
                    "    company_name VARCHAR,\n"
                    "    carrier_id VARCHAR,\n"
                    "    carrier_name VARCHAR,\n"
                    "    product_name VARCHAR,\n"
                    "    commission_month VARCHAR,          -- Date string 'YYYY-MM-DD'. Filter with LIKE: WHERE commission_month LIKE '2025-11%%'\n"
                    "    report_month VARCHAR,\n"
                    "    total_agents VARCHAR,              -- Cast to NUMERIC\n"
                    "    total_policies VARCHAR,            -- Cast to NUMERIC\n"
                    "    total_commissions_received VARCHAR, -- Cast to NUMERIC, USD\n"
                    "    total_commissions_paid VARCHAR,    -- Cast to NUMERIC, USD\n"
                    "    total_overrides_received VARCHAR,\n"
                    "    total_overrides_paid VARCHAR,\n"
                    "    average_commission_paid VARCHAR\n"
                    ");\n"
                ),
                "join_keys": {
                    "company_id": "entity.entity_id",
                    "carrier_id": "lup_carriers_short.id",
                },
                "scope_column": "company_id",
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "Total commission by carrier",
            "Show statements for January 2025",
            "Commission trend by month",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 6: PROVIDER CORE
    # -----------------------------------------------------------------
    "provider_core": {
        "description": "Provider master data: names, NPI, speciality, status, addresses, locations, identifiers. IMPORTANT: Table has ~6.8M rows, most are 'Lead' status (NPI registry imports). Only ~140 are real providers (Active, Prospect, Compliance, Deactivated). Always filter by status unless the user explicitly asks about leads.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.pch_provider_info",
                "description": "Provider master table — one row per NPI. CRITICAL: ~99.99% of rows are status='Lead' (bulk NPI imports). Real providers have status IN ('Active','Prospect','Compliance','Deactivated'). When user asks 'how many providers' they mean status != 'Lead'.",
                "ddl": (
                    "CREATE TABLE wpo.pch_provider_info (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    txn_id VARCHAR NOT NULL,\n"
                    "    company_id VARCHAR,               -- Company/entity identifier\n"
                    "    company_name VARCHAR,\n"
                    "    group_id VARCHAR,\n"
                    "    group_name VARCHAR,\n"
                    "    status VARCHAR,                    -- CRITICAL: Values: 'Lead' (~6.8M bulk NPI imports), 'Active' (33), 'Prospect' (106), 'Compliance' (1), 'Deactivated' (1). Default filter: status != 'Lead'\n"
                    "    type VARCHAR,                      -- Values: 'Individual', 'Organization'\n"
                    "    npi VARCHAR,                       -- National Provider Identifier\n"
                    "    last_name VARCHAR,\n"
                    "    first_name VARCHAR,\n"
                    "    city VARCHAR,\n"
                    "    state VARCHAR,\n"
                    "    zip VARCHAR,\n"
                    "    gender VARCHAR,\n"
                    "    email VARCHAR,\n"
                    "    primary_speciality VARCHAR,\n"
                    "    secondary_speciality VARCHAR,\n"
                    "    professional_degree VARCHAR,\n"
                    "    language VARCHAR,\n"
                    "    board_cert VARCHAR,\n"
                    "    caqh_number VARCHAR,\n"
                    "    source VARCHAR,\n"
                    "    updated_on TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {
                    "pk_id": "pch_provider_address.provider_id, pch_provider_communication.provider_id",
                    "pk_id::TEXT": "pch_affiliations.txn_id_provider, pch_carriers.txn_id_provider, pch_regulatory_validation.txn_id_provider, pch_provider_identifiers.txn_id_provider, pch_provider_location.txn_id_provider",
                    "npi": "pch_member_roster.pcp_npi",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
            {
                "name": "wpo.pch_provider_address",
                "description": "Provider mailing/practice addresses",
                "ddl": (
                    "CREATE TABLE wpo.pch_provider_address (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    provider_id UUID NOT NULL,         -- FK: wpo.pch_provider_info.pk_id\n"
                    "    provider_npi VARCHAR(20) NOT NULL,\n"
                    "    address_type_id UUID NOT NULL,     -- FK: wpo.lup_addrtype.pk_id\n"
                    "    line1 VARCHAR(500),\n"
                    "    line2 VARCHAR(500),\n"
                    "    city VARCHAR(100),\n"
                    "    state VARCHAR(100),\n"
                    "    zip VARCHAR(20),\n"
                    "    county VARCHAR(150),\n"
                    "    \"primary\" BOOLEAN DEFAULT FALSE\n"
                    ");\n"
                ),
                "join_keys": {
                    "provider_id": "pch_provider_info.pk_id",
                    "address_type_id": "lup_addrtype.pk_id",
                },
                "scope_column": None,
            },
            {
                "name": "wpo.pch_provider_location",
                "description": "Provider practice locations",
                "ddl": (
                    "CREATE TABLE wpo.pch_provider_location (\n"
                    "    txn_id NVARCHAR PRIMARY KEY,\n"
                    "    location_name NVARCHAR,\n"
                    "    contact NVARCHAR,\n"
                    "    fax NVARCHAR,\n"
                    "    address_1 NVARCHAR,\n"
                    "    address_2 NVARCHAR,\n"
                    "    city NVARCHAR,\n"
                    "    state NVARCHAR,\n"
                    "    zip NVARCHAR,\n"
                    "    txn_id_provider NVARCHAR,          -- FK: pch_provider_info.pk_id (as TEXT)\n"
                    "    source NVARCHAR,\n"
                    "    updated_on TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"txn_id_provider": "pch_provider_info.pk_id (cast to TEXT)"},
                "scope_column": None,
            },
            {
                "name": "wpo.pch_provider_communication",
                "description": "Provider phone and email contacts",
                "ddl": (
                    "CREATE TABLE wpo.pch_provider_communication (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    provider_id UUID NOT NULL,         -- FK: wpo.pch_provider_info.pk_id\n"
                    "    provider_npi VARCHAR(20) NOT NULL,\n"
                    "    communication_id UUID NOT NULL,\n"
                    "    value VARCHAR(100),                -- Phone or email value\n"
                    "    \"primary\" BOOLEAN DEFAULT FALSE,\n"
                    "    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"provider_id": "pch_provider_info.pk_id"},
                "scope_column": None,
            },
            {
                "name": "wpo.pch_provider_identifiers",
                "description": "Provider identifiers (DEA, Medicare, Medicaid, etc.)",
                "ddl": (
                    "CREATE TABLE wpo.pch_provider_identifiers (\n"
                    "    txn_id NVARCHAR PRIMARY KEY,\n"
                    "    status NVARCHAR,\n"
                    "    id_type NVARCHAR,                  -- DEA, Medicare, Medicaid, etc.\n"
                    "    id_issuer NVARCHAR,\n"
                    "    id_type_value NVARCHAR,            -- The identifier value\n"
                    "    id_description NVARCHAR,\n"
                    "    id_issue_date NVARCHAR,\n"
                    "    id_state NVARCHAR,                 -- State that issued ID\n"
                    "    txn_id_provider NVARCHAR,          -- FK: pch_provider_info.pk_id (as TEXT)\n"
                    "    source NVARCHAR\n"
                    ");\n"
                ),
                "join_keys": {"txn_id_provider": "pch_provider_info.pk_id (cast to TEXT)"},
                "scope_column": None,
            },
        ],
        "internal_joins": [
            "pch_provider_info.pk_id = pch_provider_address.provider_id",
            "pch_provider_info.pk_id = pch_provider_communication.provider_id",
            "pch_provider_info.pk_id::TEXT = pch_provider_location.txn_id_provider",
            "pch_provider_info.pk_id::TEXT = pch_provider_identifiers.txn_id_provider",
        ],
        "sample_queries": [
            "How many active providers?",
            "Providers by speciality",
            "Show provider details for NPI 1234567890",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 7: PROVIDER CREDENTIALING
    # -----------------------------------------------------------------
    "provider_credentialing": {
        "description": "Provider credentialing: affiliations, carrier contracts, network status, regulatory validation.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.pch_affiliations",
                "description": "Provider hospital/group affiliations",
                "ddl": (
                    "CREATE TABLE wpo.pch_affiliations (\n"
                    "    txn_id NVARCHAR PRIMARY KEY,\n"
                    "    affiliate_name NVARCHAR,\n"
                    "    location NVARCHAR,\n"
                    "    txn_id_provider NVARCHAR,          -- FK: pch_provider_info.pk_id (as TEXT)\n"
                    "    source NVARCHAR,\n"
                    "    updated_on TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"txn_id_provider": "pch_provider_info.pk_id (cast to TEXT)"},
                "scope_column": None,
            },
            {
                "name": "wpo.pch_carriers",
                "description": "Provider carrier/insurance contracts",
                "ddl": (
                    "CREATE TABLE wpo.pch_carriers (\n"
                    "    txn_id NVARCHAR PRIMARY KEY,\n"
                    "    carrier_name NVARCHAR,\n"
                    "    carrier_niac_number NVARCHAR,\n"
                    "    network_status NVARCHAR,           -- In-network, Out-of-network, etc.\n"
                    "    txn_id_provider NVARCHAR,          -- FK: pch_provider_info.pk_id (as TEXT)\n"
                    "    source NVARCHAR,\n"
                    "    updated_on TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"txn_id_provider": "pch_provider_info.pk_id (cast to TEXT)"},
                "scope_column": None,
            },
            {
                "name": "wpo.pch_networks",
                "description": "Provider network participation status",
                "ddl": (
                    "CREATE TABLE wpo.pch_networks (\n"
                    "    id BIGINT PRIMARY KEY,\n"
                    "    pk_id VARCHAR(50) NOT NULL,\n"
                    "    provider VARCHAR(10) NOT NULL,     -- Provider ID\n"
                    "    affiliation VARCHAR(12) NOT NULL,\n"
                    "    status VARCHAR(8) NOT NULL,        -- Network status\n"
                    "    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
                    "    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"provider": "pch_provider_info.pk_id (as short ID)"},
                "scope_column": None,
            },
            {
                "name": "wpo.pch_regulatory_validation",
                "description": "Provider regulatory audit/validation records",
                "ddl": (
                    "CREATE TABLE wpo.pch_regulatory_validation (\n"
                    "    txn_id NVARCHAR PRIMARY KEY,\n"
                    "    audit_id NVARCHAR,\n"
                    "    status NVARCHAR,                   -- Pass, Fail, Pending\n"
                    "    source NVARCHAR,\n"
                    "    date_time NVARCHAR,\n"
                    "    txn_id_provider NVARCHAR           -- FK: pch_provider_info.pk_id (as TEXT)\n"
                    ");\n"
                ),
                "join_keys": {"txn_id_provider": "pch_provider_info.pk_id (cast to TEXT)"},
                "scope_column": None,
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "How many providers are credentialed with Aetna?",
            "Show network status by provider",
            "Regulatory validation failures",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 8: MEMBER ROSTER
    # -----------------------------------------------------------------
    "member_roster": {
        "description": "Member/patient roster: demographics, PCP assignment, risk scores, coverage, utilization summary.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.pch_member_roster",
                "description": "Member roster — one row per member per load date snapshot",
                "ddl": (
                    "CREATE TABLE wpo.pch_member_roster (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    amisys_number VARCHAR,             -- Member ID (joins to claims)\n"
                    "    medicaid_number VARCHAR,\n"
                    "    first_name VARCHAR,\n"
                    "    last_name VARCHAR,\n"
                    "    member_dob VARCHAR,\n"
                    "    gender VARCHAR,                    -- Gender: 'F' (Female) or 'M' (Male)\n"
                    "    member_phone_number VARCHAR,\n"
                    "    member_city VARCHAR,\n"
                    "    member_state VARCHAR,\n"
                    "    member_zip VARCHAR,\n"
                    "    pcp_npi VARCHAR,                   -- Assigned PCP's NPI\n"
                    "    mpk_name VARCHAR,                  -- PCP name\n"
                    "    line_of_business VARCHAR,          -- Line of business: 'Medicaid' or 'Medicare'\n"
                    "    product VARCHAR,                   -- Health plan product: 'PREMIER' or 'VALUE'\n"
                    "    population_health_category VARCHAR, -- Risk stratification\n"
                    "    primary_risk_category VARCHAR,\n"
                    "    risk_score VARCHAR,                -- Cast to NUMERIC\n"
                    "    total_pcp_claims VARCHAR,\n"
                    "    ed_visits VARCHAR,\n"
                    "    acute_admits VARCHAR,\n"
                    "    member_status VARCHAR,             -- Member status: 'Active'\n"
                    "    email_address VARCHAR,\n"
                    "    report_date VARCHAR,\n"
                    "    company_id VARCHAR,                -- Entity scoping\n"
                    "    carrier_id VARCHAR,\n"
                    "    load_date VARCHAR,\n"
                    "    source VARCHAR,\n"
                    "    member_type VARCHAR\n"
                    ");\n"
                    "-- NOTE: Multiple snapshots per member (by load_date). For latest: WHERE load_date = (SELECT MAX(load_date) FROM wpo.pch_member_roster WHERE amisys_number = <id>)\n"
                ),
                "join_keys": {
                    "amisys_number": "pch_med_claims.member_amisys_nbr, pch_rx_claim.member_amisys_nbr, pch_care_gap_detail.mem_id",
                    "pcp_npi": "pch_provider_info.npi",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
            {
                "name": "wpo.pch_patient_coverages",
                "description": "Patient insurance coverage records",
                "ddl": (
                    "CREATE TABLE wpo.pch_patient_coverages (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    source VARCHAR(100) NOT NULL DEFAULT 'Tebra',\n"
                    "    pat_sub_lnam VARCHAR(500) NOT NULL,  -- Patient last name\n"
                    "    pat_fnam VARCHAR(500) NOT NULL,      -- Patient first name\n"
                    "    pat_dob VARCHAR(500) NOT NULL,\n"
                    "    cov_status VARCHAR(500),             -- Coverage status\n"
                    "    cov_type VARCHAR(500),               -- Coverage type\n"
                    "    cov_car_nam VARCHAR(500),            -- Carrier name\n"
                    "    cov_car_type VARCHAR(500),\n"
                    "    cov_start_date VARCHAR(500),\n"
                    "    cov_end_date VARCHAR(500),\n"
                    "    insurance_type VARCHAR(50),\n"
                    "    company_id TEXT,\n"
                    "    company_name VARCHAR(500),\n"
                    "    member_id VARCHAR(500)              -- Member identifier\n"
                    ");\n"
                ),
                "join_keys": {"company_id": "entity.entity_id"},
                "scope_column": "company_id",
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "How many active members?",
            "Member breakdown by risk category",
            "Show members assigned to provider NPI 1234567890",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 9: MEMBER CLINICAL
    # -----------------------------------------------------------------
    "member_clinical": {
        "description": "Member clinical data: medical claims, pharmacy claims, care gaps, discharges, inpatient census.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.pch_med_claims",
                "description": "Medical claims (163 columns — key ones shown)",
                "ddl": (
                    "CREATE TABLE wpo.pch_med_claims (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    member_amisys_nbr VARCHAR,         -- Member ID (joins to roster)\n"
                    "    entity_id VARCHAR,\n"
                    "    entity_name VARCHAR,\n"
                    "    year_mo VARCHAR,                   -- Claim period YYYY-MM\n"
                    "    first_name VARCHAR,\n"
                    "    last_name VARCHAR,\n"
                    "    paid_amt VARCHAR,                  -- Paid amount (cast to NUMERIC)\n"
                    "    primary_diag_code VARCHAR,         -- Primary diagnosis ICD code\n"
                    "    proc_code VARCHAR,                 -- Procedure code\n"
                    "    place_of_serv_desc VARCHAR,\n"
                    "    pcp_npi VARCHAR,                   -- PCP National Provider Identifier\n"
                    "    pcp_prac_first_name VARCHAR,\n"
                    "    pcp_prac_last_name VARCHAR,\n"
                    "    attending_npi VARCHAR,\n"
                    "    service_start_date_dim_ck VARCHAR,\n"
                    "    service_end_date_dim_ck VARCHAR,\n"
                    "    report_date VARCHAR,\n"
                    "    company_id VARCHAR,                -- Entity scoping\n"
                    "    carrier_id VARCHAR,\n"
                    "    load_date VARCHAR,\n"
                    "    status VARCHAR\n"
                    ");\n"
                    "-- NOTE: 163 total columns. Only query-relevant ones shown.\n"
                    "-- paid_amt is VARCHAR, cast to NUMERIC: SUM(paid_amt::NUMERIC)\n"
                ),
                "join_keys": {
                    "member_amisys_nbr": "pch_member_roster.amisys_number",
                    "pcp_npi": "pch_provider_info.npi",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
            {
                "name": "wpo.pch_rx_claim",
                "description": "Pharmacy/prescription claims (103 columns — key ones shown)",
                "ddl": (
                    "CREATE TABLE wpo.pch_rx_claim (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    member_amisys_nbr VARCHAR,         -- Member ID\n"
                    "    entity_id VARCHAR,\n"
                    "    entity_name VARCHAR,\n"
                    "    year_mo VARCHAR,                   -- Claim period YYYY-MM\n"
                    "    first_name VARCHAR,\n"
                    "    last_name VARCHAR,\n"
                    "    paid_amt VARCHAR,                  -- Cast to NUMERIC\n"
                    "    drug_desc VARCHAR,                 -- Drug name/description\n"
                    "    brand_generic_ind VARCHAR,         -- Brand or Generic indicator\n"
                    "    national_drug_code_nbr VARCHAR,\n"
                    "    days_supply VARCHAR,\n"
                    "    pcp_npi VARCHAR,\n"
                    "    prescribing_npi VARCHAR,\n"
                    "    pharmacy VARCHAR,                  -- Pharmacy name\n"
                    "    report_date VARCHAR,\n"
                    "    company_id VARCHAR,\n"
                    "    carrier_id VARCHAR,\n"
                    "    load_date VARCHAR\n"
                    ");\n"
                    "-- NOTE: 103 total columns. Only query-relevant ones shown.\n"
                ),
                "join_keys": {
                    "member_amisys_nbr": "pch_member_roster.amisys_number",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
            {
                "name": "wpo.pch_care_gap_detail",
                "description": "Care gap/quality measure records per member",
                "ddl": (
                    "CREATE TABLE wpo.pch_care_gap_detail (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    carrier_id VARCHAR,\n"
                    "    report_date DATE,\n"
                    "    health_plan VARCHAR,\n"
                    "    mem_f_name VARCHAR,\n"
                    "    mem_l_name VARCHAR,\n"
                    "    dob DATE,\n"
                    "    mem_id VARCHAR,                    -- Member ID (joins to roster.amisys_number)\n"
                    "    measure VARCHAR,                   -- Quality measure name\n"
                    "    measure_status VARCHAR,            -- Compliant, Non-Compliant, etc.\n"
                    "    npi VARCHAR,                       -- Provider NPI\n"
                    "    npi_name VARCHAR,\n"
                    "    last_visit DATE,\n"
                    "    company VARCHAR,                   -- Entity scoping (= company_id)\n"
                    "    mem_city VARCHAR,\n"
                    "    mem_state VARCHAR,\n"
                    "    mem_zip VARCHAR,\n"
                    "    star_flg VARCHAR,                  -- Star rating flag\n"
                    "    source VARCHAR\n"
                    ");\n"
                ),
                "join_keys": {
                    "mem_id": "pch_member_roster.amisys_number",
                    "npi": "pch_provider_info.npi",
                    "company": "entity.entity_id",
                },
                "scope_column": "company",
            },
            {
                "name": "wpo.pch_discharges",
                "description": "Hospital discharge records",
                "ddl": (
                    "CREATE TABLE wpo.pch_discharges (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    pcp_npi TEXT,\n"
                    "    pcp_npi_name TEXT,\n"
                    "    amisys_nbr TEXT,                   -- Member ID\n"
                    "    first_name TEXT,\n"
                    "    last_name TEXT,\n"
                    "    facility TEXT,\n"
                    "    discharge_date TEXT,\n"
                    "    discharge_code TEXT,\n"
                    "    discharge_code_description TEXT,\n"
                    "    member_type TEXT,\n"
                    "    product TEXT,\n"
                    "    report_date TEXT,\n"
                    "    company_id TEXT\n"
                    ");\n"
                ),
                "join_keys": {
                    "amisys_nbr": "pch_member_roster.amisys_number",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
            {
                "name": "wpo.pch_inpatient_census",
                "description": "Inpatient census records",
                "ddl": (
                    "CREATE TABLE wpo.pch_inpatient_census (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    pcp_npi TEXT,\n"
                    "    pcp_npi_name TEXT,\n"
                    "    amisys_nbr TEXT,                   -- Member ID\n"
                    "    first_name TEXT,\n"
                    "    last_name TEXT,\n"
                    "    facility TEXT,\n"
                    "    service_type TEXT,\n"
                    "    admit_date TEXT,\n"
                    "    diagnosis_code TEXT,\n"
                    "    diagnosis_code_description TEXT,\n"
                    "    member_type TEXT,\n"
                    "    product TEXT,\n"
                    "    report_date TEXT,\n"
                    "    company_id TEXT,\n"
                    "    status TEXT\n"
                    ");\n"
                ),
                "join_keys": {
                    "amisys_nbr": "pch_member_roster.amisys_number",
                    "company_id": "entity.entity_id",
                },
                "scope_column": "company_id",
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "Total medical claims cost this year",
            "Top drugs by spend",
            "Care gap compliance rate by measure",
            "Recent discharges this month",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 10: ASSESSMENTS
    # -----------------------------------------------------------------
    "assessments": {
        "description": "Call quality assessments: membercare and agility QA recordings, scores, campaigns.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.membercare_agent_assessment_recordings",
                "description": "Membercare call QA recordings and scores",
                "ddl": (
                    "CREATE TABLE wpo.membercare_agent_assessment_recordings (\n"
                    "    id UUID PRIMARY KEY,\n"
                    "    agent_login VARCHAR(200) NOT NULL,\n"
                    "    recorded_at TIMESTAMP NOT NULL,\n"
                    "    phone_number VARCHAR(15) NOT NULL,\n"
                    "    campaign TEXT,                     -- OEP_2024, OEP_2025, SEP_2026\n"
                    "    file_name VARCHAR(500) NOT NULL,\n"
                    "    total_score BIGINT,                -- 0-100, pass >= 84\n"
                    "    total_edited_score BIGINT,\n"
                    "    call_status VARCHAR(10),           -- pass, fail\n"
                    "    uploaded_by VARCHAR(200),\n"
                    "    entity_id VARCHAR(50),\n"
                    "    sub_entity_id VARCHAR(50),\n"
                    "    created_at TIMESTAMP NOT NULL,\n"
                    "    updated_at TIMESTAMP NOT NULL\n"
                    ");\n"
                    "-- Scoring: total_score 0-100, pass >= 84, fail < 84\n"
                ),
                "join_keys": {
                    "entity_id": "entity.entity_id",
                },
                "scope_column": "entity_id",
            },
            {
                "name": "wpo.agility_agent_assessment_recordings",
                "description": "Agility call QA recordings and scores",
                "ddl": (
                    "CREATE TABLE wpo.agility_agent_assessment_recordings (\n"
                    "    id UUID PRIMARY KEY,\n"
                    "    agent_login VARCHAR(200) NOT NULL,\n"
                    "    recorded_at TIMESTAMP NOT NULL,\n"
                    "    phone_number VARCHAR(15) NOT NULL,\n"
                    "    campaign TEXT,\n"
                    "    file_name VARCHAR(500) NOT NULL,\n"
                    "    total_score BIGINT,                -- 0-100, pass >= 84\n"
                    "    total_edited_score BIGINT,\n"
                    "    call_status VARCHAR(10),\n"
                    "    uploaded_by VARCHAR(200),\n"
                    "    created_at TIMESTAMP NOT NULL,\n"
                    "    updated_at TIMESTAMP NOT NULL\n"
                    ");\n"
                ),
                "join_keys": {},
                "scope_column": None,
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "Average QA score by agent",
            "Pass/fail rate this month",
            "Assessment count by campaign",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 11: CRM
    # -----------------------------------------------------------------
    "crm": {
        "description": "Support tickets, CRM notes, audit history. Tracks customer interactions and issue resolution.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.crm_tickets",
                "description": "Support/service tickets",
                "ddl": (
                    "CREATE TABLE wpo.crm_tickets (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    ticket_id VARCHAR(100) NOT NULL UNIQUE,\n"
                    "    subject VARCHAR(255),\n"
                    "    description TEXT,\n"
                    "    type VARCHAR(100),                 -- Ticket type/category\n"
                    "    status VARCHAR(50),                -- Open, Closed, Pending, etc.\n"
                    "    created_at TIMESTAMP NOT NULL,\n"
                    "    created_by VARCHAR(150),\n"
                    "    owner VARCHAR(200),                -- Assigned user email\n"
                    "    entity_id VARCHAR,\n"
                    "    sub_entity_id VARCHAR,\n"
                    "    resolution TEXT,\n"
                    "    agent_email VARCHAR(320),\n"
                    "    last_updated TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {
                    "owner": "users.email",
                    "entity_id": "entity.entity_id",
                },
                "scope_column": "entity_id",
            },
            {
                "name": "wpo.crm_notes",
                "description": "CRM notes on agents/interactions",
                "ddl": (
                    "CREATE TABLE wpo.crm_notes (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    type VARCHAR(50) NOT NULL,\n"
                    "    description TEXT NOT NULL,\n"
                    "    time_stamp TIMESTAMP,\n"
                    "    user_id UUID NOT NULL,             -- FK: ops_sec.users.user_id\n"
                    "    agent_id UUID NOT NULL,            -- FK: wpo.lup_agents.pk_id\n"
                    "    agent_npn VARCHAR(25),\n"
                    "    is_private BOOLEAN,\n"
                    "    sub_type VARCHAR(50)\n"
                    ");\n"
                ),
                "join_keys": {
                    "user_id": "users.user_id",
                    "agent_id": "lup_agents.pk_id",
                },
                "scope_column": None,
            },
            {
                "name": "wpo.crm_audit_history",
                "description": "CRM audit trail — who did what and when",
                "ddl": (
                    "CREATE TABLE wpo.crm_audit_history (\n"
                    "    audit_id UUID PRIMARY KEY,\n"
                    "    agent_id UUID,\n"
                    "    created_at TIMESTAMP NOT NULL,\n"
                    "    login_user VARCHAR(255) NOT NULL,\n"
                    "    action_message TEXT,\n"
                    "    action VARCHAR(50),\n"
                    "    tab VARCHAR(50),\n"
                    "    module VARCHAR(50),\n"
                    "    sub_module VARCHAR(50),\n"
                    "    entity_id VARCHAR(30) NOT NULL,\n"
                    "    sub_entity_id VARCHAR(30),\n"
                    "    npn VARCHAR(30)\n"
                    ");\n"
                ),
                "join_keys": {
                    "entity_id": "entity.entity_id",
                    "agent_id": "lup_agents.pk_id",
                },
                "scope_column": "entity_id",
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "Open tickets count",
            "Tickets by status",
            "Recent CRM activity for agent",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 12: BOOK OF BUSINESS (Synapse)
    # -----------------------------------------------------------------
    "bob": {
        "description": "Book of Business: carrier membership snapshots, agent production, member counts. Synapse DB — CANNOT join with PostgreSQL tables.",
        "db_type": "synapse",
        "tables": [
            {
                "name": "analytic_vault.bob_carrier_memberships_vw",
                "description": "Carrier membership snapshots — monthly member/contract data by agent",
                "ddl": (
                    "CREATE TABLE analytic_vault.bob_carrier_memberships_vw (\n"
                    "    agent_npn VARCHAR,\n"
                    "    agent_full_name VARCHAR,\n"
                    "    agent_recruiter VARCHAR,\n"
                    "    direct_upline_name VARCHAR,\n"
                    "    top_upline_name VARCHAR,\n"
                    "    carrier_short_name VARCHAR,\n"
                    "    mem_name VARCHAR,\n"
                    "    mem_state VARCHAR(2),\n"
                    "    mem_age INT,\n"
                    "    AgeCategory VARCHAR,\n"
                    "    product_type_mapped VARCHAR,       -- MAPD, PDP, Med Supp\n"
                    "    carrier_status VARCHAR,\n"
                    "    payment_status VARCHAR,\n"
                    "    contract_count INT,\n"
                    "    mem_count INT,\n"
                    "    report_date DATE,\n"
                    "    report_mon_year VARCHAR\n"
                    ");\n"
                    "-- Synapse/T-SQL: use TOP N (not LIMIT), YEAR()/MONTH() (not EXTRACT)\n"
                    "-- Latest: WHERE report_date = (SELECT MAX(report_date) FROM analytic_vault.bob_carrier_memberships_vw)\n"
                ),
                "join_keys": {},
                "scope_column": None,
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "Total members in book of business",
            "Member count by carrier",
            "Agent production by state",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 13: OPERATIONS
    # -----------------------------------------------------------------
    "operations": {
        "description": "Operations metrics: automation dashboard, service interruptions, RPA logs.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "ops_srv.ops_automation_dashboard",
                "description": "Automation metrics per carrier — download/process/status tracking",
                "ddl": (
                    "CREATE TABLE ops_srv.ops_automation_dashboard (\n"
                    "    id BIGINT PRIMARY KEY,\n"
                    "    record_date DATE NOT NULL,\n"
                    "    carrier_id BIGINT NOT NULL,\n"
                    "    carrier_name TEXT,\n"
                    "    carrier_status TEXT,\n"
                    "    acu_download INTEGER DEFAULT 0,\n"
                    "    acu_process INTEGER DEFAULT 0,\n"
                    "    acu_status INTEGER DEFAULT 0,\n"
                    "    bob_download INTEGER DEFAULT 0,\n"
                    "    bob_process INTEGER DEFAULT 0,\n"
                    "    bob_status INTEGER DEFAULT 0,\n"
                    "    com_download INTEGER DEFAULT 0,\n"
                    "    com_process INTEGER DEFAULT 0,\n"
                    "    com_status INTEGER DEFAULT 0,\n"
                    "    entity_id TEXT,\n"
                    "    sub_entity_id TEXT,\n"
                    "    last_updated TIMESTAMP\n"
                    ");\n"
                ),
                "join_keys": {"entity_id": "entity.entity_id"},
                "scope_column": "entity_id",
            },
            {
                "name": "ops_srv.service_interruption",
                "description": "Service interruption tracking per carrier",
                "ddl": (
                    "CREATE TABLE ops_srv.service_interruption (\n"
                    "    id UUID PRIMARY KEY,\n"
                    "    report_date VARCHAR,\n"
                    "    process_name VARCHAR(255) NOT NULL,\n"
                    "    carrier_id VARCHAR(100),\n"
                    "    carrier_name VARCHAR(255),\n"
                    "    issue_description TEXT,\n"
                    "    resolution_description TEXT,\n"
                    "    issue_status VARCHAR(100),         -- Open, Resolved, etc.\n"
                    "    issue_date VARCHAR,\n"
                    "    resolution_date VARCHAR,\n"
                    "    cadence VARCHAR(100),\n"
                    "    entity_id TEXT,\n"
                    "    sub_entity_id TEXT\n"
                    ");\n"
                ),
                "join_keys": {"entity_id": "entity.entity_id"},
                "scope_column": "entity_id",
            },
            {
                "name": "wpo.ops_rpa_script_logs",
                "description": "RPA script execution logs",
                "ddl": (
                    "CREATE TABLE wpo.ops_rpa_script_logs (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    script_name NVARCHAR,\n"
                    "    start_datetime NVARCHAR,\n"
                    "    end_datetime NVARCHAR,\n"
                    "    error NVARCHAR,\n"
                    "    success NVARCHAR,\n"
                    "    process_type NVARCHAR,\n"
                    "    company_id NVARCHAR,\n"
                    "    carrier_id NVARCHAR\n"
                    ");\n"
                ),
                "join_keys": {"company_id": "entity.entity_id"},
                "scope_column": "company_id",
            },
        ],
        "internal_joins": [],
        "sample_queries": [
            "How many service interruptions are open?",
            "Automation status by carrier",
            "RPA failures this week",
        ],
    },

    # -----------------------------------------------------------------
    # DOMAIN 14: LOOKUP (reference tables for JOINs)
    # -----------------------------------------------------------------
    "lookup": {
        "description": "Reference/lookup tables: carriers, address types, license state codes. Used for JOINs, not queried directly.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "wpo.lup_carriers",
                "description": "Carrier master — full carrier details",
                "ddl": (
                    "CREATE TABLE wpo.lup_carriers (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    id NVARCHAR,                       -- Carrier ID\n"
                    "    vendor_name NVARCHAR,              -- Carrier full name\n"
                    "    market NVARCHAR,\n"
                    "    state_availability NVARCHAR,\n"
                    "    modified_time NVARCHAR\n"
                    ");\n"
                ),
                "join_keys": {"id": "com_totals.carrier_id, com_header.carrier_id"},
                "scope_column": None,
            },
            {
                "name": "wpo.lup_carriers_short",
                "description": "Carrier short names mapping",
                "ddl": (
                    "CREATE TABLE wpo.lup_carriers_short (\n"
                    "    pk_id UUID,\n"
                    "    id TEXT,                           -- Carrier ID (joins to carrier_id columns)\n"
                    "    vendor_name TEXT,                  -- Full carrier name\n"
                    "    carrier_short_name TEXT,           -- Short display name\n"
                    "    prefix TEXT,\n"
                    "    pch_agreement TEXT,\n"
                    "    delegated_cred TEXT\n"
                    ");\n"
                ),
                "join_keys": {
                    "id": "com_totals.carrier_id, com_header.carrier_id, com_carrier_statement_totals.carrier_id",
                    "vendor_name": "vw_com_items_ai.carrier_name",
                },
                "scope_column": None,
            },
            {
                "name": "wpo.lup_licences",
                "description": "US state code to state name mapping",
                "ddl": (
                    "CREATE TABLE wpo.lup_licences (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    state_code VARCHAR,\n"
                    "    state_name VARCHAR\n"
                    ");\n"
                ),
                "join_keys": {"state_code": "lup_agent_licenses.state"},
                "scope_column": None,
            },
            {
                "name": "wpo.lup_addrtype",
                "description": "Address type lookup (Mailing, Business, etc.)",
                "ddl": (
                    "CREATE TABLE wpo.lup_addrtype (\n"
                    "    pk_id UUID PRIMARY KEY,\n"
                    "    type NVARCHAR(25)\n"
                    ");\n"
                ),
                "join_keys": {
                    "pk_id": "agent_address.address_type_id, pch_provider_address.address_type_id",
                },
                "scope_column": None,
            },
        ],
        "internal_joins": [],
        "sample_queries": [],
    },

    # -----------------------------------------------------------------
    # DOMAIN 15: ENTITY (restricted — for JOINs only)
    # -----------------------------------------------------------------
    "entity": {
        "description": "Legal entities, sub-entities, system users. Used for JOINs to resolve entity/user names, not queried directly.",
        "db_type": "postgres",
        "tables": [
            {
                "name": "ops_sec.entity",
                "description": "Legal entities (companies/organizations)",
                "ddl": (
                    "CREATE TABLE ops_sec.entity (\n"
                    "    id UUID PRIMARY KEY,\n"
                    "    entity_name VARCHAR(200),\n"
                    "    entity_affiliation VARCHAR(100),\n"
                    "    entity_active_status VARCHAR(20),\n"
                    "    entity_id TEXT UNIQUE              -- The ID used in company_id columns across tables\n"
                    ");\n"
                ),
                "join_keys": {
                    "entity_id": "*.company_id (most tables)",
                },
                "scope_column": None,
            },
            {
                "name": "ops_sec.sub_entity",
                "description": "Sub-entities within an entity",
                "ddl": (
                    "CREATE TABLE ops_sec.sub_entity (\n"
                    "    id UUID PRIMARY KEY,\n"
                    "    entity_id TEXT NOT NULL,           -- FK: ops_sec.entity.entity_id\n"
                    "    sub_entity_lname VARCHAR(100),\n"
                    "    sub_entity_fname VARCHAR(100),\n"
                    "    sub_entity_id TEXT UNIQUE NOT NULL  -- The ID used in sub_entity_id columns\n"
                    ");\n"
                ),
                "join_keys": {
                    "entity_id": "entity.entity_id",
                    "sub_entity_id": "*.sub_entity_id",
                },
                "scope_column": None,
            },
            {
                "name": "ops_sec.users",
                "description": "System users (for resolving owner names on tickets, notes, etc.)",
                "ddl": (
                    "CREATE TABLE ops_sec.users (\n"
                    "    user_id UUID PRIMARY KEY,\n"
                    "    email VARCHAR(200) NOT NULL,\n"
                    "    f_name VARCHAR(100),\n"
                    "    l_name VARCHAR(100),\n"
                    "    role VARCHAR(50) NOT NULL,\n"
                    "    active BOOLEAN,\n"
                    "    agent_npn VARCHAR(20) UNIQUE       -- Links user to agent record\n"
                    ");\n"
                ),
                "join_keys": {
                    "email": "crm_tickets.owner",
                    "user_id": "crm_notes.user_id",
                    "agent_npn": "lup_agents.npn",
                },
                "scope_column": None,
            },
        ],
        "internal_joins": [
            "entity.entity_id = sub_entity.entity_id",
        ],
        "sample_queries": [],
    },
}


# =============================================================================
# QUERY EXAMPLES (from codebase routes)
# =============================================================================

QUERY_EXAMPLES = {
    "commission + entity + carrier": (
        "-- Commission totals with entity name and carrier short name\n"
        "SELECT ct.agent_name, ct.npn, ct.statement_month,\n"
        "       SUM(ct.statement_total::NUMERIC) AS total_paid,\n"
        "       e.entity_name, cs.carrier_short_name\n"
        "FROM wpo.com_totals ct\n"
        "JOIN ops_sec.entity e ON ct.company_id = e.entity_id\n"
        "JOIN wpo.lup_carriers_short cs ON ct.carrier_id = cs.id\n"
        "WHERE {SCOPE_FILTER}\n"
        "  AND ct.statement_month LIKE '2025-11%%'\n"
        "GROUP BY ct.agent_name, ct.npn, ct.statement_month, e.entity_name, cs.carrier_short_name\n"
        "ORDER BY total_paid DESC\n"
        "LIMIT 50"
    ),
    "agent + commissions": (
        "-- Agents with their total commission payments\n"
        "SELECT a.full_name, a.npn, a.email, SUM(ci.payment::NUMERIC) AS total_payment\n"
        "FROM wpo.lup_agents a\n"
        "JOIN wpo.vw_com_items_ai ci ON a.npn = ci.npn\n"
        "WHERE {SCOPE_FILTER}\n"
        "  AND ci.coverage_month = (SELECT MAX(coverage_month) FROM wpo.vw_com_items_ai)\n"
        "GROUP BY a.full_name, a.npn, a.email\n"
        "ORDER BY total_payment DESC\n"
        "LIMIT 50"
    ),
    "provider + address type": (
        "-- Provider addresses with address type name\n"
        "SELECT pa.*, at.type AS address_type_name\n"
        "FROM wpo.pch_provider_address pa\n"
        "JOIN wpo.lup_addrtype at ON at.pk_id = pa.address_type_id\n"
        "WHERE pa.provider_id = '{provider_pk_id}'"
    ),
    "provider networks + provider info": (
        "-- Provider network participation with provider details\n"
        "SELECT n.pk_id, n.affiliation, n.status AS network_status,\n"
        "       p.first_name, p.last_name, p.npi, p.primary_speciality\n"
        "FROM wpo.pch_networks n\n"
        "JOIN wpo.pch_provider_info p ON n.provider = p.pk_id::TEXT\n"
        "WHERE p.status != 'Lead'\n"
        "ORDER BY p.last_name"
    ),
    "tickets + user info": (
        "-- Tickets with assigned user details\n"
        "SELECT t.*, u.f_name, u.l_name, u.role\n"
        "FROM wpo.crm_tickets t\n"
        "LEFT JOIN ops_sec.users u ON t.owner = u.email\n"
        "WHERE {SCOPE_FILTER}"
    ),
    "agent + licenses": (
        "-- Agents with their license information\n"
        "SELECT a.full_name, a.npn, l.state, l.status AS license_status, l.expiry_date\n"
        "FROM wpo.lup_agents a\n"
        "JOIN wpo.lup_agent_licenses l ON a.npn = l.agent_npn\n"
        "WHERE {SCOPE_FILTER}\n"
        "ORDER BY a.full_name, l.state"
    ),
    "agent state license columns": (
        "-- Count agents licensed in Texas (state columns are individual: tx, fl, ca, etc.)\n"
        "SELECT COUNT(*) AS value\n"
        "FROM wpo.lup_agents t\n"
        "WHERE {SCOPE_FILTER} AND t.tx IS NOT NULL"
    ),
    "agents by agency": (
        "-- Agents grouped by agency\n"
        "SELECT t.agency_dba, COUNT(*) AS value\n"
        "FROM wpo.lup_agents t\n"
        "WHERE {SCOPE_FILTER} AND t.agency_dba IS NOT NULL\n"
        "GROUP BY t.agency_dba\n"
        "ORDER BY value DESC\n"
        "LIMIT 50"
    ),
    "member + care gaps": (
        "-- Members with their care gap details\n"
        "SELECT m.first_name, m.last_name, m.amisys_number,\n"
        "       cg.measure, cg.measure_status, cg.last_visit\n"
        "FROM wpo.pch_member_roster m\n"
        "JOIN wpo.pch_care_gap_detail cg ON m.amisys_number = cg.mem_id\n"
        "WHERE {SCOPE_FILTER}"
    ),
    "assessments (membercare)": (
        "-- Membercare QA assessment pass/fail rate by campaign\n"
        "SELECT campaign,\n"
        "       COUNT(*) AS total_assessments,\n"
        "       SUM(CASE WHEN call_status = 'pass' THEN 1 ELSE 0 END) AS passed,\n"
        "       SUM(CASE WHEN call_status = 'fail' THEN 1 ELSE 0 END) AS failed,\n"
        "       ROUND(AVG(total_score), 1) AS avg_score\n"
        "FROM wpo.membercare_agent_assessment_recordings\n"
        "WHERE {SCOPE_FILTER}\n"
        "GROUP BY campaign\n"
        "ORDER BY campaign"
    ),
    "bob (book of business)": (
        "-- Book of business member count by carrier (Synapse — use TOP, not LIMIT)\n"
        "SELECT TOP 50 carrier_short_name,\n"
        "       SUM(mem_count) AS total_members,\n"
        "       SUM(contract_count) AS total_contracts\n"
        "FROM analytic_vault.bob_carrier_memberships_vw\n"
        "WHERE report_date = (SELECT MAX(report_date) FROM analytic_vault.bob_carrier_memberships_vw)\n"
        "GROUP BY carrier_short_name\n"
        "ORDER BY total_members DESC"
    ),
    "commission_totals summary": (
        "-- Total commission paid by carrier for a statement month\n"
        "SELECT ct.carrier_name, SUM(ct.statement_total::NUMERIC) AS total_paid,\n"
        "       COUNT(DISTINCT ct.npn) AS agent_count\n"
        "FROM wpo.com_totals ct\n"
        "WHERE {SCOPE_FILTER}\n"
        "  AND ct.statement_month LIKE '2025-11%%'\n"
        "  AND ct.status = 'Paid'\n"
        "GROUP BY ct.carrier_name\n"
        "ORDER BY total_paid DESC"
    ),
    "agent_contracts by carrier": (
        "-- Active agent contracts grouped by carrier\n"
        "SELECT ac.carrier, ac.status, COUNT(*) AS contract_count\n"
        "FROM wpo.agents_contracts_view ac\n"
        "WHERE {SCOPE_FILTER}\n"
        "  AND ac.status = 'Active'\n"
        "GROUP BY ac.carrier, ac.status\n"
        "ORDER BY contract_count DESC\n"
        "LIMIT 50"
    ),
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

DOMAIN_KEY_COLUMNS = {
    "agent_profile": [
        "status (Active/Inactive/Terminated/Lead/Prospect/Suspended/...)",
        "type (internal agent classification — NOT appointment_type)",
        "npn", "full_name", "email", "phone",
        "preferred_language (English/Spanish/Chinese/...)",
        "gender (Male/Female/Non-Binary/Other)",
    ],
    "agent_contracts": [
        "appointment_type (Producer/Subproducer/AG4/LMO/Reporting Only)",
        "carrier", "status (Active/Appointed/Terminated/Pending/...)",
        "agent_status (Active/Inactive/Terminated/...)",
        "npn", "full_name", "upline_npn", "top_upline_npn",
        "writing_number", "affiliation",
    ],
    "agent_licenses": [
        "type (lic=state license/cert=certification/sbe=state-based exchange)",
        "status (Resident/Non-Resident for licenses; AHIP/FFM for certs)",
        "state (2-letter US state code)", "expiry_date (DATE)",
        "license_market (Health/Life)", "agent_npn",
    ],
    "commission_items": [
        "carrier_name", "npn", "agent_name", "coverage_month (DATE)",
        "market (Health/Life/Senior/P&C/Group/Benefits/Supplemental)",
        "premium (NUMERIC)", "payment (NUMERIC)",
        "insured_name", "account_number", "policy_state",
    ],
    "commission_totals": [
        "carrier_name", "statement_month (DATE)", "npn", "agent_name",
        "statement_total (NUMERIC)", "total_policies (INT)", "active_policies (INT)",
    ],
    "provider_core": [
        "credentialing_status (Credentialed/Pending/Expired/...)",
        "npi", "name", "speciality", "taxonomy_desc",
        "board_certified (Yes/No)", "city", "state",
    ],
    "member_roster": [
        "status", "npi", "mem_id", "mem_name",
        "mem_dob (DATE)", "carrier_name", "plan_name",
    ],
    "bob": [
        "carrier_name", "total_members (INT)", "state",
        "product_type", "plan_year", "enrollment_type",
    ],
    "assessments": [
        "assessment_status", "agent_full_name", "npn",
        "assessment_date (DATE)", "call_recording_url",
    ],
}


def get_domain_catalog() -> Dict[str, Dict]:
    """Lightweight domain catalog for intent detection (~50 tokens/domain).

    Returns only description, db_type, table names, and key_columns — NO DDL.
    Used in Stage 1 (intent detection) to pick relevant domains.
    """
    catalog = {}
    for domain_name, domain_cfg in TABLE_DOMAINS.items():
        catalog[domain_name] = {
            "description": domain_cfg["description"],
            "db_type": domain_cfg["db_type"],
            "tables": [t["name"] for t in domain_cfg["tables"]],
            "sample_queries": domain_cfg.get("sample_queries", []),
            "key_columns": DOMAIN_KEY_COLUMNS.get(domain_name, []),
        }
    return catalog


def get_domain_ddl(domain_names: List[str]) -> str:
    """Get combined DDL for all tables in the specified domains.

    Used in Stage 2 (SQL generation) to provide schema context.
    """
    ddl_parts = []
    for domain_name in domain_names:
        domain_cfg = TABLE_DOMAINS.get(domain_name)
        if not domain_cfg:
            continue
        ddl_parts.append(f"-- ===== DOMAIN: {domain_name} ({domain_cfg['db_type']}) =====")
        for table_cfg in domain_cfg["tables"]:
            ddl_parts.append(table_cfg["ddl"])
    return "\n\n".join(ddl_parts)


def get_join_context(domain_names: List[str]) -> str:
    """Get JOIN relationship text for the specified domains.

    Returns both internal joins (within domains) and cross-domain joins
    relevant to the selected domains.
    """
    lines = ["-- JOIN RELATIONSHIPS"]
    seen_tables = set()

    # Collect all tables in selected domains
    for domain_name in domain_names:
        domain_cfg = TABLE_DOMAINS.get(domain_name)
        if not domain_cfg:
            continue
        for table_cfg in domain_cfg["tables"]:
            seen_tables.add(table_cfg["name"])

        # Internal joins
        if domain_cfg.get("internal_joins"):
            lines.append(f"\n-- Internal joins ({domain_name}):")
            for join in domain_cfg["internal_joins"]:
                lines.append(f"--   {join}")

    # Cross-domain joins (include if either table is in selected domains)
    relevant_cross = []
    for join_name, join_cfg in CROSS_DOMAIN_JOINS.items():
        left_table = join_cfg["left"]
        right_table = join_cfg["right"]

        # Check if either side is in our selected tables
        left_match = left_table in seen_tables or left_table.startswith("*")
        right_match = right_table in seen_tables or right_table.startswith("*")

        if left_match or right_match:
            relevant_cross.append((join_name, join_cfg))

    if relevant_cross:
        lines.append("\n-- Cross-domain joins:")
        for join_name, join_cfg in relevant_cross:
            join_type = join_cfg.get("type", "LEFT JOIN")
            lines.append(f"--   {join_type}: {join_cfg['on']}")
            if join_cfg.get("note"):
                lines.append(f"--     NOTE: {join_cfg['note']}")

    return "\n".join(lines)


def get_query_examples(domain_names: List[str]) -> str:
    """Get relevant SQL examples for the specified domains (DB-backed with fallback)."""
    from app.utils.jay.knowledge_cache import get_query_examples as _cached
    return _cached(domain_names)


def get_all_registered_tables() -> Set[str]:
    """Get all table names from the catalog (for SQL validator whitelist).

    Returns schema-qualified names in uppercase for case-insensitive matching.
    """
    tables = set()
    for domain_cfg in TABLE_DOMAINS.values():
        for table_cfg in domain_cfg["tables"]:
            tables.add(table_cfg["name"].upper())
    return tables


def get_domain_for_module(module_name: str) -> List[str]:
    """Map a semantic registry module name to domain name(s).

    Used for backward compatibility — when LLM returns a module instead of domains.
    """
    MODULE_TO_DOMAIN = {
        "agents": ["agent_profile"],
        "agents_contracts": ["agent_contracts"],
        "licenses": ["agent_licenses"],
        "commission_items": ["commission_items"],
        "commission_totals": ["commission_totals"],
        "pch_providers": ["provider_core", "provider_credentialing"],
        "pch_members": ["member_roster"],
        "bob": ["bob"],
        "membercare_assessments": ["assessments"],
        "agility_assessments": ["assessments"],
        "com_carrier_summary": ["commission_totals"],
    }
    return MODULE_TO_DOMAIN.get(module_name, [module_name])


# =============================================================================
# DYNAMIC DDL BUILDER (from column retrieval results)
# =============================================================================


def build_ddl_for_tables(tables: set, db_session) -> str:
    """Build DDL text for a set of table names dynamically.

    Uses existing DDL from TABLE_DOMAINS where available.
    Falls back to information_schema for tables not in the catalog.
    Batch-queries information_schema for all non-cataloged tables at once.

    Args:
        tables: Set of table names (short or schema-qualified).
        db_session: Active SQLAlchemy session for information_schema fallback.

    Returns:
        Combined DDL string for all requested tables.
    """
    cataloged = {}
    uncached = []

    for table_name in sorted(tables):
        existing_ddl = _find_ddl_in_catalog(table_name)
        if existing_ddl:
            cataloged[table_name] = existing_ddl
        elif table_name in _ddl_cache:
            cataloged[table_name] = _ddl_cache[table_name]
        else:
            uncached.append(table_name)

    # Batch-fetch DDL for all uncached tables in a single query
    if uncached:
        _batch_fetch_ddl(uncached, db_session)

    ddl_parts = []
    for table_name in sorted(tables):
        if table_name in cataloged:
            ddl_parts.append(cataloged[table_name])
        else:
            ddl_parts.append(_ddl_cache.get(table_name, f"-- Table {table_name}: no DDL available"))

    return "\n\n".join(ddl_parts)


def _batch_fetch_ddl(table_names: list, db_session) -> None:
    """Fetch DDL for multiple tables in a single information_schema query."""
    from sqlalchemy import text

    # Parse table names into (schema, table) pairs
    pairs = []
    for name in table_names:
        if "." in name:
            schema, tbl = name.split(".", 1)
        else:
            schema, tbl = "wpo", name
        pairs.append((schema, tbl, name))

    if not pairs:
        return

    # Build a single query with WHERE (schema, table) IN (...)
    conditions = " OR ".join(
        f"(table_schema = '{s}' AND table_name = '{t}')" for s, t, _ in pairs
    )
    query = text(f"""
        SELECT table_schema, table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE {conditions}
        ORDER BY table_schema, table_name, ordinal_position
    """)

    try:
        result = db_session.execute(query)
        rows = result.fetchall()
    except Exception:
        # Fallback to individual queries
        for name in table_names:
            _build_ddl_from_schema(name, db_session)
        return

    # Group rows by (schema.table)
    from collections import defaultdict
    grouped = defaultdict(list)
    for row in rows:
        key = f"{row.table_schema}.{row.table_name}"
        grouped[key].append(row)

    # Build DDL from grouped results and cache
    for original_name in table_names:
        if "." in original_name:
            schema, tbl = original_name.split(".", 1)
        else:
            schema, tbl = "wpo", original_name
        key = f"{schema}.{tbl}"
        cols = grouped.get(key, [])
        if not cols:
            _ddl_cache[original_name] = f"-- Table {original_name}: no column information available"
        else:
            lines = [f"CREATE TABLE {schema}.{tbl} ("]
            for col in cols:
                nullable = "" if col.is_nullable == "YES" else " NOT NULL"
                lines.append(f"    {col.column_name} {col.data_type.upper()}{nullable},")
            lines[-1] = lines[-1].rstrip(",")
            lines.append(");")
            _ddl_cache[original_name] = "\n".join(lines)


def _find_ddl_in_catalog(table_name: str) -> str:
    """Look up DDL for a table in TABLE_DOMAINS.

    Matches on short name (without schema prefix) or full schema-qualified name.

    Args:
        table_name: Table name to search for (e.g., "lup_agents" or "wpo.lup_agents").

    Returns:
        DDL string if found, empty string otherwise.
    """
    short_name = table_name.split(".")[-1] if "." in table_name else table_name
    for domain_cfg in TABLE_DOMAINS.values():
        for tbl in domain_cfg["tables"]:
            tbl_short = tbl["name"].split(".")[-1]
            if tbl_short == short_name or tbl["name"] == table_name:
                return tbl.get("ddl", "")
    return ""


_ddl_cache: dict = {}  # Process-level cache: {table_name: ddl_string}


def _build_ddl_from_schema(table_name: str, db_session) -> str:
    """Build a CREATE TABLE DDL from information_schema columns.

    Used as fallback for tables not in the static TABLE_DOMAINS catalog.
    Results are cached in-process since schema rarely changes.

    Args:
        table_name: Table name, optionally schema-qualified (e.g., "wpo.my_table").
        db_session: Active SQLAlchemy session.

    Returns:
        DDL string, or a comment noting the table was not found.
    """
    if table_name in _ddl_cache:
        return _ddl_cache[table_name]

    from sqlalchemy import text

    schema = "wpo"
    tbl = table_name
    if "." in table_name:
        schema, tbl = table_name.split(".", 1)

    result = db_session.execute(text("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = :schema AND table_name = :table
        ORDER BY ordinal_position
    """), {"schema": schema, "table": tbl})

    cols = result.fetchall()
    if not cols:
        ddl = f"-- Table {table_name}: no column information available"
        _ddl_cache[table_name] = ddl
        return ddl

    lines = [f"CREATE TABLE {schema}.{tbl} ("]
    for col in cols:
        nullable = "" if col.is_nullable == "YES" else " NOT NULL"
        lines.append(f"    {col.column_name} {col.data_type.upper()}{nullable},")
    lines[-1] = lines[-1].rstrip(",")
    lines.append(");")
    ddl = "\n".join(lines)
    _ddl_cache[table_name] = ddl
    return ddl
