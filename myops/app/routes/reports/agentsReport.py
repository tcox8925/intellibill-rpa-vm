from fastapi import APIRouter, Depends, HTTPException, Query
from app.models.agentModels.agent_master_contracts import AgentMasterContracts
from app.schemas.reportsDashboard.reportsSchema import CreateReportRequest
from sqlalchemy.orm import Session
from sqlalchemy import distinct, func
from app.schemas.reports import ReportRequest
from app.models import Agents, Reports
from app.db.session import get_db
from typing import Literal
from app.middleware.validator import get_current_user
from app.models import ComTotals

router = APIRouter(tags=["REPORTS ROUTES"])
allowed_roles = {"admin"}

# Returns filter + column metadata for ad-hoc reporting
@router.get("/reports-filters")
async def get_reports_filters(db: Session = Depends(get_db)):
    """
    Returns filter + column metadata for ad-hoc reporting.
    UI uses this only for rendering.
    Backend uses entity info later to infer joins.
    """

    def get_distinct_values(column):
        values = (
            db.query(distinct(column))
            .filter(column.isnot(None))
            .limit(500)
            .all()
        )
        return sorted({str(v[0]).strip() for v in values if v[0]})

    TEXT_OPERATORS = [
        "contains",
        "is",
        "does_not_contain",
        "starts_with",
        "ends_with",
        "is_empty",
        "is_not_empty",
    ]

    SELECT_OPERATORS = [
        "is",
        "is_not",
        "is_empty",
        "is_not_empty",
    ]
    
    DOB_DATE_OPERATORS = [
        "is"
    ]
    
    # DATE_OPERATORS = [
    #     "is",
    #     "is_not",
    #     "before",
    #     "after",
    #     "between",
    #     "is_empty",
    #     "is_not_empty",
    # ]

    return {

        "agents": {
            "columns": [
                {"column": "npn", "label": "NPN"},
                {"column": "first_name", "label": "First Name"},
                {"column": "last_name", "label": "Last Name"},
                {"column": "email", "label": "Email"},
                {"column": "gender", "label": "Gender"},
                {"column": "status", "label": "Status"},
                
                {"column": "assignee", "label": "Assignee"},
                {"column": "date_of_birth", "label": "Date of Birth"}, # date field
                {"column": "ssn", "label": "SSN"},
                {"column": "w9_needed", "label": "W9 Needed"},

                # Agent bank columns
                {"column": "bank_name", "label": "Bank Name"},
                {"column": "bank_routing_no", "label": "Bank Routing No"},
                {"column": "bank_account_type", "label": "Bank Account Type"},
                {"column": "bank_account_no", "label": "Bank Account No"},
            ],
            
            "filters": [
                {
                    "column": "first_name",
                    "label": "First Name",
                    "type": "text",
                    "operators": TEXT_OPERATORS,
                },
                {
                    "column": "last_name",
                    "label": "Last Name",
                    "type": "text",
                    "operators": TEXT_OPERATORS,
                },
                {
                    "column": "email",
                    "label": "Email",
                    "type": "text",
                    "operators": TEXT_OPERATORS,
                },
                {
                    "column": "npn",
                    "label": "NPN",
                    "type": "exact",
                    "operators": SELECT_OPERATORS,
                },
                {
                    "column": "gender",
                    "label": "Gender",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(Agents.gender),
                },
                {
                    "column": "status",
                    "label": "Status",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(Agents.status),
                },
                {
                    "column": "assignee",
                    "label": "Assignee",
                    "type": "select",
                    "operators": SELECT_OPERATORS
                },
                {
                    "column": "w9_needed",
                    "label": "W9 Needed",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": ["Yes", "No"],
                },
                {
                    "column": "date_of_birth",
                    "label": "Date of Birth",
                    "type": "date",
                    "operators": DOB_DATE_OPERATORS,
                },
                {
                    "column": "ssn",
                    "label": "SSN",
                    "type": "text",
                    "operators": SELECT_OPERATORS
                },
                {
                    "column": "bank_account_type",
                    "label": "Bank Account Type",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(Agents.bank_account_type),
                },
                {
                    "column": "bank_name",
                    "label": "Bank Name",
                    "type": "text",
                    "operators": SELECT_OPERATORS
                },
                {
                    "column": "bank_routing_no",
                    "label": "Bank Routing No",
                    "type": "text",
                    "operators": SELECT_OPERATORS
                },
                {
                    "column": "bank_account_no",
                    "label": "Bank Account No",
                    "type": "text",
                    "operators": SELECT_OPERATORS
                },
                {
                    "column": "e_o_needed",
                    "label": "E&O Needed",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": ["Yes", "No"],
                }
            ],
        },

        "contracts": {
            "columns": [
                {"column": "carrier_name", "label": "Carrier"},
                {"column": "product_type", "label": "Product Type"},
                {"column": "status", "label": "Contract Status"},
                {"column": "appointment_type", "label": "Appointment Type"},
                {"column": "name", "label": "Contract Id"},
                {"column": "top_upline_npn", "label": "Top Upline NPN"},
            ],
            "filters": [
                {
                    "column": "status",
                    "label": "Contract Status",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(AgentMasterContracts.status),
                },
                {
                    "column": "product_type",
                    "label": "Product Type",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(AgentMasterContracts.product_type),
                },
                {
                    "column": "carrier_name",
                    "label": "Carrier",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(AgentMasterContracts.carrier_name),
                },
                {
                    "column": "appointment_type",
                    "label": "Appointment Type",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(AgentMasterContracts.appointment_type),
                },
                {
                    "column": "top_upline_npn",
                    "label": "Top Upline NPN",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                },
                {
                    "column": "name",
                    "label": "Contract Id",
                    "type": "text",
                    "operators": SELECT_OPERATORS,
                }
                
            ],
        },

        "commissions": {
            "columns": [
                {"column": "carrier_name", "label": "Carrier"},
                {"column": "statement_month", "label": "Statement Month"},
                {"column": "payment_type", "label": "Statement Type"},
                {"column": "statement_total", "label": "Statement Total"},
                {"column": "status", "label": "Status"},
                {"column": "status_date", "label": "Status Date"},
                {"column": "agent_name", "label": "Agent"}
            ],
            "filters": [
                {
                    "column": "carrier_name",
                    "label": "Carrier",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(ComTotals.carrier_name),
                },
                {
                    "column": "statement_month",
                    "label": "Statement Month",
                    "type": "select",
                    "is_date": True,
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(ComTotals.statement_month), 
                },
                {
                    "column": "payment_type",
                    "label": "Statement Type",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(ComTotals.payment_type),
                },
                {
                    "column": "status",
                    "label": "Status",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(ComTotals.status),
                },
                {
                    "column": "status_date",
                    "label": "Status Date",
                    "type": "select",
                    "is_date": True,
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(ComTotals.status_date),
                },
                {
                    "column": "agent_name",
                    "label": "Agent",
                    "type": "select",
                    "operators": SELECT_OPERATORS,
                    "values": get_distinct_values(ComTotals.agent_name),
                },
            ],
        },

    }

# Generate ad-hoc report data based on filters and columns
@router.post("/reports-data")
async def get_reports_data(
    payload: ReportRequest,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    report_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Generate ad-hoc report data with backend-enforced joins and columns."""

    DEFAULT_COLUMNS = {
        "agents": ["npn", "first_name", "last_name", "status", "email", "assignee", "date_of_birth", "ssn", "w9_needed",
                   "bank_name", "bank_routing_no", "bank_account_type", "bank_account_no", "e_o_needed"],
        "contracts": ["carrier_name", "product_type", "status", "appointment_type", "name", "top_upline_npn"],
        "commissions": ["statement_month", "payment_type", "statement_total", "status", "status_date"],
    }

    MODEL_MAP = {
        "agents": Agents,
        "contracts": AgentMasterContracts,
        "commissions": ComTotals,
    }

    COLUMN_LABELS = {
        "agents": {
            "npn": "NPN",
            "first_name": "First Name",
            "last_name": "Last Name",
            "status": "Agent Status",
            "email": "Email",
            "assignee": "Assignee",
            "date_of_birth": "Date of Birth",
            "ssn": "SSN",
            "w9_needed": "W9 Needed",
            "bank_name": "Bank Name",
            "bank_routing_no": "Bank Routing No",
            "bank_account_type": "Bank Account Type",
            "bank_account_no": "Bank Account No",
            "e_o_needed": "E&O Needed"
        },
        "contracts": {
            "carrier_name": "Carrier",
            "product_type": "Product Type",
            "status": "Contract Status",
            "appointment_type": "Appointment Type",
            "name": "Contract Id",
            "top_upline_npn": "Top Upline NPN",
        },
        "commissions": {
            "statement_month": "Statement Month",
            "payment_type": "Payment Type",
            "statement_total": "Statement Total",
            "status": "Status",
            "status_date": "Status Date",
        },
    }

    query = db.query(Agents)

    if "contracts" in payload.filters or "contracts" in payload.columns:
        query = query.outerjoin(
            AgentMasterContracts,
            Agents.npn == AgentMasterContracts.npn,
        )

    if "commissions" in payload.filters or "commissions" in payload.columns:
        query = query.outerjoin(
            ComTotals,
            Agents.npn == ComTotals.npn,
        )

    resolved_columns: dict[str, set[str]] = {}
    all_sources = set(payload.filters.keys()) | set(payload.columns.keys())

    for source in all_sources:
        resolved_columns[source] = set()
        if source in payload.filters:
            resolved_columns[source].update(DEFAULT_COLUMNS.get(source, []))
        if source in payload.columns:
            resolved_columns[source].update(payload.columns[source])

    selected_columns = []
    columns_meta = []

    for source, columns in resolved_columns.items():
        model = MODEL_MAP.get(source)
        if not model:
            continue

        for col in columns:
            if not hasattr(model, col):
                continue

            key = f"{source}_{col}"
            label = COLUMN_LABELS.get(source, {}).get(col, col)

            selected_columns.append(getattr(model, col).label(key))
            columns_meta.append({
                "key": key,
                "label": label,
                "source": source,
                "column": col,
            })

    if not selected_columns:
        raise HTTPException(status_code=400, detail="No valid columns selected")

    query = query.with_entities(*selected_columns)

    for source, filters in payload.filters.items():
        model = MODEL_MAP.get(source)
        if not model:
            continue

        for f in filters:
            if not hasattr(model, f.column):
                continue

            column = getattr(model, f.column)

            if f.operator == "is":
                # query = query.filter(column.in_(f.value))
                query = query.filter(column.ilike(f"{f.value[0]}"))
            elif f.operator == "is_not":
                query = query.filter(~column.in_(f.value))
            elif f.operator == "contains":
                query = query.filter(column.ilike(f"%{f.value[0]}%"))
            elif f.operator == "starts_with":
                query = query.filter(column.ilike(f"{f.value[0]}%"))
            elif f.operator == "ends_with":
                query = query.filter(column.ilike(f"%{f.value[0]}"))
            elif f.operator == "is_empty":
                query = query.filter((column.is_(None)) | (column == ""))
            elif f.operator == "is_not_empty":
                query = query.filter((column.isnot(None)) & (column != ""))

    total_count = query.count()

    offset = (page - 1) * page_size
    results = query.offset(offset).limit(page_size).all()
    report = None
    if report_id:
        report = db.query(Reports).filter(Reports.pk_id == report_id).first()

    return {
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "sources_used": list(resolved_columns.keys()),
        "columns": columns_meta,
        "data": [dict(row._mapping) for row in results],
        "selected_columns_order": report.selected_columns_order if report else None
    }



# CRUD for saved reports
@router.post("/reports")
async def create_report(
    payload: CreateReportRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    filters_dict = {}
    if payload.filters:
        for source, filter_list in payload.filters.items():
            filters_dict[source] = [
                f.model_dump() if hasattr(f, "model_dump") else f
                for f in filter_list
            ]
    all_reports = db.query(Reports.report_id).filter(Reports.report_id.isnot(None)).all()
    existing_numbers = []
    for (report_id,) in all_reports:
        try:
            if report_id and report_id.startswith('RPT-'):
                num = int(report_id.split('-')[1])
                existing_numbers.append(num)
        except (IndexError, ValueError):
            continue
    new_number = max(existing_numbers) + 1 if existing_numbers else 1
    report_id = f"RPT-{new_number}"   
    report = Reports(
        report_id=report_id,
        entity_id=payload.entity_id,
        sub_entity_id=payload.sub_entity_id,
        report_name=payload.report_name,
        description=payload.description,
        filters=filters_dict,
        selected_columns_order=payload.selected_columns_order, 
        created_by=current_user.get("email")
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/reports")
async def get_reports(
    entity_id: str,
    sub_entity_id: str,
    filter_by: Literal["all", "my_reports"] = "all",
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if filter_by == "my_reports":
        report = db.query(Reports).filter(
            Reports.created_by == current_user.get("email"))

        if entity_id:
            report = report.filter(Reports.entity_id == entity_id)
        if sub_entity_id:
            report = report.filter(Reports.sub_entity_id == sub_entity_id)
        report = report.all()

    else:
        report = db.query(Reports)
        if entity_id:
            report = report.filter(Reports.entity_id == entity_id)
        if sub_entity_id:
            report = report.filter(Reports.sub_entity_id == sub_entity_id)
        report = report.all()
    return report


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: str,
    payload: CreateReportRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    report = db.query(Reports).filter(Reports.pk_id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if current_user.get("role") not in allowed_roles or report.created_by != current_user.get("email"):
        raise HTTPException(
            status_code=403, detail="Not authorized to update this report")
    filters_dict = {}
    if payload.filters:
        for source, filter_list in payload.filters.items():
            filters_dict[source] = [
                f.model_dump() if hasattr(f, "model_dump") else f
                for f in filter_list
            ]
    if payload.selected_columns_order:
        report.selected_columns_order = payload.selected_columns_order
    report.report_name = payload.report_name
    report.description = payload.description
    report.filters = filters_dict
    report.entity_id = payload.entity_id
    report.sub_entity_id = payload.sub_entity_id

    db.commit()
    db.refresh(report)

    return report


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    report = db.query(Reports).filter(Reports.pk_id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if current_user.get("role") not in allowed_roles and report.created_by != current_user.get("email"):
        raise HTTPException(
            status_code=403, detail="Not authorized to delete this report")

    db.delete(report)
    db.commit()

    return {"detail": "Report deleted successfully"}
