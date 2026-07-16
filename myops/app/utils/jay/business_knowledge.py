"""
JAI Business Knowledge - Synonyms, business glossary, and domain terminology.

Maps business language to database columns and values so the LLM can understand
natural language queries without users needing exact column names.

Contains:
1. SYNONYMS: 145 business term -> database column name mappings
2. BUSINESS_GLOSSARY: 34 business concept -> SQL pattern mappings (value-level intelligence)
3. Helper functions to format these for LLM prompt injection
"""

# =====================================================
# SYNONYMS: Business term -> Database column name
# =====================================================
# Used by both intent detection and SQL generation prompts.
# Maps how users talk about data to actual column names.

SYNONYMS = {
    # --- Firm / Company ---
    "agency": "agency_dba",
    "agency name": "agency_dba",
    "company": "agency_dba",
    "office": "agency_dba",
    "entity": "company_name",
    "entity name": "company_name",
    "fmo": "company_name",
    "imo": "company_name",
    "mga": "company_name",
    "bga": "company_name",
    "firm": "agency_dba",
    "organization": "company_name",
    # --- Agent hierarchy ---
    "recruiter": "recruiter",
    "upline": "reports_to",
    "upline agent": "reports_to",
    "direct upline": "reports_to",
    "top upline": "sales_director",
    "sales director": "sales_director",
    "field sales director": "sales_director",
    "hierarchy": "reports_to",
    "downline": "hierarchy/agent relationship",
    # --- Agent identifiers ---
    "NPN": "npn",
    "national producer number": "npn",
    "producer number": "npn",
    "writing number": "writing_number",
    "writing no": "writing_number",
    # --- Carrier ---
    "insurance company": "carrier_name",
    "insurer": "carrier_name",
    "carrier": "carrier_name",
    "payer": "carrier_name",
    "insurance carrier": "insurance_company",
    # --- Commission terms ---
    "commission": "payment",
    "commissions": "payment",
    "premium": "premium_amount",
    "payout": "statement_total",
    "earnings": "payment",
    "payment": "payment",
    "revenue": "statement_total",
    "override": "override commission",
    "overrides": "payment_type",
    "bonus": "payment_type",
    "bonuses": "payment_type",
    "adjustment": "payment_type",
    "adjustments": "payment_type",
    "base commission": "commission amount before adjustments",
    "debit balance": "negative commission balance",
    "credit balance": "positive commission balance",
    # --- Commission members ---
    "subscriber": "insured_name",
    "policyholder": "insured_name",
    "insured": "insured_name",
    "policy holder": "insured_name",
    "account number": "account_number",
    "policy number": "account_number",
    "enrollment": "member enrollment",
    "disenrollment": "member termination",
    "effective date": "effective_date/coverage_start",
    "termination date": "term_date/coverage_end",
    "dependent": "member dependent",
    "roster": "member roster/member list",
    "census": "member count/headcount",
    "headcount": "member count",
    "retention": "member retention rate",
    "churn": "member disenrollment rate",
    # --- Contract terms ---
    "appointment type": "appointment_type",
    "appointment": "appointment_type",
    "subproducer": "appointment_type",
    "sub-producer": "appointment_type",
    "sub producer": "appointment_type",
    "producer": "appointment_type",
    "producers": "appointment_type",
    "contract status": "status",
    "contract": "status",
    "appointed states": "appointed_states",
    "requested states": "requested_states",
    "appointed": "has active appointment",
    "contracted": "has active contract",
    "writing agent": "agent who wrote the policy",
    "servicing agent": "agent currently servicing",
    # --- License terms ---
    "license": "type",
    "license type": "type",
    "resident": "status",
    "non-resident": "status",
    "non resident": "status",
    "licensed in": "state",
    "license state": "state",
    "license market": "license_market",
    "AHIP": "ahip_certified (certification)",
    "FFM": "ffm_certified (certification)",
    "active license": "license where expiry_date >= CURRENT_DATE",
    "expired license": "license where expiry_date < CURRENT_DATE",
    "lapsed license": "license where expiry_date < CURRENT_DATE",
    "E&O": "errors_and_omissions insurance",
    "errors and omissions": "errors_and_omissions",
    # --- Provider terms ---
    "NPI": "npi (national provider identifier)",
    "national provider identifier": "npi",
    "specialty": "primary_speciality",
    "speciality": "primary_speciality",
    "specialization": "primary_speciality",
    "degree": "professional_degree",
    "board certification": "board_cert",
    "credentialing": "status",
    "credentialed": "credentialing_status",
    "CAQH": "caqh_number",
    "owner": "job_owner_name",
    "credentialing specialist": "job_owner_name",
    "TIN": "tax_id",
    "tax id": "tax_id",
    "network": "provider network",
    "in-network": "par provider",
    "out-of-network": "non-par provider",
    "par": "participating provider",
    "non-par": "non-participating provider",
    "specialist": "specialty provider",
    # --- Member terms ---
    "member ID": "amisys_number",
    "amisys": "amisys_number",
    "diagnosis": "primary_risk_category",
    "risk score": "risk_score",
    "risk category": "primary_risk_category",
    "health category": "population_health_category",
    "PCP": "primary care provider",
    "primary care provider": "pcp_npi",
    "line of business": "line_of_business",
    "LOB": "line_of_business",
    "ED visits": "ed_visits",
    "emergency visits": "ed_visits",
    "admissions": "all_admits",
    "hospitalizations": "all_admits",
    # --- Book of Business ---
    "book of business": "bob",
    "bob": "bob",
    # --- Product terms ---
    "plan": "product/plan_name",
    "product": "plan_name/product_name",
    "MAPD": "product_type_mapped",
    "PDP": "product_type_mapped",
    "Med Supp": "product_type_mapped",
    # --- Assessment terms ---
    "QA score": "total_score",
    "quality score": "total_score",
    "compliance score": "edited_compliance_score",
    "campaign": "campaign",
    "call status": "call_status",
    "pass rate": "call_status",
    "fail rate": "call_status",
    # --- Market terms ---
    "market": "market",
    "market segment": "market",
    "coverage month": "coverage_month",
    "statement month": "statement_month",
}


def get_synonym_context() -> str:
    """Build synonym context string for LLM prompts (DB-backed with fallback)."""
    from app.utils.jay.knowledge_cache import get_synonym_context as _cached
    return _cached()


# =====================================================
# BUSINESS GLOSSARY: Concept -> SQL pattern mapping
# =====================================================
# Value-level intelligence: maps business concepts to actual
# column + value combinations the LLM should use in SQL.

BUSINESS_GLOSSARY = {
    "subproducer": {
        "description": "Agent with appointment_type 'Subproducer' — appointed under another agent",
        "sql_hint": "WHERE t.appointment_type = 'Subproducer'",
        "module": "agents_contracts",
    },
    "producer": {
        "description": "Agent with appointment_type 'Producer' in their contract record",
        "sql_hint": "WHERE t.appointment_type = 'Producer'",
        "module": "agents_contracts",
    },
    "ag4": {
        "description": "Agent with appointment_type 'AG4' in their contract record",
        "sql_hint": "WHERE t.appointment_type = 'AG4'",
        "module": "agents_contracts",
    },
    "lmo": {
        "description": "Agent with appointment_type 'LMO' in their contract record",
        "sql_hint": "WHERE t.appointment_type = 'LMO'",
        "module": "agents_contracts",
    },
    "reporting only": {
        "description": "Agent with appointment_type 'Reporting Only' in their contract record",
        "sql_hint": "WHERE t.appointment_type = 'Reporting Only'",
        "module": "agents_contracts",
    },
    "resident agent": {
        "description": "Agent who holds a Resident license in a state (lives in that state). On lup_agent_licenses, the 'status' column stores 'Resident' or 'Non-Resident'",
        "sql_hint": "WHERE t.status = 'Resident' (on lup_agent_licenses table)",
        "module": "licenses",
    },
    "non-resident agent": {
        "description": "Agent licensed in a state but does NOT live there (out-of-state). On lup_agent_licenses, status = 'Non-Resident'",
        "sql_hint": "WHERE t.status = 'Non-Resident' OR t.status = 'Non-R' (on lup_agent_licenses)",
        "module": "licenses",
    },
    "agents in a state": {
        "description": "On lup_agents, each US state has its OWN column (tx, fl, ca, etc.) with value 'Resident', 'Non-Resident', or NULL. Do NOT look for a 'state' column — states are individual columns.",
        "sql_hint": "WHERE t.tx = 'Resident' (for Texas residents on lup_agents)",
        "module": "agents",
    },
    "agents licensed in a state": {
        "description": "On lup_agent_licenses, use the 'state' column (2-letter code) for the license state. Use 'status' column for Resident/Non-Resident type.",
        "sql_hint": "WHERE t.state = 'TX' AND t.status = 'Non-Resident' (for non-resident TX licenses)",
        "module": "licenses",
    },
    "appointed states": {
        "description": "States where agent has been officially appointed by a carrier. Stored as semicolon-separated state codes in appointed_states column.",
        "sql_hint": "WHERE t.appointed_states LIKE '%%TX%%' (check if TX is in appointed states)",
        "module": "agents_contracts",
    },
    "requested states": {
        "description": "States where carrier appointment has been requested but not yet approved. Stored as semicolon-separated state codes in requested_states column.",
        "sql_hint": "WHERE t.requested_states LIKE '%%TX%%'",
        "module": "agents_contracts",
    },
    "appointed vs requested": {
        "description": "Compare appointed_states vs requested_states on agents_contracts_view. Both are semicolon-separated state code strings.",
        "sql_hint": "SELECT appointed_states, requested_states FROM wpo.agents_contracts_view",
        "module": "agents_contracts",
    },
    "active contract": {
        "description": "Contract with status 'Active' or 'Appointed' on agents_contracts_view. Common active-like statuses: Active, Active - Re-Certification Needed, Active - Reporting Only, Appointed, Effective.",
        "sql_hint": "WHERE t.status = 'Active' or t.status IN ('Active', 'Appointed', 'Effective')",
        "module": "agents_contracts",
    },
    "pending contract": {
        "description": "Contract in a Pending state. Multiple variants: 'Pending', 'Pending - Certification Required', 'Pending - FFM Required', 'Pending - First Business', etc.",
        "sql_hint": "WHERE t.status LIKE 'Pending%%'",
        "module": "agents_contracts",
    },
    "terminated agent": {
        "description": "Agent with status 'Terminated', 'Termed by Carrier', 'Termed - Carrier Exit', or 'Termed - Reporting Only'",
        "sql_hint": "WHERE t.status LIKE 'Termed%%' OR t.status = 'Terminated'",
        "module": "agents_contracts",
    },
    "commission types": {
        "description": "Payment types for commissions: 'Commission' (base agent pay), 'Override' (upline override), 'Bonus', 'Assignment', 'Other'. The column is payment_type.",
        "sql_hint": "WHERE t.payment_type = 'Commission' or GROUP BY t.payment_type",
        "module": "commission_totals",
    },
    "overrides": {
        "description": "Override commissions paid to upline agents. Filter by payment_type = 'Override'.",
        "sql_hint": "WHERE t.payment_type = 'Override'",
        "module": "commission_totals",
    },
    "agility revenue": {
        "description": "Revenue to the agency. On com_totals, look at the total statement_total or filter by specific payment_type values.",
        "sql_hint": "SUM(t.statement_total::NUMERIC) for total revenue",
        "module": "commission_totals",
    },
    "commission status": {
        "description": "Status of commission statement on com_totals: 'Paid', 'On Hold', 'ACH Returned', 'Applied to Debit Balance', 'Debit Balance Collected', 'Debit Balance Owed', 'Direct Deposit Needed', 'Archive', 'Tech Upload'",
        "sql_hint": "WHERE t.status = 'Paid'",
        "module": "commission_totals",
    },
    "member health category": {
        "description": "Population health classification of members. Common values: HEALTHY, HEALTHY-AT RISK, CHRONIC BIG 5-AT RISK, CHRONIC BIG 5-STABLE, ACUTE EPISODIC, COMPLEX-CANCER, END OF LIFE, etc.",
        "sql_hint": "GROUP BY t.population_health_category",
        "module": "pch_members",
    },
    "member risk category": {
        "description": "Primary risk/diagnosis category: DIABETES, CARDIOLOGY, COPD, AIDS/HIV, CANCER, BEHAVIORAL HEALTH, BMI: OBESITY, etc. 70+ categories.",
        "sql_hint": "GROUP BY t.primary_risk_category",
        "module": "pch_members",
    },
    "market segment": {
        "description": "On commission items (vw_com_items_ai), market column contains product categories: ACA, Medicare, Supplemental, Dental, Vision, Life, Group, Individual. Also may contain agent names (data quality varies).",
        "sql_hint": "WHERE t.market IN ('ACA', 'Medicare', 'MED', 'MEDICARE', 'SUP', 'Supplemental')",
        "module": "commission_items",
    },
    "QA assessment": {
        "description": "Call recording quality assessment. total_score 0-100, call_status 'pass' (>=84) or 'fail' (<84). Campaigns: OEP (Open Enrollment), SEP (Special Enrollment).",
        "sql_hint": "WHERE call_status = 'pass'",
        "module": "membercare_assessments",
    },
    "sale vs no sale": {
        "description": "On membercare assessments (vw_membercare_assessments or membercare_assessments), use call_status ('pass'/'fail') and total_score to distinguish sales outcomes. A passing assessment (call_status = 'pass', total_score >= 84) typically indicates a compliant sale. Use edited_compliance_score for the compliance-adjusted score.",
        "sql_hint": "WHERE t.call_status = 'pass' -- for compliant/passing calls; use total_score >= 84 as numeric threshold",
        "module": "membercare_assessments",
    },
    "provider specialty": {
        "description": "Medical specialty of provider. Column: primary_speciality. Common values: Family Medicine, Internal Medicine, Nurse Practitioner, Pediatrics, OB/GYN, General Practice.",
        "sql_hint": "GROUP BY t.primary_speciality",
        "module": "pch_providers",
    },
    "provider status": {
        "description": "CRITICAL: pch_provider_info.status values: 'Lead' (~6.8M rows, bulk NPI registry imports — NOT real providers), 'Active' (33), 'Prospect' (106), 'Compliance' (1), 'Deactivated' (1). When user asks about 'providers' or 'how many providers' they mean status != 'Lead'. Only include Lead when user explicitly asks about leads or the NPI registry.",
        "sql_hint": "WHERE t.status != 'Lead'",
        "module": "pch_providers",
    },
    "provider count": {
        "description": "To count real providers, ALWAYS filter out Lead status. 'Lead' rows are bulk NPI registry imports, not actual credentialed providers.",
        "sql_hint": "WHERE t.status != 'Lead'",
        "module": "pch_providers",
    },
    "member product": {
        "description": "Health plan product on member roster: 'PREMIER' or 'VALUE'.",
        "sql_hint": "WHERE t.product = 'PREMIER'",
        "module": "pch_members",
    },
    "top performing agent": {
        "description": "Agent ranked by total commission amount in the most recent 12 months. Use: SELECT agent columns, SUM(commission_amount) as total FROM commission tables GROUP BY agent ORDER BY total DESC LIMIT 10",
        "sql_hint": "SELECT agent_name, SUM(payment::NUMERIC) as total FROM wpo.com_totals WHERE statement_month >= (CURRENT_DATE - INTERVAL '12 months') GROUP BY agent_name ORDER BY total DESC LIMIT 10",
        "module": "commission_totals",
    },
    "active agent": {
        "description": "Agent with at least one active contract (status='Active') AND at least one unexpired license (expiry_date >= CURRENT_DATE)",
        "sql_hint": "JOIN wpo.agents_contracts_view ac ON ac.npn = a.npn AND ac.status = 'Active' JOIN wpo.lup_agent_licenses lic ON lic.npn = a.npn AND lic.expiry_date >= CURRENT_DATE",
        "module": "agents",
    },
    "lapsed license": {
        "description": "A license where expiry_date < CURRENT_DATE. To find agents with lapsed licenses: JOIN agent_licenses WHERE expiry_date < CURRENT_DATE",
        "sql_hint": "WHERE t.expiry_date < CURRENT_DATE (on lup_agent_licenses table)",
        "module": "licenses",
    },
    "appointed vs contracted": {
        "description": "Appointed = has state appointment for a carrier (appointed_states column). Contracted = has signed contract with firm (status = 'Active' on agents_contracts_view). An agent can be contracted but not yet appointed.",
        "sql_hint": "SELECT npn, appointed_states, requested_states, status FROM wpo.agents_contracts_view",
        "module": "agents_contracts",
    },
    "E&O insurance": {
        "description": "Errors & Omissions insurance. Required for agent compliance. Check e_and_o_expiry column on agent records. An active E&O policy has e_and_o_expiry >= CURRENT_DATE.",
        "sql_hint": "WHERE t.e_and_o_expiry >= CURRENT_DATE (active E&O) OR WHERE t.e_and_o_expiry < CURRENT_DATE (expired E&O)",
        "module": "agents",
    },
    "book of business": {
        "description": "All policies/members assigned to an agent. Query the bob module tables. Represents the total block of business an agent manages.",
        "sql_hint": "SELECT * FROM wpo.bob_view WHERE agent_npn = '...'",
        "module": "bob",
    },
}


def get_business_glossary_context() -> str:
    """Build business glossary context string for LLM prompts (DB-backed with fallback)."""
    from app.utils.jay.knowledge_cache import get_business_glossary_context as _cached
    return _cached()
