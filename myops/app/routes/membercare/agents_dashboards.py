from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, cast, Float, extract
from typing import Optional
from datetime import datetime, timedelta
import json

from app.db.session import get_db
from app.models.membercare.MembercareSalesActivity import MembercareSalesActivity
from app.models.MemberCareAudioEvaluations import MembercareAgentAssessmentRecordings

router = APIRouter(tags=["MEMBERCARE AGENTS DASHBOARDS"])

S = MembercareSalesActivity
R = MembercareAgentAssessmentRecordings

NOT_AVAILABLE_COMPLAINT = {"value": None, "message": "Data source not yet available for complaint tracking"}
NOT_AVAILABLE_QA_COVERAGE = {"value": None, "message": "Data source not yet available for QA coverage tracking"}


@router.get("/membercare-agents/{agent_id}/performance-snapshot")
def get_agent_performance_snapshot(
    agent_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    edited_compliance_score: Optional[str] = Query(None),
    sale_or_not_sale: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    # Base query: sales activity left-joined to recordings
    query = db.query(
        func.count(S.id).label("total_calls"),
        func.count(
            case(
                (S.sale_or_not_sale.in_(["Sale", "Sale Complete"]), S.id),
            )
        ).label("sales_complete"),
        # Compliance: pass count / non-null count
        func.count(
            case(
                (R.edited_compliance_score == "Pass", R.id),
            )
        ).label("compliance_pass_count"),
        func.count(
            case(
                (R.edited_compliance_score.isnot(None), R.id),
            )
        ).label("compliance_total_count"),
        # QA scores
        func.avg(
            case(
                (
                    func.coalesce(R.total_edited_score, R.total_score).isnot(None),
                    cast(func.coalesce(R.total_edited_score, R.total_score), Float),
                ),
            )
        ).label("avg_qa"),
        func.avg(
            case(
                (R.total_score.isnot(None), cast(R.total_score, Float)),
            )
        ).label("avg_total_score"),
        func.avg(
            case(
                (R.total_edited_score.isnot(None), cast(R.total_edited_score, Float)),
            )
        ).label("avg_edited_score"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.agent_pk_id == agent_id
    )

    # Apply filters on recordings columns
    if date_from:
        query = query.filter(R.recorded_at >= date_from)
    if date_to:
        query = query.filter(R.recorded_at <= date_to)
    if campaign:
        query = query.filter(R.campaign == campaign)
    if entity_id:
        query = query.filter(R.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(R.sub_entity_id == sub_entity_id)
    if edited_compliance_score:
        query = query.filter(func.lower(R.edited_compliance_score) == edited_compliance_score.lower())
    if sale_or_not_sale:
        query = query.filter(func.lower(R.sale_or_not_sale) == sale_or_not_sale.lower())

    row = query.one()

    total_calls = row.total_calls or 0
    sales_complete = row.sales_complete or 0
    sale_percentage = round((sales_complete / total_calls * 100), 2) if total_calls > 0 else 0.0

    compliance_pass = row.compliance_pass_count or 0
    compliance_total = row.compliance_total_count or 0
    compliance_percentage = round((compliance_pass / compliance_total * 100), 2) if compliance_total > 0 else 0.0

    avg_qa = round(row.avg_qa, 2) if row.avg_qa is not None else 0.0

    avg_total = row.avg_total_score
    avg_edited = row.avg_edited_score
    if avg_total is not None and avg_edited is not None:
        qa_adjustment = round(avg_total - avg_edited, 2)
    else:
        qa_adjustment = 0.0

    effective_avg_qa = round(avg_edited, 2) if avg_edited is not None else 0.0

    return {
        "agent_id": agent_id,
        "total_calls": total_calls,
        "sales_complete": sales_complete,
        "sale_percentage": sale_percentage,
        "compliance_percentage": compliance_percentage,
        "avg_qa_percentage": avg_qa,
        "qa_adjustment": qa_adjustment,
        "effective_avg_qa_percentage": effective_avg_qa,
        "complaint_count": NOT_AVAILABLE_COMPLAINT,
        "complaint_percentage": NOT_AVAILABLE_COMPLAINT,
        "qa_coverage_percentage": NOT_AVAILABLE_QA_COVERAGE,
    }


@router.get("/membercare-agents/{agent_id}/agent-distribution")
def get_agent_distribution(
    agent_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    edited_compliance_score: Optional[str] = Query(None),
    sale_or_not_sale: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(
        # Campaign split
        func.count(case((R.campaign.ilike("%OEP%"), S.id))).label("oep_count"),
        func.count(case((R.campaign.ilike("%SEP%"), S.id))).label("sep_count"),
        func.count(case((R.campaign.isnot(None), S.id))).label("campaign_total"),
        # Sales split
        func.count(S.id).label("total_calls"),
        func.count(case((S.sale_or_not_sale.in_(["Sale", "Sale Complete"]), S.id))).label("sale_count"),
        # Compliance split
        func.count(case((R.edited_compliance_score == "Pass", R.id))).label("pass_count"),
        func.count(case((R.edited_compliance_score == "Fail", R.id))).label("fail_count"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.agent_pk_id == agent_id
    )

    if date_from:
        query = query.filter(R.recorded_at >= date_from)
    if date_to:
        query = query.filter(R.recorded_at <= date_to)
    if campaign:
        query = query.filter(R.campaign == campaign)
    if entity_id:
        query = query.filter(R.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(R.sub_entity_id == sub_entity_id)
    if edited_compliance_score:
        query = query.filter(func.lower(R.edited_compliance_score) == edited_compliance_score.lower())
    if sale_or_not_sale:
        query = query.filter(func.lower(R.sale_or_not_sale) == sale_or_not_sale.lower())


    row = query.one()

    oep_count = row.oep_count or 0
    sep_count = row.sep_count or 0
    campaign_total = row.campaign_total or 0

    total_calls = row.total_calls or 0
    sale_count = row.sale_count or 0
    no_sale_count = total_calls - sale_count

    pass_count = row.pass_count or 0
    fail_count = row.fail_count or 0
    compliance_total = pass_count + fail_count

    def pct(part, total):
        return round(part / total * 100, 2) if total > 0 else 0.0

    return {
        "agent_id": agent_id,
        "campaign_split": {
            "oep": {"count": oep_count, "percentage": pct(oep_count, campaign_total)},
            "sep": {"count": sep_count, "percentage": pct(sep_count, campaign_total)},
        },
        "sales_split": {
            "sale": {"count": sale_count, "percentage": pct(sale_count, total_calls)},
            "no_sale": {"count": no_sale_count, "percentage": pct(no_sale_count, total_calls)},
        },
        "compliance_split": {
            "pass": {"count": pass_count, "percentage": pct(pass_count, compliance_total)},
            "fail": {"count": fail_count, "percentage": pct(fail_count, compliance_total)},
        },
        "complaint_split": NOT_AVAILABLE_COMPLAINT,
    }


@router.get("/membercare-agents/{agent_id}/top-plans-sold")
def get_agent_top_plans_sold(
    agent_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    edited_compliance_score: Optional[str] = Query(None),
    sale_or_not_sale: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(
        S.policy_selected.label("plan_name"),
        func.count(S.id).label("sales"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.agent_pk_id == agent_id,
        S.policy_selected.isnot(None),
        S.policy_selected != "",
    )

    if date_from:
        query = query.filter(R.recorded_at >= date_from)
    if date_to:
        query = query.filter(R.recorded_at <= date_to)
    if campaign:
        query = query.filter(R.campaign == campaign)
    if entity_id:
        query = query.filter(R.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(R.sub_entity_id == sub_entity_id)
    if edited_compliance_score:
        query = query.filter(func.lower(R.edited_compliance_score) == edited_compliance_score.lower())
    if sale_or_not_sale:
        query = query.filter(func.lower(R.sale_or_not_sale) == sale_or_not_sale.lower())


    rows = query.group_by(S.policy_selected).order_by(func.count(S.id).desc()).all()

    total_plan_sales = sum(r.sales for r in rows)

    plans = [
        {
            "plan_name": r.plan_name,
            "sales": r.sales,
            "share": round(r.sales / total_plan_sales * 100, 2) if total_plan_sales > 0 else 0.0,
        }
        for r in rows
    ]

    return {
        "agent_id": agent_id,
        "total_plan_sales": total_plan_sales,
        "plans": plans,
    }


@router.get("/membercare-agents/{agent_id}/agent-trend")
def get_agent_trend(
    agent_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    edited_compliance_score: Optional[str] = Query(None),
    sale_or_not_sale: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    # Week number and year derived from recorded_at
    week_year = extract("isoyear", R.recorded_at)
    week_num = extract("week", R.recorded_at)

    query = db.query(
        week_year.label("year"),
        week_num.label("week"),
        func.count(S.id).label("total_calls"),
        # Sales
        func.count(
            case((S.sale_or_not_sale.in_(["Sale", "Sale Complete"]), S.id))
        ).label("sale_count"),
        # Effective QA (edited score preferred)
        func.avg(
            case(
                (
                    func.coalesce(R.total_edited_score, R.total_score).isnot(None),
                    cast(func.coalesce(R.total_edited_score, R.total_score), Float),
                ),
            )
        ).label("avg_effective_qa"),
        # Raw QA (total_score only)
        func.avg(
            case((R.total_score.isnot(None), cast(R.total_score, Float)))
        ).label("avg_total_score"),
        # Edited QA
        func.avg(
            case((R.total_edited_score.isnot(None), cast(R.total_edited_score, Float)))
        ).label("avg_edited_score"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.agent_pk_id == agent_id,
        R.recorded_at.isnot(None),
    )

    if date_from:
        query = query.filter(R.recorded_at >= date_from)
    if date_to:
        query = query.filter(R.recorded_at <= date_to)
    if campaign:
        query = query.filter(R.campaign == campaign)
    if entity_id:
        query = query.filter(R.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(R.sub_entity_id == sub_entity_id)
    if edited_compliance_score:
        query = query.filter(func.lower(R.edited_compliance_score) == edited_compliance_score.lower())
    if sale_or_not_sale:
        query = query.filter(func.lower(R.sale_or_not_sale) == sale_or_not_sale.lower())


    rows = (
        query
        .group_by(week_year, week_num)
        .order_by(week_year, week_num)
        .all()
    )

    series = []
    for r in rows:
        total = r.total_calls or 0
        sales = r.sale_count or 0
        sale_pct = round(sales / total * 100, 2) if total > 0 else 0.0

        avg_total = r.avg_total_score
        avg_edited = r.avg_edited_score
        if avg_total is not None and avg_edited is not None:
            qa_adjustment_pct = round(avg_total - avg_edited, 2)
        else:
            qa_adjustment_pct = 0.0

        effective_qa_pct = round(r.avg_effective_qa, 2) if r.avg_effective_qa is not None else 0.0

        # Build the Monday date for this ISO week
        week_start = datetime.strptime(f"{int(r.year)}-W{int(r.week):02d}-1", "%G-W%V-%u").strftime("%Y-%m-%d")

        series.append({
            "week_start": week_start,
            "year": int(r.year),
            "week": int(r.week),
            "total_calls": total,
            "complaint_count": NOT_AVAILABLE_COMPLAINT,
            "sale_percentage": sale_pct,
            "effective_qa_percentage": effective_qa_pct,
            "qa_adjustment_percentage": qa_adjustment_pct,
        })

    return {
        "agent_id": agent_id,
        "series": series,
    }


@router.get("/membercare-agents/{agent_id}/agent-score-breakdown")
def get_agent_score_breakdown(
    agent_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    edited_compliance_score: Optional[str] = Query(None),
    sale_or_not_sale: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(
        R.id,
        R.detailed_score,
        R.total_score,
        R.total_edited_score,
        R.file_location,
        R.file_name,
        R.recorded_at,
    ).join(
        S, S.recording_id == R.id
    ).filter(
        S.agent_pk_id == agent_id
    )

    if date_from:
        query = query.filter(R.recorded_at >= date_from)
    if date_to:
        query = query.filter(R.recorded_at <= date_to)
    if campaign:
        query = query.filter(R.campaign == campaign)
    if entity_id:
        query = query.filter(R.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(R.sub_entity_id == sub_entity_id)
    if edited_compliance_score:
        query = query.filter(func.lower(R.edited_compliance_score) == edited_compliance_score.lower())
    if sale_or_not_sale:
        query = query.filter(func.lower(R.sale_or_not_sale) == sale_or_not_sale.lower())


    rows = query.all()

    # Per-criterion data across all recordings
    # criterion_key -> list of {recording_id, file_location, file_name, recorded_at, score, max_score}
    section_data = {}
    section_names = {}
    section_max_scores = {}
    # Compliance: criterion_key -> {criterion, pass_count, fail_count}
    compliance_items = {}
    total_compliance_evaluated = 0
    total_recordings = 0

    for row in rows:
        if not row.detailed_score:
            continue

        try:
            detail = json.loads(row.detailed_score)
        except (json.JSONDecodeError, TypeError):
            continue

        detailscore = detail.get("detailscore", {})
        if not detailscore or not isinstance(detailscore, dict):
            continue

        total_recordings += 1
        recording_info = {
            "recording_id": str(row.id),
            "file_location": row.file_location,
            "file_name": row.file_name,
            "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        }

        has_compliance_for_row = False

        for crit_key, criterion in detailscore.items():
            if not crit_key.isdigit() or not isinstance(criterion, dict):
                continue

            crit_name = criterion.get("criterion", crit_key)
            is_compliance = "compliance" in criterion

            if is_compliance:
                # Compliance criterion — prefer editedScore over compliance
                status = criterion.get("editedScore", criterion.get("compliance"))
                if status is None:
                    continue
                has_compliance_for_row = True
                if crit_key not in compliance_items:
                    compliance_items[crit_key] = {"criterion": crit_name, "pass_count": 0, "fail_count": 0}
                if str(status).strip().lower() == "pass":
                    compliance_items[crit_key]["pass_count"] += 1
                else:
                    compliance_items[crit_key]["fail_count"] += 1
            else:
                # QA criterion — compute effective score using editedScore overrides
                max_score = float(criterion.get("max_score", 0) or 0)

                # If editedScore exists at criterion level, use it directly
                if "editedScore" in criterion and criterion["editedScore"] is not None:
                    try:
                        effective_score = float(criterion["editedScore"])
                    except (ValueError, TypeError):
                        effective_score = float(criterion.get("awarded_score", 0) or 0)
                else:
                    effective_score = float(criterion.get("awarded_score", 0) or 0)

                section_names[crit_key] = crit_name
                if max_score > 0:
                    section_max_scores[crit_key] = max_score

                if crit_key not in section_data:
                    section_data[crit_key] = []

                section_data[crit_key].append({
                    **recording_info,
                    "score": round(effective_score, 2),
                    "max_score": round(max_score, 2),
                })

        if has_compliance_for_row:
            total_compliance_evaluated += 1

    # Build sections response (sorted numerically by criterion key)
    sections = []
    for key in sorted(section_data.keys(), key=lambda k: int(k)):
        entries = section_data[key]
        avg_score = round(sum(e["score"] for e in entries) / len(entries), 2) if entries else 0.0
        max_score = section_max_scores.get(key, 0)

        sorted_desc = sorted(entries, key=lambda e: e["score"], reverse=True)
        top_calls = sorted_desc[:3]
        bottom_calls = sorted_desc[-3:] if len(sorted_desc) > 3 else list(reversed(sorted_desc[:3]))

        sections.append({
            "key": key,
            "name": section_names.get(key, key),
            "max_score": round(max_score, 2),
            "avg_score": avg_score,
            "top_calls": top_calls,
            "bottom_calls": bottom_calls,
        })

    # Build compliance summary
    total_pass = sum(c["pass_count"] for c in compliance_items.values())
    total_fail = sum(c["fail_count"] for c in compliance_items.values())
    compliance_summary = {
        "total_evaluated": total_compliance_evaluated,
        "pass_count": total_pass,
        "fail_count": total_fail,
        "items": [
            {"key": k, **v}
            for k, v in sorted(compliance_items.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0)
        ],
    }

    return {
        "agent_id": agent_id,
        "total_recordings": total_recordings,
        "sections": sections,
        "compliance_summary": compliance_summary,
    }