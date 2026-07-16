from fastapi import APIRouter, Depends, Query, HTTPException
from typing import List, Optional
from app.core.helpers import normalize_list
from app.models import CommissionItem, ComTotals
from sqlalchemy.orm import Session
from sqlalchemy import  Date, Integer, extract, func, cast, Float, case, text, select
from app.db.session import get_db
from datetime import datetime
from app.models.commissionsDashboard.com_bob_variance_mv import BobVarianceMV
from app.models.commissionsDashboard.commission_main_mv import CommissionMainMV
from app.schemas.commission import MetricCardSchema


router = APIRouter(tags=["AGENT COMMISSION ROUTES"])

@router.get("/commission-metric-cards", response_model=MetricCardSchema)
def get_commission_metric_cards(
    agent_npn: str,
    carrier_id: Optional[List[str]] = Query(None, description="Filter by Carrier ID list"),
    payment_type: Optional[List[str]] = Query(None, description="Filter by Payment Type list"),
    statement_month: Optional[List[str]] = Query(None, description="Filter by Statement Month list"),
    db: Session = Depends(get_db)
):
    # Base query with required filter
    query = db.query(ComTotals).filter(ComTotals.npn == agent_npn)

    # Apply optional filters
    if carrier_id:
        query = query.filter(ComTotals.carrier_id.in_(normalize_list(carrier_id)))

    if payment_type:
        query = query.filter(ComTotals.payment_type.in_(normalize_list(payment_type)))

    if statement_month:
        query = query.filter(ComTotals.statement_month.in_(normalize_list(statement_month)))

    # Apply aggregations on the filtered query
    result = (
        query.with_entities(
            func.coalesce(func.sum(func.cast(ComTotals.statement_total, Float)), 0).label("total_paid"),
            func.coalesce(func.avg(func.cast(ComTotals.statement_total, Float)), 0).label("total_average_ytd"),
            func.coalesce(
                func.sum(
                    case(
                        (ComTotals.payment_type == "Commission", func.cast(ComTotals.statement_total, Float)),
                        else_=0,
                    )
                ),
                0,
            ).label("total_commissions"),
            func.coalesce(
                func.sum(
                    case(
                        (ComTotals.payment_type == "Override", func.cast(ComTotals.statement_total, Float)),
                        else_=0,
                    )
                ),
                0,
            ).label("total_overrides"),
            func.coalesce(
                func.sum(
                    case(
                        (ComTotals.payment_type == "Assignment", func.cast(ComTotals.statement_total, Float)),
                        else_=0,
                    )
                ),
                0,
            ).label("total_assignment"),
            func.coalesce(
                func.sum(
                    case(
                        (ComTotals.payment_type == "Bonus", func.cast(ComTotals.statement_total, Float)),
                        else_=0,
                    )
                ),
                0,
            ).label("total_bonus"),
        )
        .first()
    )

    return {
        "total_paid": result.total_paid,
        "total_average_ytd": result.total_average_ytd,
        "total_commissions": result.total_commissions,
        "total_overrides": result.total_overrides,
        "total_assignment": result.total_assignment,
        "total_bonus": result.total_bonus,
    }
    


@router.get("/commission-totals-filters")
def get_commission_totals_filters(
    npn: str = Query(..., description="Agent NPN"),
    db: Session = Depends(get_db)
):
    carriers = db.query(ComTotals.carrier_id, ComTotals.carrier_name).filter(ComTotals.npn == npn).group_by(ComTotals.carrier_id, ComTotals.carrier_name).all()
    payment_types = db.query(ComTotals.payment_type).filter(ComTotals.npn == npn).group_by(ComTotals.payment_type).all()
    statement_months = db.query(ComTotals.statement_month).filter(ComTotals.npn == npn).group_by(ComTotals.statement_month).all()

    return {
        "carriers": [
            {"id": carrier.carrier_id, "value": carrier.carrier_name}
            for carrier in carriers
        ],
        "payment_types": [
            {"id": pt.payment_type, "value": pt.payment_type}
            for pt in payment_types
        ],
        "statement_months": [
            {"id": sm.statement_month, "value": sm.statement_month}
            for sm in statement_months
        ],
    }

@router.get("/commission-totals")
def search_commission_totals(
    agent_npn: Optional[List[str]] = Query(None, description="Filter by Agent NPN list"),
    carrier_id: Optional[List[str]] = Query(None, description="Filter by Carrier ID list"),
    payment_type: Optional[List[str]] = Query(None, description="Filter by Payment Type list"),
    statement_month: Optional[List[str]] = Query(None, description="Filter by Statement Month list"),
    page: int = Query(1, ge=1, description="Page number (starting from 1)"),
    page_size: int = Query(50, ge=1, le=1000, description="Records per page"),
    db: Session = Depends(get_db)
):

    try:
        query = db.query(
            ComTotals.carrier_id,
            ComTotals.carrier_name,
            ComTotals.statement_month,
            ComTotals.payment_type,
            ComTotals.statement_total,
            ComTotals.status,
            ComTotals.status_date,
            ComTotals.npn,
            ComTotals.company_id,
            ComTotals.company_name,
            ComTotals.load_date,
            ComTotals.agent_name,
            ComTotals.associated_statement,
            ComTotals.report_date,
            ComTotals.job_id   
        )

        if agent_npn:
            query = query.filter(ComTotals.npn.in_(agent_npn))

        if carrier_id:
            query = query.filter(ComTotals.carrier_id.in_(normalize_list(carrier_id)))

        if payment_type:
            query = query.filter(ComTotals.payment_type.in_(normalize_list(payment_type)))

        if statement_month:
            query = query.filter(ComTotals.statement_month.in_(normalize_list(statement_month)))

        total_count = query.with_entities(func.count()).scalar()

        results = (
            query.order_by(ComTotals.statement_month.desc())
                 .limit(page_size)
                 .offset((page - 1) * page_size)
                 .all()
        )

        records = [
            {
                "carrier_id": r.carrier_id,
                "job_id": r.job_id,
                "carrier_name": r.carrier_name,
                "statement_month": r.statement_month,
                "payment_type": r.payment_type,
                "statement_total": r.statement_total,
                "status": r.status,
                "status_date": r.status_date,
                "report_date": r.report_date,
                "load_date": r.load_date,
                "npn": r.npn,
                "company_id": r.company_id,
                "company_name": r.company_name,
                "agent_name": r.agent_name,
                "associated_statement": r.associated_statement,
            }
            for r in results
        ]

        return {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "records": records
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@router.get("/commission-items")
def search_commission_items(
    agent_npn: Optional[str] = Query(None, description="Filter by Agent NPN"),
    carrier_id: Optional[str] = Query(None, description="Filter by Carrier ID"),
    statement_month: Optional[str] = Query(None, description="Filter by Statement Month (YYYY-MM or YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Number of records per page"),
    db: Session = Depends(get_db)
):
    try:
        query = db.query(CommissionItem)

        if agent_npn:
            query = query.filter(CommissionItem.npn == agent_npn)

        if carrier_id:
            query = query.filter(CommissionItem.carrier_id == carrier_id)

        if statement_month:
            parts = statement_month.split("-")
            if len(parts) >= 2:
                year, month = int(parts[0]), int(parts[1])
                query = query.filter(
                    extract("year", cast(CommissionItem.statement_month, Date)) == year,
                    extract("month", cast(CommissionItem.statement_month, Date)) == month
                )

        total_count = query.with_entities(func.count()).scalar()

        results = (
            query
            .order_by(CommissionItem.effective_date.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
            .all()
        )

        return {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "items": [r.as_dict() if hasattr(r, "as_dict") else {
                "carrier_name": r.carrier_name,
                "payment": r.payment,
                "payment_type": r.payment_type,
                "agent_name": r.agent_name,
                "npn": r.npn,
                "effective_date": r.effective_date,
                "coverage_month": r.coverage_month,
                "market": r.market,
                "insured_name": r.insured_name,
                "premium": r.premium,
                "split": r.split,
                "first_year_renewal": r.first_year_renewal,
                "statement_month": r.statement_month,
                "plan": r.plan,
                "lives": r.lives,
                "memo": r.memo,
            } for r in results]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@router.get("/commission/dashboard/month-filters")
async def get_commission_dashboard_filters(
    entity_id: str = Query(...),
    sub_entity_id: str = Query(...),
    db: Session = Depends(get_db)
):
    try:
        # base_query = db.query(CommissionMainMV).filter(
        #     CommissionMainMV.entity_id == entity_id,
        #     CommissionMainMV.sub_entity_id == sub_entity_id,
        #     CommissionMainMV.car_statement_mon.isnot(None)
        # )

        # # Commission months (not null)
        # month_results = (
        #     base_query.with_entities(CommissionMainMV.car_statement_mon)
        #     .distinct()
        #     .all()
        # )
        month_results = (
            db.query(CommissionMainMV.car_statement_mon)
            .filter(
                CommissionMainMV.entity_id == entity_id,
                CommissionMainMV.sub_entity_id == sub_entity_id,
                CommissionMainMV.car_statement_mon.isnot(None)
            )
            .distinct()
            .all()
        )

        commission_months = [
            {"id": r.car_statement_mon, "value": r.car_statement_mon}
            for r in month_results
        ]

        # Sort months in Python (latest first)
        commission_months.sort(
            key=lambda x: datetime.strptime(x["value"], "%b-%Y"),
            reverse=True
        )

        return {
            # "carriers": carriers,
            "commission_months": commission_months
        }

    except Exception as e:
        print(f"Error fetching filters: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/commission/dashboard/other-filters")
async def get_dashboard_other_filters(
    entity_id: str = Query(...),
    sub_entity_id: str = Query(...),
    commission_month: Optional[str] = Query(...),
    carrier: Optional[str] = Query(...),
    db: Session = Depends(get_db)
):
    try:

        # base filters
        filters = [
            CommissionMainMV.entity_id == entity_id,
            CommissionMainMV.sub_entity_id == sub_entity_id
        ]

        if commission_month:
            filters.append(CommissionMainMV.car_statement_mon == commission_month)

        if carrier:
            filters.append(CommissionMainMV.carrier_id == carrier)

        # fields to fetch
        filter_fields = {
            "direct_uplines": CommissionMainMV.direct_upline_name,
            "top_uplines": CommissionMainMV.top_upline_name,
            "recruiters": CommissionMainMV.recruiter_name,
            "states": CommissionMainMV.car_state,
            "product_types": CommissionMainMV.product_type
        }

        response = {}

        for key, column in filter_fields.items():

            rows = (
                db.query(column)
                .filter(
                    *filters,
                    column.isnot(None)
                )
                .distinct()
                .all()
            )

            response[key] = [
                {"id": r[0], "value": r[0]}
                for r in sorted(rows, key=lambda x: (x[0] or "").lower())
            ]

        return response

    except Exception as e:
        print(f"Error fetching filters: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Error fetching commission filter values"
        )  

@router.get("/commissions/dashboard/carrier-filter")
async def get_dashboard_dropdowns(
    entity_id: str = Query(...),
    sub_entity_id: str = Query(...),
    commission_month: Optional[str] = Query(...),
    db: Session = Depends(get_db)
):

    try:
        # base_query = db.query(CommissionMainMV).filter(CommissionMainMV.entity_id == entity_id, CommissionMainMV.sub_entity_id == sub_entity_id)

        # if commission_month:
        #     base_query = base_query.filter(CommissionMainMV.car_statement_mon == commission_month)

        # carriers = [
        #     {"carrier_id": r[0], "carrier_name": r[1]}
        #     for r in base_query.with_entities(
        #         CommissionMainMV.carrier_id,
        #         CommissionMainMV.carrier_name
        #     ).distinct().all()
        # ]
        filters = [
            CommissionMainMV.entity_id == entity_id,
            CommissionMainMV.sub_entity_id == sub_entity_id,
        ]

        if commission_month:
            filters.append(CommissionMainMV.car_statement_mon == commission_month)

        carrier_rows = (
            db.query(
                CommissionMainMV.carrier_id,
                CommissionMainMV.carrier_name
            )
            .filter(*filters)
            .distinct()
            .all()
        )

        agent_rows = (
            db.query(
                CommissionMainMV.agent_npn,
                CommissionMainMV.agent_name
            )
            .filter(
                *filters,
                CommissionMainMV.agent_npn.isnot(None),
            )
            .distinct()
            .all()
        )

        # single loop with sorting
        carriers = [
            {"carrier_id": cid, "carrier_name": cname}
            for cid, cname in sorted(carrier_rows, key=lambda x: (x[1] or "").lower())
        ]

        agents = [
            {"agent_npn": npn, "agent_name": name}
            for npn, name in sorted(agent_rows, key=lambda x: (x[1] or "").lower())
        ]

        return {
            "carriers": carriers,
            "agent_npns": agents,
        }

    except Exception as e:
        print(f"Error fetching dropdowns: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching dropdown values")
    
def apply_commission_filters(
    query,
    entity,
    sub_entity,
    carrier,
    commission_month,
    agent_npn,
    agent_name,
    direct_upline,
    top_upline,
    recruiter,
    state,
    product_type
):

    filters = []

    if entity:
        filters.append(CommissionMainMV.entity_id == entity)

    if sub_entity:
        filters.append(CommissionMainMV.sub_entity_id == sub_entity)

    if carrier:
        filters.append(CommissionMainMV.carrier_id == carrier)

    if commission_month:
        filters.append(CommissionMainMV.car_statement_mon == commission_month)

    if agent_npn:
        filters.append(CommissionMainMV.agent_npn == agent_npn)

    if agent_name:
        filters.append(CommissionMainMV.agent_name == agent_name)

    if direct_upline:
        filters.append(CommissionMainMV.direct_upline_name == direct_upline)

    if top_upline:
        filters.append(CommissionMainMV.top_upline_name == top_upline)

    if recruiter:
        filters.append(CommissionMainMV.recruiter_name == recruiter)

    if state:
        filters.append(CommissionMainMV.car_state == state)

    if product_type:
        filters.append(CommissionMainMV.product_type == product_type)

    if filters:
        query = query.filter(*filters)

    return query

@router.get("/commissions/dashboard/cards")
async def get_dashboard_metrics(
    entity_id: Optional[str],
    sub_entity_id: Optional[str],
    carrier: Optional[str],
    commission_month: Optional[str],
    agent_npn: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    direct_upline: Optional[str] = Query(None),
    top_upline: Optional[str] = Query(None),
    recruiter: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:

        base_query = db.query(CommissionMainMV)

        base_query = apply_commission_filters(
            base_query,
            entity_id, sub_entity_id, carrier, commission_month,
            agent_npn, agent_name, direct_upline,
            top_upline, recruiter, state, product_type
        )

        query = base_query.with_entities(

            func.coalesce(func.sum(CommissionMainMV.total_received), 0).label("total_revenue_received"),
            func.count(func.distinct(
                case((CommissionMainMV.total_received != 0, CommissionMainMV.agent_npn))
            )).label("total_revenue_received_count"),

            func.coalesce(func.sum(CommissionMainMV.com_received), 0).label("total_commissions_received"),
            func.count(func.distinct(
                case((CommissionMainMV.com_received != 0, CommissionMainMV.agent_npn))
            )).label("total_commissions_received_count"),

            func.coalesce(func.sum(CommissionMainMV.or_received), 0).label("total_overrides_received"),
            func.count(func.distinct(
                case((CommissionMainMV.or_received != 0, CommissionMainMV.agent_npn))
            )).label("total_overrides_received_count"),

            func.coalesce(func.sum(CommissionMainMV.bonus_received), 0).label("total_bonus_received"),
            func.count(func.distinct(
                case((CommissionMainMV.bonus_received != 0, CommissionMainMV.agent_npn))
            )).label("total_bonus_received_count"),

            func.coalesce(func.sum(CommissionMainMV.adt_received), 0).label("total_adjustments_received"),
            func.count(func.distinct(
                case((CommissionMainMV.adt_received != 0, CommissionMainMV.agent_npn))
            )).label("total_adjustments_received_count"),

            func.coalesce(func.sum(CommissionMainMV.total_paid), 0).label("total_revenue_paid"),
            func.count(func.distinct(
                case((CommissionMainMV.total_paid != 0, CommissionMainMV.agent_npn))
            )).label("total_revenue_paid_count"),

            func.coalesce(func.sum(CommissionMainMV.com_paid), 0).label("total_commissions_paid"),
            func.count(func.distinct(
                case((CommissionMainMV.com_paid != 0, CommissionMainMV.agent_npn))
            )).label("total_commissions_paid_count"),

            func.coalesce(func.sum(CommissionMainMV.or_paid), 0).label("total_overrides_paid"),
            func.count(func.distinct(
                case((CommissionMainMV.or_paid != 0, CommissionMainMV.agent_npn))
            )).label("total_overrides_paid_count"),

            func.coalesce(func.sum(CommissionMainMV.bonus_paid), 0).label("total_bonus_paid"),
            func.count(func.distinct(
                case((CommissionMainMV.bonus_paid != 0, CommissionMainMV.agent_npn))
            )).label("total_bonus_paid_count"),

            func.coalesce(func.sum(CommissionMainMV.adt_paid), 0).label("total_adjustments_paid"),
            func.count(func.distinct(
                case((CommissionMainMV.adt_paid != 0, CommissionMainMV.agent_npn))
            )).label("total_adjustments_paid_count"),

            func.coalesce(func.sum(CommissionMainMV.agility_profit), 0).label("agility_profit")

        )

        result = query.first()

        return dict(result._mapping)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))    

@router.get("/commissions/dashboard/count-drilldown")
async def get_count_drilldown(
    metric: str,
    entity_id: Optional[str],
    sub_entity_id: Optional[str],
    carrier: Optional[str],
    commission_month: Optional[str],
    page_number: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    agent_npn: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    direct_upline: Optional[str] = Query(None),
    top_upline: Optional[str] = Query(None),
    recruiter: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):

    metric_map = {
        "total_revenue_received": CommissionMainMV.total_received,
        "total_commissions_received": CommissionMainMV.com_received,
        "total_overrides_received": CommissionMainMV.or_received,
        "total_bonus_received": CommissionMainMV.bonus_received,
        "total_adjustments_received": CommissionMainMV.adt_received,
        "total_revenue_paid": CommissionMainMV.total_paid,
        "total_commissions_paid": CommissionMainMV.com_paid,
        "total_overrides_paid": CommissionMainMV.or_paid,
        "total_bonus_paid": CommissionMainMV.bonus_paid,
        "total_adjustments_paid": CommissionMainMV.adt_paid
    }

    if metric not in metric_map:
        raise HTTPException(status_code=400, detail="Invalid metric")

    metric_column = metric_map[metric]

    query = db.query(
        CommissionMainMV.agent_npn,
        CommissionMainMV.agent_name,
        CommissionMainMV.carrier_name,
        CommissionMainMV.car_policy_id,
        CommissionMainMV.car_report_date,
        CommissionMainMV.product_type,
        CommissionMainMV.car_state,
        metric_column.label("metric_value")
    ).filter(metric_column != 0)

    query = apply_commission_filters(
        query,
        entity_id, sub_entity_id, carrier, commission_month,
        agent_npn, agent_name, direct_upline,
        top_upline, recruiter, state, product_type
    )

    total_records = db.query(func.count()).select_from(query.subquery()).scalar()

    rows = query.order_by(
        CommissionMainMV.car_report_date.desc()
    ).offset((page_number - 1) * page_size)\
     .limit(page_size)\
     .all()

    return {
        "page_number": page_number,
        "page_size": page_size,
        "total_records": total_records,
        "total_pages": (total_records + page_size - 1) // page_size,
        "data": [dict(r._mapping) for r in rows]
    }

@router.get("/commissions/dashboard/revenue-trend")
async def get_revenue_trend(
    entity_id: Optional[str],
    sub_entity_id: Optional[str],
    carrier: Optional[str],
    commission_month: Optional[str] = Query(None),
    agent_npn: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    direct_upline: Optional[str] = Query(None),
    top_upline: Optional[str] = Query(None),
    recruiter: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):

    months_cte = select(
        func.generate_series(
            func.date_trunc('month', func.current_date()) - text("interval '12 months'"),
            func.date_trunc('month', func.current_date()) - text("interval '1 month'"),
            text("interval '1 month'")
        ).label("month")
    ).cte("months")

    revenue_query = db.query(
        func.date_trunc('month', CommissionMainMV.car_report_date).label("month"),
        func.sum(CommissionMainMV.total_received).label("revenue")
    )

    revenue_query = apply_commission_filters(
        revenue_query,
        entity_id,
        sub_entity_id,
        carrier,
        commission_month,
        agent_npn,
        agent_name,
        direct_upline,
        top_upline,
        recruiter,
        state,
        product_type
    )

    revenue_cte = revenue_query.group_by(
        func.date_trunc('month', CommissionMainMV.car_report_date)
    ).cte("revenue")

    final_query = db.query(
        months_cte.c.month,
        func.coalesce(revenue_cte.c.revenue, 0).label("revenue")
    ).outerjoin(
        revenue_cte,
        months_cte.c.month == revenue_cte.c.month
    ).order_by(months_cte.c.month)

    rows = final_query.all()

    return {"data": [dict(r._mapping) for r in rows]}

def apply_bob_filters(query,
                    #   entity,
                      carrier,
                      agent_npn,
                      agent_name,
                      direct_upline,
                      top_upline,
                      recruiter,
                      state,
                      product_type):
    # if entity:
    #     query = query.filter(BobVarianceMV.entity_id == entity)
    if carrier:
        query = query.filter(BobVarianceMV.carrier_id == carrier)

    if agent_npn:
        query = query.filter(BobVarianceMV.agent_npn == agent_npn)

    if agent_name:
        query = query.filter(BobVarianceMV.agent_full_name == agent_name)

    if state:
        query = query.filter(BobVarianceMV.mem_state == state)

    if product_type:
        query = query.filter(BobVarianceMV.product_type == product_type)

    if direct_upline:
        query = query.filter(BobVarianceMV.direct_upline_name == direct_upline)

    if top_upline:
        query = query.filter(BobVarianceMV.top_upline_name == top_upline)

    if recruiter:
        query = query.filter(BobVarianceMV.agent_recruiter == recruiter)

    return query


@router.get("/commissions/dashboard/bob-com/contracts")
async def get_bob_com_contracts(
    # entity: Optional[str] = Query(None),
    carrier: Optional[str] = Query(None),
    agent_npn: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    direct_upline: Optional[str] = Query(None),
    top_upline: Optional[str] = Query(None),
    recruiter: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:

        base_query = db.query(BobVarianceMV)

        base_query = apply_bob_filters(
            base_query,
            # entity,
            carrier,
            agent_npn,
            agent_name,
            direct_upline,
            top_upline,
            recruiter,
            state,
            product_type
        )

        result = base_query.with_entities(

            func.coalesce(
                func.sum(cast(BobVarianceMV.contract_count, Integer)), 0
            ).label("bob_total_contracts"),

            func.count(func.distinct(BobVarianceMV.txn_id))
            .label("com_total_contracts")

        ).first()

        variance = (result.bob_total_contracts or 0) - (result.com_total_contracts or 0)

        return {
            "bob_total_contracts": result.bob_total_contracts,
            "com_total_contracts": result.com_total_contracts,
            "variance": variance
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/commissions/dashboard/bob-com/lives")
async def get_bob_com_lives(
    # entity: Optional[str] = Query(None),
    carrier: Optional[str] = Query(None),
    agent_npn: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    direct_upline: Optional[str] = Query(None),
    top_upline: Optional[str] = Query(None),
    recruiter: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):

    try:

        base_query = db.query(BobVarianceMV)

        base_query = apply_bob_filters(
            base_query,
            # entity,
            carrier,
            agent_npn,
            agent_name,
            direct_upline,
            top_upline,
            recruiter,
            state,
            product_type
        )

        result = base_query.with_entities(

            func.coalesce(
                func.sum(cast(BobVarianceMV.mem_count, Integer)), 0
            ).label("bob_total_lives"),

            func.count(func.distinct(BobVarianceMV.txn_id))
            .label("com_total_lives")

        ).first()

        variance = (result.bob_total_lives or 0) - (result.com_total_lives or 0)

        return {
            "bob_total_lives": result.bob_total_lives,
            "com_total_lives": result.com_total_lives,
            "variance": variance
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/commissions/dashboard/bob-com/trend")
async def get_bob_com_trend(
    # entity: Optional[str] = Query(None),
    carrier: Optional[str] = Query(None),
    agent_npn: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    direct_upline: Optional[str] = Query(None),
    top_upline: Optional[str] = Query(None),
    recruiter: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):

    try:

        query = db.query(

            func.date_trunc('month', BobVarianceMV.report_date).label("month"),

            func.coalesce(
                func.sum(cast(BobVarianceMV.contract_count, Integer)), 0
            ).label("contracts"),

            func.coalesce(
                func.sum(cast(BobVarianceMV.mem_count, Integer)), 0
            ).label("lives")

        )

        query = apply_bob_filters(
            query,
            # entity,
            carrier,
            agent_npn,
            agent_name,
            direct_upline,
            top_upline,
            recruiter,
            state,
            product_type
        )

        query = query.group_by(
            func.date_trunc('month', BobVarianceMV.report_date)
        ).order_by(
            func.date_trunc('month', BobVarianceMV.report_date)
        )

        rows = query.all()

        return {"data": [dict(r._mapping) for r in rows]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))