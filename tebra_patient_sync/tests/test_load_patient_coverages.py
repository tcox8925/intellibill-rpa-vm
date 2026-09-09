"""Tests for tebra/load_patient_coverages.py's process_coverage - the
per-coverage insert / update / terminate-and-insert / reactivate decision,
and the plain-dict payloads it builds for run_load_patient_coverages' bulk
executemany writes (see that module's docstring for why bulk instead of
one INSERT/UPDATE per row).

No DB access here - active_coverage_map/inactive_candidates are built by
hand as plain dicts, exactly the shape build_active_coverage_map/
build_inactive_coverage_map return from a real query. That's enough to
exercise every branch of process_coverage's decision logic without a live
Postgres connection.
"""

import unittest
from dataclasses import dataclass
from typing import Optional

from tebra.load_patient_coverages import process_coverage


@dataclass
class FakeHeader:
    """Stands in for a PatientHeader row - process_coverage only ever reads
    these attributes off it."""

    patient_header_id: str
    source_id: str
    client_id: Optional[int] = 1
    group_id: Optional[int] = 1
    practice_id: Optional[int] = None
    pat_id: str = ""
    source: str = "tebra"
    sub_lnam: str = "Doe"
    pat_fnam: str = "Jane"
    pat_dob: str = "1990-01-01"


def make_patient(patient_id: str, company_name: str, policy_number: str) -> dict:
    """A minimal Patients.json PatientData entry with just one Primary
    insurance policy filled in."""
    return {
        "ID": patient_id,
        "FirstName": "Jane",
        "LastName": "Doe",
        "PrimaryInsurancePolicyCompanyID": "1",
        "PrimaryInsurancePolicyCompanyName": company_name,
        "PrimaryInsurancePolicyNumber": policy_number,
        "PrimaryInsurancePolicyPatientRelationshipToInsured": "S",
    }


class NoExistingActiveRowTests(unittest.TestCase):
    """active_coverage_map has nothing for this patient/type yet - the
    "existing is None" branch of process_coverage."""

    def setUp(self):
        self.header = FakeHeader("11111111-1111-1111-1111-111111111111", "P1")
        self.active_coverage_map = {}
        self.inactive_candidates = {}
        self.insert_payload = []
        self.update_payload = []

    def _run(self, company_name, policy_number):
        return process_coverage(
            make_patient("P1", company_name, policy_number), self.header, "P", "Primary",
            [], self.active_coverage_map, self.inactive_candidates, self.insert_payload, self.update_payload,
        )

    def test_no_insurance_fields_does_nothing(self):
        # A patient with neither CompanyID nor CompanyName for this type -
        # e.g. no Secondary insurance at all - is skipped entirely, no
        # payload of any kind.
        msg = process_coverage(
            {"ID": "P1"}, self.header, "P", "Primary", [], self.active_coverage_map,
            self.inactive_candidates, self.insert_payload, self.update_payload,
        )
        self.assertIsNone(msg)
        self.assertEqual(self.insert_payload, [])
        self.assertEqual(self.update_payload, [])

    def test_plain_insert_when_nothing_exists_at_all(self):
        # No active row, no matching history -> a genuinely new coverage,
        # inserted fresh.
        msg = self._run("Aetna", "POL1")
        self.assertIn("Inserted", msg)
        self.assertEqual(len(self.insert_payload), 1)
        self.assertEqual(self.insert_payload[0]["cov_car_nam"], "Aetna")
        self.assertTrue(self.insert_payload[0]["active"])
        self.assertEqual(self.update_payload, [])
        # active_coverage_map now tracks it, but with id=None - it's only a
        # pending row until the batch's bulk INSERT actually runs.
        key = (self.header.patient_header_id, "P")
        self.assertIsNone(self.active_coverage_map[key]["id"])

    def test_reactivates_a_matching_inactive_row_instead_of_inserting(self):
        # This exact coverage (same carrier + subscriber) already exists,
        # just inactive (terminated on an earlier run) - bring it back
        # instead of creating a duplicate-looking row.
        self.inactive_candidates[(self.header.patient_header_id, "P")] = [{
            "id": 555, "cov_car_id": None, "cov_car_nam": "Aetna", "cov_sub_id": "POL1",
            "effective_start_date": "2024-01-01", "effective_end_date": "2024-12-31",
        }]
        msg = self._run("Aetna", "POL1")
        self.assertIn("Reactivated", msg)
        self.assertEqual(self.insert_payload, [])  # no new row at all
        self.assertEqual(len(self.update_payload), 1)
        self.assertEqual(self.update_payload[0]["id"], 555)
        self.assertTrue(self.update_payload[0]["active"])
        self.assertIsNone(self.update_payload[0]["effective_end_date"])  # open-ended again
        # the candidate is consumed - it can't be reactivated a second time
        # if this patient/type shows up again later in the same run.
        self.assertEqual(self.inactive_candidates[(self.header.patient_header_id, "P")], [])

    def test_non_matching_inactive_row_falls_back_to_a_plain_insert(self):
        # An inactive row exists for this patient/type, but it's a
        # different carrier/policy - not a reactivation candidate, so this
        # is still a normal fresh insert.
        self.inactive_candidates[(self.header.patient_header_id, "P")] = [{
            "id": 555, "cov_car_id": None, "cov_car_nam": "Cigna", "cov_sub_id": "OTHERPOL",
            "effective_start_date": "2024-01-01", "effective_end_date": "2024-12-31",
        }]
        msg = self._run("Aetna", "POL1")
        self.assertIn("Inserted", msg)
        self.assertEqual(len(self.insert_payload), 1)
        self.assertEqual(self.update_payload, [])


class ExistingActiveRowTests(unittest.TestCase):
    """active_coverage_map already has an active row (id=100) for this
    patient/type - simulates a coverage row that's already in the DB from a
    prior run."""

    def setUp(self):
        self.header = FakeHeader("22222222-2222-2222-2222-222222222222", "P2")
        self.active_coverage_map = {
            (self.header.patient_header_id, "P"): {
                "id": 100, "patient_header_id": self.header.patient_header_id, "cov_type": "P",
                "cov_car_id": None, "cov_car_nam": "Aetna", "cov_sub_id": "POL1",
                "effective_start_date": "2025-01-01", "effective_end_date": None,
            }
        }
        self.inactive_candidates = {}
        self.insert_payload = []
        self.update_payload = []

    def _run(self, company_name, policy_number):
        return process_coverage(
            make_patient("P2", company_name, policy_number), self.header, "P", "Primary",
            [], self.active_coverage_map, self.inactive_candidates, self.insert_payload, self.update_payload,
        )

    def test_same_carrier_and_policy_updates_the_row_in_place(self):
        # Re-syncing the same coverage - just refresh the existing row's
        # fields, don't touch anything else.
        msg = self._run("Aetna", "POL1")
        self.assertIn("Updated", msg)
        self.assertEqual(len(self.update_payload), 1)
        self.assertEqual(self.update_payload[0]["id"], 100)
        self.assertEqual(self.insert_payload, [])

    def test_carrier_change_with_no_matching_history_terminates_and_inserts_fresh(self):
        # Carrier actually changed (Aetna -> Cigna) and there's no old
        # Cigna row in this patient's history to reactivate - close out
        # the Aetna row, insert a brand-new active Cigna row.
        msg = self._run("Cigna", "POL2")
        self.assertIn("terminated", msg)
        self.assertEqual(len(self.update_payload), 1)  # terminates the old Aetna row
        self.assertEqual(self.update_payload[0]["id"], 100)
        self.assertFalse(self.update_payload[0]["active"])
        self.assertEqual(len(self.insert_payload), 1)  # brand-new Cigna row
        self.assertEqual(self.insert_payload[0]["cov_car_nam"], "Cigna")

    def test_carrier_change_with_matching_history_reactivates_instead_of_inserting(self):
        # The exact scenario this fix targets: Aetna -> Cigna -> Aetna
        # again. There's an old, inactive Aetna row (id=200) sitting in
        # history that matches exactly what's coming in now.
        self.inactive_candidates[(self.header.patient_header_id, "P")] = [{
            "id": 200, "cov_car_id": None, "cov_car_nam": "Cigna", "cov_sub_id": "POL2",
            "effective_start_date": "2023-01-01", "effective_end_date": "2023-12-31",
        }]
        msg = self._run("Cigna", "POL2")
        self.assertIn("Reactivated", msg)
        self.assertEqual(self.insert_payload, [])  # no 3rd row created
        self.assertEqual(len(self.update_payload), 2)  # terminate old + reactivate history
        ids = {row["id"] for row in self.update_payload}
        self.assertEqual(ids, {100, 200})
        terminate_row = next(r for r in self.update_payload if r["id"] == 100)
        reactivate_row = next(r for r in self.update_payload if r["id"] == 200)
        self.assertFalse(terminate_row["active"])
        self.assertTrue(reactivate_row["active"])


class SameBatchDuplicatePatientTests(unittest.TestCase):
    """The source file lists the same patient/coverage type twice in one
    run. active_coverage_map's entry for the first occurrence is still
    "pending" (id=None, not yet flushed to the DB) when the second
    occurrence is processed."""

    def setUp(self):
        self.header = FakeHeader("33333333-3333-3333-3333-333333333333", "P3")
        self.active_coverage_map = {}
        self.inactive_candidates = {}
        self.insert_payload = []
        self.update_payload = []

    def _run(self, company_name, policy_number):
        return process_coverage(
            make_patient("P3", company_name, policy_number), self.header, "P", "Primary",
            [], self.active_coverage_map, self.inactive_candidates, self.insert_payload, self.update_payload,
        )

    def test_duplicate_with_unchanged_carrier_mutates_the_pending_row_in_place(self):
        self._run("Aetna", "POL1")
        self._run("Aetna", "POL1")
        # Still just ONE queued insert - the second call updated it in
        # place rather than appending a second row or a bulk UPDATE against
        # a row that doesn't exist in the DB yet.
        self.assertEqual(len(self.insert_payload), 1)
        self.assertEqual(self.update_payload, [])

    def test_duplicate_with_carrier_change_closes_the_first_pending_row_and_adds_a_second(self):
        self._run("Aetna", "POL1")
        self._run("Cigna", "POL2")
        # Both are still inserts (neither ever reached the DB this run),
        # but the first is now flagged inactive in its own dict - so the
        # batch's bulk INSERT writes one already-closed row plus one
        # active row, instead of a bulk UPDATE against a nonexistent id.
        self.assertEqual(len(self.insert_payload), 2)
        self.assertEqual(self.update_payload, [])
        self.assertFalse(self.insert_payload[0]["active"])
        self.assertTrue(self.insert_payload[1]["active"])


if __name__ == "__main__":
    unittest.main()
