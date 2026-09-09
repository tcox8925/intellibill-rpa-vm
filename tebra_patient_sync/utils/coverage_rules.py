"""Coverage active/inactive rules ported from pf_patient_load.py.

Decides what to do with an incoming coverage row against the patient's
current active coverage of that type (cov_type): update it in place, or
terminate it and insert a new row in its place. A patient never has two
simultaneously active coverages of the same type - regardless of whether
the old and new date ranges overlap, a carrier/subscriber change always
terminates (deactivates, never deletes) the old row before the new one
becomes the active one for that type. Kept free of any DB access so the
decision rules can be unit tested directly.
"""

import re
from datetime import date, datetime, timedelta

ACTION_INSERT = "insert"
ACTION_UPDATE = "update"
ACTION_TERMINATE_AND_INSERT = "terminate_and_insert"
ACTION_REACTIVATE = "reactivate"

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d")


def parse_date(value: object) -> date | None:
    """Parses a coverage date field (arbitrary source formatting) into a
    date, or None if it's missing/unparseable. Every decide_coverage_action
    caller needs this before it has real date objects to pass in."""
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def format_date(value: date | None) -> str | None:
    return value.strftime("%Y-%m-%d") if value else None


def normalize_subscriber_id(value: object) -> str:
    """Lowercase and strip everything but letters/digits, so subscriber
    id formatting differences (dashes, spaces, casing) between CSV
    extracts and stored rows don't cause a false "different coverage"."""
    if value is None:
        value = ""
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def first_day_of_month(value: date) -> date:
    return value.replace(day=1)


def coverage_dates_overlap(
    existing_start: date | None, existing_end: date | None, new_start: date, new_end: date | None
) -> bool:
    """True if [new_start, new_end] overlaps [existing_start, existing_end].
    A missing end date is treated as open-ended (date.max). No existing
    start date means there's nothing to overlap with."""
    if existing_start is None:
        return False
    existing_end_value = existing_end or date.max
    new_end_value = new_end or date.max
    return existing_start <= new_end_value and new_start <= existing_end_value


def is_same_coverage(
    existing_carrier_key: str,
    incoming_carrier_key: str,
    existing_subscriber_id: object,
    incoming_subscriber_id: object,
    existing_type: str,
    incoming_type: str,
) -> bool:
    """Same carrier + same subscriber id + same coverage type (P/S) counts
    as "the same coverage" - i.e. the existing active row should just be
    refreshed rather than replaced."""
    same_carrier = existing_carrier_key == incoming_carrier_key
    same_subscriber = normalize_subscriber_id(existing_subscriber_id) == normalize_subscriber_id(
        incoming_subscriber_id
    )
    same_type = (existing_type or "").strip() == (incoming_type or "").strip()
    return same_carrier and same_subscriber and same_type


def decide_coverage_action(
    existing_carrier_key: str,
    incoming_carrier_key: str,
    existing_subscriber_id: object,
    incoming_subscriber_id: object,
    existing_type: str,
    incoming_type: str,
    existing_active: bool = True,
) -> str:
    """Core active/inactive coverage rule for a patient that already has
    a coverage row of this type to compare against - either the currently
    active one, or (if the caller went looking for reactivation candidates
    because there's no active row) an inactive one with the same identity.

    - Same carrier + subscriber + type, and that existing row is currently
      active -> ACTION_UPDATE (refresh the same row in place).
    - Same carrier + subscriber + type, but that existing row is currently
      INACTIVE (existing_active=False) -> ACTION_REACTIVATE: the same
      coverage came back (e.g. the patient re-enrolled under the same
      plan) - bring that row back to active instead of inserting a
      duplicate. The caller is responsible for terminating any *other*
      currently-active row of this type first, same as it would for
      ACTION_TERMINATE_AND_INSERT, since a patient never has two active
      coverages of the same type at once.
    - Anything different (carrier and/or subscriber changed) ->
      ACTION_TERMINATE_AND_INSERT: the existing row is always terminated
      (marked inactive, never deleted) and the new one becomes the sole
      active row for that type - a patient never has two active coverages
      of the same type at once, regardless of whether their date ranges
      happen to overlap.

    Callers handle the "no existing coverage row at all for this type"
    case (always ACTION_INSERT) before reaching this function.
    """
    if is_same_coverage(
        existing_carrier_key,
        incoming_carrier_key,
        existing_subscriber_id,
        incoming_subscriber_id,
        existing_type,
        incoming_type,
    ):
        return ACTION_UPDATE if existing_active else ACTION_REACTIVATE

    return ACTION_TERMINATE_AND_INSERT


def terminate_end_date(new_start: date, existing_start: date | None = None) -> date:
    """End-of-prior-month date an old coverage gets set to when it's
    terminated in favor of a new coverage starting in new_start's month.

    Never earlier than the row's own effective_start_date - both the old
    and new coverage default to Jan 1 of the current year when their
    source date is missing/unparseable, so "the day before new_start's
    month" can land before the old row's own start (e.g. old row starts
    2026-01-01, this would set its end to 2025-12-31 - an inverted range
    where the row ends before it began). When that would happen, the old
    row is terminated at its own start date instead: it was active for
    zero days before being replaced, not active in the prior year."""
    end = first_day_of_month(new_start) - timedelta(days=1)
    if existing_start is not None and end < existing_start:
        return existing_start
    return end
