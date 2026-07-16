from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import Date, DateTime, cast, func, case, desc, asc
from app.db.session import get_db
from app.models.MemberCareAudioEvaluations import MembercareAgentAssessmentRecordings
from typing import Optional
from datetime import date, timedelta
from collections import defaultdict
from math import ceil

router = APIRouter(tags=["MEMBERCARE DASHBOARD"])

R = MembercareAgentAssessmentRecordings


def apply_filters(query, date_from, date_to, campaign, compliance, sale_status, agent_name):
    if date_from:
        query = query.filter(R.recorded_at >= date_from)
    if date_to:
        query = query.filter(R.recorded_at < date_to + timedelta(days=1))
    if campaign:
        query = query.filter(R.campaign.ilike(f"%{campaign}%"))
    if compliance:
        query = query.filter(R.edited_compliance_score.ilike(f"%{compliance}%"))
    if sale_status:
        query = query.filter(R.call_status.ilike(f"%{sale_status}%"))
    if agent_name:
        query = query.filter(R.agent_login.ilike(f"%{agent_name}%"))
    return query


@router.get("/membercare-dashboard/summary")
async def get_membercare_summary(
    entity_id: str = Query(...), 
    sub_entity_id: str = Query(...), 
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    campaign: Optional[str] = Query(None),
    compliance: Optional[str] = Query(None),
    sale_status: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        query = db.query(
            func.count(R.id).label("total_recordings"),
            func.count(case((R.campaign.ilike("SEP%"), R.id))).label("total_sep_calls"),
            func.count(case((R.campaign.ilike("OEP%"), R.id))).label("total_oep_calls"),
            func.count(case((func.lower(R.call_status) == "pass", R.id))).label("pass_count"),
            func.count(case((R.total_score.isnot(None), R.id))).label("audited_count"),
            func.avg(func.coalesce(R.total_edited_score, R.total_score)).label("avg_qa_score"),
            func.count(case((R.edited_compliance_score == "Pass", R.id))).label("compliance_pass_count"),
            func.count(case((R.edited_compliance_score.isnot(None), R.id))).label("compliance_total"),
        )

        query = query.filter(
            R.entity_id == entity_id,
            R.sub_entity_id == sub_entity_id,
        )

        query = apply_filters(query, date_from, date_to, campaign, compliance, sale_status, agent_name)
        row = query.one()

        total = row.total_recordings or 0
        audited = row.audited_count or 0
        comp_total = row.compliance_total or 0

        return {
            "total_recordings": total,
            "total_sep_calls": row.total_sep_calls or 0,
            "total_oep_calls": row.total_oep_calls or 0,
            "sale_rate": round((row.pass_count or 0) * 100.0 / total, 2) if total > 0 else 0,
            "audit_percentage": round(audited * 100.0 / total, 2) if total > 0 else 0,
            "avg_qa_score": round(float(row.avg_qa_score), 2) if row.avg_qa_score else 0,
            "avg_compliance_score": round((row.compliance_pass_count or 0) * 100.0 / comp_total, 2) if comp_total > 0 else 0,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch membercare dashboard summary: {str(e)}",
        )


@router.get("/membercare-dashboard/agent-performance")
async def get_agent_performance(
    entity_id: str = Query(...),
    sub_entity_id: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    campaign: Optional[str] = Query(None),
    compliance: Optional[str] = Query(None),
    sale_status: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    try:
        base_query = db.query(
            R.agent_login,
            func.avg(func.coalesce(R.total_edited_score, R.total_score)).label("avg_score"),
            func.count(R.id).label("total_calls"),
            func.count(case((func.lower(R.call_status) == "pass", R.id))).label("pass_count"),
            func.count(case((R.edited_compliance_score == "Pass", R.id))).label("compliance_pass"),
            func.count(case((R.edited_compliance_score.isnot(None), R.id))).label("compliance_total"),
        ).filter(R.total_score.isnot(None))

        base_query = base_query.filter(
            R.entity_id == entity_id,
            R.sub_entity_id == sub_entity_id,
        )

        base_query = apply_filters(base_query, date_from, date_to, campaign, compliance, sale_status, agent_name)
        base_query = base_query.group_by(R.agent_login)

        total_agents = base_query.count()

        top_performers = base_query.order_by(desc("avg_score")).limit(limit).all()
        needs_attention = base_query.order_by(asc("avg_score")).limit(limit).all()

        top_score = float(top_performers[0].avg_score) if top_performers else 0

        def format_agent(row, rank, top_score):
            score = float(row.avg_score or 0)
            total = row.total_calls or 0
            passes = row.pass_count or 0
            comp_total = row.compliance_total or 0
            comparison = round(score * 100.0 / top_score, 1) if top_score > 0 else 0
            return {
                "agent_login": row.agent_login,
                "rank": rank,
                "qa_score": round(score, 2),
                "comparison": comparison,
                "total_calls": total,
                "sale_rate": round(passes * 100.0 / total, 2) if total > 0 else 0,
                "compliance_rate": round((row.compliance_pass or 0) * 100.0 / comp_total, 2) if comp_total > 0 else 0,
            }

        return {
            "top_performers": [format_agent(r, i + 1, top_score) for i, r in enumerate(top_performers)],
            "needs_attention": [format_agent(r, total_agents - i, top_score) for i, r in enumerate(needs_attention)],
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch agent performance: {str(e)}",
        )

@router.get("/membercare-dashboard/data-analytics-filters")
async def get_membercare_dashboard_filters(
    entity_id: Optional[str] = None,
    sub_entity_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    try:
        query = db.query(R)
        if entity_id:
            query = query.filter(R.entity_id == entity_id)
        if sub_entity_id:
            query = query.filter(R.sub_entity_id == sub_entity_id)
        dates = (
            query.with_entities(func.distinct(cast(R.recorded_at, Date)))
            .filter(R.recorded_at.isnot(None))
            .all()
        )
        campaigns = ["SEP", "OEP"]
        compliance = (
            query.with_entities(func.distinct(R.edited_compliance_score))
            .filter(R.edited_compliance_score.isnot(None))
            .order_by(R.edited_compliance_score)
            .all()
        )
        compliance = [item[0] for item in compliance if item[0]]
        sale_status = (
            query.with_entities(func.distinct(R.sale_or_not_sale))
            .filter(R.sale_or_not_sale.isnot(None))
            .order_by(R.sale_or_not_sale)
            .all()
        )
        sale_status = [item[0] for item in sale_status if item[0]]
        agents = (
            query.with_entities(func.distinct(R.agent_login))
            .filter(R.agent_login.isnot(None))
            .all()
        )
        return {
            "dates": sorted([d[0] for d in dates if d[0]]),
            "campaigns": sorted(campaigns),
            "compliance": sorted(compliance),
            "sale_status": sorted(sale_status),
            "agent_names": sorted([a[0] for a in agents if a[0]]),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch filters: {str(e)}"
        )
 

@router.get("/membercare-dashboard/trend-analysis")
async def get_trend_analysis(
    entity_id: str = Query(...),
    sub_entity_id: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    campaign: Optional[str] = Query(None),
    compliance: Optional[str] = Query(None),
    sale_status: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        # Base query with entity filters
        query = db.query(R).filter(
            R.entity_id == entity_id,
            R.sub_entity_id == sub_entity_id,
        )

        # Reuse existing filters
        query = apply_filters(query, date_from, date_to, campaign, compliance, sale_status, agent_name)

        records = query.all()

        if not records:
            return []

        weekly = defaultdict(lambda: {
            "total_calls": 0,
            "sale_complete": 0,
            "qa_scores": [],
            "adj_scores": [],
            "complaints": 0,
        })
        for r in records:
            if not r.recorded_at:
                continue

            week_label = f"{r.recorded_at.strftime('%b')} W{((r.recorded_at.day - 1)//7) + 1}"
            weekly[week_label]["total_calls"] += 1

            # Sale complete (QA pass)
            if (r.call_status or "").lower() == "pass":
                weekly[week_label]["sale_complete"] += 1

            # QA scores
            if r.total_score:
                weekly[week_label]["qa_scores"].append(r.total_score)

            if r.total_edited_score:
                weekly[week_label]["adj_scores"].append(r.total_edited_score)

            # Complaints = compliance fail
            if (r.edited_compliance_score or "").lower() == "fail":
                weekly[week_label]["complaints"] += 1

        response = []

        for week, data in sorted(weekly.items()):
            avg_qa = sum(data["qa_scores"]) / len(data["qa_scores"]) if data["qa_scores"] else 0
            avg_adj = sum(data["adj_scores"]) / len(data["adj_scores"]) if data["adj_scores"] else 0

            response.append({
                "label": week,
                "totalCalls": data["total_calls"],
                "saleComplete": data["sale_complete"],
                "avgQAScore": round(avg_qa, 2),
                "adjQAScore": round(avg_adj, 2),
                "complaints": data["complaints"],
            })

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ALL AGENTS PERFORMANCE 

@router.get("/membercare-dashboard/all-agent-performance")
async def get_all_agent_performance(
    entity_id: str = Query(...),
    sub_entity_id: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    campaign: Optional[str] = Query(None),
    compliance: Optional[str] = Query(None),
    sale_status: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        base_query = db.query(
            R.agent_login.label("agent_login"),

            func.count(R.id).label("total_recordings"),

            func.count(
                case((func.lower(R.call_status) == "pass", 1))
            ).label("pass_count"),

            func.count(
                case((R.total_score.isnot(None), 1))
            ).label("audited_count"),

            func.avg(
                func.coalesce(R.total_edited_score, R.total_score)
            ).label("avg_qa_score"),

            func.count(
                case((func.lower(R.edited_compliance_score) == "pass", 1))
            ).label("compliance_pass_count"),

            func.count(
                case((R.edited_compliance_score.isnot(None), 1))
            ).label("compliance_total"),

        ).filter(
            R.entity_id == entity_id,
            R.sub_entity_id == sub_entity_id,
        )

        # Apply shared filters
        base_query = apply_filters(
            base_query,
            date_from,
            date_to,
            campaign,
            compliance,
            sale_status,
            agent_name
        )

        base_query = base_query.group_by(R.agent_login)

        # Total agents count (for pagination)
        total_agents = base_query.count()

        # Pagination
        offset = (page - 1) * page_size
        rows = base_query.order_by(desc("avg_qa_score")) \
                         .offset(offset) \
                         .limit(page_size) \
                         .all()

        results = []

        for row in rows:
            total = row.total_recordings or 0
            audited = row.audited_count or 0
            comp_total = row.compliance_total or 0

            results.append({
                "agent_login": row.agent_login,
                "total_recordings": total,
                "sale_rate": round((row.pass_count or 0) * 100.0 / total, 2) if total else 0,
                "audit_percentage": round(audited * 100.0 / total, 2) if total else 0,
                "avg_qa_score": round(float(row.avg_qa_score), 2) if row.avg_qa_score else 0,
                "compliance_percentage": round(
                    (row.compliance_pass_count or 0) * 100.0 / comp_total, 2
                ) if comp_total else 0,
            })

        return {
            "page": page,
            "page_size": page_size,
            "total_agents": total_agents,
            "total_pages": ceil(total_agents / page_size) if page_size else 1,
            "data": results,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch all agent performance: {str(e)}"
        )
    
from math import ceil

@router.get("/membercare-dashboard/search")
async def search_agents_full_data(
    q: str = Query(..., min_length=1),
    entity_id: str = Query(...),
    sub_entity_id: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    compliance: Optional[str] = Query(None),
    sale_status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    try:
        # ---------------------------------------
        # Base Query (NO campaign)
        # ---------------------------------------
        base = db.query(R).filter(
            R.entity_id == entity_id,
            R.sub_entity_id == sub_entity_id,
        )

        base = apply_filters(
            base,
            date_from,
            date_to,
            None,
            compliance,
            sale_status,
            None
        )

        base = base.filter(R.agent_login.ilike(f"%{q}%"))

        records = base.all()

        if not records:
            return {
                "page": page,
                "page_size": page_size,
                "total_agents": 0,
                "total_pages": 0,
                "agents": []
            }

        # ---------------------------------------
        # Group By Agent
        # ---------------------------------------
        from collections import defaultdict

        agent_map = defaultdict(list)
        for r in records:
            agent_map[r.agent_login].append(r)

        agent_list = list(agent_map.items())
        total_agents = len(agent_list)

        # ---------------------------------------
        # Pagination at Agent Level
        # ---------------------------------------
        start = (page - 1) * page_size
        end = start + page_size
        paginated_agents = agent_list[start:end]

        response = []

        for agent, calls in paginated_agents:
            total = len(calls)
            pass_count = sum(1 for r in calls if (r.call_status or "").lower() == "pass")
            audited = sum(1 for r in calls if r.total_score is not None)
            compliance_pass = sum(
                1 for r in calls if (r.edited_compliance_score or "").lower() == "pass"
            )
            compliance_total = sum(
                1 for r in calls if r.edited_compliance_score is not None
            )

            avg_score_list = [
                r.total_edited_score if r.total_edited_score is not None else r.total_score
                for r in calls if r.total_score is not None
            ]

            avg_score = (
                sum(avg_score_list) / len(avg_score_list)
                if avg_score_list else 0
            )

            response.append({
                "agent_login": agent,
                "total_recordings": total,
                "sale_rate": round(pass_count * 100.0 / total, 2) if total else 0,
                "audit_percentage": round(audited * 100.0 / total, 2) if total else 0,
                "avg_qa_score": round(avg_score, 2),
                "compliance_percentage": round(
                    compliance_pass * 100.0 / compliance_total, 2
                ) if compliance_total else 0,
                "recordings": [
                    {
                        "id": r.id,
                        "recorded_at": r.recorded_at,
                        "call_status": r.call_status,
                        "total_score": r.total_score,
                        "total_edited_score": r.total_edited_score,
                        "edited_compliance_score": r.edited_compliance_score,
                        "campaign": r.campaign,
                    }
                    for r in calls
                ]
            })

        return {
            "page": page,
            "page_size": page_size,
            "total_agents": total_agents,
            "total_pages": ceil(total_agents / page_size),
            "agents": response
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Agent search failed: {str(e)}"
        )