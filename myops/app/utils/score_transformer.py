"""
Utility module for transforming and merging detailed score data between API format and DB format.

API Format: Flat array of score items with data_id (e.g., "1", "1>1a")
DB Format: Hierarchical structure with criteria and subcriteria
"""

import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone


def transform_and_merge_detailed_score(
    db_detailed_score: Dict[str, Any], api_detailed_score_data: List[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[int], Optional[str]]:
    """
    Transform API format detailed score and merge editedScore and remarks into DB format.

    Args:
        db_detailed_score: Current detailed score in DB format (hierarchical)
        api_detailed_score_data: New detailed score in API format (flat array with data_id)

    Returns:
        Tuple containing:
        - Updated detailed score in DB format with merged editedScore and remarks
        - Total edited score (sum of all editedScore values) or None if no edits
        - Edited timestamp (ISO format) or None if no edits

    Raises:
        ValueError: If some metrics have editedScore but not all
    """

    print("Merging detailed score data from API format into DB format...")
    print(f"DB Detailed Score: {json.dumps(db_detailed_score, indent=2)}")
    print(f"API Detailed Score Data: {json.dumps(api_detailed_score_data, indent=2)}")

    # Create a mapping from data_id to API item for quick lookup
    api_items_by_id = {item.get("data_id"): item for item in api_detailed_score_data}

    print(f"API Items by ID: {json.dumps(api_items_by_id, indent=2)}")

    # Create a copy to avoid mutating the original
    updated_score = json.loads(json.dumps(db_detailed_score))
    updated_detailed_score = updated_score.get("detailscore", {})

    # Track metrics with editedScore for validation
    metrics_with_edited_score = []
    metrics_without_edited_score = []
    total_edited_score = 0
    has_any_edited_score = False

    # Helper: detect if a criterion is compliance (has "compliance" field) vs QA
    def is_compliance_criterion(crit: dict) -> bool:
        return "compliance" in crit

    # Process all numeric criterion keys dynamically (no hardcoded upper limit)
    for criterion_key in list(updated_detailed_score.keys()):
        # Skip non-criterion keys (metadata fields like "agent_name", "max_total_score", etc.)
        if not criterion_key.isdigit():
            continue

        criterion = updated_detailed_score[criterion_key]
        if not isinstance(criterion, dict):
            continue

        criterion_id = criterion_key
        is_compliance = is_compliance_criterion(criterion)

        # Check if this criterion has editedScore or remarks in the API data
        if criterion_id in api_items_by_id:
            api_item = api_items_by_id[criterion_id]

            # Add editedScore if present
            if "editedScore" in api_item and api_item["editedScore"] is not None:
                criterion["editedScore"] = api_item["editedScore"]
                has_any_edited_score = True
                metrics_with_edited_score.append(criterion_id)
                # Add to total only for QA criteria (not compliance Pass/Fail)
                if not is_compliance:
                    total_edited_score += api_item["editedScore"]
            else:
                # Only track QA criteria for validation
                if not is_compliance:
                    metrics_without_edited_score.append(criterion_id)

            # Add remarks if present
            if "remarks" in api_item and api_item["remarks"]:
                criterion["remarks"] = api_item["remarks"]

        # Process subcriteria if they exist
        if "subcriteria" in criterion and isinstance(criterion["subcriteria"], dict):
            for subcriterion_key, subcriterion in criterion["subcriteria"].items():
                # Build full data_id for subcriteria (e.g., "1>1a")
                sub_data_id = f"{criterion_id}>{subcriterion_key}"

                # Check if this subcriteria has editedScore or remarks in the API data
                if sub_data_id in api_items_by_id:
                    api_item = api_items_by_id[sub_data_id]

                    # Add editedScore if present
                    if (
                        "editedScore" in api_item
                        and api_item["editedScore"] is not None
                    ):
                        subcriterion["editedScore"] = api_item["editedScore"]
                        has_any_edited_score = True
                        metrics_with_edited_score.append(sub_data_id)
                        # Add to total
                        total_edited_score += api_item["editedScore"]
                    else:
                        metrics_without_edited_score.append(sub_data_id)

                    # Add remarks if present
                    if "remarks" in api_item and api_item["remarks"]:
                        subcriterion["remarks"] = api_item["remarks"]

    # Validation: If any metric has editedScore, all must have editedScore
    if has_any_edited_score:
        # if metrics_without_edited_score:
        #     raise ValueError(
        #         f"Incomplete edited scores: If one metric has editedScore, all metrics must have editedScore. "
        #         f"Metrics with editedScore: {metrics_with_edited_score}. "
        #         f"Metrics without editedScore: {metrics_without_edited_score}"
        #     )

        # Generate timestamp for when the edit was made
        edited_at = datetime.now(timezone.utc).isoformat()

        updated_score["detailscore"] = updated_detailed_score
        return updated_score, total_edited_score, edited_at
    else:
        updated_score["detailscore"] = updated_detailed_score
        return updated_score, None, None


def validate_detailed_score_structure(detailed_score: Dict[str, Any]) -> bool:
    """
    Validate that the detailed score has the expected DB format structure.

    Args:
        detailed_score: Detailed score object to validate

    Returns:
        True if valid, False otherwise
    """

    # Check all numeric criterion keys dynamically
    for key, criterion in detailed_score.items():
        if not key.isdigit():
            continue

        if not isinstance(criterion, dict):
            return False
        if "criterion" not in criterion:
            return False

        # Compliance criteria have a "compliance" field; QA criteria need max_score and awarded_score
        is_compliance = "compliance" in criterion
        if not is_compliance:
            if "max_score" not in criterion or "awarded_score" not in criterion:
                return False

    return True
