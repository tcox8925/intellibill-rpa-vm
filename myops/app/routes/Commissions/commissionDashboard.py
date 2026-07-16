
from fastapi import APIRouter, Depends, Query, HTTPException, Request, UploadFile, File, Form, Path
from fastapi.responses import JSONResponse, StreamingResponse
from app.models import CarrierShort, ProductType
from sqlalchemy.orm import Session
from sqlalchemy import select, func, literal, cast, DateTime, String, and_, Integer, desc, asc, or_, distinct, case
from typing import List, Optional
from app.db.session import get_db
from app.models.Entity import Entity
from app.models import ComAuditHistory
from app.schemas.commissionsDashboard.ComAuditHistorySchema import ComAuditHistorySchema, ComAuditHistoryCreateSchema
from app.models import CommissionProcessHistory, CommissionStatus, CommissionHeader, CommissionSummary, CommissionExceptionSummary, CommissionCalcs, CommissionItem, ComTotals, ComTotalsLob, OpsRpaScriptLogs, AgentContracts, AppointmentType
from app.schemas.commissionsDashboard.commissionDashboardSchema import CommissionHistoryFilter, CommissionProcessHistoryJoinedDTO, PagedResult, ComExceptionTotalContractsFilters
from app.models import CommissionProcessHistory, CommissionStatus, CommissionHeader, CommissionSummary, CommissionExceptionSummary, CommissionCalcs, CommissionOverrides, OpsLoadMatrixCom, CarrierShort, AgentMasterContracts
from app.schemas.commissionsDashboard.commissionDashboardSchema import CommissionHistoryFilter, CommissionProcessHistoryJoinedDTO, PagedResult, ComExceptionTotalContractsFilters, ComCalsFiltersSchema, OpsRpaScriptLogsFilterSchema, RunCommissionRequest, OpsRpaScriptLogsAddSchema, CommItemsFiltersSchema, CommTotalsFiltersSchema, ComExceptionsFiltersSchema, ComCalcsFromHeadersFiltersSchema, SummaryEmailSchema, ExportHistoryRequestSchema
from app.models import ContractScheduleDetail, ContractScheduleHeader, ContractScheduleHeaderCreateUpdate, LupTerritory, LupPaymentLevel, LupRateType, ContractScheduleDetailBulkRequest
from app.core.config import settings, get_blob_service_client
from app.utils.pagination import paginate
import httpx
import urllib.parse
import logger
import logging
import os
from azure.core.exceptions import AzureError, ResourceNotFoundError
import json
from openpyxl import Workbook
from io import BytesIO
from urllib.parse import unquote
from datetime import datetime
from uuid import UUID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def month_range(date_str: str):
    start = datetime.fromisoformat(date_str).replace(day=1)

    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    return start, end

router = APIRouter(tags=["COMMISSION DASHBOARD ROUTES"])

@router.get(
    "/commissions/audit-history",
    summary="List Commission audit history",
)
def list_com_audit_history(
    txn_id: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    sortColumn: str = Query("created_at"),
    sortOrder: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(ComAuditHistory)

    if txn_id:
        query = query.filter(ComAuditHistory.txn_id == txn_id)

    if user_email:
        query = query.filter(ComAuditHistory.user_email == user_email)

    if action:
        query = query.filter(ComAuditHistory.action == action)

    if module:
        query = query.filter(ComAuditHistory.module == module)

    return paginate(
        query,
        db,
        model=ComAuditHistory,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )

@router.post(
    "/commissions/audit-history",
    response_model=ComAuditHistorySchema,
    summary="Create Commission audit history entry"
)
def create_com_audit_history(
    request : Request,
    audit_data: ComAuditHistoryCreateSchema,
    db: Session = Depends(get_db),
):
    user = request.state.user
    audit_entry = ComAuditHistory(
        txn_id=audit_data.txn_id,
        user_email=user['email'],
        user_id=user['id'],
        action_message=audit_data.action_message,
        action=audit_data.action,
        tab=audit_data.tab,
        module=audit_data.module,
        sub_module=audit_data.sub_module,
    )

    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)

    return audit_entry

# @router.post("/commission-history", response_model=PagedResult)
@router.post("/commission-history")
async def get_commission_history(filters: CommissionHistoryFilter, db: Session = Depends(get_db)):
    page = filters.page
    page_size = filters.page_size

    query = (
        select(CommissionProcessHistory, Entity, CarrierShort)
        .join(Entity, CommissionProcessHistory.company_id == Entity.entity_id)
        .join(CarrierShort, CommissionProcessHistory.carrier_id == CarrierShort.id)
    )

    # Dynamic filters
    if filters.entitiy_id:
        query = query.filter(CommissionProcessHistory.company_id.in_(filters.entitiy_id))
    if filters.carrier_id:
        query = query.filter(CommissionProcessHistory.carrier_id.in_(filters.carrier_id))
    if filters.job_status:
        query = query.filter(CommissionProcessHistory.job_status.in_(filters.job_status))
    if filters.commission_status:
        query = query.filter(CommissionProcessHistory.commission_status.in_(filters.commission_status))
    if filters.product:
        query = query.filter(CommissionProcessHistory.product_name.in_(filters.product))
    if filters.report_date:
        report_start, report_end = month_range(filters.report_date)
        query = query.filter(
            cast(CommissionProcessHistory.report_month, DateTime) >= report_start,
            cast(CommissionProcessHistory.report_month, DateTime) < report_end
        )
    if filters.comm_month:
        comm_start, comm_end = month_range(filters.comm_month)
        query = query.filter(
            cast(CommissionProcessHistory.com_month, DateTime) >= comm_start,
            cast(CommissionProcessHistory.com_month, DateTime) < comm_end
        )

    if hasattr(CommissionProcessHistory, filters.sort_column):
        sort_attr = getattr(CommissionProcessHistory, filters.sort_column)
        query = query.order_by(desc(sort_attr) if filters.sort_order.lower() == "desc" else asc(sort_attr))
    else:
        query = query.order_by(CommissionProcessHistory.job_start_datetime.desc())

    # Execute query
    result = db.execute(query).all()

    # Deduplicate based on job_id
    unique_jobs = {}
    for cph, comp, carrier in result:
        if cph.job_id not in unique_jobs:
            unique_jobs[cph.job_id] = CommissionProcessHistoryJoinedDTO(
                job_id=cph.job_id or "0",
                company_id=cph.company_id or "Unknown",
                company_name=comp.entity_name or "Unknown",
                process_type=cph.process_type or "Unknown",
                carrier_id=cph.carrier_id or "Unknown",
                carrier_name=carrier.carrier_short_name or "Unknown",
                product_id=cph.product_id or "Unknown",
                product_name=cph.product_name or "Unknown",
                report_month=cph.report_month or "1900-01-01",
                com_month=cph.com_month or "1900-01-01",
                file_name=cph.file_name or "Unknown",
                job_status=cph.job_status or "Unknown",
                commission_status=cph.commission_status or "Unknown",
                job_start_datetime=cph.job_start_datetime,
                job_update_datetime=cph.job_update_datetime,
                job_end_datetime=cph.job_end_datetime,
            )

    all_records = list(unique_jobs.values())
    total_count = len(all_records)

    # Manual pagination AFTER unique filtering
    start = (page - 1) * page_size
    end = start + page_size
    paginated_records = all_records[start:end]

    return PagedResult(total_count=total_count, page=page, page_size=page_size, items=paginated_records)

@router.get("/commissions/{job_id}")
def get_commission_by_job_id(
    job_id: str,
    db: Session = Depends(get_db)
):
    result = db.query(
        func.coalesce(CommissionProcessHistory.job_id, "0").label("job_id"),
        func.coalesce(CommissionHeader.txn_id, "0").label("txn_id"),
        func.coalesce(CommissionProcessHistory.company_id, "Unknown").label("company_id"),
        func.coalesce(Entity.entity_name, "Unknown").label("company_name"),
        func.coalesce(CommissionProcessHistory.process_type, "Unknown").label("process_type"),
        func.coalesce(CommissionProcessHistory.carrier_id, "Unknown").label("carrier_id"),
        func.coalesce(CarrierShort.carrier_short_name, "Unknown").label("carrier_name"),
        func.coalesce(CommissionProcessHistory.product_id, "Unknown").label("product_id"),
        func.coalesce(CommissionProcessHistory.product_name, "Unknown").label("product_name"),
        cast(func.coalesce(CommissionProcessHistory.report_month, literal("1900-01-01")), String(10)).label("report_month"),
        cast(func.coalesce(CommissionProcessHistory.com_month, literal("1900-01-01")), String(10)).label("com_month"),
        func.coalesce(CommissionProcessHistory.file_name, "Unknown").label("file_name"),
        func.coalesce(CommissionProcessHistory.job_status, "Unknown").label("job_status"),
        func.coalesce(CommissionProcessHistory.commission_status, "Unknown").label("commission_status"),
        func.coalesce(CommissionProcessHistory.job_start_datetime, None).label("job_start_datetime"),
        func.coalesce(CommissionProcessHistory.job_update_datetime, None).label("job_update_datetime"),
        func.coalesce(CommissionProcessHistory.job_end_datetime, None).label("job_end_datetime"),
    ).outerjoin(
        Entity, CommissionProcessHistory.company_id == Entity.entity_id
    ).outerjoin(
        CarrierShort, CommissionProcessHistory.carrier_id == CarrierShort.id
    ).outerjoin(
        CommissionHeader, CommissionProcessHistory.job_id == CommissionHeader.job_id
    ).where(
        CommissionProcessHistory.job_id == job_id
    ).first()

    # If no row found, return empty list to match previous response shape
    if not result:
        return []

    # result is a single row (Row) from SQLAlchemy — build a dict and return as a single-item list
    row = result
    row_dict = {
        "job_id": row[0],
        "txn_id": row[1],
        "company_id": row[2],
        "company_name": row[3],
        "process_type": row[4],
        "carrier_id": row[5],
        "carrier_name": row[6],
        "product_id": row[7],
        "product_name": row[8],
        "report_month": row[9],
        "com_month": row[10],
        "file_name": row[11],
        "job_status": row[12],
        "commission_status": row[13],
        "job_start_datetime": row[14],
        "job_update_datetime": row[15],
        "job_end_datetime": row[16],
    }

    return row_dict

@router.patch("/commissions/update-com-status")
def update_commission_status(
    job_id = Query(None),
    commission_status = Query(None),
    db: Session = Depends(get_db)
):
    existing_commission = db.query(CommissionProcessHistory).filter(CommissionProcessHistory.job_id == job_id).first()
    if existing_commission:
        existing_commission.commission_status = commission_status
        db.commit()
        db.refresh(existing_commission)
        return existing_commission
    else:
        return "failed to update the commission status"
    
@router.post("/commission/export-history")
async def export_history_to_excel(
    payload: ExportHistoryRequestSchema,
    db: Session = Depends(get_db)
):
    try:
        job_id = payload.job_id

        # default pagination values (not used because export returns full data)
        DEFAULT_PAGE = 1
        DEFAULT_PAGE_SIZE = 20

        # Single-call fetch using export mode
        def _fetch_all(fn_get, base_filters_ctor):
            filters = base_filters_ctor(
                page=DEFAULT_PAGE,
                page_size=DEFAULT_PAGE_SIZE,
                view_type="export"
            )
            resp = fn_get(filters, db)
            return resp.get("items", []) if isinstance(resp, dict) else getattr(resp, "items", []) or []

        # Filter constructors
        def _comm_items_ctor(page, page_size, view_type):
            return CommItemsFiltersSchema(
                job_id=job_id,
                carriers=[],
                states=[],
                agents=[],
                page=page,
                page_size=page_size,
                sort_by="job_id",
                sort_order="DESC",
                view_type=view_type
            )

        def _comm_totals_ctor(page, page_size, view_type):
            return CommTotalsFiltersSchema(
                job_id=job_id,
                carriers=[],
                states=[],
                agents=[],
                page=page,
                page_size=page_size,
                sort_by="job_id",
                sort_order="DESC",
                view_type=view_type
            )

        # Fetch full data in ONE call each
        com_items = _fetch_all(get_commission_items, _comm_items_ctor)
        com_totals = _fetch_all(get_paged_commission_totals, _comm_totals_ctor)

        # Excel workbook (openpyxl)
        wb = Workbook()
        ws1 = wb.active
        ws1.title = "ComItems"

        def _to_dict(item):
            if hasattr(item, "dict"):
                return item.dict()
            if isinstance(item, dict):
                return item
            return item.__dict__ if hasattr(item, "__dict__") else {"value": item}

        # Write ComItems sheet
        if com_items:
            first = _to_dict(com_items[0])
            headers = list(first.keys())
            ws1.append(headers)
            for row in com_items:
                data = _to_dict(row)
                ws1.append([data.get(h, "") for h in headers])
        else:
            ws1.append(["No Comm Items available"])

        # Write ComTotals sheet
        ws2 = wb.create_sheet("ComTotals")
        if com_totals:
            first = _to_dict(com_totals[0])
            headers = list(first.keys())
            ws2.append(headers)
            for row in com_totals:
                data = _to_dict(row)
                ws2.append([data.get(h, "") for h in headers])
        else:
            ws2.append(["No Comm Totals available"])

        # Stream Excel response
        stream = BytesIO()
        wb.save(stream)
        stream.seek(0)

        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename=CommissionHistory_{job_id}.xlsx"
            }
        )

    except Exception as e:
        logger.error(f"Error exporting history for JobId {payload.job_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to export history: {str(e)}")


@router.get("/commission-filters")
def get_commission_filters(
    db: Session = Depends(get_db),
    company_id: str = Query(None)
):
    carriers = db.query(
        distinct(CarrierShort.id).label("id"),
        CarrierShort.vendor_name,
        CarrierShort.carrier_short_name
    ).all()

    products = db.query(
        ProductType.company_id,
        ProductType.company_name,
        ProductType.product_type
    ).filter(ProductType.company_id == company_id) \
    .group_by(ProductType.company_id, ProductType.company_name, ProductType.product_type).all()

    com_status = db.query(
        CommissionStatus.pk_id,
        CommissionStatus.commission_status
    ).all()

    appointment_types = db.query(
        AppointmentType.appointment_type
    ).all()

    com_months = db.query(
        distinct(CommissionProcessHistory.com_month).label("com_month")
    ).filter(
        CommissionProcessHistory.company_id == company_id,
        CommissionProcessHistory.com_month.isnot(None)
    ).order_by(
        CommissionProcessHistory.com_month.desc()
    ).all()

    result = {
        "carriers": [
            {
                "id": carrier.id,
                "value": carrier.vendor_name,
            } for carrier in carriers
        ],
        "short_carriers": [
            {
                "id": carrier.id,
                "value": carrier.carrier_short_name,
            } for carrier in carriers
        ],
        "products": [
            {
                "id": product.product_type,
                "value": product.product_type
            } for product in products
        ],
        "job_status": [
            # {"status": "Pending"},
            {"id": "Processing", "value": "Processing"},
            {"id": "Completed", "value": "Completed"},
            {"id": "Failed", "value": "Failed"}
        ],
        "com_status": [
            {
                "id": status.commission_status,
                "value": status.commission_status
            } for status in com_status
        ],
        "appointment_types": [
            {
                "id": type.appointment_type,
                "value": type.appointment_type
            } for type in appointment_types
        ],
        "com_months": [
            {
                "id": month.com_month,
                "value": month.com_month
            } for month in com_months
        ]
    }

    return result

@router.get("/commission/history/filters")
def get_commission_history_filters(
    company_id: str = Query(..., description="Company ID"),
    db: Session = Depends(get_db),
):
    """
    Returns filter dropdowns for Commission History based on company_id
    """

    rows = (
        db.query(
            CommissionProcessHistory.carrier_id,
            CommissionProcessHistory.product_name,
            CommissionProcessHistory.commission_status,
            CommissionProcessHistory.job_status,
            CommissionProcessHistory.com_month,
            CommissionProcessHistory.report_month,
        )
        .filter(CommissionProcessHistory.company_id == company_id)
        .all()
    )

    carrier_ids = set()
    product_set = set()
    commission_status_set = set()
    job_status_set = set()
    com_month_set = set()
    report_month_set = set()

    for r in rows:
        if r.carrier_id:
            carrier_ids.add(r.carrier_id)

        if r.product_name:
            product_set.update(
                p.strip()
                for p in r.product_name.split(",")
                if p.strip()
            )

        if r.commission_status:
            commission_status_set.add(r.commission_status)

        if r.job_status:
            job_status_set.add(r.job_status)

        if r.com_month:
            com_month_set.add(r.com_month)

        if r.report_month:
            report_month_set.add(r.report_month)

    carriers = (
        db.query(
            distinct(CarrierShort.id).label("id"),
            CarrierShort.carrier_short_name
        )
        .filter(CarrierShort.id.in_(carrier_ids))
        .order_by(CarrierShort.carrier_short_name)
        .all()
    )

    return {
        "carriers": [
            {"id": c.id, "value": c.carrier_short_name}
            for c in carriers
        ],
        "products": [
            {"id": p, "value": p}
            for p in sorted(product_set)
        ],
        "com_status": [
            {"id": s, "value": s}
            for s in sorted(commission_status_set)
        ],
        "job_status": [
            {"id": s, "value": s}
            for s in sorted(job_status_set)
        ],
        "com_months": [
            {"id": m, "value": m}
            for m in sorted(com_month_set, reverse=True)
        ],
        "report_months": [
            {"id": m, "value": m}
            for m in sorted(report_month_set, reverse=True)
        ],
    }

@router.get("/commission/summary")
def get_commission_summary(
    db: Session = Depends(get_db),
    carrier_id: str = Query(None),
    report_month: str = Query(None)
):
    result = db.query(
        CommissionSummary
    ).where(
        CommissionSummary.carrier_id == carrier_id,
        CommissionSummary.report_month == report_month
    ).first()
    return result

@router.post("/commission/summary/exc-total-contract-count")
def get_total_contracts_count(
    filters: ComExceptionTotalContractsFilters,
    db: Session = Depends(get_db)
):
    query = db.query(
        func.coalesce(func.sum(cast(CommissionExceptionSummary.total_contracts, Integer)), 0)
    )

    count_query = db.query(
        func.count(CommissionExceptionSummary.pk_id)
    )

    if filters.selectedAgentNpns:
        query = query.filter(CommissionExceptionSummary.agent_name.in_(filters.selectedAgentNpns))
        count_query = count_query.filter(CommissionExceptionSummary.agent_name.in_(filters.selectedAgentNpns))
    if filters.selectedAgentNames:
        query = query.filter(CommissionExceptionSummary.car_prod_npn.in_(filters.selectedAgentNames))
        count_query = count_query.filter(CommissionExceptionSummary.car_prod_npn.in_(filters.selectedAgentNames))
    if filters.selectedExceptionCodes:
        query = query.filter(CommissionExceptionSummary.ex_code.in_(filters.selectedExceptionCodes))
        count_query = count_query.filter(CommissionExceptionSummary.ex_code.in_(filters.selectedExceptionCodes))
    if filters.selectedWritingNumbers:
        query = query.filter(CommissionExceptionSummary.car_prod_writing_num.in_(filters.selectedWritingNumbers))
        count_query = count_query.filter(CommissionExceptionSummary.car_prod_writing_num.in_(filters.selectedWritingNumbers))
    if filters.txnId:
        query = query.filter(CommissionExceptionSummary.job_id == filters.txnId)
        count_query = count_query.filter(CommissionExceptionSummary.job_id == filters.txnId)

    result = query.scalar()
    count_result = count_query.scalar()

    return {"total_contracts": result, "total_count": count_result}

@router.get("/commission/summary/calcs-count")
def get_commission_calcs_count(
    db: Session = Depends(get_db),
    job_id = Query(None),
    txn_status = Query(None),
):
    query = db.query(
        func.count(CommissionCalcs.job_id)
    ).filter(
        CommissionCalcs.job_id == job_id,
        CommissionCalcs.txn_status == txn_status
    )

    result = query.scalar()
    return {"total_count": result}

@router.post("/commission/summary/send-email-summary")
async def send_summary_email(data: SummaryEmailSchema):
    try:
        # HTML Email Template
        html_message = f"""
        <p>Hi</p>

        <p>Here is the statement breakdown :</p>

        <table border="1" cellpadding="5" cellspacing="0">
            <tr><td><strong>Statement Total</strong></td><td>{data.revenue}</td></tr>
            <tr><td><strong>Commissions</strong></td><td>{data.commissions}</td></tr>
            <tr><td><strong>Overrides</strong></td><td>{data.overrides}</td></tr>
            <tr><td><strong>Bonus</strong></td><td>{data.bonus}</td></tr>
            <tr><td><strong>Adjustments</strong></td><td>{data.adjustments}</td></tr>
        </table>

        <br/>

        <p>Please let me know if you have any questions or concerns.</p>
        <p>Thanks</p>
        """

        logic_app_url = (
            "https://prod-00.eastus2.logic.azure.com:443/workflows/0a4c86c5bfed4f7884ea33af1505fd4a"
            "/triggers/When_a_HTTP_request_is_received/paths/invoke"
            "?api-version=2016-10-01&sp=%2Ftriggers%2FWhen_a_HTTP_request_is_received%2Frun"
            "&sv=1.0&sig=PKxwZ5MjRYRSl1Ou-weVanO9Bp7NraTfbmkFXVLyNbU"
        )

        payload = {
            "body": "",
            "to_list": data.email,
            "cc_list": "",
            "subject": f"Summary for {data.commission_month}, {data.carrier_name}",
            "message": html_message,
            "report_date": ""
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                logic_app_url,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"}
            )

        if response.status_code not in (200, 202):
            raise HTTPException(
                status_code=500,
                detail=f"Failed to call Logic App. Status: {response.status_code}"
            )

        return {"success": True, "message": "Email sent successfully"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# @router.get("/commission/adjustment/com-calcs-filter")
# def get_adjustment_com_calcs_filter(
#     job_id: str,
#     db: Session = Depends(get_db)
# ):
#     query = db.query(
#         distinct(CommissionCalcs.agent_npn).label("agent_npn"),
#         CommissionCalcs.agent_name
#     ).filter(CommissionCalcs.job_id == job_id)

#     results = query.all()

#     npns_list = [
#             {
#                 "id": row[0],
#                 "value": f"{row[0]} - {row[1] or ''}".strip(),
#             }
#             for row in results
#             if row[0]
#         ]

#     return npns_list

# @router.post("/commission/adjustment/com-calcs")
# def get_adjustment_com_calcs(
#     filters: AdjustmentComCalcsFiltersSchema,
#     db: Session = Depends(get_db),
# ):
#     query = db.query(CommissionCalcs).filter(CommissionCalcs.job_id == filters.job_id)

#     if filters.agent_npn:
#         query = query.filter(CommissionCalcs.agent_npn.in_(filters.agent_npn))
#     if filters.agent_name:
#         query = query.filter(CommissionCalcs.agent_name.in_(filters.agent_name))
#     if filters.selected_transaction_statuses:
#         query = query.filter(CommissionCalcs.txn_status.in_(filters.selected_transaction_statuses))

#     sort_attr = getattr(CommissionCalcs, filters.sort_column, CommissionCalcs.agent_name)
#     query = query.order_by(sort_attr.asc() if filters.sort_order.lower() == "asc" else sort_attr.desc())

#     total_records = None
#     if filters.view_type.lower() == "dashboard":
#         total_records = db.query(func.count()).select_from(query.subquery()).scalar()
#         query = query.offset((filters.page - 1) * filters.page_size).limit(filters.page_size)

#     results = query.all()

#     records = [
#         {
#             "agent_npn": r.agent_npn,
#             "agent_writing_number": r.agent_writing_number,
#             "agent_name": r.agent_name,
#             "lives": r.lives,
#             "comm_rate": r.comm_rate,
#             "comm_amt": r.comm_amt,
#             "override_rate": r.override_rate,
#             "override_amt": r.override_amt,
#             "agent_level": r.agent_level,
#             "agent_rate": r.agent_rate,
#             "agent_pay": r.agent_pay,
#             "assignee_npn": r.a_npn,
#             "assignee_name": r.assignee_name,
#             "assignee_rate": r.a_rate,
#             "assignee_pay": r.a_pay,
#             "account": r.acct,
#             "market": r.market,
#             "policy_eff_date": r.policy_eff_date,
#             "statement_date": r.statement_date,
#         }
#         for r in results
#     ]

#     return {
#         "total_count": total_records if total_records is not None else len(records),
#         "page": filters.page if filters.view_type.lower() == "dashboard" else None,
#         "page_size": filters.page_size if filters.view_type.lower() == "dashboard" else None,
#         "items": records,
#     }

@router.post("/commission/comm-items")
def get_commission_items(
    filters: CommItemsFiltersSchema,
    db: Session = Depends(get_db),
):
    query = db.query(CommissionItem).filter(CommissionItem.job_id == filters.job_id)
    if filters.selected_agent_npns:
        query = query.filter(CommissionItem.npn.in_(filters.selected_agent_npns))
    if filters.selected_agent_names:
        query = query.filter(CommissionItem.agent_name.in_(filters.selected_agent_names))
    if filters.selected_payment_types:
        query = query.filter(CommissionItem.payment_type.in_(filters.selected_payment_types))

    sort_attr = getattr(CommissionItem, filters.sort_column, CommissionItem.agent_name)
    query = query.order_by(sort_attr.desc() if filters.sort_order.lower() == "desc" else sort_attr.asc())

    total_count = None
    if filters.view_type.lower() == "dashboard":
        total_count = db.query(func.count()).select_from(query.subquery()).scalar()
        query = query.offset((filters.page - 1) * filters.page_size).limit(filters.page_size)

    items = query.all()
    return {
        "total_count": total_count if total_count is not None else len(items),
        "page": filters.page if filters.view_type.lower() == "dashboard" else None,
        "page_size": filters.page_size if filters.view_type.lower() == "dashboard" else None,
        "items": [
            {
                "job_id": i.job_id,
                "npn": i.npn,
                "agent_name": i.agent_name,
                "payment_type": i.payment_type,
                "payment": i.payment,
                "effective_date": i.effective_date,
                "coverage_month": i.coverage_month,
                "plan": i.plan,
                "lives": i.lives,
            }
            for i in items
        ],
    }

@router.get("/commission/comm-items-filters/{job_id}")
def get_commission_item_filters(
    job_id: str,
    db: Session = Depends(get_db),
):
    query = db.query(
        distinct(CommissionItem.npn).label("agent_npn"),
        CommissionItem.agent_name
    ).filter(CommissionItem.job_id == job_id)

    npns = query.all()

    npns_list = [
            {
                "id": row[0],
                "value": f"{row[0]} - {row[1] or ''}".strip(),
            }
            for row in npns
            if row[0]
        ]


    query = (
        db.query(
            distinct(CommissionItem.payment_type),
        )
        .filter(CommissionItem.job_id == job_id)
    )

    payments = query.all()
    payment_types = [
            {
                "id": row[0],
                "value": row[0]
            }
            for row in payments
            if row[0]
        ]

    return {
        "agent_npns": npns_list,
        "payment_types": payment_types
    }

@router.post("/commission/comm-totals")
def get_paged_commission_totals(
    filters: CommTotalsFiltersSchema,
    db: Session = Depends(get_db),
):

    query = db.query(ComTotals).filter(ComTotals.job_id == filters.job_id)
    if filters.selected_agent_npns:
        query = query.filter(ComTotals.npn.in_(filters.selected_agent_npns))

    if filters.selected_agent_names:
        query = query.filter(ComTotals.agent_name.in_(filters.selected_agent_names))

    if filters.selected_payment_types:
        query = query.filter(ComTotals.payment_type.in_(filters.selected_payment_types))

    sort_attr = getattr(ComTotals, filters.sort_column, ComTotals.job_id)
    query = query.order_by(sort_attr.desc() if filters.sort_order.lower() == "desc" else sort_attr.asc())

    total_count = None
    if filters.view_type.lower() == "dashboard":
        total_count = db.query(func.count()).select_from(query.subquery()).scalar()
        query = query.offset((filters.page - 1) * filters.page_size).limit(filters.page_size)

    results = query.all()
    return {
        "total_count": total_count if total_count is not None else len(results),
        "page": filters.page if filters.view_type.lower() == "dashboard" else None,
        "page_size": filters.page_size if filters.view_type.lower() == "dashboard" else None,
        "items": [
            {
                "job_id": r.job_id,
                "npn": r.npn,
                "agent_name": r.agent_name,
                "payment_type": r.payment_type,
                "statement_month": r.statement_month,
                "statement_total": r.statement_total,
                "status": r.status,
                "status_date": r.status_date,
            }
            for r in results
        ],
    }

@router.get("/commission/comm-totals-filters/{job_id}")
def get_commission_totals_filters(
    job_id: str,
    db: Session = Depends(get_db),
):
    query = db.query(
        distinct(ComTotals.npn).label("agent_npn"),
        ComTotals.agent_name
    ).filter(ComTotals.job_id == job_id)

    npns = query.all()

    npns_list = [
            {
                "id": row[0],
                "value": f"{row[0]} - {row[1] or ''}".strip(),
            }
            for row in npns
            if row[0]
        ]


    query = (
        db.query(
            distinct(ComTotals.payment_type),
        )
        .filter(ComTotals.job_id == job_id)
    )

    payments = query.all()
    payment_types = [
            {
                "id": row[0],
                "value": row[0]
            }
            for row in payments
            if row[0]
        ]

    return {
        "agent_npns": npns_list,
        "payment_types": payment_types
    }

@router.post("/commission/comm-totals-erp/com-totals-lob")
def get_commission_totals_lob(
    filters: CommTotalsFiltersSchema,
    db: Session = Depends(get_db),
):

    sort_attr = getattr(ComTotalsLob, filters.sort_column)
    query = db.query(ComTotalsLob).filter(ComTotalsLob.job_id == filters.job_id)

    if filters.selected_agent_npns:
        query = query.filter(ComTotalsLob.npn.in_(filters.selected_agent_npns))
    if filters.selected_agent_names:
        query = query.filter(ComTotalsLob.agent_name.in_(filters.selected_agent_names))
    if filters.selected_payment_types:
        query = query.filter(ComTotalsLob.payment_type.in_(filters.selected_payment_types))
    if filters.selected_lobs:
        query = query.filter(ComTotalsLob.lob_abbv.in_(filters.selected_lobs))

    query = query.order_by(sort_attr.desc() if filters.sort_order.lower() == "desc" else sort_attr.asc())

    total_count = None
    if filters.view_type.lower() == "dashboard":
        total_count = db.query(func.count()).select_from(query.subquery()).scalar()
        query = query.offset((filters.page - 1) * filters.page_size).limit(filters.page_size)

    items = query.all()
    return {
        "total_count": total_count if total_count is not None else len(items),
        "page": filters.page if filters.view_type.lower() == "dashboard" else None,
        "page_size": filters.page_size if filters.view_type.lower() == "dashboard" else None,
        "items": [
            {
                "job_id": item.job_id,
                "statement_month": item.statement_month,
                "npn": item.npn,
                "agent_name": item.agent_name,
                "market": item.market,
                "lob_abbv": item.lob_abbv,
                "lob_description": item.lob_description,
                "payment_type": item.payment_type,
                "associated_statement": item.associated_statement,
                "statement_total": item.statement_total,
                "status": item.status,
                "status_date": item.status_date,
            }
            for item in items
        ],
    }

@router.get("/commission/comm-totals-erp/com-totals-lob-filters/{job_id}")
def get_commission_totals_lob_filters(
    job_id: str,
    db: Session = Depends(get_db),
):
    query = db.query(
        distinct(ComTotalsLob.npn).label("agent_npn"),
        ComTotalsLob.agent_name
    ).filter(ComTotalsLob.job_id == job_id)

    npns = query.all()

    npns_list = [
            {
                "id": row[0],
                "value": f"{row[0]} - {row[1] or ''}".strip(),
            }
            for row in npns
            if row[0]
        ]


    query = (
        db.query(
            distinct(ComTotalsLob.payment_type),
        )
        .filter(ComTotalsLob.job_id == job_id)
    )

    payments = query.all()
    payment_types = [
            {
                "id": row[0],
                "value": row[0]
            }
            for row in payments
            if row[0]
        ]
    
    lobquery = (
        db.query(
            distinct(ComTotalsLob.lob_abbv),
        )
        .filter(ComTotalsLob.job_id == job_id)
    )

    lobs = lobquery.all()
    lob = [
            {
                "id": row[0],
                "value": row[0]
            }
            for row in lobs
            if row[0]
        ]

    return {
        "agent_npns": npns_list,
        "payment_types": payment_types,
        "lob": lob
    }

@router.post("/commission/calculations/com-calcs")
def get_commission_calcs(
    filters: ComCalsFiltersSchema,
    db: Session = Depends(get_db),
):
    # Column map (same as .NET _comCalcColumnMap)
    sort_column_map = {
        "agent_name": CommissionCalcs.agent_name,
        "agent_npn": CommissionCalcs.agent_npn,
        "txn_status": CommissionCalcs.txn_status,
        "writing_number": CommissionCalcs.agent_writing_number,
    }

    # default sort column = agent_name (same as .NET fallback)
    sort_col = sort_column_map.get(filters.sort_column.lower(), CommissionCalcs.agent_name)

    sort_order = filters.sort_order.upper()
    order_by_clause = sort_col.desc() if sort_order == "DESC" else sort_col.asc()

    # base where
    base_query = db.query(CommissionCalcs).filter(CommissionCalcs.job_id == filters.job_id)

    # ====== MATCH .NET OR LOGIC FOR NPN FILTER ======
    if filters.selected_agent_npns:
        base_query = base_query.filter(
            or_(
                CommissionCalcs.agent_npn.in_(filters.selected_agent_npns),
                CommissionCalcs.agent_writing_number.in_(filters.selected_agent_npns)
            )
        )

    # ====== NAME FILTER ======
    if filters.selected_agent_name:
        base_query = base_query.filter(CommissionCalcs.agent_name.in_(filters.selected_agent_name))

    # ====== TRANSACTION STATUS FILTER ======
    if filters.selected_transaction_statuses:
        base_query = base_query.filter(CommissionCalcs.txn_status.in_(filters.selected_transaction_statuses))

    # ====== TOTAL COUNT (same as .NET countQuery) ======
    total_count = base_query.with_entities(func.count()).scalar()

    # ====== Pagination only for dashboard ======
    if filters.view_type.lower() == "dashboard":
        page = max(filters.page, 1)
        page_size = max(min(filters.page_size, 500), 10)
        offset = (page - 1) * page_size

        q = base_query.order_by(order_by_clause).offset(offset).limit(page_size)
    else:
        # download mode = return all data (no pagination)
        q = base_query.order_by(order_by_clause)

    records = q.all()

    return {
        "total_count": total_count,
        "page": filters.page,
        "page_size": filters.page_size,
        "items": records
    }

@router.get("/commission/calculations/com-calcs-filters/{job_id}")
def get_comcalc_filters(
    job_id: str,
    # page: int = Query(1),
    # page_size: int = Query(100),
    db: Session = Depends(get_db)):
    """
    Returns distinct AgentNpn and AgentName for a given transaction ID.
    """
    query = (
        db.query(
            distinct(CommissionCalcs.agent_npn).label("agent_npn"),
            CommissionCalcs.agent_name.label("agent_name")
        )
        .filter(CommissionCalcs.job_id == job_id)
    )

    # offset = (page - 1) * page_size
    # query = query.order_by(CommissionCalcs.agent_npn.asc()).offset(offset).limit(page_size)

    results = query.all()

    # Convert to list of dicts or schema objects
    npns_list = [
            {
                "id": row[0],
                "value": f"{row[0]} - {row[1] or ''}".strip(),
            }
            for row in results
            if row[0]
        ]

    # return {
    #     "total_count": len(npns_list),
    #     "page": page,
    #     "page_size": page_size,
    #     "items": npns_list
    # }
    return npns_list

@router.get("/commission/calculations/com-overrides/{job_id}")
def get_commission_overrides(
    job_id: str,
    db: Session = Depends(get_db)
):
    result = db.query(CommissionOverrides).filter(CommissionOverrides.txn_id_com_header == job_id).all()

    return result

@router.post("/commission/exceptions/com-exceptions")
def get_commission_exceptions(
    filters: ComExceptionsFiltersSchema,
    db: Session = Depends(get_db),
):
    sort_column_map = {
        "total_contracts": CommissionExceptionSummary.total_contracts,
        # "ex_code": CommissionExceptionSummary.ex_code,
        # "car_prod_writing_num": CommissionExceptionSummary.car_prod_writing_num,
        # "car_prod_name": CommissionExceptionSummary.car_prod_name,
    }
    sort_col = sort_column_map.get(filters.sort_column.lower(), CommissionExceptionSummary.total_contracts)
    sort_order = filters.sort_order.upper()
    order_by_clause = sort_col.desc() if sort_order == "DESC" else sort_col.asc()

    base_query = db.query(CommissionExceptionSummary).filter(CommissionExceptionSummary.job_id == filters.job_id)

    if filters.selected_agent_npns:
        base_query = base_query.filter(
            or_(
                CommissionExceptionSummary.car_prod_npn.in_(filters.selected_agent_npns),
            )
        )

    if filters.selected_agent_name:
        base_query = base_query.filter(CommissionExceptionSummary.car_prod_name.in_(filters.selected_agent_name))

    if filters.selected_exception_codes:
        base_query = base_query.filter(CommissionExceptionSummary.ex_code.in_(filters.selected_exception_codes))

    if filters.selected_writing_numbers:
        base_query = base_query.filter(CommissionExceptionSummary.car_prod_writing_num.in_(filters.selected_writing_numbers))


    total_count = base_query.with_entities(func.count()).scalar()

    if filters.view_type.lower() == "dashboard":
        page = max(filters.page, 1)
        page_size = max(min(filters.page_size, 500), 10)
        offset = (page - 1) * page_size

        q = base_query.order_by(order_by_clause).offset(offset).limit(filters.page_size)

    else:
        q = base_query.order_by(order_by_clause)

    records = q.all()

    return {
        "total_count": total_count,
        "page": filters.page,
        "page_size": filters.page_size,
        "items": records
    }

@router.patch("/commission/exceptions/{job_id}/{exception_id}/fix")
def fix_commission_exception(
    job_id: str,
    exception_id: UUID,
    db: Session = Depends(get_db),
):
    exception = (
        db.query(CommissionExceptionSummary)
        .filter(
            CommissionExceptionSummary.job_id == job_id,
            CommissionExceptionSummary.pk_id == exception_id,
        )
        .first()
    )

    if not exception:
        raise HTTPException(
            status_code=404,
            detail="Commission exception not found"
        )

    exception.fixed = True

    db.commit()
    db.refresh(exception)

    return {
        "success": True,
        "message": "Commission exception marked as fixed",
        "pk_id": str(exception_id),
        "job_id": job_id,
        "fixed": exception.fixed,
    }

@router.get("/commission/exceptions/com-exceptions-filters/{job_id}")
def get_com_exceptions_filters(
    job_id: str,
    db: Session = Depends(get_db)
):
    npn = db.query(
        distinct(CommissionExceptionSummary.car_prod_npn).label("agent_npn"),
        CommissionExceptionSummary.car_prod_name
    ).filter(CommissionExceptionSummary.job_id == job_id)

    npns = npn.all()

    npns_list = [
            {
                "id": row[0],
                "value": f"{row[0]} - {row[1] or ''}".strip(),
            }
            for row in npns
            if row[0]
        ]
    
    writing_numbers = db.query(
        distinct(CommissionExceptionSummary.car_prod_writing_num).label("writing_number")
    ).filter(CommissionExceptionSummary.job_id == job_id)

    writing_numbers = writing_numbers.all()

    writing_list = [
            {
                "id": row[0],
                "value": row[0]
            }
            for row in writing_numbers
            if row[0]
        ]

    exception_codes = db.query(
        distinct(CommissionExceptionSummary.ex_code)
    ).filter(CommissionExceptionSummary.job_id == job_id)

    exception_codes = exception_codes.all()

    exception_list = [
            {
                "id": row[0],
                "value": row[0]
            }
            for row in exception_codes
            if row[0]
        ]


    return {
        "agent_npns": npns_list,
        "writing_numbers": writing_list,
        "exception_codes": exception_list
    }

@router.post("/commission/exceptions/com-calcs-from-header")
def get_commission_calcs_from_header(
    filters: ComCalcsFromHeadersFiltersSchema,
    db: Session = Depends(get_db)
):
    query = db.query(CommissionHeader)

    # ===== Filter by NPN OR Writing Number =====
    if filters.selected_agent_npns:
        query = query.filter(
            or_(
                CommissionHeader.car_prod_npn.in_(filters.selected_agent_npns),
                CommissionHeader.car_prod_writing_num.in_(filters.selected_agent_npns)
            )
        )

    # ===== Filter by Transaction Status =====
    if filters.selected_transaction_statuses:
        query = query.filter(
            CommissionHeader.txn_status.in_(filters.selected_transaction_statuses)
        )

    # ===== Filter by Report Date =====
    if filters.report_date:
        query = query.filter(CommissionHeader.report_date.contains(filters.report_date))

    # ===== Total count before pagination =====
    total_count = query.with_entities(func.count()).scalar()

    # ===== Pagination only in dashboard mode =====
    if filters.view_type.lower() == "dashboard":
        page = max(filters.page, 1)
        page_size = max(min(filters.page_size, 500), 10)
        offset = (page - 1) * page_size

        query = query.order_by(CommissionHeader.job_id.asc()).offset(offset).limit(page_size)
    else:
        # download mode → no pagination, return all rows
        query = query.order_by(CommissionHeader.job_id.asc())

    from sqlalchemy.dialects import postgresql
    sql = query.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True}
    )
    print("SQL QUERY:", sql)

    records = query.all()

    items: List[CommissionCalcs] = [
        CommissionCalcs(
            job_id=r.job_id,
            txn_status=r.txn_status,
            txn_id_com_header=r.txn_id,
            company_id=r.company_id,
            company_name=r.company_name,
            carrier_id=r.carrier_id,
            carrier_name=r.carrier_name,
            acct=r.car_prod_name,
            statement_date=r.car_statement_date,
            market=r.car_market,
            lives=r.car_policy_live_cnt,
            payable=r.car_com_amt,
            agent_npn=r.car_prod_npn,
            agent_writing_number=r.car_prod_writing_num,
            agent_name=r.car_prod_name,
            policy_eff_date=r.car_policy_eff_date,
            coverage_month=r.car_policy_year,
            split=str(r.car_com_split_rate),
            premium=r.car_policy_prem_amt,
            num_days=0,
            num_months=r.car_pol_mnths,
            comm_rate=r.car_com_rate if r.car_commission_type != "OR" else 0,
            comm_amt=r.car_com_amt if r.car_commission_type != "OR" else 0,
            override_rate=r.car_com_rate if r.car_commission_type == "OR" else 0,
            override_amt=r.car_com_amt if r.car_commission_type == "OR" else 0,
            report_date=r.report_date,
            load_date=r.load_date,
            raw_file_name=r.raw_file_name,
        )
        for r in records
    ]

    return {
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "items": items
    }

@router.get("/commission/exceptions/agent-npn")
def get_npn_by_writitng_number(
    db: Session = Depends(get_db),
    writing_number: str = Query(None),
):
    query = db.query(
        AgentContracts.npn
    ).filter(AgentContracts.writing_number == writing_number)
    result = query.first()
    return {"npn": result.npn if result else None}

@router.get("/commission/exceptions/agent-list/{entity_id}/{value}")
def get_agent_list(
    entity_id: str,
    value: str,
    db: Session = Depends(get_db)
):
    if not value:
        return []

    pattern = f"%{value}%"
    reverse_pattern = None

    # Handle "first last" vs "last first"
    if " " in value.strip():
        parts = value.split()
        if len(parts) == 2:
            reverse_pattern = f"%{parts[1]} {parts[0]}%"

    # Base query
    stmt = select(
        AgentMasterContracts.npn.label("value"),
        func.min(AgentMasterContracts.agent_name).label("name")  # pick one name per NPN
    )

    # Apply filters
    conditions = []
    if entity_id:
        conditions.append(AgentMasterContracts.company_id == entity_id)

    if reverse_pattern:
        conditions.append(
            func.lower(AgentMasterContracts.agent_name).like(func.lower(pattern)) |
            func.lower(AgentMasterContracts.agent_name).like(func.lower(reverse_pattern)) |
            func.lower(AgentMasterContracts.npn).like(func.lower(pattern))
        )
    else:
        conditions.append(
            func.lower(AgentMasterContracts.agent_name).like(func.lower(pattern)) |
            func.lower(AgentMasterContracts.npn).like(func.lower(pattern))
        )

    stmt = stmt.where(*conditions)

    # Group by NPN, order, and limit
    stmt = (
        stmt.group_by(AgentMasterContracts.npn)
        .order_by(func.min(AgentMasterContracts.agent_name))
        .limit(50)
    )

    # Execute
    result =  db.execute(stmt)
    agents = result.all()

    agent_list = [
        {
            "id": agent.value,
            "value": f"{agent.name} ({agent.value or ''})".strip(),
        }
        for agent in agents
    ]

    # Convert into Pydantic models
    return agent_list

@router.post("/commission/statements/ops-rpa-script-logs")
def get_ops_rpa_script_logs(
    filters: OpsRpaScriptLogsFilterSchema,
    db: Session = Depends(get_db)
):

    query = (
        db.query(
            OpsRpaScriptLogs.script_name,
            OpsRpaScriptLogs.start_datetime,
            OpsRpaScriptLogs.end_datetime,
            OpsRpaScriptLogs.error,
            OpsRpaScriptLogs.success,
            OpsRpaScriptLogs.file_status,
            OpsRpaScriptLogs.file_path,
            OpsRpaScriptLogs.process_type,
            OpsRpaScriptLogs.file_report_month,
            OpsRpaScriptLogs.file_com_month,
            OpsRpaScriptLogs.company_id,
            OpsRpaScriptLogs.carrier_id,
            OpsRpaScriptLogs.product_name,
            Entity.entity_name.label("name"),
            CarrierShort.carrier_short_name.label("vendor_name"),
            CarrierShort.vendor_name.label("vendor_name_full")
        )
        .join(Entity, OpsRpaScriptLogs.company_id == Entity.entity_id)
        .join(CarrierShort, OpsRpaScriptLogs.carrier_id == CarrierShort.id)
        .filter(
            OpsRpaScriptLogs.process_type == "COM",
            OpsRpaScriptLogs.file_status == "Ready",
            OpsRpaScriptLogs.company_id.isnot(None),
            OpsRpaScriptLogs.carrier_id.isnot(None)
        )
        
    )

    query = query.group_by(
        OpsRpaScriptLogs.script_name,
        OpsRpaScriptLogs.start_datetime,
        OpsRpaScriptLogs.end_datetime,
        OpsRpaScriptLogs.error,
        OpsRpaScriptLogs.success,
        OpsRpaScriptLogs.file_status,
        OpsRpaScriptLogs.file_path,
        OpsRpaScriptLogs.process_type,
        OpsRpaScriptLogs.file_report_month,
        OpsRpaScriptLogs.file_com_month,
        OpsRpaScriptLogs.company_id,
        OpsRpaScriptLogs.carrier_id,
        OpsRpaScriptLogs.product_name,
        Entity.entity_name,
        CarrierShort.carrier_short_name,
        CarrierShort.vendor_name
    )

    if filters.companyId:
        query = query.filter(OpsRpaScriptLogs.company_id.in_(filters.companyId))

    if filters.carrierId:
        query = query.filter(OpsRpaScriptLogs.carrier_id.in_(filters.carrierId))

    if filters.product:
        query = query.filter(OpsRpaScriptLogs.product_name.in_(filters.product))

    if filters.reportDate:
        query = query.filter(OpsRpaScriptLogs.file_report_month == filters.reportDate)

    if filters.commMonth:
        query = query.filter(OpsRpaScriptLogs.file_com_month == filters.commMonth)

    query = query.order_by(desc(OpsRpaScriptLogs.start_datetime))

    results = query.all()

    data = [
        {
            "script_name": r.script_name,
            "start_datetime": r.start_datetime,
            "end_datetime": r.end_datetime,
            "error": r.error,
            "success": r.success,
            "file_status": r.file_status,
            "file_path": r.file_path,
            "process_type": r.process_type,
            "file_report_month": r.file_report_month,
            "file_com_month": r.file_com_month,
            "company_id": r.company_id,
            "carrier_id": r.carrier_id,
            "product_name": r.product_name,
            "name": r.name or "",
            "vendor_name": r.vendor_name or "",
            "vendor_name_full": r.vendor_name_full or ""
        }
        for r in results
    ]

    return {"count": len(data), "items": data}

@router.post("/commission/statements/run-commission")
async def run_commission(payload: RunCommissionRequest):
    """
    Trigger the Azure Function 'run_commission' pipeline.
    """
    try:
        azure_functions_base_url = settings.COMMISSIONS_AZURE_FUNCTION_URL
        functions_key = settings.COMMISSIONS_AZURE_FUNCTION_KEY

        # URL-encode string params safely
        url = (
            f"{azure_functions_base_url}/pipeline_run"
            f"?pipeline_name=run_commission"
            f"&report_date={urllib.parse.quote(payload.report_date)}"
            f"&statement_date={urllib.parse.quote(payload.statement_date)}"
            f"&commission_status={urllib.parse.quote(payload.commission_status)}"
            f"&carrier_name={urllib.parse.quote(payload.carrier_name)}"
            f"&company_id={urllib.parse.quote(payload.company_id)}"
            f"&company_name={urllib.parse.quote(payload.company_name)}"
            f"&entity_affiliation={urllib.parse.quote(payload.entity_affiliation)}"
            f"&file_name={urllib.parse.quote(payload.file_name)}"
            f"&job_owner_name={urllib.parse.quote(payload.login_name)}"
            f"&job_owner_email={urllib.parse.quote(payload.login_email)}"
        )

        headers = {"x-functions-key": functions_key}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json={})
            response_content = response.text

        if response.is_success:
            return {"status": "success", "data": response_content}

        logger.error(
            f"Error calling Azure Function | Status: {response.status_code}, Response: {response_content}"
        )
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Error calling Azure Function: {response_content}",
        )

    except Exception as e:
        logger.exception("Exception in run_commission API")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/commission/statements/archive-file")
def archive_commission_file(file_path: str, db: Session = Depends(get_db)):
    file_path = unquote(file_path)
    blob_service_client = get_blob_service_client()
    container_name = settings.ATTACHMENT_CONTAINER_NAME
    container_client = blob_service_client.get_container_client(container_name)

    try:
        # Ensure the container exists
        if not container_client.exists():
            raise HTTPException(
                status_code=404, detail=f"Container '{container_name}' not found"
            )

        directory = os.path.dirname(file_path).replace("\\", "/")
        file_name = os.path.basename(file_path)
        archive_directory = f"{directory}/archive" if directory else "archive"

        source_blob_client = container_client.get_blob_client(file_path)
        destination_blob_client = container_client.get_blob_client(
            f"{archive_directory}/{file_name}"
        )

        # Check source blob existence
        source_exists = source_blob_client.exists()

        if source_exists:
            copy_operation = destination_blob_client.start_copy_from_url(
                source_blob_client.url
            )
            logger.info(f"Started copying blob '{file_name}' to '{archive_directory}/'")

            if copy_operation["copy_status"] == "success":
                source_blob_client.delete_blob()
                logger.info(f"Blob '{file_name}' moved successfully to archive folder.")
        else:
            logger.warning(f"Source blob not found: {file_path}. Updating DB only.")

        existing_records = (
            db.query(OpsRpaScriptLogs)
            .filter(
                OpsRpaScriptLogs.file_path == file_path,
                OpsRpaScriptLogs.file_status == "Ready",
            )
            .all()
        )

        for record in existing_records:
            record.file_status = "Archive"

        db.commit()

        return {"success": True, "archivedFilePath": f"{archive_directory}/{file_name}"}

    # except ResourceNotFoundError:
    #     raise HTTPException(status_code=404, detail="Source file not found")
    except AzureError as e:
        logger.error(f"Azure error while archiving file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Azure Blob error: {str(e)}")
    except Exception as e:
        logger.exception(f"Unexpected error while archiving '{file_path}': {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get("/commission/upload/raw-file-name")
def get_raw_file_name(
    carrier: str,
    product: str,
    db: Session = Depends(get_db)
):
    stmt = (
        select(
            distinct(OpsLoadMatrixCom.raw_file_name_prefix).label("raw_file_name_prefix"),
            case(
                (OpsLoadMatrixCom.raw_file_required == '1', True),
                else_=False
            ).label("raw_file_required")
        )
        .join(
            CarrierShort,
            OpsLoadMatrixCom.carrier_name == CarrierShort.vendor_name
        )
        .where(
            CarrierShort.carrier_short_name == carrier,
            OpsLoadMatrixCom.contract_type == product
        )
    )

    rows = db.execute(stmt).all()

    return [
        {
            "raw_file_name_prefix": row.raw_file_name_prefix,
            "raw_file_required": row.raw_file_required
        }
        for row in rows
    ]

@router.post("/commission/upload/count")
async def get_count_by_file_path_and_status(
    file_path: str,
    status: str,
    db: Session = Depends(get_db)
):
    decoded_file_path = unquote(file_path)
    print(decoded_file_path)
    stmt = (
        select(func.count())
        .select_from(OpsRpaScriptLogs)
        .where(
            OpsRpaScriptLogs.file_path == file_path,
            OpsRpaScriptLogs.file_status == status
        )
    )

    result = db.execute(stmt).scalar()
    return {"count": result}

@router.post("/commission/upload/file")
async def upload_file(
    directory: str = Form(...),
    fileName: str = Form(...),
    file: UploadFile = File(...)
):
    if not directory:
        return "Directory parameter is required."
    if not fileName:
        return "FileName parameter is required."
    if file is None or file.filename == "":
        return "File is required."

    try:
        directory = unquote(directory)

        blob_path = f"raw/{directory}/{fileName}".replace("//", "/")

        blob_client = settings.blob_service_client.get_blob_client(
            container=settings.ATTACHMENT_CONTAINER_NAME,
            blob=blob_path,
        )

        file_data = await file.read()
        blob_client.upload_blob(file_data, overwrite=True)

        return JSONResponse({
            "success": True,
            "message": f"File '{fileName}' uploaded successfully.",
            "path": blob_path
        })

    except Exception as e:
        logger.exception("Unexpected error while uploading the file.")
        raise HTTPException(status_code=500, detail="Unexpected internal server error.")
    
@router.post("/commission/upload/file-search")
async def list_files(directory: str = Query(..., description="Directory prefix in Blob Storage")):
    """
    List all CSV and XLSX files from a given directory prefix in Azure Blob Storage.
    """
    try:
        container_client = settings.blob_service_client.get_container_client(settings.ATTACHMENT_CONTAINER_NAME)

        # Check if the container exists
        if not container_client.exists():
            logging.error(f"Container '{settings.ATTACHMENT_CONTAINER_NAME}' does not exist.")
            raise HTTPException(status_code=404, detail=f"Container '{settings.ATTACHMENT_CONTAINER_NAME}' not found")

        blob_list = container_client.list_blobs(name_starts_with=directory)
        files = []

        for blob in blob_list:
            if blob.name.endswith(".csv") or blob.name.endswith(".xlsx"):
                files.append(blob.name)

        if not files:
            logging.info(f"No matching files found in directory: {directory}")
            return []

        return files

    except Exception as e:
        logging.exception("Error occurred while listing files.")
        raise HTTPException(status_code=500, detail=f"Error listing files: {str(e)}")

@router.post("/commission/ops-rpa-script-logs/add")
def add_ops_rpa_script_log(
    payload: OpsRpaScriptLogsAddSchema,
    db: Session = Depends(get_db)
):
    new_log = OpsRpaScriptLogs(
        script_name=payload.script_name,
        start_datetime=payload.start_datetime,
        error=payload.error,
        success=payload.success,
        file_status=payload.file_status,
        file_path=payload.file_path,
        process_type=payload.process_type,
        file_report_month=payload.file_report_month,
        file_com_month=payload.file_com_month,
        company_id=payload.company_id,
        carrier_id=payload.carrier_id,
        product_name=payload.product_name
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)
    return new_log


@router.get("/commission/contracts/filters")
def get_commission_contract_filters(
    company_id: str = Query(..., description="Company ID"),
    plan_year: Optional[str] = Query(..., description="Plan Year"),
    db: Session = Depends(get_db),
):
    """
    Returns 4 filter dropdowns for commission contracts:
    - carriers
    - level_categories
    - payment_types
    - product_names
    Based on company_id + plan_year
    """
    # Base filter
    base = db.query(ContractScheduleHeader).filter(
        ContractScheduleHeader.company_id == company_id
    )

    if plan_year:
        base = base.filter(
            ContractScheduleHeader.plan_year == plan_year
        )

    # 1. Carriers (carrier_id + carrier_name)
    carriers = (
        base
        .filter(ContractScheduleHeader.carrier_id.is_not(None))
        .with_entities(
            ContractScheduleHeader.carrier_id,
            ContractScheduleHeader.carrier_name
        )
        .distinct()
        .order_by(ContractScheduleHeader.carrier_name)
        .all()
    )

    # 2. Level Categories
    level_categories = (
        base
        .filter(ContractScheduleHeader.level_cat.is_not(None))
        .with_entities(ContractScheduleHeader.level_cat)
        .distinct()
        .order_by(ContractScheduleHeader.level_cat)
        .all()
    )

    # 3. Payment Types
    payment_types = (
        base
        .filter(ContractScheduleHeader.payment_type.is_not(None))
        .with_entities(ContractScheduleHeader.payment_type)
        .distinct()
        .order_by(ContractScheduleHeader.payment_type)
        .all()
    )

    # 4. Product Names
    product_names = (
        base
        .filter(ContractScheduleHeader.product_name.is_not(None))
        .with_entities(ContractScheduleHeader.product_name)
        .distinct()
        .order_by(ContractScheduleHeader.product_name)
        .all()
    )

    return {
        "carriers": [
            {"id": c.carrier_id, "value": c.carrier_name or "Unknown"}
            for c in carriers
        ],
        "level_categories": [
            {"id": lc.level_cat, "value": lc.level_cat}
            for lc in level_categories
        ],
        "payment_types": [
            {"id": pt.payment_type, "value": pt.payment_type}
            for pt in payment_types
        ],
        "product_names": [
            {"id": pn.product_name, "value": pn.product_name}
            for pn in product_names
        ],
    }


@router.get("/commission/contracts/header-schedule-list")
def get_commission_header_schedule_list(
    company_id: str = Query(..., description="Company ID"),
    plan_year: str = Query(..., description="Plan Year"),
    
    carrier_ids: Optional[List[str]] = Query(None, description="Filter by carrier_id"),
    level_categories: Optional[List[str]] = Query(None, description="Filter by level_cat"),
    payment_types: Optional[List[str]] = Query(None, description="Filter by payment_type"),
    product_names: Optional[List[str]] = Query(None, description="Filter by product_name"),
    
    sort_column: str = Query("carrier_name", description="Column to sort by"),
    sort_order: str = Query("desc", regex="^(asc|desc)$", description="Sort direction"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    
    db: Session = Depends(get_db),
):
    """
    Get paginated list of commission schedule headers with multi-filter support.
    """
    # Base query
    query = (
        db.query(ContractScheduleHeader, Entity.entity_name.label("company_name"))
        .join(
            Entity,
            ContractScheduleHeader.company_id == Entity.entity_id
        )
        .filter(
            ContractScheduleHeader.company_id == company_id,
            ContractScheduleHeader.plan_year == plan_year
        )
    )

    # === Optional Array Filters ===
    if carrier_ids:
        query = query.filter(ContractScheduleHeader.carrier_id.in_(carrier_ids))
    
    if level_categories:
        query = query.filter(ContractScheduleHeader.level_cat.in_(level_categories))
    
    if payment_types:
        query = query.filter(ContractScheduleHeader.payment_type.in_(payment_types))
    
    if product_names:
        query = query.filter(ContractScheduleHeader.product_name.in_(product_names))

    # === Count total for pagination ===
    total = query.count()

    # === Sorting ===
    sort_map = {
        "id": ContractScheduleHeader.id,
        "carrier_id": ContractScheduleHeader.carrier_id,
        "carrier_name": ContractScheduleHeader.carrier_name,
        "company_name": Entity.entity_name,
        "payment_type": ContractScheduleHeader.payment_type,
        "level_cat": ContractScheduleHeader.level_cat,
        "product_name": ContractScheduleHeader.product_name,
        "load_date": ContractScheduleHeader.load_date,
        "plan_year": ContractScheduleHeader.plan_year,
        "or_schedule_id": ContractScheduleHeader.or_schedule_id,
    }

    order_column = sort_map.get(sort_column.lower())
    if not order_column:
        order_column = ContractScheduleHeader.carrier_name  # default fallback

    if sort_order.lower() == "asc":
        query = query.order_by(asc(order_column))
    else:
        query = query.order_by(desc(order_column))

    # === Pagination ===
    items = (
        query
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # === Transform to dict (clean output) ===
    result_items = [
        {
            "pk_id": str(item.pk_id),
            "id": item.id or "",
            "company_id": item.company_id or "",
            "company_name": company_name or "",
            "carrier_id": item.carrier_id or "",
            "carrier_name": item.carrier_name or "",
            "seq_id": item.seq_id or "",
            "or_schedule_id": item.or_schedule_id or "",
            "plan_year": item.plan_year or "",
            "payment_type": item.payment_type or "",
            "or_system": item.or_system or "",
            "product_name": item.product_name or "",
            "level_cat": item.level_cat or "",
            "load_date": item.load_date or "",
        }
        for item, company_name in items
    ]

    return {
        "items": result_items,
        "total_count": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/commission/contracts/header-schedule")
def create_or_update_header_schedule(
    payload: ContractScheduleHeaderCreateUpdate,
    db: Session = Depends(get_db),
):
    """
    Create or update commission schedule header.
    - If id == "0" → create new with auto seq_id + or_schedule_id
    - Else → update existing by id
    - Prevents duplicate: (company_id, carrier_id, payment_type, product_name, level_cat)
    """
    # Normalize inputs
    payload_dict = payload.dict()
    id_val = payload_dict.pop("id")
    is_create = id_val == "0"

    # === Duplicate Check (only on create or if key fields changed) ===
    duplicate_check = db.query(ContractScheduleHeader).filter(
        ContractScheduleHeader.company_id == payload.company_id,
        ContractScheduleHeader.carrier_id == payload.carrier_id,
        ContractScheduleHeader.payment_type == payload.payment_type,
        ContractScheduleHeader.product_name == payload.product_name,
        ContractScheduleHeader.level_cat == payload.level_cat,
    )

    if is_create:
        # On create: must not exist
        if db.query(duplicate_check.exists()).scalar():
            raise HTTPException(
                status_code=409,
                detail="Duplicate schedule exists with same company, carrier, payment type, product, and level category"
            )
    else:
        # On update: allow if it's the same record
        duplicate_check = duplicate_check.filter(ContractScheduleHeader.id != id_val)
        if db.query(duplicate_check.exists()).scalar():
            raise HTTPException(
                status_code=409,
                detail="Another schedule already exists with these key values"
            )

    if is_create:
        # === Generate next seq_id ===
        max_id = db.query(func.max(func.cast(ContractScheduleHeader.id, Integer)))\
                    .filter(ContractScheduleHeader.id.regexp_match(r'^\d+$')).scalar() or 0
        next_id = str(int(max_id) + 1)

        max_seq = db.query(func.max(func.cast(ContractScheduleHeader.seq_id, Integer)))\
                    .filter(ContractScheduleHeader.seq_id.regexp_match(r'^\d+$')).scalar() or 0
        next_seq = str(int(max_seq) + 1)
        seq_id = next_seq
        or_schedule_id = f"ORS-{next_seq.zfill(10)[-9:]}"

        # Create new record
        new_header = ContractScheduleHeader(
            id=next_id,
            company_id=payload.company_id,
            company_name=payload.company_name,
            carrier_id=payload.carrier_id,
            carrier_name=payload.carrier_name,
            seq_id=seq_id,
            or_schedule_id=or_schedule_id,
            plan_year=payload.plan_year,
            payment_type=payload.payment_type,
            product_name=payload.product_name,
            level_cat=payload.level_cat,
            or_system="agility-new",
            load_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        db.add(new_header)
        db.flush()  # to get pk_id if needed

        return {
            "success": True,
            "action": "created",
            "id": new_header.id or "generated",
            "seq_id": seq_id,
            "or_schedule_id": or_schedule_id,
            "message": "Schedule created successfully"
        }

    else:
        # === Update existing ===
        header = db.query(ContractScheduleHeader)\
            .filter(ContractScheduleHeader.id == id_val)\
            .first()

        if not header:
            raise HTTPException(status_code=404, detail="Schedule not found")

        # Update fields
        for key, value in payload_dict.items():
            if key != "id":
                setattr(header, key, value)

        header.or_system = "agility"
        header.load_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return {
            "success": True,
            "action": "updated",
            "id": header.id,
            "or_schedule_id": header.or_schedule_id,
            "message": "Schedule updated successfully"
        }

@router.delete("/commission/contracts/{or_schedule_id}")
def delete_contract_schedule(
    or_schedule_id: str,
    id: Optional[str] = Query(None, description="Header ID (optional extra filter)"),
    db: Session = Depends(get_db),
):
    """
    Delete commission schedule header + details.
    - or_schedule_id: required (path)
    - id: optional (query param) → extra safety
    - Fully atomic transaction
    """
    if not or_schedule_id or or_schedule_id.strip() == "":
        raise HTTPException(
            status_code=400,
            detail="or_schedule_id is required"
        )

    or_schedule_id = or_schedule_id.strip()

    with db.begin():
        # === 1. Delete details (only by or_schedule_id) ===
        details_deleted = (
            db.query(ContractScheduleDetail)
            .filter(ContractScheduleDetail.or_schedule_id == or_schedule_id)
            .delete()
        )

        # === 2. Delete header: with optional id filter ===
        header_query = db.query(ContractScheduleHeader).filter(
            ContractScheduleHeader.or_schedule_id == or_schedule_id
        )

        if id:
            header_query = header_query.filter(ContractScheduleHeader.id == id.strip())

        header_deleted = header_query.delete()

        # Optional: extra safety check
        if header_deleted == 0:
            raise HTTPException(
                status_code=400,
                detail=f"No header found for or_schedule_id='{or_schedule_id}'"
                + (f" and id='{id}'" if id else "")
            )

    return {
        "success": True,
        "message": "Contract schedule deleted successfully",
        "or_schedule_id": or_schedule_id,
        "header_id": id,
        "details_deleted": details_deleted,
        "header_deleted": header_deleted,
        "total_deleted": details_deleted + header_deleted,
    }


@router.get("/commission/contracts/details/{or_schedule_id}")
def get_commission_schedule_details(
    or_schedule_id: str = Path(..., description="Schedule ID to fetch details"),
    level_category: str = Query(None, description="Level Category"),
    db: Session = Depends(get_db),
):
    """
    Return commission schedule detail rows for a given or_schedule_id.
    Returns a list of detail objects (usually multiple detail lines per schedule).
    """

    if not or_schedule_id or or_schedule_id.strip() == "":
        raise HTTPException(
            status_code=400,
            detail="or_schedule_id is required"
        )

    details = (
        db.query(ContractScheduleDetail)
        .filter(
            ContractScheduleDetail.or_schedule_id == or_schedule_id.strip(),
            ContractScheduleDetail.level_cat == level_category,
        )
        .all()
    )

    if not details:
        raise HTTPException(
            status_code=404,
            detail=f"No commission schedule details found for or_schedule_id '{or_schedule_id}'"
        )

    return details

@router.delete("/commission/contracts/details/{or_detail_id}")
def delete_commission_schedule_details(
    or_detail_id: str = Path(..., description="Schedule detail ID to delete"),
    db: Session = Depends(get_db),
):
    """
    Delete all commission schedule detail rows for a given or_detail_id.
    """

    if not or_detail_id or or_detail_id.strip() == "":
        raise HTTPException(
            status_code=400,
            detail="Schedule Detail ID is required"
        )

    # Check if records exist
    query = db.query(ContractScheduleDetail).filter(
        ContractScheduleDetail.or_detail_id == or_detail_id.strip()
    )

    records = query.all()

    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"No commission schedule details found for or_detail_id '{or_detail_id}'"
        )

    deleted_count = query.delete(synchronize_session=False)
    db.commit()

    return {
        "message": "Commission schedule details deleted successfully",
        "deleted_count": deleted_count,
        "or_schedule_id": or_detail_id,
    }

@router.post("/commission/contracts/schedule-details")
def create_or_update_schedule_detail(
    payload: ContractScheduleDetailBulkRequest,
    db: Session = Depends(get_db),
):
    """
    Create or update schedule details.
    - If id = "0": create new record
    - If id != "0": update existing
    """
    if not payload.details:
        raise HTTPException(status_code=400, detail="No details provided")

    created_details = []
    updated_details = []

    # Group by or_schedule_id to keep seq_id together
    grouped = {}
    for item in payload.details:
        grouped.setdefault(item.or_schedule_id, []).append(item)

    with db.begin():

        # global numeric id
        max_id = (
            db.query(func.max(func.cast(ContractScheduleDetail.id, Integer)))
            .filter(ContractScheduleDetail.id.regexp_match(r"^\d+$"))
            .scalar()
            or 0
        )
        next_id = max_id + 1

        # seq_id cache per each or_schedule_id
        seq_cache = {}

        for or_schedule_id, items in grouped.items():

            # Find max seq_id for this parent
            max_seq = (
                db.query(func.max(func.cast(ContractScheduleDetail.seq_id, Integer)))
                .filter(
                    ContractScheduleDetail.or_schedule_id == or_schedule_id,
                    ContractScheduleDetail.seq_id.regexp_match(r"^\d+$")
                )
                .scalar()
                or 0
            )
            seq_cache[or_schedule_id] = max_seq + 1

            for item in items:

                # -----------------------------
                # UPDATE CASE
                # -----------------------------
                if item.id and item.id != "0":
                    existing = (
                        db.query(ContractScheduleDetail)
                        .filter(ContractScheduleDetail.id == item.id)
                        .first()
                    )

                    if not existing:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Detail with id {item.id} not found"
                        )

                    # Update ONLY safe fields
                    safe_fields = [
                        "company_id", "company_name",
                        "carrier_id", "carrier_name",
                        "payment_type", "level_cat", "level",
                        "territory", "rate_type", "rate_value",
                        "base_product", "carrier_base_rate",
                        "agent_base_rate", "agility_base_rate",
                        "rate_type_0", "rate_value_0",
                        "rate_type_1", "rate_value_1",
                        "rate_type_2", "rate_value_2"
                    ]

                    for field in safe_fields:
                        setattr(existing, field, getattr(item, field))

                    updated_details.append({
                        "id": existing.id,
                        "or_schedule_id": existing.or_schedule_id,
                        "payment_type": existing.payment_type,
                        "level": existing.level
                    })

                    continue

                # -----------------------------
                # CREATE CASE
                # -----------------------------
                new_id = str(next_id)
                new_seq = str(seq_cache[or_schedule_id])
                new_or_detail_id = f"ORS-{new_seq.zfill(10)[-9:]}"

                new_record = ContractScheduleDetail(
                    id=new_id,
                    company_id=item.company_id,
                    company_name=item.company_name,
                    carrier_id=item.carrier_id,
                    carrier_name=item.carrier_name,
                    or_schedule_id=or_schedule_id,
                    seq_id=new_seq,
                    or_detail_id=new_or_detail_id,
                    payment_type=item.payment_type,
                    level_cat=item.level_cat,
                    level=item.level,
                    territory=item.territory,
                    rate_type=item.rate_type,
                    rate_value=item.rate_value,
                    base_product=item.base_product,
                    carrier_base_rate=item.carrier_base_rate,
                    agent_base_rate=item.agent_base_rate,
                    agility_base_rate=item.agility_base_rate,
                    rate_type_0=item.rate_type_0,
                    rate_value_0=item.rate_value_0,
                    rate_type_1=item.rate_type_1,
                    rate_value_1=item.rate_value_1,
                    rate_type_2=item.rate_type_2,
                    rate_value_2=item.rate_value_2,
                    status="Active-new",
                    load_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )

                db.add(new_record)

                created_details.append({
                    "id": new_id,
                    "seq_id": new_seq,
                    "or_detail_id": new_or_detail_id,
                    "or_schedule_id": or_schedule_id
                })

                next_id += 1
                seq_cache[or_schedule_id] += 1

    return {
        "success": True,
        "created_count": len(created_details),
        "updated_count": len(updated_details),
        "created_details": created_details,
        "updated_details": updated_details
    }


@router.delete("/commission/contracts/schedule-detail/{or_detail_id}")
def delete_schedule_detail(
    or_detail_id: str,
    db: Session = Depends(get_db),
):
    """
    Delete a single commission schedule detail record by or_detail_id.
    - Atomic & safe
    - Returns 404 if not found
    - Returns success + confirmation
    """
    if not or_detail_id or or_detail_id.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="or_detail_id is required"
        )

    or_detail_id = or_detail_id.strip()

    with db.begin: 
        deleted_count = (
            db.query(ContractScheduleDetail)
            .filter(ContractScheduleDetail.or_detail_id == or_detail_id)
            .delete()
        )

        if deleted_count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Detail record with or_detail_id='{or_detail_id}' not found"
            )

    return {
        "success": True,
        "message": "Schedule detail deleted successfully",
        "or_detail_id": or_detail_id,
        "deleted_count": deleted_count
    }

@router.get("/commission/contracts/schedule-details-filters")
def get_schedule_details_filters(
    company_id: Optional[str] = Query(None, description="Filter by company ID"),
    db: Session = Depends(get_db),
):
    """
    Returns dropdown filters for commission contract schedule details:
    - territories
    - payment_levels
    - rate_types

    If company_id is provided → filters by that company.
    If not provided → returns all distinct values across all companies.
    """

    # -------------------------
    # Territories
    # -------------------------
    territory_query = db.query(LupTerritory)
    if company_id:
        territory_query = territory_query.filter(LupTerritory.company_id == company_id)

    territories = (
        territory_query
        .with_entities(LupTerritory.territory)
        .distinct()
        .order_by(LupTerritory.territory)
        .all()
    )

    # -------------------------
    # Payment Levels
    # -------------------------
    payment_level_query = db.query(LupPaymentLevel)
    if company_id:
        payment_level_query = payment_level_query.filter(LupPaymentLevel.company_id == company_id)

    payment_levels = (
        payment_level_query
        .with_entities(LupPaymentLevel.level)
        .distinct()
        .order_by(LupPaymentLevel.level)
        .all()
    )

    # -------------------------
    # Rate Types
    # -------------------------
    rate_type_query = db.query(LupRateType)
    if company_id:
        rate_type_query = rate_type_query.filter(LupRateType.company_id == company_id)

    rate_types = (
        rate_type_query
        .with_entities(LupRateType.rate_type)
        .distinct()
        .order_by(LupRateType.rate_type)
        .all()
    )

    return {
        "territories": [
            {"id": t.territory, "value": t.territory}
            for t in territories
        ],
        "payment_levels": [
            {"id": pl.level, "value": pl.level}
            for pl in payment_levels
        ],
        "rate_types": [
            {"id": rt.rate_type, "value": rt.rate_type}
            for rt in rate_types
        ],
    }
