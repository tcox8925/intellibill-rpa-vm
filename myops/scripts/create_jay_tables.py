"""
Create Jay tables and populate schema metadata.

Run from the backend root:
    python -m scripts.create_jay_tables

This script:
1. Creates the 4 Jay tables (jay_conversations, jay_messages, jay_favorites, jay_schema_metadata) in wpo schema
2. Populates jay_schema_metadata with DDL and client context for each module
"""

import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, timezone
from sqlalchemy import text, inspect
from app.db.session import engine, SessionLocal
from app.models.BaseClasses import Base

# Import Jay models so they're registered on Base.metadata
from app.models.jay.JayConversation import JayConversation
from app.models.jay.JayMessage import JayMessage
from app.models.jay.JayFavorite import JayFavorite
from app.models.jay.JaySchemaMetadata import JaySchemaMetadata


# ─────────────────────────────────────────────────────────────
# Step 1: Create Jay tables
# ─────────────────────────────────────────────────────────────

def create_tables():
    """Create only the Jay tables using SQLAlchemy metadata."""
    jay_tables = [
        Base.metadata.tables.get("wpo.jay_conversations"),
        Base.metadata.tables.get("wpo.jay_messages"),
        Base.metadata.tables.get("wpo.jay_favorites"),
        Base.metadata.tables.get("wpo.jay_schema_metadata"),
    ]
    # Filter out None (in case table name doesn't match)
    jay_tables = [t for t in jay_tables if t is not None]

    if not jay_tables:
        print("ERROR: No Jay tables found in metadata. Check model imports.")
        print("Available tables:", list(Base.metadata.tables.keys()))
        sys.exit(1)

    print(f"Found {len(jay_tables)} Jay tables to create:")
    for t in jay_tables:
        print(f"  - {t.fullname}")

    # Ensure wpo schema exists
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS wpo"))
        conn.commit()

    # Create tables (checkfirst=True means it won't fail if they already exist)
    Base.metadata.create_all(engine, tables=jay_tables, checkfirst=True)
    print("\nJay tables created successfully.")


# ─────────────────────────────────────────────────────────────
# Step 2: Populate jay_schema_metadata
# ─────────────────────────────────────────────────────────────

SCHEMA_METADATA = [
    # ── Book of Business (Synapse) ──
    {
        "module_name": "bob",
        "registry_json": {
            "display_name": "Book of Business",
            "description": "Carrier membership snapshots showing agent production, member counts, and carrier enrollment status across all carriers.",
            "client": "Agility Insurance Services",
            "client_type": "agility",
            "db_type": "synapse",
            "schema": "analytic_vault",
            "table": "bob_carrier_memberships_vw",
            "grain": "member_snapshot",
            "scope": "global",
            "ddl": {
                "columns": [
                    {"name": "agent_npn", "type": "VARCHAR", "description": "Agent National Producer Number"},
                    {"name": "agent_full_name", "type": "VARCHAR", "description": "Agent full name"},
                    {"name": "agent_recruiter", "type": "VARCHAR", "description": "Recruiting agent name"},
                    {"name": "direct_upline_name", "type": "VARCHAR", "description": "Direct upline agent name"},
                    {"name": "top_upline_name", "type": "VARCHAR", "description": "Top of hierarchy upline name"},
                    {"name": "carrier_short_name", "type": "VARCHAR", "description": "Carrier abbreviation (e.g., UHC, Aetna)"},
                    {"name": "mem_name", "type": "VARCHAR", "description": "Member/insured name"},
                    {"name": "mem_state", "type": "VARCHAR(2)", "description": "Member state code"},
                    {"name": "mem_age", "type": "INT", "description": "Member age"},
                    {"name": "AgeCategory", "type": "VARCHAR", "description": "Age bucket (e.g., 65-69, 70-74)"},
                    {"name": "product_type_mapped", "type": "VARCHAR", "description": "Product type (MAPD, PDP, Med Supp)"},
                    {"name": "carrier_status", "type": "VARCHAR", "description": "Carrier enrollment status"},
                    {"name": "payment_status", "type": "VARCHAR", "description": "Payment/commission status"},
                    {"name": "contract_count", "type": "INT", "description": "Number of contracts"},
                    {"name": "mem_count", "type": "INT", "description": "Number of members"},
                    {"name": "report_date", "type": "DATE", "description": "Report snapshot date"},
                    {"name": "report_mon_year", "type": "VARCHAR", "description": "Report month-year (e.g., Jan 2025)"},
                ],
                "primary_key": None,
                "indexes": [],
            },
            "metrics": {
                "count_records": "COUNT(*)",
                "sum_contracts": "SUM(contract_count)",
                "sum_members": "SUM(mem_count)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "How many total members do we have?",
                "Show member count by carrier",
                "List agents with most members",
                "Break down members by age category",
            ],
        },
    },

    # ── Commission Items (Postgres) ──
    {
        "module_name": "commission_items",
        "registry_json": {
            "display_name": "Commission Items",
            "description": "Individual commission line items showing payments, premiums, and agent earnings per policy/member per carrier per period.",
            "client": "Agility Insurance Services",
            "client_type": "agility",
            "db_type": "postgres",
            "schema": "wpo",
            "table": "vw_com_items_ai",
            "grain": "commission_line",
            "scope": "global",
            "ddl": {
                "columns": [
                    {"name": "pk_id", "type": "UUID", "description": "Primary key"},
                    {"name": "txn_id_com_header", "type": "VARCHAR", "description": "Commission header transaction ID"},
                    {"name": "company_id", "type": "VARCHAR", "description": "Company/entity ID"},
                    {"name": "company_name", "type": "VARCHAR", "description": "Company name"},
                    {"name": "carrier_id", "type": "VARCHAR", "description": "Carrier ID"},
                    {"name": "carrier_name", "type": "VARCHAR", "description": "Carrier name (e.g., UHC, Aetna, Humana)"},
                    {"name": "npn", "type": "VARCHAR", "description": "Agent NPN"},
                    {"name": "agent_name", "type": "VARCHAR", "description": "Agent full name"},
                    {"name": "writing_agent", "type": "VARCHAR", "description": "Writing agent name"},
                    {"name": "writing_agent_npn", "type": "VARCHAR", "description": "Writing agent NPN"},
                    {"name": "upline_name", "type": "VARCHAR", "description": "Upline agent name"},
                    {"name": "top_upline_name", "type": "VARCHAR", "description": "Top upline agent name"},
                    {"name": "account_number", "type": "VARCHAR", "description": "Policy/account number"},
                    {"name": "insured_name", "type": "VARCHAR", "description": "Insured member name"},
                    {"name": "payment", "type": "NUMERIC", "description": "Commission payment amount ($)"},
                    {"name": "premium", "type": "NUMERIC", "description": "Policy premium amount ($)"},
                    {"name": "policy_state", "type": "VARCHAR(2)", "description": "Policy state code"},
                    {"name": "coverage_month", "type": "VARCHAR", "description": "Coverage month (YYYY-MM)"},
                    {"name": "market", "type": "VARCHAR", "description": "Market segment"},
                    {"name": "report_date", "type": "DATE", "description": "Report/load date"},
                    {"name": "load_date", "type": "TIMESTAMP", "description": "Data load timestamp"},
                    {"name": "agility_id", "type": "VARCHAR", "description": "Agility-specific agent ID"},
                ],
                "primary_key": "pk_id",
                "indexes": ["npn", "carrier_name", "report_date", "account_number"],
            },
            "metrics": {
                "sum_commission": "SUM(payment)",
                "count_records": "COUNT(*)",
                "count_agents": "COUNT(DISTINCT npn)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "Total commissions this month",
                "Commission breakdown by carrier",
                "Show top 10 agents by commission",
                "How much did agent John Smith earn?",
            ],
        },
    },

    # ── Commission Totals (Postgres) ──
    {
        "module_name": "commission_totals",
        "registry_json": {
            "display_name": "Commission Totals",
            "description": "Commission statement summaries showing total payouts per carrier per statement period. Used for high-level commission reporting.",
            "client": "Agility Insurance Services",
            "client_type": "agility",
            "db_type": "postgres",
            "schema": "wpo",
            "table": "com_totals",
            "grain": "statement_summary",
            "scope": "global",
            "ddl": {
                "columns": [
                    {"name": "pk_id", "type": "UUID", "description": "Primary key"},
                    {"name": "job_id", "type": "TEXT", "description": "Processing job ID"},
                    {"name": "company_id", "type": "TEXT", "description": "Company/entity ID"},
                    {"name": "company_name", "type": "TEXT", "description": "Company name"},
                    {"name": "carrier_id", "type": "TEXT", "description": "Carrier ID"},
                    {"name": "carrier_name", "type": "TEXT", "description": "Carrier name"},
                    {"name": "statement_month", "type": "TEXT", "description": "Statement month (YYYY-MM)"},
                    {"name": "statement_date", "type": "TEXT", "description": "Statement date"},
                    {"name": "agent_npn", "type": "TEXT", "description": "Agent NPN (alias for npn)"},
                    {"name": "agent_name", "type": "TEXT", "description": "Agent name"},
                    {"name": "payment_type", "type": "TEXT", "description": "Payment type"},
                    {"name": "statement_total", "type": "TEXT", "description": "Statement total amount ($)"},
                    {"name": "statement_type", "type": "TEXT", "description": "Statement type"},
                    {"name": "status", "type": "TEXT", "description": "Processing status"},
                    {"name": "status_date", "type": "TEXT", "description": "Status date"},
                    {"name": "report_date", "type": "TEXT", "description": "Report date"},
                    {"name": "source", "type": "TEXT", "description": "Data source"},
                ],
                "primary_key": "pk_id",
                "indexes": ["carrier_name", "statement_month", "agent_npn"],
            },
            "metrics": {
                "sum_statement_total": "SUM(statement_total)",
                "count_statements": "COUNT(*)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "Total commission statements this quarter",
                "Statement totals by carrier",
                "Show all statements for January 2025",
            ],
        },
    },

    # ── Agents Contracts (Postgres) ──
    {
        "module_name": "agents_contracts",
        "registry_json": {
            "display_name": "Agent Contracts",
            "description": "Agent-carrier contract records showing appointment status, writing numbers, upline hierarchy, and state appointments. Core of agent management.",
            "client": "Agility Insurance Services",
            "client_type": "agility",
            "db_type": "postgres",
            "schema": "wpo",
            "table": "agents_contracts_view",
            "grain": "agent_contract",
            "scope": "global",
            "ddl": {
                "columns": [
                    {"name": "pk_id", "type": "UUID", "description": "Primary key"},
                    {"name": "company_id", "type": "VARCHAR", "description": "Company/entity ID"},
                    {"name": "company_name", "type": "VARCHAR", "description": "Company name"},
                    {"name": "npn", "type": "VARCHAR", "description": "Agent NPN"},
                    {"name": "first_name", "type": "VARCHAR", "description": "Agent first name"},
                    {"name": "last_name", "type": "VARCHAR", "description": "Agent last name"},
                    {"name": "full_name", "type": "VARCHAR", "description": "Agent full name"},
                    {"name": "carrier", "type": "VARCHAR", "description": "Carrier name"},
                    {"name": "status", "type": "VARCHAR", "description": "Contract status (Active, Inactive, Pending)"},
                    {"name": "agent_status", "type": "VARCHAR", "description": "Agent overall status"},
                    {"name": "appointment_type", "type": "VARCHAR", "description": "Appointment type"},
                    {"name": "affiliation", "type": "VARCHAR", "description": "Agent affiliation/agency"},
                    {"name": "writing_number", "type": "VARCHAR", "description": "Carrier-assigned writing number"},
                    {"name": "upline_npn", "type": "VARCHAR", "description": "Direct upline NPN"},
                    {"name": "upline_full_name", "type": "VARCHAR", "description": "Direct upline name"},
                    {"name": "top_upline_npn", "type": "VARCHAR", "description": "Top upline NPN"},
                    {"name": "top_upline_full_name", "type": "VARCHAR", "description": "Top upline name"},
                    {"name": "appointed_states", "type": "TEXT", "description": "Comma-separated appointed states"},
                    {"name": "requested_states", "type": "TEXT", "description": "Comma-separated requested states"},
                    {"name": "assignee", "type": "VARCHAR", "description": "Assigned contracting rep"},
                    {"name": "field_sales_director", "type": "VARCHAR", "description": "Field sales director"},
                    {"name": "marketer", "type": "VARCHAR", "description": "Assigned marketer"},
                    {"name": "commission_schedule_status", "type": "VARCHAR", "description": "Commission schedule status"},
                    {"name": "current_or_sch_status", "type": "VARCHAR", "description": "Current or scheduled status"},
                    {"name": "created_time", "type": "TIMESTAMP", "description": "Contract creation date"},
                    {"name": "modified_time", "type": "TIMESTAMP", "description": "Last modification date"},
                    {"name": "status_date", "type": "TIMESTAMP", "description": "Status change date"},
                    {"name": "email", "type": "VARCHAR", "description": "Agent email"},
                    {"name": "phone", "type": "VARCHAR", "description": "Agent phone number"},
                ],
                "primary_key": "pk_id",
                "indexes": ["npn", "carrier", "status", "upline_npn", "writing_number"],
            },
            "metrics": {
                "count_agents": "COUNT(DISTINCT npn)",
                "count_contracts": "COUNT(*)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "How many active agents?",
                "Show agent count by carrier",
                "List agents with pending contracts",
                "Who are the top uplines by agent count?",
            ],
        },
    },

    # ── Agents Master (Postgres) ──
    {
        "module_name": "agents",
        "registry_json": {
            "display_name": "Agents Master",
            "description": "Master agent directory with contact information, communication preferences, and compliance status. One row per agent.",
            "client": "Agility Insurance Services",
            "client_type": "agility",
            "db_type": "postgres",
            "schema": "wpo",
            "table": "lup_agents",
            "grain": "agent_master",
            "scope": "global",
            "ddl": {
                "columns": [
                    {"name": "pk_id", "type": "UUID", "description": "Primary key"},
                    {"name": "npn", "type": "VARCHAR", "description": "National Producer Number (unique agent ID)"},
                    {"name": "full_name", "type": "VARCHAR", "description": "Agent full name"},
                    {"name": "first_name", "type": "VARCHAR", "description": "First name"},
                    {"name": "last_name", "type": "VARCHAR", "description": "Last name"},
                    {"name": "email", "type": "VARCHAR", "description": "Primary email"},
                    {"name": "phone", "type": "VARCHAR", "description": "Primary phone"},
                    {"name": "mobile", "type": "VARCHAR", "description": "Mobile phone"},
                    {"name": "status", "type": "VARCHAR", "description": "Agent status (Active, Inactive)"},
                    {"name": "company_id", "type": "VARCHAR", "description": "Company/entity ID"},
                    {"name": "company_name", "type": "VARCHAR", "description": "Company name"},
                    {"name": "affiliation", "type": "VARCHAR", "description": "Agency affiliation"},
                    {"name": "sub_entity_id", "type": "VARCHAR", "description": "Sub-entity/group ID"},
                    {"name": "preferred_language", "type": "VARCHAR", "description": "Preferred communication language"},
                    {"name": "permission_to_text", "type": "VARCHAR", "description": "SMS opt-in (Yes/No)"},
                    {"name": "do_not_call", "type": "VARCHAR", "description": "Do not call flag (Yes/No)"},
                    {"name": "email_opt_out", "type": "VARCHAR", "description": "Email opt-out flag (Yes/No)"},
                    {"name": "agility_id", "type": "VARCHAR", "description": "Agility-specific agent ID"},
                    {"name": "agility_dmp", "type": "VARCHAR", "description": "Agility DMP flag"},
                ],
                "primary_key": "pk_id",
                "indexes": ["npn", "full_name", "email", "status"],
            },
            "metrics": {
                "count_agents": "COUNT(*)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "How many active agents do we have?",
                "Find agent by NPN",
                "List agents who opted out of email",
                "Show agents by preferred language",
            ],
        },
    },

    # ── Licenses (Postgres) ──
    {
        "module_name": "licenses",
        "registry_json": {
            "display_name": "Agent Licenses",
            "description": "Agent state licensing records showing license type, status, state, issue/expiry dates, and certification year. Used for compliance tracking.",
            "client": "Agility Insurance Services",
            "client_type": "agility",
            "db_type": "postgres",
            "schema": "wpo",
            "table": "lup_agent_licenses",
            "grain": "agent_license",
            "scope": "global",
            "ddl": {
                "columns": [
                    {"name": "transaction_id", "type": "UUID", "description": "Primary key / transaction ID"},
                    {"name": "agent_npn", "type": "VARCHAR", "description": "Agent NPN (FK to agents)"},
                    {"name": "lic_id", "type": "VARCHAR", "description": "License ID"},
                    {"name": "type", "type": "VARCHAR", "description": "License type (Resident, Non-Resident)"},
                    {"name": "status", "type": "VARCHAR", "description": "License status (Active, Expired, Cancelled)"},
                    {"name": "state", "type": "VARCHAR(2)", "description": "State code (e.g., TX, CA, FL)"},
                    {"name": "issue_date", "type": "DATE", "description": "License issue date"},
                    {"name": "expiry_date", "type": "DATE", "description": "License expiry date"},
                    {"name": "license_number", "type": "VARCHAR", "description": "License number"},
                    {"name": "license_owner", "type": "VARCHAR", "description": "License owner name"},
                    {"name": "license_market", "type": "VARCHAR", "description": "Market type (Health, Life, etc.)"},
                    {"name": "certification_year", "type": "VARCHAR", "description": "Certification year"},
                    {"name": "npn_valid", "type": "VARCHAR", "description": "NPN validity flag"},
                ],
                "primary_key": "transaction_id",
                "indexes": ["agent_npn", "state", "status", "license_number"],
            },
            "metrics": {
                "count_licenses": "COUNT(*)",
                "count_distinct_agents": "COUNT(DISTINCT agent_npn)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "How many active licenses?",
                "Show expired licenses",
                "List licenses by state",
                "Find licenses expiring this month",
            ],
        },
    },

    # ── PCH Providers (Postgres) ──
    {
        "module_name": "pch_providers",
        "registry_json": {
            "display_name": "PCH Providers",
            "description": "Provider credentialing records for PCH health plan clients. Contains NPI, specialties, credentialing status, and organization assignment. Scoped by company (entity) and group (sub-entity).",
            "client": "PCH Health (Primary Care Health)",
            "client_type": "pch",
            "db_type": "postgres",
            "schema": "wpo",
            "table": "pch_provider_info",
            "grain": "provider_record",
            "scope": "entity_scoped",
            "scope_columns": {
                "entity_id": "company_id",
                "sub_entity_id": "group_id",
            },
            "ddl": {
                "columns": [
                    {"name": "pk_id", "type": "UUID", "description": "Primary key"},
                    {"name": "txn_id", "type": "VARCHAR(MAX)", "description": "Transaction ID"},
                    {"name": "npi", "type": "VARCHAR(MAX)", "description": "National Provider Identifier (unique provider ID)"},
                    {"name": "first_name", "type": "VARCHAR(MAX)", "description": "Provider first name"},
                    {"name": "last_name", "type": "VARCHAR(MAX)", "description": "Provider last name"},
                    {"name": "company_id", "type": "VARCHAR(MAX)", "description": "Entity/company ID (permission scope)"},
                    {"name": "company_name", "type": "VARCHAR(MAX)", "description": "Company name"},
                    {"name": "group_id", "type": "VARCHAR(MAX)", "description": "Sub-entity/group ID (permission scope)"},
                    {"name": "group_name", "type": "VARCHAR(MAX)", "description": "Group name"},
                    {"name": "group_pch_ipa", "type": "VARCHAR(MAX)", "description": "PCH IPA group designation"},
                    {"name": "status", "type": "VARCHAR(MAX)", "description": "Credentialing status (Active, Pending, Terminated)"},
                    {"name": "type", "type": "VARCHAR(MAX)", "description": "Provider type (Individual, Organization)"},
                    {"name": "state", "type": "VARCHAR(MAX)", "description": "Provider state"},
                    {"name": "city", "type": "VARCHAR(MAX)", "description": "Provider city"},
                    {"name": "zip", "type": "VARCHAR(MAX)", "description": "Provider zip code"},
                    {"name": "gender", "type": "VARCHAR(MAX)", "description": "Provider gender"},
                    {"name": "primary_speciality", "type": "VARCHAR(MAX)", "description": "Primary medical specialty"},
                    {"name": "secondary_speciality", "type": "VARCHAR(MAX)", "description": "Secondary specialty"},
                    {"name": "professional_degree", "type": "VARCHAR(MAX)", "description": "Degree (MD, DO, NP, etc.)"},
                    {"name": "board_cert", "type": "VARCHAR(MAX)", "description": "Board certification status"},
                    {"name": "email", "type": "VARCHAR(MAX)", "description": "Provider email"},
                    {"name": "source", "type": "VARCHAR(MAX)", "description": "Data source"},
                    {"name": "job_owner_name", "type": "VARCHAR(MAX)", "description": "Assigned credentialing specialist"},
                    {"name": "caqh_number", "type": "VARCHAR(MAX)", "description": "CAQH ProView number"},
                    {"name": "primary_address_1", "type": "VARCHAR(MAX)", "description": "Primary address line 1"},
                    {"name": "primary_address_2", "type": "VARCHAR(MAX)", "description": "Primary address line 2"},
                    {"name": "race", "type": "VARCHAR(MAX)", "description": "Provider race/ethnicity"},
                    {"name": "rx_waiver_expiration_date", "type": "VARCHAR(MAX)", "description": "Prescription waiver expiry"},
                ],
                "primary_key": "pk_id",
                "indexes": ["npi", "company_id", "group_id", "status"],
            },
            "metrics": {
                "count_providers": "COUNT(*)",
                "count_distinct_npi": "COUNT(DISTINCT npi)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "How many active providers?",
                "Show providers by specialty",
                "List providers pending credentialing",
                "Find provider by NPI",
            ],
        },
    },

    # ── PCH Members (Postgres) ──
    {
        "module_name": "pch_members",
        "registry_json": {
            "display_name": "PCH Members",
            "description": "Health plan member roster for PCH clients. Contains demographics, clinical utilization, risk scores, and PCP assignments. Scoped by company (entity).",
            "client": "PCH Health (Primary Care Health)",
            "client_type": "pch",
            "db_type": "postgres",
            "schema": "wpo",
            "table": "pch_member_roster",
            "grain": "member_record",
            "scope": "entity_scoped",
            "scope_columns": {
                "entity_id": "company_id",
            },
            "ddl": {
                "columns": [
                    {"name": "pk_id", "type": "UUID", "description": "Primary key"},
                    {"name": "amisys_number", "type": "VARCHAR", "description": "Amisys member ID (unique member identifier)"},
                    {"name": "medicaid_number", "type": "VARCHAR", "description": "Medicaid ID number"},
                    {"name": "first_name", "type": "VARCHAR", "description": "Member first name"},
                    {"name": "last_name", "type": "VARCHAR", "description": "Member last name"},
                    {"name": "member_dob", "type": "VARCHAR", "description": "Date of birth"},
                    {"name": "gender", "type": "VARCHAR", "description": "Member gender"},
                    {"name": "member_state", "type": "VARCHAR", "description": "Member state"},
                    {"name": "member_city", "type": "VARCHAR", "description": "Member city"},
                    {"name": "member_zip", "type": "VARCHAR", "description": "Member zip code"},
                    {"name": "member_phone_number", "type": "VARCHAR", "description": "Member phone"},
                    {"name": "pcp_npi", "type": "VARCHAR", "description": "Assigned PCP's NPI"},
                    {"name": "line_of_business", "type": "VARCHAR", "description": "Line of business (Medicaid, Medicare, etc.)"},
                    {"name": "product", "type": "VARCHAR", "description": "Product name"},
                    {"name": "population_health_category", "type": "VARCHAR", "description": "Population health category"},
                    {"name": "primary_risk_category", "type": "VARCHAR", "description": "Primary risk category"},
                    {"name": "risk_score", "type": "VARCHAR", "description": "Risk score value"},
                    {"name": "member_type", "type": "VARCHAR", "description": "Member type"},
                    {"name": "member_status", "type": "VARCHAR", "description": "Member enrollment status"},
                    {"name": "total_pcp_claims", "type": "VARCHAR", "description": "Total PCP claim count"},
                    {"name": "all_admits", "type": "VARCHAR", "description": "Total admissions"},
                    {"name": "acute_admits", "type": "VARCHAR", "description": "Acute care admissions"},
                    {"name": "op_claims", "type": "VARCHAR", "description": "Outpatient claims"},
                    {"name": "ed_visits", "type": "VARCHAR", "description": "Emergency department visits"},
                    {"name": "snf_admits", "type": "VARCHAR", "description": "Skilled nursing facility admissions"},
                    {"name": "inntwrk_spec_claims", "type": "VARCHAR", "description": "In-network specialist claims"},
                    {"name": "outntwrk_spec_claims", "type": "VARCHAR", "description": "Out-of-network specialist claims"},
                    {"name": "total_brand_rx_count", "type": "VARCHAR", "description": "Brand Rx prescriptions count"},
                    {"name": "generic_rx_count", "type": "VARCHAR", "description": "Generic Rx prescriptions count"},
                    {"name": "preventable_admits", "type": "VARCHAR", "description": "Preventable admissions"},
                    {"name": "preventable_ed_visits", "type": "VARCHAR", "description": "Preventable ED visits"},
                    {"name": "company_id", "type": "VARCHAR", "description": "Entity/company ID (permission scope)"},
                    {"name": "carrier_id", "type": "VARCHAR", "description": "Carrier ID"},
                    {"name": "report_date", "type": "VARCHAR", "description": "Report date"},
                    {"name": "email_address", "type": "VARCHAR", "description": "Member email"},
                ],
                "primary_key": "pk_id",
                "indexes": ["amisys_number", "pcp_npi", "company_id", "member_status"],
            },
            "metrics": {
                "count_members": "COUNT(*)",
                "list_records": "SELECT *",
            },
            "sample_queries": [
                "How many active members?",
                "Show members by risk category",
                "List members with high ED visits",
                "Find member by Amisys number",
            ],
        },
    },
]


def populate_schema_metadata():
    """Insert or update schema metadata rows."""
    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        inserted = 0
        updated = 0

        for entry in SCHEMA_METADATA:
            existing = (
                session.query(JaySchemaMetadata)
                .filter(JaySchemaMetadata.module_name == entry["module_name"])
                .first()
            )

            if existing:
                existing.registry_json = entry["registry_json"]
                existing.updated_at = now
                updated += 1
                print(f"  Updated: {entry['module_name']}")
            else:
                row = JaySchemaMetadata(
                    module_name=entry["module_name"],
                    registry_json=entry["registry_json"],
                    updated_at=now,
                )
                session.add(row)
                inserted += 1
                print(f"  Inserted: {entry['module_name']}")

        session.commit()
        print(f"\nSchema metadata: {inserted} inserted, {updated} updated.")
    except Exception as e:
        session.rollback()
        print(f"ERROR populating schema metadata: {e}")
        raise
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────
# Step 2b: Migrate schema (add new columns to existing tables)
# ─────────────────────────────────────────────────────────────

def migrate_schema():
    """Add new columns to existing tables safely (idempotent)."""
    migrations = [
        "ALTER TABLE wpo.jay_conversations ADD COLUMN IF NOT EXISTS entity_id VARCHAR;",
        "ALTER TABLE wpo.jay_conversations ADD COLUMN IF NOT EXISTS entity_name VARCHAR(200);",
        "CREATE INDEX IF NOT EXISTS ix_jay_conversations_entity_id ON wpo.jay_conversations(entity_id);",
    ]

    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                print(f"  OK: {stmt.split('IF NOT EXISTS')[1].split(';')[0].strip()}")
            except Exception as e:
                print(f"  SKIP: {stmt[:60]}... ({e})")
        conn.commit()

    print("\nSchema migration complete.")


# ─────────────────────────────────────────────────────────────
# Step 3: Verify
# ─────────────────────────────────────────────────────────────

def verify():
    """Check tables exist and show row counts."""
    session = SessionLocal()
    try:
        tables = [
            "wpo.jay_conversations",
            "wpo.jay_messages",
            "wpo.jay_favorites",
            "wpo.jay_schema_metadata",
        ]
        print("\nVerification:")
        for table in tables:
            try:
                result = session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                print(f"  {table}: {count} rows")
            except Exception as e:
                print(f"  {table}: ERROR - {e}")
                session.rollback()

        # Show metadata modules
        result = session.execute(
            text("SELECT module_name, registry_json->>'display_name' as display_name, registry_json->>'client' as client FROM wpo.jay_schema_metadata ORDER BY module_name")
        )
        rows = result.fetchall()
        if rows:
            print("\nSchema Metadata Modules:")
            print(f"  {'Module':<25} {'Display Name':<25} {'Client'}")
            print(f"  {'-'*25} {'-'*25} {'-'*30}")
            for row in rows:
                print(f"  {row[0]:<25} {row[1]:<25} {row[2]}")
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Jay Tables Setup Script")
    print("=" * 60)

    print("\n[1/4] Creating Jay tables...")
    create_tables()

    print("\n[2/4] Running schema migrations...")
    migrate_schema()

    print("\n[3/4] Populating schema metadata...")
    populate_schema_metadata()

    print("\n[4/4] Verifying...")
    verify()

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)
