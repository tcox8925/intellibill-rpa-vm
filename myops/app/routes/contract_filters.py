from fastapi import APIRouter, Depends
from sqlalchemy import select, union_all, literal, or_, and_
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db.session import get_db
from app.schemas import AgentStatusBase, CarrierBase, AgentMasterContractsBase
from collections import defaultdict
from app.models import AgentStatus, PaymentType, ProductType, AppointmentType, LevelCategory, Carrier, AgentMasterContracts, ContractScheduleDetail


router = APIRouter(tags=["CONTRACT_FILTER_ROUTES"])

@router.get("/contracts/status", response_model=List[dict])
def get_contract_status(
    company_id: str,
    db: Session = Depends(get_db),
):
    status_tuples = db.query(AgentStatus.status).filter(AgentStatus.company_id == company_id).distinct().all()
    # return [status[0] for status in status_tuples if status[0]]

    return [
        {"id": row.status, "value": row.status}
        for row in status_tuples if row.status
    ]

@router.get("/contracts/type-filters", response_model=dict)
def get_contract_type_filters(
    company_id: str,
    db: Session = Depends(get_db),
):
    payment_query = (
        select(
            PaymentType.payment_type.label("value"),
            literal("payment_type").label("type")
        )
        .where(PaymentType.company_id == company_id)
    )

    product_query = (
        select(
            ProductType.product_type.label("value"),
            literal("product_type").label("type")
        )
        .where(ProductType.company_id == company_id)
    )

    appointment_query = (
        select(
            AppointmentType.appointment_type.label("value"),
            literal("appointment_type").label("type")
        )
        .where(AppointmentType.company_id == company_id)
    )

    level_query = (
        select(
            LevelCategory.level_cat.label("value"),
            literal("level_cat").label("type")
        )
        .where(LevelCategory.company_id == company_id)
    )

    union_query = union_all(payment_query, product_query, appointment_query, level_query)

    result = db.execute(union_query).all()

    filters_dict = defaultdict(list)
    for row in result:
        if row.value:
            filters_dict[row.type].append(row.value)

    return dict(filters_dict)

@router.get("/contracts/carriers", response_model=List[CarrierBase])
def get_contract_carriers(
    db: Session = Depends(get_db),
):
    carriers = db.query(Carrier).all()
    return carriers

@router.get("/contracts/agent-search", response_model=List[dict])
def get_agent_search(
    term: str,
    db: Session = Depends(get_db),
):
    query = db.query(AgentMasterContracts).filter(
        or_(
            AgentMasterContracts.npn.ilike(f"%{term}%"),
            AgentMasterContracts.agent_name.ilike(f"%{term}%")
        )
    )
    
    # Get distinct pairs of (npn, agent_name) and limit the results
    result = query.distinct().limit(50).all()
    
    # Format the result for the frontend, ensuring both npn and agent_name exist
    return [
        {"id": row.npn, "value": f"{row.agent_name} ({row.npn})"}
        for row in result if row.npn and row.agent_name
    ]

@router.get("/contracts/contract-schedule", response_model=List[dict])
def get_contract_schedule_details(
    carrier_id: str,
    company_id: str,
    plan_year: str,
    npn: str,
    level_category: str,
    product_type: str,
    payment_type: str,
    db: Session = Depends(get_db),
):
    query = db.query(
        AgentMasterContracts.plan_year,
        ContractScheduleDetail.id.label("id"),
        ContractScheduleDetail.or_schedule_id.label("or_schedule_id"),
        ContractScheduleDetail.or_detail_id.label("or_detail_id"),
        ContractScheduleDetail.company_id.label("company_id"),
        ContractScheduleDetail.company_name.label("company_name"),
        ContractScheduleDetail.carrier_name.label("carrier_name"),
        ContractScheduleDetail.payment_type.label("payment_type"),
        ContractScheduleDetail.status.label("status"),
        ContractScheduleDetail.level_cat.label("level_category"),
        ContractScheduleDetail.level.label("level"),
        ContractScheduleDetail.territory.label("territory"),
        ContractScheduleDetail.rate_type.label("rate_type"),
        ContractScheduleDetail.rate_value.label("rate_value"),
        ContractScheduleDetail.base_product.label("base_product"),
        ContractScheduleDetail.carrier_base_rate.label("carrier_base_rate"),
        ContractScheduleDetail.agent_base_rate.label("agent_base_rate"),
        ContractScheduleDetail.agility_base_rate.label("agility_base_rate"),
        ContractScheduleDetail.rate_type_0.label("rate_type0"),
        ContractScheduleDetail.rate_value_0.label("rate_value0"),
        ContractScheduleDetail.rate_type_1.label("rate_type1"),
        ContractScheduleDetail.rate_value_1.label("rate_value1"),
        ContractScheduleDetail.rate_type_2.label("rate_type2"),
        ContractScheduleDetail.rate_value_2.label("rate_value2"),
        ContractScheduleDetail.load_date.label("load_date")
        ).join(
        ContractScheduleDetail, and_(
        AgentMasterContracts.com_schedule == ContractScheduleDetail.level,
        AgentMasterContracts.carrier_id == ContractScheduleDetail.carrier_id,
        AgentMasterContracts.company_id == ContractScheduleDetail.company_id,
        AgentMasterContracts.product_type == ContractScheduleDetail.base_product)
    ).filter(
        AgentMasterContracts.carrier_id == carrier_id,
        AgentMasterContracts.company_id == company_id,
        AgentMasterContracts.plan_year == plan_year,
        AgentMasterContracts.npn == npn,
        AgentMasterContracts.level_cat == level_category,
        AgentMasterContracts.product_type == product_type,
        ContractScheduleDetail.payment_type == payment_type
    )

    results = query.all()

    return [dict(row._mapping) for row in results]

@router.post("/contracts/master-contract", response_model=AgentMasterContractsBase)
def create_master_contract(
    contract_data: AgentMasterContractsBase,
    db: Session = Depends(get_db),
):
    new_contract = AgentMasterContracts(**contract_data)
    db.add(new_contract)
    db.commit()
    db.refresh(new_contract)
    return new_contract