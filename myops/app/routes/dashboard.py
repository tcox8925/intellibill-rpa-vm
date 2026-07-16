from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, func, and_, or_, case, extract, cast, Integer, Date as SQLDate
from app.db.session import get_db
from app.models.agentModels.dashboard import BobCarrierSummaryMV, BobCarrierMembershipsMV, VwFileLogs, TimeSeriesForecasts
from app.models.agentModels.carriers import CarrierShort
from typing import Optional, List
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
# from concurrent.futures import ThreadPoolExecutor
# import asyncio
import traceback


router = APIRouter(tags=["DASHBOARD"])


@router.get("/dashboard/production-summary-by-carrier")
async def get_production_summary_by_carrier(
    report_month: Optional[List[str]] = Query(None),
    current_month: Optional[bool] = Query(False),
    carrier_id: Optional[List[str]] = Query(None),
    agent_npn: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=10000),
    db: Session = Depends(get_db)
):
    try:
        if current_month:
            report_month = [datetime.now().strftime('%b-%Y')]

        # If no month given, find the most recent month with actual data
        if not report_month:
            latest = db.query(func.max(BobCarrierSummaryMV.report_date)).scalar()
            if not latest:
                return {"items": [], "total_count": 0, "page": page, "page_size": page_size}
            report_month = [latest.strftime('%b-%Y')]

        # Build date filters for multiple months
        date_filters = []
        for month in report_month:
            report_date = datetime.strptime(f"01-{month}", '%d-%b-%Y')
            start_date = func.date_trunc('month', report_date)
            end_date = start_date + text("INTERVAL '1 month'")
            date_filters.append(and_(
                BobCarrierSummaryMV.report_date >= start_date,
                BobCarrierSummaryMV.report_date < end_date
            ))

        # Build base filter conditions
        filters = [and_(*date_filters)]
        
        if carrier_id:
            filters.append(BobCarrierSummaryMV.carrier_short_name.in_(carrier_id))
        if agent_npn:
            filters.append(BobCarrierSummaryMV.agent_npn.in_(agent_npn))

        # Get count of distinct carriers (for pagination)
        total_count = (
            db.query(func.count(func.distinct(BobCarrierSummaryMV.carrier_short_name)))
            .filter(*filters)
            .scalar()
        )

        # Get the actual data with grouping and pagination
        rows = (
            db.query(
                BobCarrierSummaryMV.carrier_short_name.label('carrier'),
                func.sum(BobCarrierSummaryMV.total_policies).label('total_policies'),
                func.sum(BobCarrierSummaryMV.total_members).label('total_members')
            )
            .filter(*filters)
            .group_by(BobCarrierSummaryMV.carrier_short_name)
            .order_by(BobCarrierSummaryMV.carrier_short_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "items": [{
                "carrier": r.carrier,
                "total_policies": int(r.total_policies or 0),
                "total_members": int(r.total_members or 0)
            } for r in rows],
            "total_count": total_count,
            "page": page,
            "page_size": page_size
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch production summary")

@router.get("/dashboard/production-summary-graph")
async def get_production_summary_graph(
    report_month: Optional[List[str]] = Query(None),
    current_month: Optional[bool] = Query(False),
    carrier_id: Optional[List[str]] = Query(None),
    agent_npn: Optional[List[str]] = Query(None),
    agent_name: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        if current_month:
            report_month = [datetime.now().strftime('%b-%Y')]

        # Build filters
        filters = []
        
        if report_month:
            date_filters = []
            for month in report_month:
                report_date = datetime.strptime(f"01-{month}", '%d-%b-%Y')
                start_date = func.date_trunc('month', report_date)
                end_date = start_date + text("INTERVAL '1 month'")
                date_filters.append(and_(
                    BobCarrierSummaryMV.report_date >= start_date,
                    BobCarrierSummaryMV.report_date < end_date
                ))
            filters.append(and_(*date_filters))
        else:
            # If no month specified, get latest month only
            latest = db.query(func.max(BobCarrierSummaryMV.report_date)).scalar()
            if latest:
                start_date = func.date_trunc('month', latest)
                end_date = start_date + text("INTERVAL '1 month'")
                filters.extend([
                    BobCarrierSummaryMV.report_date >= start_date,
                    BobCarrierSummaryMV.report_date < end_date
                ])

        if carrier_id:
            filters.append(BobCarrierSummaryMV.carrier_short_name.in_(carrier_id))
        if agent_npn:
            filters.append(BobCarrierSummaryMV.agent_npn.in_(agent_npn))
        if agent_name:
            filters.append(BobCarrierSummaryMV.agent_name.ilike(f'{agent_name}%'))

        # Single optimized query
        rows = (
            db.query(
                BobCarrierSummaryMV.carrier_short_name.label('carrier'),
                func.sum(BobCarrierSummaryMV.total_policies).label('total_policies'),
                func.sum(BobCarrierSummaryMV.total_members).label('total_members')
            )
            .filter(*filters)
            .group_by(BobCarrierSummaryMV.carrier_short_name)
            .all()
        )

        return [{
            "carrier": r.carrier,
            "total_policies": int(r.total_policies or 0),
            "total_members": int(r.total_members or 0)
        } for r in rows]

    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch graph data")
        
# @router.get("/dashboard/production-summary-filters")
# async def get_production_filters(
#     report_month: Optional[str] = Query(None),
#     carrier_id: Optional[str] = Query(None),
#     agent_npn: Optional[str] = Query(None),
#     db: Session = Depends(get_db)
# ):
#     try:
#         base_filter = BobCarrierMembershipsMV.txn_status == 'G'

#         def get_carriers():
#             query = (
#                 db.query(
#                     CarrierShort.id,
#                     CarrierShort.carrier_short_name
#                 )
#                 .join(BobCarrierMembershipsMV, BobCarrierMembershipsMV.carrier_id == CarrierShort.id)
#                 .filter(
#                     # base_filter,
#                     CarrierShort.carrier_short_name.isnot(None)
#                 )
#             )
#             if report_month:
#                 report_date = datetime.strptime(f"01-{report_month}", '%d-%b-%Y')
#                 query = query.filter(
#                     BobCarrierMembershipsMV.report_date >= func.date_trunc('month', report_date),
#                     BobCarrierMembershipsMV.report_date < func.date_trunc('month', report_date) + text("INTERVAL '1 month'")
#                 )
#             if agent_npn:
#                 query = query.filter(BobCarrierMembershipsMV.agent_npn == agent_npn)
#             if carrier_id:
#                 query = query.filter(CarrierShort.id == carrier_id)
#             return query.distinct().all()

#         def get_months():
#             query = (
#                 db.query(
#                     func.date_trunc('month', BobCarrierMembershipsMV.report_date).label('month_date')
#                 )
#                 .filter(
#                     # base_filter,
#                     BobCarrierMembershipsMV.report_date.isnot(None)
#                 )
#             )
#             if carrier_id:
#                 query = query.filter(BobCarrierMembershipsMV.carrier_id == carrier_id)
#             if agent_npn:
#                 query = query.filter(BobCarrierMembershipsMV.agent_npn == agent_npn)

#             rows = query.distinct().all()
#             return [r.month_date.strftime('%b-%Y') for r in rows]

#         def get_agents():
#             query = (
#                 db.query(
#                     BobCarrierMembershipsMV.agent_full_name,
#                     BobCarrierMembershipsMV.agent_npn
#                 )
#                 .filter(
#                     # base_filter,
#                     BobCarrierMembershipsMV.agent_npn.isnot(None),
#                     BobCarrierMembershipsMV.agent_full_name.isnot(None)
#                 )
#             )
#             if report_month:
#                 report_date = datetime.strptime(f"01-{report_month}", '%d-%b-%Y')
#                 query = query.filter(
#                     BobCarrierMembershipsMV.report_date >= func.date_trunc('month', report_date),
#                     BobCarrierMembershipsMV.report_date < func.date_trunc('month', report_date) + text("INTERVAL '1 month'")
#                 )
#             if carrier_id:
#                 query = query.filter(BobCarrierMembershipsMV.carrier_id == carrier_id)
#             if agent_npn:
#                 query = query.filter(BobCarrierMembershipsMV.agent_npn == agent_npn)

#             return query.distinct().limit(1000).all()

#         loop = asyncio.get_event_loop()
#         with ThreadPoolExecutor(max_workers=1) as executor:
#             carriers_future = loop.run_in_executor(executor, get_carriers)
#             months_future = loop.run_in_executor(executor, get_months)
#             agents_future = loop.run_in_executor(executor, get_agents)

#             carrier_rows, month_rows, agent_rows = await asyncio.gather(
#                 carriers_future, months_future, agents_future
#             )

#         return {
#             "carriers": sorted(
#                 [{"id": r[0], "name": r[1]} for r in carrier_rows],
#                 key=lambda x: x["name"]
#             ),
#             "report_months": sorted(
#                 month_rows, 
#                 reverse=True,
#                 key=lambda m: datetime.strptime(m, "%b-%Y")
#             ),
#             "agents": sorted(
#                 [{"npn": r[1], "name": r[0]} for r in agent_rows],
#                 key=lambda x: x["name"]
#             )
#         }
#     except Exception as e:
#         print(f"[ERROR] fetch-filters: {str(e)}")
#         raise HTTPException(status_code=500, detail="Failed to fetch filters")

@router.get("/dashboard/production-summary-filters")
async def get_production_filters(
    report_month: Optional[List[str]] = Query(None),
    carrier_id: Optional[List[str]] = Query(None),
    agent_npn: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        # If no specific filters, get latest month data only for performance
        if not report_month and not carrier_id and not agent_npn:
            latest = db.query(func.max(BobCarrierSummaryMV.report_date)).scalar()
            if latest:
                report_month = [latest.strftime('%b-%Y')]

        # Build base filters
        filters = []
        
        if report_month:
            date_filters = []
            for month in report_month:
                report_date = datetime.strptime(f"01-{month}", '%d-%b-%Y')
                start_date = func.date_trunc('month', report_date)
                end_date = start_date + text("INTERVAL '1 month'")
                date_filters.append(and_(
                    BobCarrierSummaryMV.report_date >= start_date,
                    BobCarrierSummaryMV.report_date < end_date
                ))
            filters.append(and_(*date_filters))
        
        if carrier_id:
            filters.append(BobCarrierSummaryMV.carrier_short_name.in_(carrier_id))
        if agent_npn:
            filters.append(BobCarrierSummaryMV.agent_npn.in_(agent_npn))

        # Execute all queries with minimal data scanning
        carriers = (
            db.query(BobCarrierSummaryMV.carrier_short_name)
            .filter(*filters)
            .filter(BobCarrierSummaryMV.carrier_short_name.isnot(None))
            .distinct()
            .order_by(BobCarrierSummaryMV.carrier_short_name)
            .limit(100)  # Limit for performance
            .all()
        )
        
        # For months, exclude date filters to get all available months but limit scope
        month_filters = [f for f in filters if 'report_date' not in str(f)]
        months = (
            db.query(func.date_trunc('month', BobCarrierSummaryMV.report_date).label('month_date'))
            .filter(*month_filters)
            .filter(BobCarrierSummaryMV.report_date.isnot(None))
            .distinct()
            .order_by(text('month_date DESC'))
            .limit(24)  # Last 2 years max
            .all()
        )
        
        agents = (
            db.query(
                BobCarrierSummaryMV.agent_name,
                BobCarrierSummaryMV.agent_npn
            )
            .filter(*filters)
            .filter(
                BobCarrierSummaryMV.agent_name.isnot(None),
                BobCarrierSummaryMV.agent_npn.isnot(None)
            )
            .distinct()
            .order_by(BobCarrierSummaryMV.agent_name)
            .limit(500)  # Reasonable limit
            .all()
        )

        return {
            "carriers": [
                {"id": carrier[0], "name": carrier[0]}
                for carrier in carriers
            ],
            "report_months": [
                month.month_date.strftime("%b-%Y")
                for month in months
            ],
            "agents": [
                {
                    "npn": agent.agent_npn,
                    "name": (agent.agent_name or "").strip() or agent.agent_npn or "Unknown Agent"
                }
                for agent in agents
                if agent.agent_npn  # Only include agents with NPNs
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch filters")

@router.get("/dashboard/production-summary-cards")
async def get_production_summary_cards(
    report_month: Optional[List[str]] = Query(None),
    current_month: Optional[bool] = Query(False),
    carrier_id: Optional[List[str]] = Query(None),
    agent_npn: Optional[List[str]] = Query(None),
    agent_name: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        if current_month:
            report_month = [datetime.now().strftime('%b-%Y')]

        # Build filters - always include a date filter for performance
        filters = []
        
        if report_month:
            date_filters = []
            for month in report_month:
                report_date = datetime.strptime(f"01-{month}", '%d-%b-%Y')
                start_date = func.date_trunc('month', report_date)
                end_date = start_date + text("INTERVAL '1 month'")
                date_filters.append(and_(
                    BobCarrierSummaryMV.report_date >= start_date,
                    BobCarrierSummaryMV.report_date < end_date
                ))
            filters.append(and_(*date_filters))
        else:
            # If no month specified, use latest month only
            latest = db.query(func.max(BobCarrierSummaryMV.report_date)).scalar()
            if latest:
                start_date = func.date_trunc('month', latest)
                end_date = start_date + text("INTERVAL '1 month'")
                filters.extend([
                    BobCarrierSummaryMV.report_date >= start_date,
                    BobCarrierSummaryMV.report_date < end_date
                ])

        if carrier_id:
            filters.append(BobCarrierSummaryMV.carrier_short_name.in_(carrier_id))
        if agent_npn:
            filters.append(BobCarrierSummaryMV.agent_npn.in_(agent_npn))
        if agent_name:
            filters.append(BobCarrierSummaryMV.agent_name.ilike(f'%{agent_name}%'))

        # Single optimized query using the materialized view
        result = (
            db.query(
                func.sum(BobCarrierSummaryMV.total_policies).label('total_policies'),
                func.sum(BobCarrierSummaryMV.total_members).label('total_members')
            )
            .filter(*filters)
            .first()
        )

        return {
            "total_policies": int(result.total_policies or 0) if result else 0,
            "total_members": int(result.total_members or 0) if result else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch summary cards")


@router.get("/dashboard/production-summary-detail")
async def get_carrier_agent_summary(
    carrier: str,
    report_month: Optional[str] = Query(None),
    current_month: Optional[bool] = Query(False),
    agent_npn: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    try:
        if current_month:
            report_month = datetime.now().strftime('%b-%Y')

        # Get carrier_id first - handle multiple matches by taking the first one
        carrier_result = (
            db.query(CarrierShort.id)
            .filter(CarrierShort.carrier_short_name == carrier)
            .first()
        )
        
        if not carrier_result:
            return {
                "items": [],
                "total_count": 0,
                "page": page,
                "page_size": page_size
            }
        
        carrier_id = carrier_result[0]

        # Build base filters with carrier_id directly
        filters = [
            BobCarrierMembershipsMV.txn_status == 'G',
            BobCarrierMembershipsMV.carrier_id == carrier_id
        ]
        
        if report_month:
            report_date = datetime.strptime(f"01-{report_month}", '%d-%b-%Y')
            start_date = func.date_trunc('month', report_date)
            end_date = start_date + text("INTERVAL '1 month'")
            filters.extend([
                BobCarrierMembershipsMV.report_date >= start_date,
                BobCarrierMembershipsMV.report_date < end_date
            ])
        else:
            # If no month specified, use latest month only
            latest = (
                db.query(func.max(BobCarrierMembershipsMV.report_date))
                .filter(
                    BobCarrierMembershipsMV.txn_status == 'G',
                    BobCarrierMembershipsMV.carrier_id == carrier_id
                )
                .scalar()
            )
            if latest:
                start_date = func.date_trunc('month', latest)
                end_date = start_date + text("INTERVAL '1 month'")
                filters.extend([
                    BobCarrierMembershipsMV.report_date >= start_date,
                    BobCarrierMembershipsMV.report_date < end_date
                ])

        if agent_npn:
            filters.append(BobCarrierMembershipsMV.agent_npn == agent_npn)

        if agent_name:
            filters.append(BobCarrierMembershipsMV.agent_full_name.ilike(f'%{agent_name}%'))

        # Single query to get both count and data efficiently
        base_query = (
            db.query(
                BobCarrierMembershipsMV.agent_full_name,
                BobCarrierMembershipsMV.agent_npn,
                func.sum(cast(BobCarrierMembershipsMV.contract_count, Integer)).label('policy_count'),
                func.sum(cast(BobCarrierMembershipsMV.mem_count, Integer)).label('member_count')
            )
            .filter(*filters)
            .group_by(
                BobCarrierMembershipsMV.agent_full_name,
                BobCarrierMembershipsMV.agent_npn
            )
        )

        # Get total count from the grouped query
        total_count = (
            db.query(func.count())
            .select_from(base_query.subquery())
            .scalar()
        )

        # Get paginated results
        rows = (
            base_query
            .order_by(func.sum(cast(BobCarrierMembershipsMV.contract_count, Integer)).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "items": [{
                "agent_name": (r.agent_full_name or "").strip() or r.agent_npn or "Unknown Agent",
                "agent_npn": r.agent_npn,
                "policy_count": int(r.policy_count or 0),
                "member_count": int(r.member_count or 0)
            } for r in rows],
            "total_count": total_count,
            "page": page,
            "page_size": page_size
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch carrier agent summary")


@router.get("/dashboard/production-trend")
async def get_production_trend(
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    effective_month: Optional[str] = Query(None),
    carrier_id: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    agent_npn: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        # Default to latest month if no filter given — prevents full table scan
        if not effective_month:
            latest = (
                db.query(func.max(BobCarrierMembershipsMV.report_date))
                # .filter(BobCarrierMembershipsMV.txn_status == 'G')
                .scalar()
            )
            if not latest:
                return {}
            effective_month = latest.strftime('%b-%Y')

        month_date = datetime.strptime(f"01-{effective_month}", '%d-%b-%Y')

        # Only select needed columns — not SELECT *
        query = (
            db.query(
                extract('year', BobCarrierMembershipsMV.report_date).label('yr'),
                extract('month', BobCarrierMembershipsMV.report_date).label('mo'),
                func.sum(cast(BobCarrierMembershipsMV.contract_count, Integer)).label('total_policies'),
                func.sum(cast(BobCarrierMembershipsMV.mem_count, Integer)).label('total_members'),
                func.sum(
                    case(
                        (BobCarrierMembershipsMV.mem_effective_date >= func.date_trunc('month', BobCarrierMembershipsMV.report_date),
                         cast(BobCarrierMembershipsMV.contract_count, Integer)),
                        else_=0
                    )
                ).label('new_policies'),
                func.sum(
                    case(
                        (BobCarrierMembershipsMV.mem_effective_date >= func.date_trunc('month', BobCarrierMembershipsMV.report_date),
                         cast(BobCarrierMembershipsMV.mem_count, Integer)),
                        else_=0
                    )
                ).label('new_members')
            )
            .filter(
                # BobCarrierMembershipsMV.txn_status == 'G',
                # Always filter by month
                BobCarrierMembershipsMV.report_date >= func.date_trunc('month', month_date),
                BobCarrierMembershipsMV.report_date < func.date_trunc('month', month_date) + text("INTERVAL '1 month'")
            )
        )

        if entity_id:
            query = query.filter(BobCarrierMembershipsMV.entity_id == entity_id)
        if sub_entity_id:
            query = query.filter(BobCarrierMembershipsMV.sub_entity_id == sub_entity_id)
        if carrier_id:
            query = query.filter(BobCarrierMembershipsMV.carrier_id == carrier_id)
        if market:
            query = query.filter(BobCarrierMembershipsMV.mem_market == market)
        if agent_npn:
            query = query.filter(BobCarrierMembershipsMV.agent_npn == agent_npn)

        past_rows = (
            query
            .group_by(
                extract('year', BobCarrierMembershipsMV.report_date),
                extract('month', BobCarrierMembershipsMV.report_date)
            )
            .order_by('yr', 'mo')
            .all()
        )

        actual = {
            "total_policies": [],
            "total_members": [],
            "new_policies": [],
            "new_members": []
        }

        for r in past_rows:
            month = f"{int(r.yr)}-{str(int(r.mo)).zfill(2)}"
            actual["total_policies"].append({"month": month, "value": int(r.total_policies or 0)})
            actual["total_members"].append({"month": month, "value": int(r.total_members or 0)})
            actual["new_policies"].append({"month": month, "value": int(r.new_policies or 0)})
            actual["new_members"].append({"month": month, "value": int(r.new_members or 0)})

        # FORECAST
        forecast = {"total_policies": [], "total_members": [], "new_policies": [], "new_members": []}
        try:
            forecast_rows = (
                db.query(
                    extract('year', TimeSeriesForecasts.forecast_month).label('yr'),
                    extract('month', TimeSeriesForecasts.forecast_month).label('mo'),
                    TimeSeriesForecasts.metric_name,
                    TimeSeriesForecasts.prediction,
                    TimeSeriesForecasts.lower_ci,
                    TimeSeriesForecasts.upper_ci,
                )
                .filter(
                    TimeSeriesForecasts.process_name == 'BOB',
                    TimeSeriesForecasts.is_active == True
                )
                .order_by('yr', 'mo')
                .all()
            )
            for r in forecast_rows:
                month = f"{int(r.yr)}-{str(int(r.mo)).zfill(2)}"
                if r.metric_name in forecast:
                    forecast[r.metric_name].append({
                        "month": month,
                        "prediction": float(r.prediction),
                        "lower_ci": float(r.lower_ci),
                        "upper_ci": float(r.upper_ci)
                    })
        except Exception:
            pass

        return {
            metric: {"actual": actual[metric], "forecast": forecast[metric]}
            for metric in actual.keys()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch production trend")

@router.get("/dashboard/production-trend-filters")
async def get_production_trend_filters(
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    # effective_month: Optional[str] = Query(None),
    carrier_id: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    agent_npn: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    try:
        base_query = db.query(BobCarrierMembershipsMV)
        if entity_id:
            base_query = base_query.filter(BobCarrierMembershipsMV.entity_id == entity_id)
        if sub_entity_id:
            base_query = base_query.filter(BobCarrierMembershipsMV.sub_entity_id == sub_entity_id)
        if carrier_id:
            base_query = base_query.filter(BobCarrierMembershipsMV.carrier_id == carrier_id)
        if market:
            base_query = base_query.filter(BobCarrierMembershipsMV.mem_market == market)
        if agent_npn:
            base_query = base_query.filter(BobCarrierMembershipsMV.agent_npn == agent_npn)

        # Query 1: Months only — small result (only 14 distinct months)
        month_rows = (
            base_query.with_entities(
                func.date_trunc('month', BobCarrierMembershipsMV.report_date).label('month_date')
            )
            .filter(BobCarrierMembershipsMV.report_date.isnot(None))
            .distinct()
            .order_by('month_date')
            .all()
        )
        
        months = [r.month_date.strftime('%b-%Y') for r in month_rows]

        carrier_rows = (
            db.query(BobCarrierSummaryMV.carrier_short_name)
            .filter(BobCarrierSummaryMV.carrier_short_name.isnot(None))
            .distinct()
            # .order_by(BobCarrierSummaryMV.carrier_short_name)
            .all()
        )

        market_rows = (
            base_query.with_entities(BobCarrierMembershipsMV.mem_market)
            .filter(
                BobCarrierMembershipsMV.mem_market.isnot(None)
            )
            .distinct()
            .all()
        )

        return {
            "effective_months": sorted(
                months,
                reverse=True,
                key=lambda m: datetime.strptime(m, "%b-%Y")
            ),
            "carriers": [{"id": r[0], "name": r[0]} for r in carrier_rows],
            "markets": sorted([r.mem_market for r in market_rows if r.mem_market]),
            "agents": []
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch filters")

@router.get("/dashboard/production-trend-filters/agents")
async def get_agent_filters(
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str = Query(None)
):
    try:
        query = (
            db.query(
                BobCarrierMembershipsMV.agent_full_name.label('name'),
                BobCarrierMembershipsMV.agent_npn.label('npn')
            )
            .filter(
                # BobCarrierMembershipsMV.txn_status == 'G',
                BobCarrierMembershipsMV.agent_full_name.isnot(None),
                BobCarrierMembershipsMV.agent_npn.isnot(None)
            )
        )

        if entity_id:
            query = query.filter(BobCarrierMembershipsMV.entity_id == entity_id)
        if sub_entity_id:
            query = query.filter(BobCarrierMembershipsMV.sub_entity_id == sub_entity_id)
        if search:
            query = query.filter(BobCarrierMembershipsMV.agent_full_name.ilike(f'%{search}%'))

        rows = (
            query
            .distinct()
            .order_by(BobCarrierMembershipsMV.agent_full_name)
            .limit(page_size)
            .offset((page - 1) * page_size)
            .all()
        )

        return {
            "agents": [{"name": r.name, "npn": r.npn} for r in rows],
            "pagination": {"page": page, "page_size": page_size}
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch agents filters")

@router.get("/dashboard/file-detail-table")
async def get_file_detail_table(
    db: Session = Depends(get_db),
    carrier: str = Query(None),
    month: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200)
):
    try:
        query = db.query(VwFileLogs).filter(VwFileLogs.process_type == 'BOB')

        # If no month specified, default to latest month
        if not month:
            try:
                latest_month = (
                    db.query(
                        func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').label('month_label')
                    )
                    .filter(
                        VwFileLogs.process_type == 'BOB',
                        func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').isnot(None)
                    )
                    .distinct()
                    .order_by(cast(VwFileLogs.report_date, SQLDate).desc())
                    .first()
                )
                if latest_month:
                    month = latest_month.month_label
            except Exception:
                pass

        if carrier:
            query = query.filter(VwFileLogs.carrier_short_name == carrier)
        if month:
            query = query.filter(func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY') == month)

        rows = (
            query.with_entities(
                VwFileLogs.carrier_short_name,
                func.max(VwFileLogs.process_date_end).label('latest_file_processed'),
                func.sum(cast(VwFileLogs.txn_tot_cnt, Integer)).label('tot_count'),
                func.sum(cast(VwFileLogs.txn_process_cnt, Integer)).label('tot_process'),
                func.sum(cast(VwFileLogs.txn_error_cnt, Integer)).label('tot_error'),
                func.count().label('files_processed'),
                func.max(case((VwFileLogs.load_status == 'Failed', 1), else_=0)).label('has_error')
            )
            .group_by(VwFileLogs.carrier_short_name)
            .order_by(VwFileLogs.carrier_short_name)
            .limit(page_size)
            .offset((page - 1) * page_size)
            .all()
        )

        return {
            "data": [
                {
                    "carrier_name": r.carrier_short_name,
                    "latest_file_processed": str(r.latest_file_processed) if r.latest_file_processed else None,
                    "tot_count": r.tot_count or 0,
                    "tot_process": r.tot_process or 0,
                    "tot_error": r.tot_error or 0,
                    "files_processed": r.files_processed,
                    "has_error": bool(r.has_error)
                }
                for r in rows
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "has_next": len(rows) == page_size
            }
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch table")


@router.get("/dashboard/file-detail-graph")
async def get_file_detail_graph(
    db: Session = Depends(get_db),
    carrier: str = Query(None),
    month: str = Query(None)
):
    try:
        query = db.query(VwFileLogs).filter(VwFileLogs.process_type == 'BOB')

        # If no month specified, default to latest month
        if not month:
            try:
                latest_month = (
                    db.query(
                        func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').label('month_label')
                    )
                    .filter(
                        VwFileLogs.process_type == 'BOB',
                        func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').isnot(None)
                    )
                    .distinct()
                    .order_by(cast(VwFileLogs.report_date, SQLDate).desc())
                    .first()
                )
                if latest_month:
                    month = latest_month.month_label
            except Exception:
                pass

        if carrier:
            query = query.filter(VwFileLogs.carrier_short_name == carrier)
        if month:
            query = query.filter(func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY') == month)

        rows = (
            query.with_entities(
                func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').label('month_label'),
                func.count().label('total_files'),
                cast(VwFileLogs.report_date, SQLDate).label('report_date')
            )
            .group_by(func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY'), cast(VwFileLogs.report_date, SQLDate))
            .order_by(cast(VwFileLogs.report_date, SQLDate))
            .all()
        )
        
        total_files = sum(r.total_files for r in rows)

        return {
            "total_files": total_files,
            "graph": [
                {
                    "month": r.month_label,
                    "total_files": r.total_files
                }
                for r in rows
            ]
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch graph")

@router.get("/dashboard/file-detail-filters")
async def get_file_detail_filters(
    db: Session = Depends(get_db)
):
    try:
        carrier_rows = (
            db.query(VwFileLogs.carrier_short_name)
            .filter(
                VwFileLogs.process_type == 'BOB',
                VwFileLogs.carrier_short_name.isnot(None)
            )
            .distinct()
            .order_by(VwFileLogs.carrier_short_name)
            .all()
        )

        month_rows = (
            db.query(
                func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').label('month_label'),
                cast(VwFileLogs.report_date, SQLDate).label('report_date')
            )
            .filter(
                VwFileLogs.process_type == 'BOB',
                func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').isnot(None)
            )
            .distinct()
            .order_by(cast(VwFileLogs.report_date, SQLDate).desc())
            .all()
        )

        return {
            "carriers": [r[0] for r in carrier_rows],
            "months": [r.month_label for r in month_rows]
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch filters")


@router.get("/dashboard/file-detail-carrier")
async def get_file_detail_carrier(
    db: Session = Depends(get_db),
    carrier: str = Query(...),
    month: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200)
):
    try:
        # If no month specified, default to latest month for this carrier
        if not month:
            try:
                latest_month = (
                    db.query(
                        func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').label('month_label')
                    )
                    .filter(
                        VwFileLogs.process_type == 'BOB',
                        VwFileLogs.carrier_short_name == carrier,
                        func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY').isnot(None)
                    )
                    .distinct()
                    .order_by(cast(VwFileLogs.report_date, SQLDate).desc())
                    .first()
                )
                if latest_month:
                    month = latest_month.month_label
            except Exception:
                pass

        query = (
            db.query(VwFileLogs)
            .filter(
                VwFileLogs.process_type == 'BOB',
                VwFileLogs.carrier_short_name == carrier
            )
        )

        if month:
            query = query.filter(func.to_char(cast(VwFileLogs.report_date, SQLDate), 'Mon-YYYY') == month)

        rows = (
            query.with_entities(
                VwFileLogs.file_name,
                VwFileLogs.process_date_end.label('process_date'),
                VwFileLogs.load_status,
                VwFileLogs.txn_tot_cnt.label('tot_count'),
                VwFileLogs.txn_process_cnt.label('tot_process'),
                VwFileLogs.txn_error_cnt.label('tot_error'),
                case((VwFileLogs.load_status.ilike('Failed'), 2), else_=1).label('order_status')
            )
            .order_by(case((VwFileLogs.load_status.ilike('Failed'), 2), else_=1).desc(), VwFileLogs.process_date_end.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
            .all()
        )

        return {
            "data": [
                {
                    "file_name": r.file_name,
                    "process_date": str(r.process_date) if r.process_date else None,
                    "load_status": r.load_status,
                    "tot_count": r.tot_count or 0,
                    "tot_process": r.tot_process or 0,
                    "tot_error": r.tot_error or 0,
                }
                for r in rows
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "has_next": len(rows) == page_size
            }
        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch carrier detail")