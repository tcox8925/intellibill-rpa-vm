"""Tests for utils/payer_lookup.py - lookup_payers matching rules,
including the "no claim type -> default to professional" rule."""

import unittest

from utils.payer_lookup import (
    Payer,
    allowed_transaction_types,
    carrier_key,
    find_payer,
    normalize_lookup_value,
)

# (payer_id, payer_name, payer_type, transaction_type, payer_alias)
AETNA_PROFESSIONAL = ("AETNA1", "Aetna", ["commercial"], ["837P"], ["Aetna Health"])
AETNA_INSTITUTIONAL = ("AETNA2", "Aetna", ["commercial"], ["837I"], [])
BCBS_BOTH = ("BCBS1", "Blue Cross Blue Shield", ["commercial"], ["837P", "837I"], ["BCBS"])
ACTIVE_PAYERS = [AETNA_PROFESSIONAL, AETNA_INSTITUTIONAL, BCBS_BOTH]


PROFESSIONAL_NORMALIZED = {"837p", "professionalclaims837p", "prof", "professionalclaims"}
INSTITUTIONAL_NORMALIZED = {"837i", "institutionalclaims837i", "inst", "institutionalclaims"}


class AllowedTransactionTypesTests(unittest.TestCase):
    def test_professional_claim_type_variants(self):
        for claim_type in ["P", "p", "PROFESSIONAL", "prof"]:
            self.assertEqual(allowed_transaction_types(claim_type), PROFESSIONAL_NORMALIZED)

    def test_institutional_claim_type_variants(self):
        for claim_type in ["I", "i", "INSTITUTIONAL", "inst"]:
            self.assertEqual(allowed_transaction_types(claim_type), INSTITUTIONAL_NORMALIZED)

    def test_blank_claim_type_defaults_to_professional(self):
        # This is the "if no claim type found make it default P" rule.
        self.assertEqual(allowed_transaction_types(""), PROFESSIONAL_NORMALIZED)
        self.assertEqual(allowed_transaction_types(None), PROFESSIONAL_NORMALIZED)
        self.assertEqual(allowed_transaction_types("   "), PROFESSIONAL_NORMALIZED)

    def test_unrecognized_claim_type_matches_everything(self):
        self.assertIsNone(allowed_transaction_types("XYZ"))


class FindPayerTests(unittest.TestCase):
    def test_no_match_returns_none(self):
        self.assertIsNone(find_payer("Nonexistent Payer", "P", ACTIVE_PAYERS))

    def test_blank_payer_name_returns_none(self):
        self.assertIsNone(find_payer("", "P", ACTIVE_PAYERS))

    def test_matches_by_name_case_and_whitespace_insensitive(self):
        payer = find_payer("  aetna  ", "P", ACTIVE_PAYERS)
        self.assertIsNotNone(payer)
        self.assertEqual(payer.payer_id, "AETNA1")

    def test_matches_by_alias(self):
        payer = find_payer("BCBS", "P", ACTIVE_PAYERS)
        self.assertIsNotNone(payer)
        self.assertEqual(payer.payer_id, "BCBS1")

    def test_narrows_by_professional_claim_type(self):
        payer = find_payer("Aetna", "P", ACTIVE_PAYERS)
        self.assertEqual(payer.payer_id, "AETNA1")

    def test_narrows_by_institutional_claim_type(self):
        payer = find_payer("Aetna", "I", ACTIVE_PAYERS)
        self.assertEqual(payer.payer_id, "AETNA2")

    def test_blank_claim_type_resolves_to_professional_row(self):
        # With no claim type supplied, "Aetna" has two active rows (P and
        # I); defaulting to professional must resolve the P row.
        payer = find_payer("Aetna", "", ACTIVE_PAYERS)
        self.assertIsNotNone(payer)
        self.assertEqual(payer.payer_id, "AETNA1")

    def test_unrecognized_claim_type_matches_first_row_regardless_of_type(self):
        payer = find_payer("Aetna", "SOMETHING_ELSE", ACTIVE_PAYERS)
        self.assertIsNotNone(payer)
        self.assertEqual(payer.payer_id, "AETNA1")


class CarrierKeyTests(unittest.TestCase):
    def test_resolved_payer_uses_payer_id(self):
        payer = Payer(payer_id="AETNA1", payer_name="Aetna", payer_type=["commercial"])
        self.assertEqual(carrier_key(payer, "Aetna"), "id:AETNA1")

    def test_no_payer_falls_back_to_stored_id(self):
        self.assertEqual(carrier_key(None, "Some Carrier", stored_id="LEGACY1"), "id:LEGACY1")

    def test_no_payer_and_no_stored_id_falls_back_to_normalized_name(self):
        self.assertEqual(
            carrier_key(None, "  Some   Carrier  "),
            f"name:{normalize_lookup_value('Some Carrier')}",
        )


if __name__ == "__main__":
    unittest.main()
