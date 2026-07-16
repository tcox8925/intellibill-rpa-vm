from fastapi import APIRouter, Depends, Query, HTTPException, status
from fastapi.encoders import jsonable_encoder
from typing import List, Optional
from app.models.agentModels.agents import Agents
from sqlalchemy.orm import Session, aliased
from sqlalchemy import distinct, func, and_, select, or_, tuple_, exists
from datetime import date
from app.db.session import get_db
from app.schemas.Agent import AgentContractsResponse, MasterContractOperation, AgentContractsBulkRequest, AgentHierarchyModel, NameValueModel, ContractCreateRequest
from app.models import AgentMasterContracts, Agents, Carrier, CrmNotes, CrmAttachments
from app.models.agentModels.lup_agent_licenses import LupAgentLicenses
from app.models import ContractScheduleDetail, ContractScheduleHeader
from app.models.agentModels.carriers import CarrierShort
from datetime import datetime, date
from sqlalchemy.dialects import postgresql
from app.middleware.validator import get_current_user
from fastapi.security import HTTPBearer
from uuid import UUID
import random



router = APIRouter(tags=["AGENT CONTRACT ROUTES"])
security = HTTPBearer()

@router.get("/contract/requirements", dependencies=[Depends(security)])
def get_contract_requirements(
    Contract_id: str = Query(..., description="Contract UUID"),
    Agent_id: str = Query(..., description="Agent UUID"),
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.pk_id == Agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    contract = db.query(AgentMasterContracts).filter(AgentMasterContracts.pk_id == Contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    requirements = {}
    
    e_o_status = "Completed" if agent.e_o_needed and str(agent.e_o_needed).lower() == "yes" else "Pending"
    requirements["E&O"] = e_o_status
    
    w9_status = "Completed" if agent.w9_needed and str(agent.w9_needed).lower() == "yes" else "Pending"
    requirements["W-9"] = w9_status
    
    dob = agent.date_of_birth if agent.date_of_birth else agent.bday
    dob_status = "Completed" if dob else "Pending"
    requirements["DOB"] = dob_status
    
    ffm_certificate = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.agent_npn == agent.npn,
        LupAgentLicenses.type == "Certification"
    ).first()
    
    ffm_status = "Completed" if ffm_certificate else "Pending"
    requirements["FFM"] = ffm_status
    requirements["Certification"] = "Completed"
    requirements["Agency Appointment"] = "Completed"
    if contract.appointment_type and contract.appointment_type.lower() == "producer":
        voided_check_status = "NA"
    elif contract.appointment_type and contract.appointment_type.lower() == "sub producer":
        voided_check_doc = db.query(CrmAttachments).filter(
            CrmAttachments.agent_id == Agent_id,
            CrmAttachments.file_type.ilike("%voided%check%")
        ).first()
        voided_check_status = "Completed" if voided_check_doc else "Pending"
    else:
        voided_check_status = "Pending"
    
    requirements["Voided Check"] = voided_check_status
    
    return requirements

@router.post("/contracts", dependencies=[Depends(security)])
def create_agent_contract(
    contract_data: ContractCreateRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in authentication token")
    
    agent = db.query(Agents).filter(Agents.pk_id == contract_data.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with ID {contract_data.agent_id} not found")
    
    if contract_data.carrier_id:
        carrier = db.query(Carrier).filter(Carrier.id == contract_data.carrier_id).first()
        if not carrier:
            raise HTTPException(status_code=404, detail=f"Carrier with ID {contract_data.carrier_id} not found")
    
    # Generate unique contract_id_crm 
    max_seq_id = db.query(func.max(AgentMasterContracts.seq_id)).scalar() or 0
    next_id = str(int(max_seq_id) + 1)
    contract_id = f"CON-{next_id.zfill(10)[-9:]}"
        
    for _ in range(5):
        contract_name_id = str(random.randint(1000000, 9999999))
        existing = db.query(AgentMasterContracts).filter(
            AgentMasterContracts.name == contract_name_id
        ).first()
        if not existing:
            break
        
    if contract_data.assigns_commissions == "Yes":
        if not contract_data.assignee or not contract_data.assignee_npn:
            raise HTTPException(
                status_code=400, 
                detail="Assignee name and NPN are required when assigning commissions"
            )
    
    
    new_contract = AgentMasterContracts(
        name=str(contract_name_id), 
        contract_id_crm=contract_id,
        npn=agent.npn,
        carrier_id=contract_data.carrier_id,
        company_id=contract_data.entity_id,
        sub_entity_id=contract_data.subentity_id,
        company_name=contract_data.entity_name,
        carrier_name=contract_data.carrier_name,
        product_type=contract_data.product_type,
        upline_npn=contract_data.upline_npn,
        top_upline_npn=contract_data.topupline_npn,
        agent_name=contract_data.agent_name,
        source_system="CRM",
        assigns_commissions=contract_data.assigns_commissions,
        assignee=contract_data.assignee if contract_data.assignee else None,
        assignee_npn=contract_data.assignee_npn if contract_data.assignee_npn else None,
        status="Pending",
        requested_state=contract_data.requested_state or None,
        created_date=date.today(),
    )
    
    db.add(new_contract)
    db.flush()
    
    if contract_data.notes and contract_data.notes.strip():
        notes_entry = CrmNotes(
            type="Contract",
            sub_type="Contract Request",
            description=contract_data.notes,
            user_id=user_id,
            agent_id=agent.pk_id,
            source_id=new_contract.pk_id,
            is_private=False,
            agent_npn=agent.npn           
        )
        db.add(notes_entry)
    
    db.commit()
    db.refresh(new_contract)
    
    return new_contract

@router.get("/contracts", dependencies=[Depends(security)])
def get_agent_contracts(
    npn: str,
    status: Optional[List[str]] = Query(None),
    carrier_id: Optional[List[str]] = Query(None),
    appointment_type: Optional[List[str]] = Query(None),
    product_type: Optional[List[str]] = Query(None),
    direct_upline_npn: Optional[List[str]] = Query(None),
    top_upline_npn: Optional[List[str]] = Query(None),
    parent_contract: Optional[List[str]] = Query(None),
    downlineType: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sort_column: Optional[str] = Query(None),
    sort_order: Optional[str] = Query("desc"),
    db: Session = Depends(get_db)
):

    Upline = aliased(Agents)
    TopUpline = aliased(Agents)
    Assignee = aliased(Agents)
    Recruiter = aliased(Agents)
    Parent = aliased(Agents)

    query = (
        db.query(
            AgentMasterContracts,
            Upline.first_name.label("upline_first_name"),
            Upline.last_name.label("upline_last_name"),
            TopUpline.first_name.label("top_upline_first_name"),
            TopUpline.last_name.label("top_upline_last_name"),
            Assignee.first_name.label("assignee_first_name"),
            Assignee.last_name.label("assignee_last_name"),
            Recruiter.first_name.label("recruiter_first_name"),
            Recruiter.last_name.label("recruiter_last_name"),
            Parent.first_name.label("parent_first_name"),
            Parent.last_name.label("parent_last_name"),
        )
        .outerjoin(Upline, AgentMasterContracts.upline_npn == Upline.npn)
        .outerjoin(TopUpline, AgentMasterContracts.top_upline_npn == TopUpline.npn)
        .outerjoin(Assignee, AgentMasterContracts.assignee_npn == Assignee.npn)
        .outerjoin(Recruiter, AgentMasterContracts.recruiter == Recruiter.npn)
        .outerjoin(Parent, AgentMasterContracts.parent_contract == Parent.npn)
    )

    if downlineType:
        if downlineType == "direct":
            query = query.filter(AgentMasterContracts.upline_npn == npn)
        elif downlineType == "top":
            query = query.filter(AgentMasterContracts.top_upline_npn == npn)
    else:
        query = query.filter(AgentMasterContracts.npn == npn)

    if direct_upline_npn:
        query = query.filter(AgentMasterContracts.upline_npn.in_(direct_upline_npn))

    if top_upline_npn:
        query = query.filter(AgentMasterContracts.top_upline_npn.in_(top_upline_npn))

    if parent_contract:
        query = query.filter(AgentMasterContracts.parent_contract.in_(parent_contract))

    if carrier_id:
        query = query.filter(AgentMasterContracts.carrier_name.in_(carrier_id))

    if product_type:
        query = query.filter(AgentMasterContracts.product_type.in_(product_type))

    if appointment_type:
        query = query.filter(AgentMasterContracts.appointment_type.in_(appointment_type))

    baseline_query = query.filter(True)
    results_query = baseline_query

    if status:
        status_conditions = []

        for s in status:
            s_lower = s.lower()
            if s_lower == "active":
                status_conditions.append(
                    or_(
                        AgentMasterContracts.status.ilike("Active%"),
                        AgentMasterContracts.status.ilike("Hold%"),
                        AgentMasterContracts.status.ilike("Suspend%"),
                    )
                )
            elif s_lower == "pending":
                status_conditions.append(AgentMasterContracts.status.ilike("Pending%"))
            elif s_lower == "other":
                status_conditions.append(
                    ~or_(
                        AgentMasterContracts.status.ilike("Active%"),
                        AgentMasterContracts.status.ilike("Pending%"),
                        AgentMasterContracts.status.ilike("Hold%"),
                        AgentMasterContracts.status.ilike("Suspend%"),  
                    )
                )
            else:
                status_conditions.append(AgentMasterContracts.status.ilike(f"{s}%"))

        if status_conditions:
            results_query = results_query.filter(or_(*status_conditions))

    total_count = results_query.with_entities(func.count()).scalar() or 0
    # active_count = (
    #     baseline_query
    #     .filter(
    #         or_(
    #             AgentMasterContracts.status.ilike("%Active%"),
    #             AgentMasterContracts.status.ilike("%Hold%")
    #         )
    #     )
    #     .with_entities(func.count())
    #     .scalar()
    #     or 0
    # )

    # other_count = (
    #     baseline_query
    #     .filter(
    #         or_(
    #             AgentMasterContracts.status.ilike("%Term%"),
    #             # AgentMasterContracts.status.ilike("%Terminate%")
    #         )
    #     )
    #     .with_entities(func.count())
    #     .scalar()
    #     or 0
    # )
    # active_count = 0
    # other_count = 2
    # pending_count = 1
    
    # pending_count = (
    #     baseline_query
    #     .filter(AgentMasterContracts.status.ilike("%Pending%"))
    #     .with_entities(func.count())
    #     .scalar()
    #     or 0
    # )
    # Mapping of response field names to database columns
    column_mapping = {
        "status": AgentMasterContracts.status,
        "name": AgentMasterContracts.name,
        "agentName": AgentMasterContracts.agent_name,
        "npn": AgentMasterContracts.npn,
        "writingNumber": AgentMasterContracts.writing_number,
        "productType": AgentMasterContracts.product_type,
        "parentContract": AgentMasterContracts.parent_contract,
        "levelCat": AgentMasterContracts.level_cat,
        "comSchedule": AgentMasterContracts.com_schedule,
        "orSchedule": AgentMasterContracts.or_schedule,
        "contractIdCrm": AgentMasterContracts.contract_id_crm,
        "carrierId": AgentMasterContracts.carrier_id,
        "carrierName": AgentMasterContracts.carrier_name,
        "companyId": AgentMasterContracts.company_id,
        "companyName": AgentMasterContracts.company_name,
        "planYear": AgentMasterContracts.plan_year,
        "appointmentType": AgentMasterContracts.appointment_type,
        "uplineNPN": AgentMasterContracts.upline_npn,
        "topUplineNPN": AgentMasterContracts.top_upline_npn,
        "recruiter": AgentMasterContracts.recruiter,
        "assigneeNPN": AgentMasterContracts.assignee_npn,
        "assignsCommissions": AgentMasterContracts.assigns_commissions,
        "type": AgentMasterContracts.type,
        "overrides1": AgentMasterContracts.overrides1,
        "orExclusion": AgentMasterContracts.or_exclusion,
        "sourceSystem": AgentMasterContracts.source_system,
        "startDateTime": AgentMasterContracts.start_datetime,
        "endDateTime": AgentMasterContracts.end_datetime,
        "createdDate": AgentMasterContracts.created_date,
        "pkId": AgentMasterContracts.pk_id,
    }

    # Apply sorting
    if sort_column and sort_column in column_mapping:
        order_column = column_mapping[sort_column]
        if sort_order and sort_order.lower() == "asc":
            results_query = results_query.order_by(order_column.asc())
        else:
            results_query = results_query.order_by(order_column.desc())
    else:
        # Default sorting by carrier_name
        results_query = results_query.order_by(AgentMasterContracts.carrier_name.asc())

    contracts = (
        results_query
        .limit(page_size)
        .offset((page - 1) * page_size)
        .all()
    )

    items = []
    for ac, u_fn, u_ln, tu_fn, tu_ln, a_fn, a_ln, r_fn, r_ln, p_fn, p_ln in contracts:
        items.append({
            "status": ac.status,
            "name": ac.name,
            "agentName": ac.agent_name or "",
            "npn": ac.npn or "",
            "writingNumber": ac.writing_number,
            "productType": ac.product_type,
            "parentContract": ac.parent_contract,
            "parentContractName": f"{p_fn or ''} {p_ln or ''}".strip() or "NA",
            "levelCat": ac.level_cat,
            "comSchedule": ac.com_schedule,
            "orSchedule": ac.or_schedule,
            "contractIdCrm": ac.contract_id_crm,
            "carrierId": ac.carrier_id or "",
            "carrierName": ac.carrier_name or "",
            "companyId": ac.company_id or "",
            "companyName": ac.company_name or "",
            "planYear": ac.plan_year,
            "appointmentType": ac.appointment_type,
            "uplineNPN": ac.upline_npn,
            "upline": f"{u_fn or ''} {u_ln or ''}".strip() or "NA",
            "topUplineNPN": ac.top_upline_npn,
            "topUpline": f"{tu_fn or ''} {tu_ln or ''}".strip() or "NA",
            "recruiter": ac.recruiter,
            "recruiterName": f"{r_fn or ''} {r_ln or ''}".strip() or "NA",
            "assigneeNPN": ac.assignee_npn,
            "assignee": f"{a_fn or ''} {a_ln or ''}".strip() or "NA",
            "assignsCommissions": ac.assigns_commissions,
            "type": ac.type,
            "overrides1": ac.overrides1,
            "orExclusion": ac.or_exclusion,
            "sourceSystem": ac.source_system,
            "startDateTime": ac.start_datetime,
            "endDateTime": ac.end_datetime,
            "createdDate": ac.created_date,
            "pkId": ac.pk_id,
        })

    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "items": items,
    }

@router.get("/contracts/counts", dependencies=[Depends(security)])
def get_agent_contract_counts(
    npn: str,
    # status: Optional[List[str]] = Query(None),
    carrier_id: Optional[List[str]] = Query(None),
    appointment_type: Optional[List[str]] = Query(None),
    product_type: Optional[List[str]] = Query(None),
    direct_upline_npn: Optional[List[str]] = Query(None),
    top_upline_npn: Optional[List[str]] = Query(None),
    parent_contract: Optional[List[str]] = Query(None),
    downlineType: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    baseline_query = db.query(AgentMasterContracts).filter(AgentMasterContracts.npn == npn)
    if downlineType:
        if downlineType == "direct":
            baseline_query = baseline_query.filter(AgentMasterContracts.upline_npn == npn)
        elif downlineType == "top":
            baseline_query = baseline_query.filter(AgentMasterContracts.top_upline_npn == npn)
    if direct_upline_npn:
        baseline_query = baseline_query.filter(AgentMasterContracts.upline_npn.in_(direct_upline_npn))
    if top_upline_npn:
        baseline_query = baseline_query.filter(AgentMasterContracts.top_upline_npn.in_(top_upline_npn))
    if parent_contract:
        baseline_query = baseline_query.filter(AgentMasterContracts.parent_contract.in_(parent_contract))
    if carrier_id:
        baseline_query = baseline_query.filter(AgentMasterContracts.carrier_name.in_(carrier_id))
    if product_type:
        baseline_query = baseline_query.filter(AgentMasterContracts.product_type.in_(product_type))
    if appointment_type:
        baseline_query = baseline_query.filter(AgentMasterContracts.appointment_type.in_(appointment_type))
    

    active_count = (
        baseline_query
        .filter(
            or_(
                AgentMasterContracts.status.ilike("Active%"),
                AgentMasterContracts.status.ilike("Hold%"),
                AgentMasterContracts.status.ilike("Suspend%")
            )
        )
        .with_entities(func.count())
        .scalar()
        or 0
    )

    other_count = (
        baseline_query
        .filter(
            ~or_(
                AgentMasterContracts.status.ilike("Active%"),
                AgentMasterContracts.status.ilike("Pending%"),
                AgentMasterContracts.status.ilike("Hold%"),
                AgentMasterContracts.status.ilike("Suspend%"),  
            )
        )
        .with_entities(func.count())    
            .scalar()
            or 0    
    )
    pending_count = (
        baseline_query
        .filter(AgentMasterContracts.status.ilike("Pending%"))
        .with_entities(func.count())
        .scalar()
        or 0
    )
    
    all_count = (
        baseline_query
        .with_entities(func.count())
        .scalar()
        or 0
    )
    return {
        "active_count": active_count,
        "other_count": other_count,
        "pending_count": pending_count,
        "all_count": all_count,
    }
# Get Available agent contracts based on upline and license states
@router.get("/contract/available", dependencies=[Depends(security)])
def get_available_agent_contracts(
    npn: str,
    states: Optional[str] = Query(
        None,
        description="Comma separated full state names, e.g. Alabama,California"
    ),
    db: Session = Depends(get_db)
):
    AgentContracts = aliased(AgentMasterContracts)

    # Parse input states
    state_list = []
    if states:
        state_list = [s.strip() for s in states.split(",") if s.strip()]


    # Step 1: get upline_npn
    upline_npn_subq = (
        db.query(AgentMasterContracts.upline_npn)
        .filter(
            AgentMasterContracts.npn == npn,
            AgentMasterContracts.type == "Individual"
        )
        .limit(1)
        .scalar_subquery()
    )

    # Base query
    query = (
        db.query(
            AgentMasterContracts,
            Carrier.state_availability.label("carrier_states")
        )
        .join(
            Carrier,
            Carrier.id == AgentMasterContracts.carrier_id
        )
        .filter(
            # upline contracts
            AgentMasterContracts.npn == upline_npn_subq,
            AgentMasterContracts.status.isnot(None),

            # exclude carriers where agent already has ACTIVE contract
            ~exists().where(
                and_(
                    AgentContracts.npn == npn,
                    AgentContracts.status == "Active",
                    AgentContracts.carrier_id == AgentMasterContracts.carrier_id,
                )
            )
        )
    )
    # State filtering
    if state_list:
        query = query.filter(
            or_(
                # carrier available in all states
                Carrier.state_availability == "All States",

                # carrier has matching states
                and_(
                    Carrier.state_availability.isnot(None),
                    or_(
                        *[
                            Carrier.state_availability.ilike(f"%{state}%")
                            for state in state_list
                        ]
                    )
                )
            )
        )

    # Execute
    results = query.order_by(AgentMasterContracts.carrier_name).all()

    # Build response
    response = []
    for contract, carrier_states in results:
        data = contract.__dict__.copy()
        data.pop("_sa_instance_state", None)

        data["carrier_state_availability"] = carrier_states

        if not carrier_states:
            data["carrier_states_list"] = []
        elif carrier_states == "All States":
            data["carrier_states_list"] = ["All States"]
        else:
            data["carrier_states_list"] = [
                s.strip() for s in carrier_states.split(";") if s.strip()
            ]

        response.append(data)

    return response



@router.get("/contracts/agent-line-filters", dependencies=[Depends(security)])
def get_agent_line_filters(npn: str = Query(...), db: Session = Depends(get_db)):
    
    parent_contracts = db.query(distinct(AgentMasterContracts.parent_contract),
            Agents.first_name,
            Agents.last_name
            ).join(Agents, AgentMasterContracts.parent_contract == Agents.npn).filter(AgentMasterContracts.npn == npn).all()
    parent_contracts_list = [{"id": pc[0], "value": pc[1]+" "+pc[2] or ""} for pc in parent_contracts if pc[0]]

    direct_uplines = db.query(distinct(AgentMasterContracts.upline_npn),
            Agents.first_name,
            Agents.last_name
            ).join(Agents, AgentMasterContracts.upline_npn == Agents.npn).filter(AgentMasterContracts.npn == npn).all()
    direct_uplines_list = [{"id": du[0], "value": du[1]+" "+du[2] or ""} for du in direct_uplines if du[0]]

    top_uplines = db.query(distinct(AgentMasterContracts.top_upline_npn),
            Agents.first_name,
            Agents.last_name
            ).join(Agents, AgentMasterContracts.top_upline_npn == Agents.npn).filter(AgentMasterContracts.npn == npn).all()
    top_uplines_list = [{"id": tu[0], "value": tu[1]+" "+tu[2] or ""} for tu in top_uplines if tu[0]]

    return {
        "parentContracts": parent_contracts_list,
        "directUplines": direct_uplines_list,
        "topUplines": top_uplines_list,
    }

# >>>>>>>>>> the below api is using "com_schedule_detail" table will enable once COMMISSIONS us migrated

@router.get("/contracts/schedule/detail", dependencies=[Depends(security)])
def get_schedule_detail_list(
    npn: Optional[str] = Query(...),
    carrierId: str = Query(...),
    companyId: str = Query(...),
    planYear: str = Query(...),
    levelCategory: Optional[str] = Query(...),
    productType: Optional[str] = Query(...),
    paymentType: str = Query(...),
    db: Session = Depends(get_db),
):
        # Create a subquery to get valid contract matches

    query = (
        db.query(
            ContractScheduleDetail.id.label("id"),
            ContractScheduleDetail.or_schedule_id.label("or_schedule_id"),
            ContractScheduleDetail.or_detail_id.label("or_detail_id"),
            ContractScheduleDetail.company_id.label("company_id"),
            ContractScheduleDetail.company_name.label("company_name"),
            ContractScheduleDetail.carrier_name.label("carrier_name"),
            ContractScheduleDetail.payment_type.label("payment_type"),
            ContractScheduleHeader.plan_year.label("plan_year"),
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
            ContractScheduleDetail.load_date.label("load_date"),
        )
        .join(
            ContractScheduleDetail,
            ContractScheduleHeader.or_schedule_id == ContractScheduleDetail.or_schedule_id,
        )
        .filter(
            ContractScheduleHeader.carrier_id == carrierId,
            ContractScheduleHeader.company_id == companyId,
            ContractScheduleHeader.product_name == productType,
            ContractScheduleHeader.payment_type == paymentType,
            ContractScheduleHeader.plan_year == planYear,
        )
    )

    if productType:
        query = query.filter(
            ContractScheduleHeader.product_name == productType,
        )

    if levelCategory:
        query = query.filter(
            ContractScheduleHeader.level_cat == levelCategory,
            ContractScheduleDetail.level_cat == levelCategory,
        )

    # if npn:
    #     query = query.filter(ContractScheduleDetail.npn == npn)

    
    results = query.all()
    return [dict(row._mapping) for row in results]

# TODO: Move below functions to separate layer 
# Repository-like function: AgentByNPNAndCarrierIdAsync
def get_agent_by_npn_and_carrier(
    db: Session,
    npn: str,
    carrier_id: str,
    status: Optional[str] = None,
    contract_id_crm: Optional[str] = None,
) -> Optional[AgentHierarchyModel]:
    query = db.query(AgentMasterContracts).filter(
        AgentMasterContracts.npn == npn,
        AgentMasterContracts.carrier_id == carrier_id,
    )

    if status:
        query = query.filter(AgentMasterContracts.status == status)
    if contract_id_crm:
        query = query.filter(AgentMasterContracts.contract_id_crm == contract_id_crm)

    agent = query.first()
    if agent:
        return AgentHierarchyModel(
            carrier_name=agent.carrier_name,
            agent_npn=agent.npn,
            agent_name=agent.agent_name,
            upline_agent_npn=agent.upline_npn,
            upline_agent_name=agent.upline,
            carrier_id=agent.carrier_id,
        )
    return None


# Recursive helper
def build_hierarchy(
    db: Session,
    npn: str,
    carrier_id: str,
    status: Optional[str] = None,
    contract_id_crm: Optional[str] = None,
    hierarchy: Optional[List[AgentHierarchyModel]] = None,
) -> List[AgentHierarchyModel]:
    if hierarchy is None:
        hierarchy = []

    agent = get_agent_by_npn_and_carrier(db, npn, carrier_id, status, contract_id_crm)
    if agent:
        hierarchy.insert(0, agent)  # prepend

        if agent.upline_agent_npn and agent.upline_agent_npn != agent.agent_npn:
            build_hierarchy(db, agent.upline_agent_npn, carrier_id, None, None, hierarchy)

    return hierarchy


@router.get("/contracts/agent_hierarchy", response_model=List[AgentHierarchyModel], dependencies=[Depends(security)])
def agent_hierarchy_by_npn_and_carrier(
    npn: str = Query(...),
    carrierId: str = Query(...),
    status: Optional[str] = Query(None),
    contractIdCrm: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    hierarchy = build_hierarchy(db, npn, carrierId, status, contractIdCrm)

    # assign order_num like in C# loop
    for i, agent in enumerate(reversed(hierarchy)):
        agent.order_num = i

    return hierarchy

# APi for unique agent hierarchy
@router.get("/carriers/all_agent_hierarchies", dependencies=[Depends(security)])
def get_agent_hierarchies(
    npn: str,
    db: Session = Depends(get_db),
):
    lm = AgentMasterContracts

    # Step 1: Fetch base agent contracts
    agent_contracts = (
        db.query(
            lm.npn,
            lm.upline_npn,
            lm.type,
            lm.status,
            lm.carrier_id,
            lm.carrier_name,
            lm.agent_name,
        )
        .filter(lm.npn == npn)
        .all()
    )

    if not agent_contracts:
        return []

    # Step 2: Fetch all uplines (carrier-aware)
    all_contracts = {}
    to_visit = set()

    for c in agent_contracts:
        key = (c.npn, c.carrier_id)
        all_contracts.setdefault(key, []).append(c)

        if c.upline_npn and c.upline_npn != c.npn:
            to_visit.add((c.upline_npn, c.carrier_id))

    depth = 0
    while to_visit and depth < 15:
        rows = (
            db.query(
                lm.npn,
                lm.upline_npn,
                lm.type,
                lm.status,
                lm.carrier_id,
                lm.carrier_name,
                lm.agent_name,
            )
            .filter(tuple_(lm.npn, lm.carrier_id).in_(to_visit))
            .all()
        )

        next_visit = set()

        for r in rows:
            key = (r.npn, r.carrier_id)
            if key not in all_contracts:
                all_contracts.setdefault(key, []).append(r)

                if r.upline_npn and r.upline_npn != r.npn:
                    next_visit.add((r.upline_npn, r.carrier_id))

        to_visit = next_visit
        depth += 1

    # Step 3: Build hierarchy chain
    def build_chain(start):
        seen = set()
        chain = []
        current = start
        level = 0

        while current and current.npn not in seen and level <= 15:
            seen.add(current.npn)

            chain.append(
                {
                    "npn": current.npn,
                    "agent_name": current.agent_name,
                    "type": current.type,
                    "status": current.status,
                    "level": level,
                    "carrier_id": current.carrier_id,
                    "carrier_name": current.carrier_name,
                    "upline_npn": current.upline_npn,
                }
            )

            if not current.upline_npn or current.upline_npn == current.npn:
                break

            parents = all_contracts.get(
                (current.upline_npn, current.carrier_id)
            )
            current = parents[0] if parents else None
            level += 1

        return chain

    # Step 4: Deduplicate by NPN chain
    hierarchies = {}

    for contract in agent_contracts:
        hierarchy = build_chain(contract)

        if not hierarchy or hierarchy[0]["npn"] != npn:
            continue

        signature = tuple(node["npn"] for node in hierarchy)

        hierarchies[signature] = {
            "agent_npn": npn,
            "upline_npn": hierarchy[1]["npn"] if len(hierarchy) > 1 else None,
            "top_upline_npn": hierarchy[-1]["npn"],
            "hierarchy": hierarchy,
        }

    # Step 5: REMOVE prefix (partial) hierarchies
    signatures = list(hierarchies.keys())
    final = {}

    for sig in signatures:
        is_prefix = False
        for other in signatures:
            if sig != other and len(sig) < len(other):
                if other[: len(sig)] == sig:
                    is_prefix = True
                    break

        if not is_prefix:
            final[sig] = hierarchies[sig]

    return list(final.values())


@router.get("/carriers/writing-number-required", response_model=bool, dependencies=[Depends(security)])
async def is_writing_number_required(
    carrierId: str, db = Depends(get_db)
) -> bool:
    carrier = db.query(CarrierShort).filter(CarrierShort.id == carrierId).first()
    
    if carrier and carrier.writing_num_flag is not None:
        return carrier.writing_num_flag
    
    return False


@router.get("/contract/agent_list", response_model=list[NameValueModel], dependencies=[Depends(security)])
async def get_agent_list_with_company_id(
    entityId: str | None = None,
    value: str | None = None,
    db = Depends(get_db),
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
    if entityId:
        conditions.append(AgentMasterContracts.company_id == entityId)

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

    # Convert into Pydantic models
    return [NameValueModel(value=row.value, name=row.name) for row in agents]


@router.post(
    "/update_master_contract",
    response_model=AgentContractsResponse,
    dependencies=[Depends(security)]
)
async def update_master_contract(
    request: AgentContractsResponse, db: Session = Depends(get_db)
):
    try:
        db_contract = db.query(AgentMasterContracts).filter(
            AgentMasterContracts.contract_id_crm == request.contract_id_crm
        ).first()

        if not db_contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        for field, value in request.model_dump(exclude_unset=True, by_alias=True).items():
            db_field = field.lower()
            if hasattr(db_contract, db_field):
                setattr(db_contract, db_field, value)

        db.commit()
        db.refresh(db_contract)

        return db_contract
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating contract: {str(e)}")
    
@router.post(
    "/create_master_contract",
    response_model=AgentContractsResponse,
    dependencies=[Depends(security)]
)
async def create_master_contract(
    request: AgentContractsResponse, db: Session = Depends(get_db)
):
    try:
        data = request.model_dump(by_alias=False)
        data.pop("contract_id_crm", None)

        total_records = db.query(func.count()).select_from(AgentMasterContracts).scalar() or 0
        next_id = str(int(total_records) + 1)
        contract_id = f"CON-{next_id.zfill(10)[-9:]}"  # CON-000000001


        db_contract = AgentMasterContracts(
            # id=next_id,
            contract_id_crm=contract_id,
            **data
        )

        db.add(db_contract)
        db.commit()
        db.refresh(db_contract)

        return db_contract
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating contract: {str(e)}")

@router.post(
    "/modify_add_update_master_contracts",
    summary="Create or update master contracts",
    dependencies=[Depends(security)]
)
def modify_add_update_master_contracts(
    payload: MasterContractOperation,
    db: Session = Depends(get_db)
):
    """
    Handle adding a new master contract and updating an existing master contract in a single request.

    - `create_contract`: Provide the full contract details to create a new contract. Fields:
        - `companyId`: Company ID
        - `companyName`: Company name
        - `carrierId`: Carrier ID
        - `carrierName`: Carrier name
        - `contractIdCrm`: Contract CRM ID
        - `name`: Contract name
        - `status`: Contract status
        - `writingNumber`: Writing number
        - `productType`: Product type
        - `planYear`: Plan year
        - `comSchedule`: Commission schedule
        - `orSchedule`: Override schedule
        - `npn`: Agent NPN
        - `appointmentType`: Appointment type
        - ... (other fields from `AgentContractsResponse`)

    - `update_contract`: Provide the `contractIdCrm` and any fields to update. Only provided fields will be updated.
    """

    results = {}

    try:
        with db.begin():

            # Update existing contract
            if payload.update_contract:
                update_data = payload.update_contract

                # Check for contract_id_crm
                contract_id_crm = getattr(update_data, "contract_id_crm", None)
                if not contract_id_crm:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "data": None,
                            "success": False,
                            "message": "Cannot update contract: 'contract_id_crm' is missing or null"
                        }
                    )

                db_contract = db.query(AgentMasterContracts).filter(
                    AgentMasterContracts.contract_id_crm == update_data.contract_id_crm
                ).first()

                if not db_contract:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"data": None, "success": False, "message": "Contract to update not found"}
                    )

                for field, value in update_data.model_dump(exclude_unset=True).items():
                    db_field = field
                    if hasattr(db_contract, db_field):
                        setattr(db_contract, db_field, value)

                db.flush()
                db.refresh(db_contract)
                results["updated_contract"] = jsonable_encoder(db_contract)

            # Create new contract
            if payload.create_contract:
                create_data = payload.create_contract.model_dump(by_alias=False)
                create_data.pop("contract_id_crm", None)

                # max_id = (
                #     db.query(func.max(func.cast(AgentMasterContracts.id, Integer)))
                #     .filter(AgentMasterContracts.id.regexp_match(r"^\d+$"))
                #     .scalar()
                # ) or 0
                # next_id = int(max_id) + 1

                total_records = db.query(func.count()).select_from(AgentMasterContracts).scalar() or 0
                next_id = str(int(total_records) + 1)
                contract_id = f"CON-{next_id.zfill(10)[-9:]}"

                db_contract = AgentMasterContracts(
                    # id=next_id,
                    contract_id_crm=contract_id,
                    **create_data
                )
                db.add(db_contract)

                db.flush()
                db.refresh(db_contract)
                results["created_contract"] = jsonable_encoder(db_contract)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"data": None, "success": False, "message": f"Transaction failed: {str(e)}"}
        )

    return {
        "message": "Master contracts upserted",
        "data": results,
        "success": True
    }

@router.post(
    "/upsert_master_contracts",
    response_model=List[AgentContractsResponse],
    dependencies=[Depends(security)]
)
def upsert_master_contracts(
    request: AgentContractsBulkRequest,
    db: Session = Depends(get_db)
):
    try:
        contracts = request.contracts

        if not contracts:
            return []

        contract_ids = [
            c.contract_id_crm for c in contracts if c.contract_id_crm is not None
        ]

        with db.begin():
            existing_contracts = db.execute(
                select(AgentMasterContracts)
                .where(AgentMasterContracts.contract_id_crm.in_(contract_ids))
            ).scalars().all()

            existing_map = {
                c.contract_id_crm: c for c in existing_contracts
            }

            result_objects = []

            total_records = db.query(func.count()).select_from(AgentMasterContracts).scalar() or 0
            next_id = int(total_records) + 1

            for contract in contracts:
                data = contract.model_dump(
                    exclude_unset=True,
                    by_alias=True
                )
                contract_id_crm = contract.contract_id_crm

                if contract_id_crm in existing_map:
                    db_contract = existing_map[contract_id_crm]
                    for field, value in data.items():
                        db_field = field.lower()
                        if hasattr(db_contract, db_field):
                            setattr(db_contract, db_field, value)
                else:
                    contract.pop("contract_id_crm", None)
                    new_id = str(next_id)
                    new_contract_id = f"CON-{str(next_id).zfill(10)[-9:]}"
                    db_contract = AgentMasterContracts(
                        # id=new_id,
                        contract_id_crm=new_contract_id,
                        **contract.model_dump(by_alias=False)
                    )
                    db.add(db_contract)
                    next_id += 1

                result_objects.append(db_contract)

        for obj in result_objects:
            db.refresh(obj)

        return result_objects

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error upserting master contracts: {str(e)}"
        )

@router.get("/contract/existing", dependencies=[Depends(security)])
async def get_existing_contracts(
    Status: Optional[str] = Query(None),
    CarrierId: str = Query(...),
    ProductType: str = Query(...),
    NPN: str = Query(...),
    WritingNumber: str = Query(None),
    AppointmentType: str = Query(...),
    StartDateTime: Optional[datetime] = Query(None),
    EndDateTime: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    if StartDateTime:
        first_day_of_month = datetime(StartDateTime.year, StartDateTime.month, 1)
        first_day_of_next_month = datetime(
            StartDateTime.year + (StartDateTime.month // 12),
            (StartDateTime.month % 12) + 1,
            1
        )
    else:
        first_day_of_month = None
        first_day_of_next_month = None

    query = (
        select(
            AgentMasterContracts.status,
            AgentMasterContracts.name,
            AgentMasterContracts.npn,
            AgentMasterContracts.writing_number,
            AgentMasterContracts.product_type,
            AgentMasterContracts.parent_contract,
            AgentMasterContracts.agent_name.label("ParentContractName"),
            AgentMasterContracts.level_cat,
            AgentMasterContracts.com_schedule,
            AgentMasterContracts.or_schedule,
            AgentMasterContracts.contract_id_crm,
            AgentMasterContracts.carrier_id,
            AgentMasterContracts.carrier_name,
            AgentMasterContracts.company_id,
            AgentMasterContracts.company_name,
            AgentMasterContracts.plan_year,
            AgentMasterContracts.appointment_type,
            AgentMasterContracts.upline_npn,
            AgentMasterContracts.agent_name.label("Upline"),
            AgentMasterContracts.top_upline_npn,
            AgentMasterContracts.agent_name.label("TopUpline"),
            AgentMasterContracts.recruiter,
            AgentMasterContracts.agent_name.label("RecruiterName"),
            AgentMasterContracts.assignee_npn,
            AgentMasterContracts.agent_name.label("Assignee"),
            AgentMasterContracts.assigns_commissions,
            AgentMasterContracts.type,
            AgentMasterContracts.overrides1,
            AgentMasterContracts.or_exclusion,
            AgentMasterContracts.source_system,
            AgentMasterContracts.start_datetime,
            AgentMasterContracts.end_datetime
        )
        .select_from(AgentMasterContracts)
        .join(Agents, and_(AgentMasterContracts.upline_npn == Agents.npn, AgentMasterContracts.top_upline_npn == Agents.npn, AgentMasterContracts.assignee_npn == Agents.npn, AgentMasterContracts.recruiter == Agents.npn, AgentMasterContracts.parent_contract == Agents.npn), isouter=True)
        .where(
            and_(
                AgentMasterContracts.carrier_id == CarrierId,
                AgentMasterContracts.product_type == ProductType,
                AgentMasterContracts.npn == NPN,
                AgentMasterContracts.writing_number == WritingNumber,
                AgentMasterContracts.appointment_type == AppointmentType,
                ~AgentMasterContracts.status.like("Term%")
            )
        )
    )
    # if first_day_of_month:
    #     query = query.where(
    #         and_(
    #             cast(AgentMasterContracts.start_datetime, DateTime) >= first_day_of_month,
    #             cast(AgentMasterContracts.start_datetime, DateTime) < first_day_of_next_month
    #         )
    #     )

    result = db.execute(query)
    rows = result.mappings().all()

    return [dict(row) for row in rows]

@router.get("/carrier-contracts/filters", dependencies=[Depends(security)])
def get_contract_filters(
    npn: str,
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):

    def to_key_value(items):
        return [{"id": item, "value": item} for item in sorted(items)]

    def get_agents_with_names(npn_list):
        if not npn_list:
            return []

        agents = (
            db.query(Agents.npn, Agents.first_name, Agents.last_name)
            .filter(Agents.npn.in_(npn_list))
            .all()
        )

        return [
            {
                "id": npn,
                "value": f"{first or ''} {last or ''}".strip() or "NA",
            }
            for npn, first, last in agents
        ]

    # Base filter for hierarchy
    filters = [
        or_(
            AgentMasterContracts.npn == npn,
            AgentMasterContracts.upline_npn == npn,
            AgentMasterContracts.top_upline_npn == npn,
        )
    ]

    # Optional status filter
    if status:
        contract_statuses = status.strip().lower()
        if contract_statuses == "other":
            filters.append(
                ~or_(
                    AgentMasterContracts.status.ilike("Active%"),
                    AgentMasterContracts.status.ilike("Pending%"),
                    AgentMasterContracts.status.ilike("Hold%"),
                    AgentMasterContracts.status.ilike("Suspend%"),  
                )
            )
        elif contract_statuses == "active":
            filters.append(
                or_(
                    AgentMasterContracts.status.ilike("Active%"),
                    AgentMasterContracts.status.ilike("Hold%"),
                    AgentMasterContracts.status.ilike("Suspend%"),
                )
            )
        elif contract_statuses == "pending":
            filters.append(AgentMasterContracts.status.ilike("Pending%"))
        else:
            filters.append(AgentMasterContracts.status == status)

    # Single optimized query
    records = (
        db.query(
            AgentMasterContracts.status,
            AgentMasterContracts.carrier_name,
            AgentMasterContracts.appointment_type,
            AgentMasterContracts.product_type,
            AgentMasterContracts.upline_npn,
            AgentMasterContracts.top_upline_npn,
            AgentMasterContracts.parent_contract,
        )
        .filter(and_(*filters))
        .all()
    )

    # Use sets for uniqueness
    status_set = set()
    carrier_set = set()
    appointment_set = set()
    product_set = set()
    direct_upline_set = set()
    top_upline_set = set()
    parent_contract_set = set()

    for row in records:
        if row.status:
            status_set.add(row.status)
        if row.carrier_name:
            carrier_set.add(row.carrier_name)
        if row.appointment_type:
            appointment_set.add(row.appointment_type)
        if row.product_type:
            product_set.add(row.product_type)
        if row.upline_npn:
            direct_upline_set.add(row.upline_npn)
        if row.top_upline_npn:
            top_upline_set.add(row.top_upline_npn)
        if row.parent_contract:
            parent_contract_set.add(row.parent_contract)

    return {
        "status": to_key_value(status_set),
        "carriers": to_key_value(carrier_set),
        "appointment_types": to_key_value(appointment_set),
        "products": to_key_value(product_set),
        "directUplines": get_agents_with_names(direct_upline_set),
        "topUplines": get_agents_with_names(top_upline_set),
        "parentContracts": get_agents_with_names(parent_contract_set),
    }

@router.get("/contract/states", dependencies=[Depends(security)])
def get_contract_states(
    contract_id: UUID = Query(...),
    db: Session = Depends(get_db)
):
    states = (
        db.query(
            AgentMasterContracts.appointed_state,
            AgentMasterContracts.requested_state
        )
        .filter(AgentMasterContracts.pk_id == contract_id)
        .first()
    )
    if not states:
        raise HTTPException(status_code=404, detail="Contract not found")

    return {
        "appointed_state": states.appointed_state,
        "requested_state": states.requested_state
    }
