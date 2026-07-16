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
    "notifications": True,

    # Email attachments — zip exceptions + missing CSVs
    "email_attachments": True,

    # AI carrier mapper — detect new carriers + AI-suggest mappings
    "ai_carrier_mapper": True,

    # File archiving — move processed files to archive/ after run
    "file_archiving": True,

    # Schema drift check — compare file headers against stored schema
    "schema_check": True,

    # Row variance check — compare row count against previous run
    "variance_check": True,
}

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
    "suspend",
]
