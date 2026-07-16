"""
Backfill script: Populate new extracted columns from detailed_score JSON.
Idempotent — only fills rows where columns are still NULL.

Usage:
    cd MyOpsPortal-Backend
    python -m scripts.backfill_extracted_columns
"""

import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal
from app.models.MemberCareAudioEvaluations import MembercareAgentAssessmentRecordings


def parse_json_safe(text):
    """Parse JSON from text, returning empty dict on failure."""
    if not text:
        return {}
    try:
        parsed = json.loads(text) if isinstance(text, str) else text
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def backfill():
    db = SessionLocal()
    try:
        # Only rows that have detailed_score but haven't been backfilled yet
        rows = (
            db.query(MembercareAgentAssessmentRecordings)
            .filter(MembercareAgentAssessmentRecordings.detailed_score.isnot(None))
            .filter(
                # At least one new column is still NULL
                MembercareAgentAssessmentRecordings.confidence_score.is_(None)
                | MembercareAgentAssessmentRecordings.agent_name.is_(None)
                | MembercareAgentAssessmentRecordings.sale_or_not_sale.is_(None)
            )
            .all()
        )

        print(f"Found {len(rows)} rows to backfill")
        updated = 0

        for row in rows:
            json_data = parse_json_safe(row.detailed_score)
            if not json_data:
                continue

            detailscore = json_data.get("detailscore", {})
            if isinstance(detailscore, str):
                detailscore = parse_json_safe(detailscore)

            changed = False

            # confidence_score (top-level)
            if row.confidence_score is None and "confidence_score" in json_data:
                try:
                    row.confidence_score = float(json_data["confidence_score"])
                    changed = True
                except (ValueError, TypeError):
                    pass

            # max_total_score (from detailscore)
            if row.max_total_score is None and "max_total_score" in detailscore:
                try:
                    row.max_total_score = int(detailscore["max_total_score"])
                    changed = True
                except (ValueError, TypeError):
                    pass

            # agent_name (from detailscore)
            if row.agent_name is None and detailscore.get("agent_name"):
                row.agent_name = str(detailscore["agent_name"])[:200]
                changed = True

            # caller_name (from detailscore)
            if row.caller_name is None and detailscore.get("caller_name"):
                row.caller_name = str(detailscore["caller_name"])[:200]
                changed = True

            # sale_or_not_sale (top-level)
            if row.sale_or_not_sale is None and "sale or not sale" in json_data:
                raw = json_data["sale or not sale"]
                if raw:
                    row.sale_or_not_sale = (
                        "Sale" if str(raw).lower() == "sale"
                        else "Not sale" if str(raw).lower() in ("not sale", "no sale")
                        else str(raw)[:20]
                    )
                    changed = True

            # edited_sale_or_not_sale (top-level)
            if row.edited_sale_or_not_sale is None and "edited_sale_or_not_sale" in json_data:
                val = json_data["edited_sale_or_not_sale"]
                if val:
                    row.edited_sale_or_not_sale = str(val)[:20]
                    changed = True

            # edited_sale_reason (top-level)
            if row.edited_sale_reason is None and "edited_sale_reason" in json_data:
                val = json_data["edited_sale_reason"]
                if val:
                    row.edited_sale_reason = str(val)
                    changed = True

            if changed:
                updated += 1

        db.commit()
        print(f"Backfill complete: {updated}/{len(rows)} rows updated")

    except Exception as e:
        db.rollback()
        print(f"Backfill failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
