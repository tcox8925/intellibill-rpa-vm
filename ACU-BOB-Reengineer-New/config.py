# ==========================================================
#  config.py
# ==========================================================
"""
Global configuration for the ACU/BOB platform.

Feature flags, processing rules, and pipeline settings.
All in one place — not hardcoded across processor/runner code.
"""

# ==========================================================
#  FEATURE FLAGS
# ==========================================================

FEATURES = {
    # AI intelligence — generate run analysis report
    "ai_report": True,

    # Notifications — send Teams email
    "notifications": False,

    # Email attachments — zip exceptions + missing CSVs
    "email_attachments": False,

    # AI carrier mapper — detect new carriers + AI-suggest mappings
    "ai_carrier_mapper": True,

    # File archiving — move processed files to archive/ after run
    "file_archiving": True,

    # Schema drift check — compare file headers against stored schema
    "schema_check": True,

    # Row variance check — compare row count against previous run
    "variance_check": True,

    # Test mode — no DB writes (CLI --test also sets this)
    "test_mode": False,

    # Use config/ CSVs for rules + load matrix instead of DB (DB writes still happen if test_mode is False)
    "use_local_matrix": False,

    # Job tracking — writes processing/Success/Failure to ops_srv.ops_process_history
    "job_tracking": True,
}

# ==========================================================
#  FILE OVERRIDE PATHS
# ==========================================================
# When True, reads raw files from local directory instead of blob,
# and writes results/exceptions locally too.
# Runners check: if FILE_OVERRIDE: scan local dirs, write local.
# DB connection still needed for identity resolution.

FILE_OVERRIDE = False
FILE_OVERRIDE_PATH = r"C:\Users\poorn\Microsoft\Downloads\ACUBOB"


def get_override_path(process_type):
    """Get local file override directory for ACU or BOB."""
    import os
    if process_type == "ACU":
        return os.path.join(FILE_OVERRIDE_PATH, "ACU")
    return os.path.join(FILE_OVERRIDE_PATH, "BOB")

# ==========================================================
#  PIPELINE SETTINGS
# ==========================================================

# Max parallel processing threads
MAX_THREADS = 5

# Exception rate threshold — carriers above this get flagged
EXCEPTION_THRESHOLD_PCT = 10

# Row variance — percentage change that triggers critical (deactivation)
ROW_VARIANCE_CRITICAL_PCT = 0.50  # 50%

# ==========================================================
#  ENTITY / SUB-ENTITY
# ==========================================================
# Stamped into DB tables only (not output files).
# Same for all carriers in both ACU and BOB.

ENTITY_ID = ""
SUB_ENTITY_ID = ""

# ==========================================================
#  CONTRACT STATUS EXCLUSIONS (wpo.lup_agents_contracts.status)
# ==========================================================
# Contracts with these statuses are loaded and matched,
# but matched agents are routed to EXCEPTIONS (not results)
# with reason: "Contract Status - {status}"

EXCLUDED_CONTRACT_STATUSES = [
    "On Hold",
]

# ==========================================================
#  AGENT STATUS EXCLUSIONS (wpo.lup_agents.status)
# ==========================================================
# After identity resolution, matched agents are looked up
# in lup_agents. If their agent-level status is in this list,
# they are routed to EXCEPTIONS with reason:
# "Agent Status - {status}"
#
# If a matched agent does NOT exist in lup_agents at all,
# they are exceptioned as "Agent not in registry"

EXCLUDED_AGENT_STATUSES = [
    "Quarantined",
    "Suspended",
]

# ==========================================================
#  PROCESS COLUMN MAP — generic rule columns → data columns
# ==========================================================
# Rule columns in the matrix are generic (status_value_map,
# type_value_map, default_type_value). This map tells the
# processor which DATA column to apply each rule to, per
# process_type. Keeps the matrix shared, code process-aware.

PROCESS_COLUMN_MAP = {
    "ACU": {
        # status_value_map → remap values in contract_status
        "status_value_map_target":   "contract_status",
        # type_value_map → remap values in appointment_type (in-place)
        "type_value_map_source":     "appointment_type",
        "type_value_map_target":     "appointment_type",
        # default_type_value → default for appointment_type
        "default_type_target":       "appointment_type",
        # identity resolution table
        "identity_table":            "wpo.lup_agents_contracts",
        # date columns to parse
        "date_columns":              ["contract_date", "appointed_date"],
    },
    "BOB": {
        # status_value_map → remap values in mem_status
        "status_value_map_target":   "mem_status",
        # type_value_map → read product_type, write mem_market
        "type_value_map_source":     "product_type",
        "type_value_map_target":     "mem_market",
        # default_type_value → default for agent_writing_num (Oscar)
        "default_type_target":       "agent_writing_num",
        # identity resolution table
        "identity_table":            "wpo.lup_agents_contracts",
        # date columns to parse
        "date_columns":              ["mem_effective_date", "mem_cov_end_date", "mem_dob",
                                      "mem_paid_thru_date", "mem_app_date", "mem_enroll_date",
                                      "agent_start_date", "agent_end_date", "renew_date",
                                      "link_start_date", "cov_date", "app_recvd_date",
                                      "aptc_subsidy_start_date", "comm_eff_date", "policy_due_date"],
    },
}

# ==========================================================
#  BOB EXCEPTION CODES  (loaded from wpo.lup_exception_list at runtime)
# ==========================================================
# Logical label-name constants (stable keys into BOB_EXC_CODES dict).
# These match the suffix in exception_code after "E##-BOB-".
BOB_EXC_LABEL_NO_CONTRACT    = "ActiveContractError"       # NPN found, no active contract
BOB_EXC_LABEL_NO_WRITING_NUM = "MatchingWritingNoError"    # WR not in CRM
BOB_EXC_LABEL_NAME_NOT_FOUND = "MatchingNameError"         # name lookup found nothing
BOB_EXC_LABEL_NAME_MULTIPLE  = "MatchingNameMultiple"      # name matched >1 agent (ambiguous)
BOB_EXC_LABEL_FILE_ERROR     = "FileError"                 # file-level processing error

# Populated by load_bob_exception_codes(conn) — maps label → "E{id}" string.
BOB_EXC_CODES = {}


def load_bob_exception_codes(conn):
    """
    Load BOB exception codes from wpo.lup_exception_list at pipeline startup.
    Parses exception_code labels (e.g. 'E13-BOB-ActiveContractError') into
    a dict keyed by the suffix ('ActiveContractError') → 'E13'.
    """
    import pandas as pd
    global BOB_EXC_CODES
    df = pd.read_sql(
        "SELECT exception_id, exception_code FROM wpo.lup_exception_list "
        "WHERE exception_process_code = 'BOB'",
        conn
    )
    for _, row in df.iterrows():
        parts = str(row["exception_code"]).split("-", 2)
        if len(parts) >= 3:
            BOB_EXC_CODES[parts[2]] = f"E{row['exception_id']}"
    print(f"  📋 Loaded {len(BOB_EXC_CODES)} BOB exception codes: {BOB_EXC_CODES}")
    return BOB_EXC_CODES

# ==========================================================
#  BLOCKED STATUS TRANSITIONS
# ==========================================================
# Rule 1: Reverse reactivation
#   If OLD status matches BLOCKED_TRANSITION_FROM and
#   NEW status matches BLOCKED_TRANSITION_TO → BLOCKED.
#   e.g., terminated → active = blocked
#
# Rule 2: Unusual deactivation from active
#   If OLD status = active and NEW status is NOT in
#   ALLOWED_FROM_ACTIVE → BLOCKED.
#   e.g., active → suspended = blocked
#   e.g., active → terminated = allowed (normal lifecycle)
#
# A contract cannot appear in both results and exceptions.
# Matching is case-insensitive substring.

BLOCKED_TRANSITION_FROM = [
    "terminat",  # terminated
    "termed",  # termed (short form)
    "inactive",
    "cancelled",
]

BLOCKED_TRANSITION_TO = [
    "active",  # active, Active - Recertification Needed, etc.
]

# When transitioning FROM active, only these destinations are allowed.
# Anything else (e.g., active → suspended, active → pending) is blocked.
ALLOWED_FROM_ACTIVE = [
    "active",  # active variants (Active - Recertification Needed, etc.)
    "terminat",  # terminated
    "termed",  # termed (short form)
    "inactive",
    "cancel",# cancelled
    # Certification/license renewal: licenses & certifications renew annually, so
    # an active agent moving into a pending-certification / pending-license state
    # is normal and must NOT be blocked (applies to ALL carriers). This whitelists
    # "active -> pending certification", "active -> pending - certification required",
    # and "active -> pending state license/certification". Note: "active -> suspended"
    # and "active -> pending contract" remain guarded (not certification/license).
    "certification",  # ...pending certification / recertification / pending - certification required
    "license",        # ...pending state license/certification
    # Guide-defined pending statuses that carriers map active agents INTO and that
    # must post as updates (not be blocked as "unusual deactivation"). E.g. Zing maps
    # "Pending Principal/Contract" -> "Pending - Agency Appointment" for active agents.
    "agency appointment",  # ...pending - agency appointment
]
