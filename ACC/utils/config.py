# ==========================================================
#  config.py
# ==========================================================
"""
Global configuration for ACC RPA Framework
------------------------------------------
Holds environment-wide constants and operational parameters.
No carrier-specific data (e.g., blob paths, GDrive paths, or
download directories) should be defined here — those come
exclusively from [raw].[ops_acc_process_matrix].
"""

import pytz
from datetime import datetime,timedelta,time
# ==========================================================
#  ENVIRONMENT SETTINGS
# ==========================================================
# Toggle between test and production
TEST_MODE = False
TEST_MODE_RUN_ALL = False
TEST_CARRIER_NAME = "CareSource"
TEST_NPNS = ["19176744"]

# Default timezone (used throughout logger and runner)
TIMEZONE = "America/Chicago"

# Runner operating window (CST)
CST = pytz.timezone(TIMEZONE)
BUSINESS_TZ = CST                # already defined in your file
BUSINESS_START = time(8, 0)      # 08:00 CST
BUSINESS_END   = time(23, 0)     # 18:00 CST
MIN_LOOP_GRANULARITY_MIN = 20     # runner call interval assumption
# ==========================================================
#  EMAIL / ALERT CONFIGURATION
# ==========================================================
# Recipients for system-level critical alerts
ALERT_EMAILS = [
    "acorcoran@834labs.com"
]

# Recipients for end-of-run summaries
SUMMARY_EMAILS = [
    "acorcoran@834labs.com"
]


# ==========================================================
# Global Email CC recipients
# ==========================================================
EMAIL_CC = [
    "dataops@834labs.com"
]


# Toggle outbound email alerts globally
# (Ignored or suppressed when TEST_MODE = True)
ENABLE_EMAIL_ALERTS = True

# ==========================================================
#  LOGGING / FAILSAFE BEHAVIOR
# ==========================================================
# If True, logger_utils.log_error_alert() will deactivate all carriers
AUTO_HALT_ON_CRITICAL = True

# ==========================================================
#  OTHER GLOBALS
# ==========================================================
# Placeholder for future environment-wide constants
# (e.g., Power Automate trigger URLs, API endpoints, etc.)
ACC_BLOB_BASE_PATH = "raw/agent_contract_request_carrier/"
ACC_TEMPLATE_PATH  = f"{ACC_BLOB_BASE_PATH}templates/"
ACC_SUCCESS_PATH   = f"{ACC_BLOB_BASE_PATH}success/"
ACC_ERROR_PATH     = f"{ACC_BLOB_BASE_PATH}error/"


# ==========================================================
# Carrier Variant Map (for Drive folder / filename matching)
# ==========================================================
CARRIER_VARIANT_MAP = {
    "blue cross blue shield nebraska": [
        "bcbsne", "bluecrossblueshieldnebraska", "nebraska", "bcbs ne", "Blue Cross Blue Shield Nebraska", "BCBS NE"
    ],
    "priority health": ["priority", "priorityhealth", "ph"],
    "ambetter": ["ambetter", "centene"],
    "cigna": ["cigna", "evernorth"],
    "aetna": ["aetna", "cvshealth", "aetnainsurance"],
    "michigan": ["mi", "michigan", "bcbsm"],
    "wellcare": ["wellcare", "centene", "wc"],
    "molina": ["molina", "molinahealthcare", "mhi"],
}
