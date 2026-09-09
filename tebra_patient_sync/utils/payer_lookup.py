"""Payer lookup helpers ported from lookup_payers.sql / payer_lookup.ts.

A carrier name is matched against lookup_payers.payer_name / .payer_alias
(case/whitespace insensitive) among active_status = TRUE rows, optionally
narrowed by claim type (transaction_type). Kept free of any DB access so
the matching rules can be unit tested directly - callers own fetching
(and caching) the active lookup_payers rows and pass them in.
"""

import re
from dataclasses import dataclass, field

PROFESSIONAL_TRANSACTION_TYPES = ["837p", "professionalclaims837p", "prof", "professionalclaims"]
INSTITUTIONAL_TRANSACTION_TYPES = ["837i", "institutionalclaims837i", "inst", "institutionalclaims"]

# Claim type used when a coverage/claim row doesn't specify one. Our
# sources (e.g. Practice Fusion patient coverage) are professional claims
# unless stated otherwise, so "no claim type" defaults to professional
# instead of matching every transaction type.
DEFAULT_CLAIM_TYPE = "P"


def clean_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_lookup_value(value: object) -> str:
    return re.sub(r"\s+", "", clean_value(value).lower())


def normalize_transaction_type(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_value(value).lower())


@dataclass
class Payer:
    payer_id: str
    payer_name: str
    payer_type: list = field(default_factory=list)


def allowed_transaction_types(claim_type: str) -> set[str] | None:
    """Transaction-type codes accepted for a claim type, normalized.

    - Blank/missing claim type defaults to DEFAULT_CLAIM_TYPE (professional).
    - Recognized claim types (P/PROFESSIONAL/PROF, I/INSTITUTIONAL/INST)
      narrow the match to their transaction types.
    - Any other, unrecognized non-blank claim type returns None, meaning
      "no transaction-type filter" (matches any transaction type) - same
      behavior as before this default was added.
    """
    claim = clean_value(claim_type).upper() or DEFAULT_CLAIM_TYPE
    if claim in {"P", "PROFESSIONAL", "PROF"}:
        return {normalize_transaction_type(value) for value in PROFESSIONAL_TRANSACTION_TYPES}
    if claim in {"I", "INSTITUTIONAL", "INST"}:
        return {normalize_transaction_type(value) for value in INSTITUTIONAL_TRANSACTION_TYPES}
    return None


def find_payer(payer_name: str, claim_type: str, active_payers) -> Payer | None:
    """Resolve payer_name against a preloaded list of active lookup_payers rows.

    active_payers: iterable of
    (payer_id, payer_name, payer_type, transaction_type, payer_alias) tuples,
    e.g. as fetched from lookup_payers WHERE active_status = TRUE.
    """
    payer_name = clean_value(payer_name)
    if not payer_name:
        return None

    normalized_target = normalize_lookup_value(payer_name)
    allowed = allowed_transaction_types(claim_type)

    for row_payer_id, row_payer_name, row_payer_type, row_transaction_type, row_payer_alias in active_payers:
        name_matches = normalize_lookup_value(row_payer_name) == normalized_target
        alias_matches = any(
            normalize_lookup_value(alias) == normalized_target
            for alias in (row_payer_alias or [])
        )
        if not name_matches and not alias_matches:
            continue

        if allowed is not None:
            row_transaction_types = {
                normalize_transaction_type(value) for value in (row_transaction_type or [])
            }
            if not (row_transaction_types & allowed):
                continue

        return Payer(
            payer_id=clean_value(row_payer_id),
            payer_name=clean_value(row_payer_name),
            payer_type=list(row_payer_type or []),
        )

    return None


def carrier_key(payer: Payer | None, csv_name: str, stored_id: str = "") -> str:
    """Comparison key for "is this the same carrier" checks.

    Both sides of a comparison must go through find_payer() first; this
    turns the result into a single key so a lookup match compares as
    payer_id, and a lookup miss falls back to the previously stored id
    (for legacy rows) or the normalized raw name.
    """
    if payer is not None and payer.payer_id:
        return f"id:{payer.payer_id}"
    if clean_value(stored_id):
        return f"id:{clean_value(stored_id)}"
    return f"name:{normalize_lookup_value(csv_name)}"
