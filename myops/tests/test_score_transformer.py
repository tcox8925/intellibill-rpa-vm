"""
Unit tests for score_transformer module.
Tests the transformation and merging of detailed score data between API and DB formats.
"""

import pytest
import json
from app.utils.score_transformer import (
    transform_and_merge_detailed_score,
    validate_detailed_score_structure,
)


class TestTransformAndMergeDetailedScore:
    """Test transformation and merging of scores between formats"""

    def test_merge_edited_score_to_criterion(self):
        """Should merge editedScore into criterion level"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {},
                }
            }
        }

        api_data = [
            {
                "data_id": "1",
                "label": "Professional Introduction",
                "awarded_score": 2,
                "max_score": 4,
                "editedScore": 3,
                "remarks": "Updated",
            }
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )

        assert result["detailscore"]["1"]["editedScore"] == 3
        assert result["detailscore"]["1"]["remarks"] == "Updated"
        assert result["detailscore"]["1"]["criterion"] == "Professional Introduction"
        assert total_edited_score == 3
        assert edited_at is not None

    def test_merge_edited_score_to_subcriterion(self):
        """Should merge editedScore into subcriteria level"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {
                        "1a": {
                            "name": "Brands the call",
                            "max_points": 2,
                            "awarded_points": 0,
                        }
                    },
                }
            }
        }

        api_data = [
            {
                "data_id": "1>1a",
                "label": "Brands the call",
                "awarded_score": 0,
                "max_score": 2,
                "editedScore": 1,
                "remarks": "Brand mentioned",
            }
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )

        assert result["detailscore"]["1"]["subcriteria"]["1a"]["editedScore"] == 1
        assert (
            result["detailscore"]["1"]["subcriteria"]["1a"]["remarks"]
            == "Brand mentioned"
        )
        assert (
            result["detailscore"]["1"]["subcriteria"]["1a"]["name"] == "Brands the call"
        )
        assert total_edited_score == 1
        assert edited_at is not None

    def test_merge_multiple_edits(self):
        """Should merge multiple edits from different criteria and subcriteria"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {
                        "1a": {
                            "name": "Brands the call",
                            "max_points": 2,
                            "awarded_points": 0,
                        },
                        "1b": {
                            "name": "Secures the line",
                            "max_points": 2,
                            "awarded_points": 2,
                        },
                    },
                },
                "2": {
                    "criterion": "Current Situation",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {},
                },
            }
        }

        api_data = [
            {
                "data_id": "1",
                "label": "Professional Introduction",
                "editedScore": 3,
                "remarks": "Better overall",
            },
            {
                "data_id": "1>1a",
                "label": "Brands the call",
                "editedScore": 1,
                "remarks": "Needs improvement",
            },
            {
                "data_id": "1>1b",
                "label": "Secures the line",
                "editedScore": 2,
                "remarks": "Good",
            },
            {
                "data_id": "2",
                "label": "Current Situation",
                "editedScore": 2,
                "remarks": "Minor issues",
            },
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )

        # Criterion 1 edits
        assert result["detailscore"]["1"]["editedScore"] == 3
        assert result["detailscore"]["1"]["remarks"] == "Better overall"

        # Subcriteria 1a edits
        assert result["detailscore"]["1"]["subcriteria"]["1a"]["editedScore"] == 1
        assert (
            result["detailscore"]["1"]["subcriteria"]["1a"]["remarks"]
            == "Needs improvement"
        )

        # Subcriteria 1b edits
        assert result["detailscore"]["1"]["subcriteria"]["1b"]["editedScore"] == 2
        assert result["detailscore"]["1"]["subcriteria"]["1b"]["remarks"] == "Good"

        # Criterion 2 edits
        assert result["detailscore"]["2"]["editedScore"] == 2
        assert result["detailscore"]["2"]["remarks"] == "Minor issues"

        # Total edited score should be 3 + 1 + 2 + 2 = 8
        assert total_edited_score == 8
        assert edited_at is not None

    def test_preserve_existing_data(self):
        """Should preserve existing data not being edited"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "existing_field": "original_value",
                    "subcriteria": {
                        "1a": {
                            "name": "Brands the call",
                            "max_points": 2,
                            "awarded_points": 0,
                            "evidence": "Some evidence",
                        }
                    },
                }
            }
        }

        api_data = [
            {
                "data_id": "1",
                "label": "Professional Introduction",
                "editedScore": 3,
                "remarks": "Updated",
            },
            {
                "data_id": "1>1a",
                "label": "Brands the call",
                "editedScore": 1,
            },
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )

        # Existing fields should be preserved
        assert result["detailscore"]["1"]["existing_field"] == "original_value"
        assert (
            result["detailscore"]["1"]["subcriteria"]["1a"]["evidence"]
            == "Some evidence"
        )
        assert (
            result["detailscore"]["1"]["subcriteria"]["1a"]["name"] == "Brands the call"
        )

        # New fields should be added
        assert result["detailscore"]["1"]["editedScore"] == 3
        assert result["detailscore"]["1"]["remarks"] == "Updated"

        # Total edited score: 3 (criterion 1) + 1 (subcriterion 1a) = 4
        assert total_edited_score == 4
        assert edited_at is not None

    def test_partial_update_allows_partial_edits(self):
        """Should allow partial edits (only some subcriteria edited)"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {
                        "1a": {
                            "name": "Brands the call",
                            "max_points": 2,
                            "awarded_points": 0,
                        },
                        "1b": {
                            "name": "Secures the line",
                            "max_points": 2,
                            "awarded_points": 2,
                        },
                    },
                }
            }
        }

        # Only edit 1a, not 1b - should succeed (partial edits allowed)
        api_data = [
            {
                "data_id": "1>1a",
                "label": "Brands the call",
                "editedScore": 1,
                "remarks": "Brand mentioned",
            }
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )
        assert result["detailscore"]["1"]["subcriteria"]["1a"]["editedScore"] == 1
        assert total_edited_score == 1
        assert edited_at is not None

    def test_skip_non_digit_keys(self):
        """Should skip non-digit keys like metadata fields"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {},
                },
                "agent_name": "John Doe",
                "caller_name": "Jane Doe",
                "max_total_score": 100,
            }
        }

        api_data = [
            {
                "data_id": "1",
                "label": "Professional Introduction",
                "editedScore": 3,
                "remarks": "Good",
            }
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )

        # Metadata should be preserved
        assert result["detailscore"]["agent_name"] == "John Doe"
        assert result["detailscore"]["caller_name"] == "Jane Doe"
        assert result["detailscore"]["max_total_score"] == 100

        # Criterion should be updated
        assert result["detailscore"]["1"]["editedScore"] == 3
        assert total_edited_score == 3
        assert edited_at is not None

    def test_idempotent_updates(self):
        """Multiple updates with the same data should produce the same result"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {},
                }
            }
        }

        api_data = [
            {
                "data_id": "1",
                "label": "Professional Introduction",
                "editedScore": 3,
                "remarks": "Updated",
            }
        ]

        # First update
        result1, total1, edited_at1 = transform_and_merge_detailed_score(
            db_score, api_data
        )

        # Second update with the same data
        result2, total2, edited_at2 = transform_and_merge_detailed_score(
            result1, api_data
        )

        # Results should be identical (except timestamps will differ)
        assert (
            result1["detailscore"]["1"]["editedScore"]
            == result2["detailscore"]["1"]["editedScore"]
        )
        assert (
            result1["detailscore"]["1"]["remarks"]
            == result2["detailscore"]["1"]["remarks"]
        )
        assert result2["detailscore"]["1"]["editedScore"] == 3
        assert result2["detailscore"]["1"]["remarks"] == "Updated"
        assert total1 == total2 == 3
        assert edited_at1 is not None
        assert edited_at2 is not None

    def test_merge_edited_score_for_criteria_beyond_20(self):
        """Should merge editedScore for criteria numbered beyond 20"""
        db_score = {
            "detailscore": {
                "21": {
                    "criterion": "Extra QA Category",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {},
                },
                "22": {
                    "criterion": "Another QA Category",
                    "max_score": 4,
                    "awarded_score": 3,
                    "subcriteria": {
                        "22a": {
                            "name": "Sub item",
                            "max_points": 2,
                            "awarded_points": 1,
                        }
                    },
                },
                "23": {
                    "criterion": "Compliance Category",
                    "compliance": "Pass",
                },
            }
        }

        api_data = [
            {
                "data_id": "21",
                "label": "Extra QA Category",
                "editedScore": 3,
                "remarks": "Improved",
            },
            {
                "data_id": "22",
                "label": "Another QA Category",
                "editedScore": 4,
            },
            {
                "data_id": "22>22a",
                "label": "Sub item",
                "editedScore": 2,
            },
            {
                "data_id": "23",
                "label": "Compliance Category",
                "editedScore": "Fail",
            },
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )

        # QA criteria beyond 20 should have editedScore merged
        assert result["detailscore"]["21"]["editedScore"] == 3
        assert result["detailscore"]["21"]["remarks"] == "Improved"
        assert result["detailscore"]["22"]["editedScore"] == 4
        assert result["detailscore"]["22"]["subcriteria"]["22a"]["editedScore"] == 2

        # Compliance criterion should also have editedScore merged
        assert result["detailscore"]["23"]["editedScore"] == "Fail"

        # total_edited_score should include QA criteria (21, 22) and subcriteria (22a)
        # but NOT compliance criterion (23)
        assert total_edited_score == 3 + 4 + 2  # 9
        assert edited_at is not None

    def test_no_edits_returns_none(self):
        """Should return None for total_edited_score and edited_at when no edits are made"""
        db_score = {
            "detailscore": {
                "1": {
                    "criterion": "Professional Introduction",
                    "max_score": 4,
                    "awarded_score": 2,
                    "subcriteria": {},
                }
            }
        }

        # No editedScore in api_data
        api_data = [
            {
                "data_id": "1",
                "label": "Professional Introduction",
                "remarks": "Just a remark, no score edit",
            }
        ]

        result, total_edited_score, edited_at = transform_and_merge_detailed_score(
            db_score, api_data
        )

        assert result["detailscore"]["1"]["remarks"] == "Just a remark, no score edit"
        assert "editedScore" not in result["detailscore"]["1"]
        assert total_edited_score is None
        assert edited_at is None


class TestValidateDetailedScoreStructure:
    """Test validation of detailed score structure"""

    def test_valid_structure(self):
        """Should validate a correct DB format structure"""
        valid_score = {
            "1": {
                "criterion": "Professional Introduction",
                "max_score": 4,
                "awarded_score": 2,
                "subcriteria": {},
            },
            "2": {
                "criterion": "Current Situation",
                "max_score": 4,
                "awarded_score": 2,
                "subcriteria": {},
            },
        }

        assert validate_detailed_score_structure(valid_score) is True

    def test_invalid_structure_missing_criterion(self):
        """Should reject structure missing required fields"""
        invalid_score = {
            "1": {
                # Missing "criterion" field
                "max_score": 4,
                "awarded_score": 2,
                "subcriteria": {},
            }
        }

        assert validate_detailed_score_structure(invalid_score) is False

    def test_invalid_structure_missing_max_score(self):
        """Should reject structure missing max_score"""
        invalid_score = {
            "1": {
                "criterion": "Professional Introduction",
                # Missing "max_score"
                "awarded_score": 2,
                "subcriteria": {},
            }
        }

        assert validate_detailed_score_structure(invalid_score) is False

    def test_compliance_criteria_structure(self):
        """Should accept compliance criteria (18-20) with different structure"""
        valid_score = {
            "18": {
                "criterion": "Disclaimers & Attestations",
                "subcriteria": {},
                "compliance": "Fail",
                "evidence": "",
            },
            "19": {"criterion": "OB Call Disclaimer", "compliance": "Pass"},
        }

        assert validate_detailed_score_structure(valid_score) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
