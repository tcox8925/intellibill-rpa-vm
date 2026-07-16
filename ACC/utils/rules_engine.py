# ==========================================================
# utils/rules_engine.py  (UPDATED: uses last_eod_sent)
# ==========================================================
"""
ACC RPA Rules Engine — Updated to prevent duplicate EOD runs
using last_eod_sent instead of last_run_time.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, Optional, Union

import pandas as pd

from utils import config
from utils.runner_utils import now_cst
from utils.logger_utils import safe_log

# ==========================================================
# MODE DEFINITIONS
# ==========================================================
RPA_RULES = {
    "EOD_EOD": {
        "schedule_type": "once",
        "email_mode": "batch",
        "template_required": True,
    },
    "BATCH_EOD": {
        "schedule_type": "interval",
        "email_mode": "batch",
        "template_required": True,
    },
    "BATCH_SINGLE": {
        "schedule_type": "continuous",
        "email_mode": "single",
        "template_required": False,
    },
}

DEFAULT_RULE = {
    "schedule_type": "continuous",
    "email_mode": "single",
    "template_required": False,
}

# ==========================================================
# HELPERS
# ==========================================================
def _get(row, key, default=None):
    if isinstance(row, pd.Series):
        return row.get(key, default)
    return (row or {}).get(key, default)


def _norm_upper(val):
    return str(val or "").strip().upper()


def _parse_minutes(val):
    if val is None:
        return None
    try:
        iv = int(float(val))
        return iv if iv > 0 else None
    except:
        return None


def _parse_time_hhmm(val) -> Optional[time]:
    if val is None or val == "":
        return None
    if isinstance(val, time):
        return val
    if isinstance(val, pd.Timestamp):
        return time(val.hour, val.minute, val.second)
    s = str(val).strip()
    for fmt in ("%H:%M", "%I:%M %p", "%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return time(dt.hour, dt.minute, dt.second)
        except:
            continue
    try:
        hh, mm = s.split(":")[:2]
        return time(int(hh), int(mm), 0)
    except:
        return None


def _minutes_since(dt_like):
    if not dt_like:
        return None
    try:
        if isinstance(dt_like, pd.Timestamp):
            past = dt_like.to_pydatetime()
        elif isinstance(dt_like, datetime):
            past = dt_like
        else:
            past = pd.to_datetime(dt_like, errors="coerce")
            if pd.isna(past):
                return None
            past = past.to_pydatetime()
        delta = now_cst() - past
        return max(0, int(delta.total_seconds() // 60))
    except:
        return None


def _is_active(row):
    flag = _get(row, "active_flag", 1)
    return str(flag).strip().lower() in ("1", "true", "yes")


# ==========================================================
# DECISION OBJECT
# ==========================================================
@dataclass
class RunDecision:
    run_now: bool
    reason: str
    mode_key: str
    schedule_type: str
    email_mode: str
    template_required: bool
    eod_due: bool
    frequency_min: Optional[int]
    eod_time: Optional[time]
    template_filter: Optional[dict] = None
    single_email_filter: Optional[dict] = None
    crm_filter: Optional[dict] = None


# ==========================================================
# SHOULD SEND EOD
# ==========================================================
def should_send_eod_email(row):
    eod_t = _parse_time_hhmm(_get(row, "eod_time"))
    if not eod_t:
        return False
    return now_cst().time() >= eod_t

def get_run_mode(row: Union[pd.Series, Dict[str, Any]]) -> Dict[str, Any]:
    key = f"{_norm_upper(_get(row, 'process_type'))}_{_norm_upper(_get(row, 'email_cadence'))}"
    return RPA_RULES.get(key, DEFAULT_RULE)


# ==========================================================
# MAIN LOGIC
# ==========================================================
def evaluate_run(row):

    carrier = _get(row, "carrier_name") or "UNKNOWN"

    if not _is_active(row):
        return RunDecision(False, "inactive", "N/A", "N/A", "N/A", False, False, None, None)

    mode = get_run_mode(row)
    mode_key = f"{_norm_upper(_get(row, 'process_type'))}_{_norm_upper(_get(row, 'email_cadence'))}"
    schedule = mode["schedule_type"]

    # TEST MODE ALWAYS RUN
    if getattr(config, "TEST_MODE", False):
        crm_success = str(_get(row, "crm_success_status") or "").strip().lower()
        return RunDecision(
            True,
            "test_mode",
            mode_key,
            schedule,
            mode["email_mode"],
            mode["template_required"],
            True,
            _parse_minutes(_get(row, "frequency")),
            _parse_time_hhmm(_get(row, "eod_time")),
            template_filter={"status": ["success"], "contract_status": [crm_success]},
            single_email_filter={"status": ["success"], "contract_status": [crm_success]},
            crm_filter={"status": ["success"]},
        )

    # PRODUCTION LOGIC
    last_eod_sent = _get(row, "last_eod_sent")
    mins_since_last_run = _minutes_since(_get(row, "last_run_time"))
    freq_min = _parse_minutes(_get(row, "frequency"))
    eod_t = _parse_time_hhmm(_get(row, "eod_time"))
    eod_due = should_send_eod_email(row)
    crm_success = str(_get(row, "crm_success_status") or "").strip().lower()

    # ======================================================
    # 🔥 EOD SAFETY BLOCKS
    # ======================================================
    try:
        if last_eod_sent is not None:
            last_eod_dt = pd.to_datetime(last_eod_sent)
            if last_eod_dt.date() == now_cst().date():

                # HARD BLOCK for EOD_EOD
                if mode_key == "EOD_EOD":
                    return RunDecision(
                        False, "eod_already_ran_today",
                        mode_key, schedule, mode["email_mode"],
                        False, False, freq_min, eod_t
                    )

                # Prevent second EOD email for BATCH_EOD
                if mode_key == "BATCH_EOD" and eod_due:
                    return RunDecision(
                        False, "eod_email_already_sent_today",
                        mode_key, schedule, mode["email_mode"],
                        False, False, freq_min, eod_t
                    )
    except Exception as e:
        safe_log("RULES_ENGINE", f"EOD safety check failed: {e}")

    data_filters = {
        "template_filter": {"status": ["success"], "contract_status": [crm_success]},
        "single_email_filter": {"status": ["success"], "contract_status": [crm_success]},
        "crm_filter": {"status": ["success"]},
    }

    # ======================================================
    # 1️⃣ ONCE MODE (EOD_EOD)
    # ======================================================
    if schedule == "once":
        if eod_t and eod_due:
            return RunDecision(
                True, "eod_window",
                mode_key, schedule, mode["email_mode"],
                mode["template_required"], True, freq_min, eod_t, **data_filters
            )
        return RunDecision(
            False, "awaiting_eod",
            mode_key, schedule, mode["email_mode"],
            mode["template_required"], False, freq_min, eod_t, **data_filters
        )

    # ======================================================
    # 2️⃣ INTERVAL MODE (BATCH_EOD)
    # ======================================================
    if schedule == "interval":
        if mins_since_last_run is None or (freq_min and mins_since_last_run >= freq_min):
            return RunDecision(
                True, "interval_due",
                mode_key, schedule, mode["email_mode"],
                mode["template_required"], eod_due, freq_min, eod_t, **data_filters
            )
        return RunDecision(
            False, f"interval_wait({mins_since_last_run}/{freq_min}m)",
            mode_key, schedule, mode["email_mode"],
            mode["template_required"], eod_due, freq_min, eod_t, **data_filters
        )

    # ======================================================
    # 3️⃣ CONTINUOUS MODE (BATCH_SINGLE)
    # ======================================================
    return RunDecision(
        True, "continuous",
        mode_key, schedule, mode["email_mode"],
        mode["template_required"], eod_due, freq_min, eod_t, **data_filters
    )
