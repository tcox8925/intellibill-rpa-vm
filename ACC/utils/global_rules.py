# utils/global_rules.py
"""
Global validation rules for ACC RPA.

These run BEFORE inserting to ops_acc_process_queue,
to prevent unnecessary processing of known invalid records.
"""

from typing import List, Dict
from utils.logger_utils import safe_log


GLOBAL_RULES = [
    {
        "name": "email_not_blank",
        "field": "email",    # <-- FIXED: validate enriched email, not CRM field
        "condition": lambda v: bool(str(v or "").strip()),
        "fail_reason": "Missing Email",
    },
    {
        "name": "npn_not_blank",
        "field": "npn",      # <-- also use queue field name, not Agent.NPN
        "condition": lambda v: bool(str(v or "").strip()),
        "fail_reason": "Missing NPN",
    },
]



def apply_global_rules(records: List[Dict], carrier_row: Dict):
    """
    Runs global post-enrichment validation.
    Only validates queue fields (email, npn).
    """
    valid, invalid = [], []
    crm_fail_status = (carrier_row.get("crm_fail_status") or "Needs Attention").strip()

    for rec in records:
        failed_rules = []
        for rule in GLOBAL_RULES:
            field_val = rec.get(rule["field"])
            if not rule["condition"](field_val):
                failed_rules.append(rule["fail_reason"])

        if failed_rules:
            rec["status"] = "Success"
            rec["contract_status"] = crm_fail_status
            rec["error_reason"] = "; ".join(failed_rules)
            invalid.append(rec)
            safe_log(
                "GLOBAL_RULES",
                f"Validation failed for NPN={rec.get('npn')}: {failed_rules}",
                code="GLOBAL_RULE_FAIL"
            )
        else:
            valid.append(rec)

    return valid, invalid

