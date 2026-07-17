"""
Pure helpers for name matching and date coercion — moved verbatim from
tebra_rpa.py. No browser/DB dependencies, so these are unit-testable.
"""

import re
from datetime import datetime, date


def to_date_obj(appt_date_val):
    if appt_date_val is None:
        return None
    if isinstance(appt_date_val, datetime):
        return appt_date_val.date()
    if isinstance(appt_date_val, date):
        return appt_date_val
    if isinstance(appt_date_val, str):
        return datetime.strptime(appt_date_val.strip(), "%Y-%m-%d").date()
    return datetime.strptime(str(appt_date_val), "%Y-%m-%d").date()


def normalize_text(text):
    return re.sub(r"[^A-Za-z0-9]", "", text).lower()


def name_key(text):
    """
    Build a set of name parts for matching.
    'Brown, Sara R' -> frozenset({'brown', 'r', 'sara'})
    'SARA R BROWN'  -> frozenset({'brown', 'r', 'sara'})
    """
    clean = re.sub(r"[^A-Za-z ]", "", text).lower().split()
    return frozenset(clean)


def find_name_match(card_key, needed):
    """
    Match a card name-key against the `needed` dict keys.
      1. Exact frozenset match
      2. Subset match — the shorter name's words all appear in the longer
    Returns the matched key or None.
    """
    if card_key in needed:
        return card_key
    for db_key in needed:
        shorter, longer = (
            (card_key, db_key) if len(card_key) <= len(db_key) else (db_key, card_key)
        )
        if shorter.issubset(longer):
            return db_key
    return None
