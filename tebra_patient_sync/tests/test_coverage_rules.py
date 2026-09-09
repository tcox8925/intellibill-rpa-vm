"""Tests for utils/coverage_rules.py - the active/inactive coverage rules
used to decide whether an incoming coverage row updates the existing
active row, inserts alongside it, or terminates it in favor of a new row."""

import unittest
from datetime import date

from utils.coverage_rules import (
    ACTION_REACTIVATE,
    ACTION_TERMINATE_AND_INSERT,
    ACTION_UPDATE,
    coverage_dates_overlap,
    decide_coverage_action,
    first_day_of_month,
    is_same_coverage,
    normalize_subscriber_id,
    terminate_end_date,
)


class NormalizeSubscriberIdTests(unittest.TestCase):
    def test_strips_dashes_spaces_and_case(self):
        self.assertEqual(normalize_subscriber_id("ABC-123 456"), "abc123456")

    def test_none_and_empty_normalize_to_empty_string(self):
        self.assertEqual(normalize_subscriber_id(None), "")
        self.assertEqual(normalize_subscriber_id(""), "")


class FirstDayOfMonthTests(unittest.TestCase):
    def test_returns_first_of_the_same_month(self):
        self.assertEqual(first_day_of_month(date(2026, 3, 17)), date(2026, 3, 1))


class TerminateEndDateTests(unittest.TestCase):
    def test_is_last_day_of_prior_month(self):
        self.assertEqual(terminate_end_date(date(2026, 3, 1)), date(2026, 2, 28))

    def test_handles_january_rollover_to_prior_year(self):
        self.assertEqual(terminate_end_date(date(2026, 1, 15)), date(2025, 12, 31))

    def test_never_ends_before_the_row_s_own_start(self):
        # Old and new coverage both defaulted to Jan 1 of the same year (no
        # source effective_start_date on either) - "day before new_start's
        # month" would be 2025-12-31, before the old row's own 2026-01-01
        # start. It gets terminated at its own start instead of an inverted
        # end-before-start range.
        self.assertEqual(
            terminate_end_date(date(2026, 1, 1), existing_start=date(2026, 1, 1)),
            date(2026, 1, 1),
        )

    def test_existing_start_after_default_is_unaffected(self):
        # Existing row genuinely started before the new coverage's month -
        # normal case, no clamping needed.
        self.assertEqual(
            terminate_end_date(date(2026, 3, 1), existing_start=date(2025, 6, 1)),
            date(2026, 2, 28),
        )


class CoverageDatesOverlapTests(unittest.TestCase):
    def test_no_existing_start_means_no_overlap(self):
        self.assertFalse(
            coverage_dates_overlap(None, None, date(2026, 1, 1), date(2026, 12, 31))
        )

    def test_overlapping_ranges(self):
        self.assertTrue(
            coverage_dates_overlap(
                date(2026, 1, 1), date(2026, 6, 30), date(2026, 6, 1), date(2026, 12, 31)
            )
        )

    def test_non_overlapping_ranges(self):
        self.assertFalse(
            coverage_dates_overlap(
                date(2026, 1, 1), date(2026, 3, 31), date(2026, 4, 1), date(2026, 12, 31)
            )
        )

    def test_open_ended_existing_coverage_overlaps_any_future_start(self):
        self.assertTrue(
            coverage_dates_overlap(date(2020, 1, 1), None, date(2030, 1, 1), None)
        )

    def test_open_ended_new_coverage_overlaps_existing(self):
        self.assertTrue(
            coverage_dates_overlap(date(2026, 1, 1), date(2026, 6, 30), date(2026, 3, 1), None)
        )

    def test_touching_boundaries_count_as_overlap(self):
        self.assertTrue(
            coverage_dates_overlap(
                date(2026, 1, 1), date(2026, 6, 30), date(2026, 6, 30), date(2026, 12, 31)
            )
        )


class IsSameCoverageTests(unittest.TestCase):
    def test_same_carrier_subscriber_and_type_matches(self):
        self.assertTrue(
            is_same_coverage("id:AETNA1", "id:AETNA1", "ABC-123", "abc123", "P", "P")
        )

    def test_different_carrier_does_not_match(self):
        self.assertFalse(
            is_same_coverage("id:AETNA1", "id:BCBS1", "ABC-123", "ABC-123", "P", "P")
        )

    def test_different_subscriber_does_not_match(self):
        self.assertFalse(
            is_same_coverage("id:AETNA1", "id:AETNA1", "ABC-123", "XYZ-999", "P", "P")
        )

    def test_different_type_does_not_match(self):
        self.assertFalse(
            is_same_coverage("id:AETNA1", "id:AETNA1", "ABC-123", "ABC-123", "P", "S")
        )


class DecideCoverageActionTests(unittest.TestCase):
    """A patient never has two simultaneously active coverages of the same
    type - any carrier/subscriber change always terminates the old row and
    inserts the new one, regardless of whether their date ranges overlap
    (overlap is not a decide_coverage_action input at all)."""

    def test_same_coverage_updates_in_place(self):
        action = decide_coverage_action(
            existing_carrier_key="id:AETNA1",
            incoming_carrier_key="id:AETNA1",
            existing_subscriber_id="ABC-123",
            incoming_subscriber_id="abc123",
            existing_type="P",
            incoming_type="P",
        )
        self.assertEqual(action, ACTION_UPDATE)

    def test_different_carrier_always_terminates_and_inserts(self):
        # Even with non-overlapping dates - a carrier change on the same
        # type is never "just insert alongside the old one".
        action = decide_coverage_action(
            existing_carrier_key="id:AETNA1",
            incoming_carrier_key="id:BCBS1",
            existing_subscriber_id="ABC-123",
            incoming_subscriber_id="XYZ-999",
            existing_type="P",
            incoming_type="P",
        )
        self.assertEqual(action, ACTION_TERMINATE_AND_INSERT)

    def test_same_carrier_and_type_but_different_subscriber_terminates_and_inserts(self):
        # A subscriber id change (e.g. re-enrolled under a new member id)
        # under the same carrier/type is not "the same coverage".
        action = decide_coverage_action(
            existing_carrier_key="id:AETNA1",
            incoming_carrier_key="id:AETNA1",
            existing_subscriber_id="ABC-123",
            incoming_subscriber_id="NEW-999",
            existing_type="P",
            incoming_type="P",
        )
        self.assertEqual(action, ACTION_TERMINATE_AND_INSERT)

    def test_same_coverage_but_inactive_reactivates_instead_of_updating(self):
        # The exact same coverage (carrier + subscriber + type) came back
        # after being terminated - e.g. the patient re-enrolled under the
        # same plan. It should be revived, not silently "updated" as if it
        # had been active the whole time.
        action = decide_coverage_action(
            existing_carrier_key="id:AETNA1",
            incoming_carrier_key="id:AETNA1",
            existing_subscriber_id="ABC-123",
            incoming_subscriber_id="abc123",
            existing_type="P",
            incoming_type="P",
            existing_active=False,
        )
        self.assertEqual(action, ACTION_REACTIVATE)

    def test_existing_active_defaults_to_true(self):
        # Callers that don't pass existing_active at all keep today's
        # behavior unchanged.
        action = decide_coverage_action(
            existing_carrier_key="id:AETNA1",
            incoming_carrier_key="id:AETNA1",
            existing_subscriber_id="ABC-123",
            incoming_subscriber_id="abc123",
            existing_type="P",
            incoming_type="P",
        )
        self.assertEqual(action, ACTION_UPDATE)

    def test_different_carrier_with_inactive_existing_still_terminates_and_inserts(self):
        # A non-match against an inactive row is not a reactivation - it's
        # simply a different coverage, same as against an active row.
        action = decide_coverage_action(
            existing_carrier_key="id:AETNA1",
            incoming_carrier_key="id:BCBS1",
            existing_subscriber_id="ABC-123",
            incoming_subscriber_id="XYZ-999",
            existing_type="P",
            incoming_type="P",
            existing_active=False,
        )
        self.assertEqual(action, ACTION_TERMINATE_AND_INSERT)


if __name__ == "__main__":
    unittest.main()
