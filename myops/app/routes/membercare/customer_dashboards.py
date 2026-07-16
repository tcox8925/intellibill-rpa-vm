from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, cast, Float, extract
from typing import Optional
from datetime import datetime

from app.db.session import get_db
from app.models.membercare.MembercareSalesActivity import MembercareSalesActivity
from app.models.MemberCareAudioEvaluations import MembercareAgentAssessmentRecordings

router = APIRouter(tags=["MEMBERCARE CUSTOMER DASHBOARDS"])

S = MembercareSalesActivity
R = MembercareAgentAssessmentRecordings

NOT_AVAILABLE_COMPLAINT = {"value": None, "message": "Data source not yet available for complaint tracking"}
NOT_AVAILABLE_DURATION = {"value": None, "message": "Duration data not yet available"}


def _apply_filters(query, date_from, date_to, campaign, entity_id, sub_entity_id, policy_selected=None):
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
    if policy_selected:
        query = query.filter(S.policy_selected == policy_selected)
    return query


@router.get("/membercare-customers/{customer_id}/performance-snapshot")
def get_customer_performance_snapshot(
    customer_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    policy_selected: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(
        func.count(S.id).label("total_calls"),
        func.count(
            case((
                S.policy_selected.isnot(None) & (S.policy_selected != ""),
                S.id,
            ))
        ).label("plans_enrolled"),
        func.count(
            case((
                func.array_to_string(S.coverage_type_requested, ",").ilike("%Family%"),
                S.id,
            ))
        ).label("family_coverage_count"),
        func.count(
            case((
                func.array_to_string(S.coverage_type_requested, ",").ilike("%Individual%"),
                S.id,
            ))
        ).label("individual_coverage_count"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.customer_id == customer_id
    )

    query = _apply_filters(query, date_from, date_to, campaign, entity_id, sub_entity_id, policy_selected)

    row = query.one()

    total_calls = row.total_calls or 0
    plans_enrolled = row.plans_enrolled or 0
    family_count = row.family_coverage_count or 0
    individual_count = row.individual_coverage_count or 0

    plan_enrollment_pct = round(plans_enrolled / total_calls * 100, 2) if total_calls > 0 else 0.0
    coverage_pct = round((family_count + individual_count) / total_calls * 100, 2) if total_calls > 0 else 0.0

    return {
        "customer_id": customer_id,
        "total_calls": total_calls,
        "plans_enrolled": plans_enrolled,
        "plan_enrollment_percentage": plan_enrollment_pct,
        "avg_call_duration": NOT_AVAILABLE_DURATION,
        "family_coverage_count": family_count,
        "individual_coverage_count": individual_count,
        "coverage_percentage": coverage_pct,
        "complaint_count": NOT_AVAILABLE_COMPLAINT,
    }


@router.get("/membercare-customers/{customer_id}/customer-distribution")
def get_customer_distribution(
    customer_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    policy_selected: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(
        func.count(S.id).label("total_calls"),
        # Campaign split
        func.count(case((R.campaign.ilike("%OEP%"), S.id))).label("oep_count"),
        func.count(case((R.campaign.ilike("%SEP%"), S.id))).label("sep_count"),
        func.count(case((R.campaign.isnot(None), S.id))).label("campaign_total"),
        # Enrollment split
        func.count(
            case((S.policy_selected.isnot(None) & (S.policy_selected != ""), S.id))
        ).label("enrolled_count"),
        # Coverage split
        func.count(
            case((func.array_to_string(S.coverage_type_requested, ",").ilike("%Individual%"), S.id))
        ).label("individual_count"),
        func.count(
            case((func.array_to_string(S.coverage_type_requested, ",").ilike("%Family%"), S.id))
        ).label("family_count"),
        func.count(
            case(((S.policy_selected.is_(None) | (S.policy_selected == "")), S.id))
        ).label("did_not_enroll_count"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.customer_id == customer_id
    )

    query = _apply_filters(query, date_from, date_to, campaign, entity_id, sub_entity_id, policy_selected)

    row = query.one()

    total_calls = row.total_calls or 0
    oep_count = row.oep_count or 0
    sep_count = row.sep_count or 0
    campaign_total = row.campaign_total or 0
    enrolled_count = row.enrolled_count or 0
    not_enrolled_count = total_calls - enrolled_count
    individual_count = row.individual_count or 0
    family_count = row.family_count or 0
    did_not_enroll_count = row.did_not_enroll_count or 0

    def pct(part, total):
        return round(part / total * 100, 2) if total > 0 else 0.0

    return {
        "customer_id": customer_id,
        "campaign_split": {
            "oep": {"count": oep_count, "percentage": pct(oep_count, campaign_total)},
            "sep": {"count": sep_count, "percentage": pct(sep_count, campaign_total)},
        },
        "enrollment_split": {
            "enrolled": {"count": enrolled_count, "percentage": pct(enrolled_count, total_calls)},
            "not_enrolled": {"count": not_enrolled_count, "percentage": pct(not_enrolled_count, total_calls)},
        },
        "coverage_split": {
            "individual": {"count": individual_count, "percentage": pct(individual_count, total_calls)},
            "family": {"count": family_count, "percentage": pct(family_count, total_calls)},
            "did_not_enroll": {"count": did_not_enroll_count, "percentage": pct(did_not_enroll_count, total_calls)},
        },
        "complaint_split": NOT_AVAILABLE_COMPLAINT,
    }


@router.get("/membercare-customers/{customer_id}/top-plans-enrolled")
def get_customer_top_plans_sold(
    customer_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    policy_selected: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(
        S.policy_selected.label("plan_name"),
        func.count(S.id).label("sales"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.customer_id == customer_id,
        S.policy_selected.isnot(None),
        S.policy_selected != "",
    )

    query = _apply_filters(query, date_from, date_to, campaign, entity_id, sub_entity_id, policy_selected)

    rows = query.group_by(S.policy_selected).order_by(func.count(S.id).desc()).all()

    total_plan_sales = sum(r.sales for r in rows)

    plans = [
        {
            "plan_name": r.plan_name,
            "count": r.sales,
            "share": round(r.sales / total_plan_sales * 100, 2) if total_plan_sales > 0 else 0.0,
        }
        for r in rows
    ]

    return {
        "customer_id": customer_id,
        "total_plan_sales": total_plan_sales,
        "plans": plans,
    }


@router.get("/membercare-customers/{customer_id}/customer-trend")
def get_customer_trend(
    customer_id: str,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    campaign: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    policy_selected: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    month_year = extract("year", R.recorded_at)
    month_num = extract("month", R.recorded_at)

    query = db.query(
        month_year.label("year"),
        month_num.label("month"),
        func.count(S.id).label("total_calls"),
        func.count(
            case((S.policy_selected.isnot(None) & (S.policy_selected != ""), S.id))
        ).label("plans_enrolled"),
        func.count(case((R.campaign.ilike("%OEP%"), S.id))).label("oep_count"),
        func.count(case((R.campaign.ilike("%SEP%"), S.id))).label("sep_count"),
    ).outerjoin(
        R, S.recording_id == R.id
    ).filter(
        S.customer_id == customer_id,
        R.recorded_at.isnot(None),
    )

    query = _apply_filters(query, date_from, date_to, campaign, entity_id, sub_entity_id, policy_selected)

    rows = (
        query
        .group_by(month_year, month_num)
        .order_by(month_year, month_num)
        .all()
    )

    series = []
    for r in rows:
        total = r.total_calls or 0
        enrolled = r.plans_enrolled or 0
        enrollment_pct = round(enrolled / total * 100, 2) if total > 0 else 0.0
        month_start = f"{int(r.year)}-{int(r.month):02d}-01"

        series.append({
            "month_start": month_start,
            "year": int(r.year),
            "month": int(r.month),
            "total_calls": total,
            "plans_enrolled": enrolled,
            "plan_enrollment_percentage": enrollment_pct,
            "oep_count": r.oep_count or 0,
            "sep_count": r.sep_count or 0,
            "complaint_count": NOT_AVAILABLE_COMPLAINT,
        })

    return {
        "customer_id": customer_id,
        "series": series,
    }


@router.get("/membercare-customers/{customer_id}/filter-options")
def get_customer_filter_options(
    customer_id: str,
    db: Session = Depends(get_db),
):
    policy_rows = (
        db.query(S.policy_selected)
        .filter(
            S.customer_id == customer_id,
            S.policy_selected.isnot(None),
            S.policy_selected != "",
        )
        .distinct()
        .all()
    )

    campaign_rows = (
        db.query(R.campaign)
        .join(S, S.recording_id == R.id)
        .filter(
            S.customer_id == customer_id,
            R.campaign.isnot(None),
            R.campaign != "",
        )
        .distinct()
        .all()
    )

    return {
        "policy_selected": sorted([r.policy_selected for r in policy_rows]),
        "campaign": sorted([r.campaign for r in campaign_rows]),
    }
