from datetime import datetime, timezone
import logging
from typing import Optional, List
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import String, func, or_
from uuid import UUID
from app.models import ServiceInterruption, ProcessType, InterruptionActivity, ProcessTypeUsers, Entity, Users, UserPermissions, Sub_Entity
from app.models.Emails.EmailModal import EmailService
from app.schemas.Agent import InterruptionActivitySchema, ServiceInterruptionEditSchema, ServiceInterruptionSchema, InterruptionActivityCreateSchema
from app.schemas.processType import ProcessTypeCreate, ProcessTypeUpdate
from app.schemas.processTypeUsers import ProcessTypeUsersSchema, ProcessTypeUsersCreate, ProcessTypeUsersUpdate
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.AzureGraphModel import get_channel_details, get_channel_messages, list_all_channels, provision_channel_email
from fastapi.security import HTTPBearer

router = APIRouter(tags=["SERVICE INTERRUPTION ROUTES"])
security = HTTPBearer()

@router.get("/service-interruptions", dependencies=[Depends(security)])
async def get_service_interruptions(
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    sub_entity_id: Optional[str] = Query(None, description="Filter by sub-entity ID"),
    business_entity: Optional[str] = Query(None, description="Filter by business entity"),
    business_sub_entity: Optional[str] = Query(None, description="Filter by business sub-entity"),
    carrier_id: Optional[List[str]] = Query(None),
    date: Optional[List[str]] = Query(None),
    process_name: Optional[List[str]] = Query(None),
    issue_status: Optional[str] = Query(None),
    sort_column: Optional[str] = Query(None, description="Column to sort by (interruption_id, report_date, carrier_name, issue_status, process_type)"),
    sort_order: Optional[str] = Query("desc", description="Sort direction: asc or desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    # Base query with joins to Entity and Sub_Entity
    query = (
        db.query(ServiceInterruption, ProcessType, Entity, Sub_Entity)
        .join(
            ProcessType,
            ServiceInterruption.process_name.ilike(
                func.concat("%", ProcessType.process_type, "%")
            )
        )
        .outerjoin(
            Entity,
            ServiceInterruption.buisness_entity == Entity.entity_id
        )
        .outerjoin(
            Sub_Entity,
            ServiceInterruption.buisness_sub_entity == Sub_Entity.sub_entity_id
        )
    )

    # Filters
    if entity_id:
        query = query.filter(ServiceInterruption.entity_id == entity_id)

    if sub_entity_id:
        query = query.filter(ServiceInterruption.sub_entity_id == sub_entity_id)      
    if business_entity:
        query = query.filter(ServiceInterruption.buisness_entity == business_entity)
    if business_sub_entity:
        query = query.filter(ServiceInterruption.buisness_sub_entity == business_sub_entity)
    if carrier_id :
        query = query.filter(ServiceInterruption.carrier_id.in_(carrier_id))

    if date :
        query = query.filter(ServiceInterruption.report_date.in_(date))

    if process_name:
        all_codes = []
        for name in process_name:
            codes = name.split(',')
            for code in codes:
                extracted = code.split(" - ")[0].strip() if " - " in code else code.strip()
                all_codes.append(extracted)
        query = query.filter(ProcessType.process_type.in_(all_codes))

    if issue_status:
        if issue_status == "other":
            query = query.filter(ServiceInterruption.issue_status != "Open")
        else:
            query = query.filter(ServiceInterruption.issue_status == issue_status)

    # Total count (optimized)
    total = (
        db.query(func.count())
        .select_from(query.subquery())
        .scalar()
    )

    # Sorting
    if sort_column:
        sort_attr = getattr(ServiceInterruption, sort_column, None)
        if sort_attr is not None:
            query = query.order_by(
                sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
            )
        else:
            sort_attr = getattr(ProcessType, sort_column, None)
            if sort_attr is not None:
                query = query.order_by(
                    sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
                )
    else:
        query = query.order_by(ServiceInterruption.report_date.desc())


    rows = (
        query
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # Response formatting
    data = [
        {
            "id": si.id,
            "interruption_id": si.interruption_id,
            "process_id": si.process_id,
            "process_name": si.process_name,
            "process_type": pt.process_type,
            "process_description": pt.process_description,
            "report_date": si.report_date,
            "carrier_id": si.carrier_id,
            "carrier_name": si.carrier_name,
            "raw_file_name": si.raw_file_name,
            "received": si.received,
            "processed": si.processed,
            "issue_description": si.issue_description,
            "resolution_description": si.resolution_description,
            "issue_status": si.issue_status,
            "issue_date": si.issue_date,
            "resolution_date": si.resolution_date,
            "cadence": si.cadence,
            "owner": pt.owner,
            "status": pt.status,
            "entity_id": si.entity_id,
            "sub_entity_id": si.sub_entity_id,
            "buisness_entity": si.buisness_entity,
            "buisness_sub_entity": si.buisness_sub_entity,
            "buisness_entity_name": e.entity_name if e else None,
            "buisness_sub_entity_name": f"{se.sub_entity_fname or ''} {se.sub_entity_lname or ''}".strip() if se else None,
            "business_lead": si.business_lead,
            "issue_count": si.issue_count
        }
        for si, pt, e, se in rows
    ]

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "data": data,
    }


@router.get("/service-interruptions/check", response_model=ServiceInterruptionSchema, dependencies=[Depends(security)])
async def check_service_interruption(
    interruption_id: int,
    db: Session = Depends(get_db),
):
    rec = (
        db.query(ServiceInterruption)
        .filter(ServiceInterruption.interruption_id == interruption_id)
        .first()
    )

    if not rec:
        raise HTTPException(status_code=404, detail="Service interruption not found")

    return rec

@router.post("/service-interruptions", response_model=ServiceInterruptionSchema, dependencies=[Depends(security)])
async def create_service_interruption(
    service_interruption: ServiceInterruptionSchema,
    email_flag: bool = False,
    db: Session = Depends(get_db)
):
    new_interruption = ServiceInterruption(
        report_date=datetime.utcnow().strftime("%Y-%m-%d"),
        process_id=service_interruption.process_id,
        process_name=service_interruption.process_name,
        carrier_id=service_interruption.carrier_id,
        carrier_name=service_interruption.carrier_name,
        raw_file_name=service_interruption.raw_file_name,
        received=service_interruption.received,
        processed=service_interruption.processed,
        issue_description=service_interruption.issue_description,
        issue_status="Open",  
        issue_date=service_interruption.issue_date,
        resolution_date=service_interruption.resolution_date,  
        cadence=service_interruption.cadence,
        resolution_description=service_interruption.resolution_description,
        entity_id=service_interruption.entity_id,
        sub_entity_id=service_interruption.sub_entity_id,
        buisness_entity=service_interruption.buisness_entity,
        buisness_sub_entity=service_interruption.buisness_sub_entity,
        business_lead=service_interruption.business_lead
    )
    db.add(new_interruption)
    db.commit()
    db.refresh(new_interruption)

    email_service = EmailService()
    email_status = None
    
    if email_flag:
        users = await get_process_type_users(service_interruption.process_id, db)
        email_recipient_list = [u.get("user_email") for u in users if u.get("email")]
        
        print("Sending Service Interruption Notification...")

        email_status = await email_service.send_service_interruption_email(
            "service_interruption",
            service_interruption,
            email_recipient_list
        )

    channels = await get_process_type(process_type=[service_interruption.process_name], db=db)
    teams_recipient_list = [
        c.get("email") 
        for c in channels.get("data", []) 
        for c in (c.get("teams_channel") or [])   # unwrap the nested list
        if c.get("email")
    ]

    teams_status = await email_service.send_teams_notification(
        "service_interruption",
        service_interruption,
        teams_recipient_list
    )
    if not email_status:
        logging.error("Failed to send service interruption email")
    elif not teams_status:
        logging.error("Failed to send service interruption Teams notification")
    else:
        logging.info("Service interruption notification sent successfully")

    return new_interruption

@router.patch("/service-interruptions/{interruption_id}", response_model=ServiceInterruptionSchema, dependencies=[Depends(security)])
def update_service_interruption(
    interruption_id: str,
    service_interruption: ServiceInterruptionEditSchema,
    db: Session = Depends(get_db)
):
    interruption = db.query(ServiceInterruption).filter(ServiceInterruption.id == interruption_id).first()
    if not interruption:
        return {"error": "Service Interruption not found"}

    update_data = service_interruption.dict(exclude_unset=True)

    for field, value in update_data.items():
        if hasattr(interruption, field):
            setattr(interruption, field, value)

    db.commit()
    db.refresh(interruption)
    return interruption

# @router.post("/service-interruptions/send-email")
# async def send_service_interruption_email(
#     service_interruption: ServiceInterruptionSchema,
#     recipient_email: str,
#     db: Session = Depends(get_db)
# ):
#     print("Sending Service Interruption Email...")
#     email_service = EmailService()
#     email_status = await email_service.send_service_interruption_email(
#         recipient_email,
#         service_interruption
#     )
#     if not email_status:
#         logging.error("Failed to send service interruption email")
#         return {"error": "Failed to send email"}
#     else:
#         logging.info("Service interruption email sent successfully")
#         return {"message": "Email sent successfully"}

@router.get("/process-type", dependencies=[Depends(security)])
async def get_process_type(
    page: int = 1,
    page_size: int = 50,
    sort_column: Optional[str] = None,
    sort_order: Optional[str] = "asc",
    process_type: Optional[List[str]] = Query(None),
    entity_id: Optional[str] = None,
    sub_entity_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    # Base query
    query = (
        db.query(
            ProcessType.process_id.label("process_id"),
            ProcessType.process_type.label("process_type"),
            ProcessType.process_description.label("process_description"),
            ProcessType.owner.label("owner"),
            ProcessType.status.label("status"),
            ProcessType.entity.label("entity"),
            ProcessType.sub_entity.label("sub_entity"),
            ProcessType.teams_channel.label("teams_channel"),
            Entity.entity_name.label("entity_name"),
            Sub_Entity.sub_entity_fname.label("sub_entity_name"),
        )
        .outerjoin(Entity, ProcessType.entity == Entity.entity_id)
        .outerjoin(Sub_Entity, ProcessType.sub_entity == Sub_Entity.sub_entity_id)
    )

    if process_type and len(process_type) > 0:
        query = query.filter(ProcessType.process_type.in_(process_type))
        
    if entity_id:
        query = query.filter(ProcessType.entity == entity_id)
    
    if sub_entity_id:
        query = query.filter(ProcessType.sub_entity == sub_entity_id)
    
    total = query.count()
    
    if sort_column:
        column_mapping = {
            "process_id": ProcessType.process_id,
            "process_type": ProcessType.process_type,
            "owner": ProcessType.owner,
            "status": ProcessType.status,
            "entity_name": Entity.entity_name,
            "sub_entity_name": Sub_Entity.sub_entity_fname,
        }
        sort_attr = column_mapping.get(sort_column)
        if sort_attr is not None:
            query = query.order_by(
                sort_attr.desc() if sort_order == "desc" else sort_attr.asc()
            )
    else:
        query = query.order_by(ProcessType.process_id.asc())

    # Pagination
    rows = (
        query
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    
    data = [
        {
            "process_id": row.process_id,
            "process_type": row.process_type,
            "process_description": row.process_description,
            "owner": row.owner,
            "status": row.status,
            "entity": row.entity,
            "sub_entity": row.sub_entity,
            "entity_name": row.entity_name,
            "sub_entity_name": row.sub_entity_name,
            "teams_channel": row.teams_channel
        }
        for row in rows
    ]
    
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "data": data,
    }


@router.get("/process-type/filters", dependencies=[Depends(security)])
async def get_process_type_filters(
    status: Optional[str] = Query(None, description="Filter by status"),
    buisness_entity: Optional[str] = Query(None, description="Filter by business entity"),
    buisness_sub_entity: Optional[str] = Query(None, description="Filter by business sub-entity"),
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    sub_entity_id: Optional[str] = Query(None, description="Filter by sub-entity ID"),
    db: Session = Depends(get_db),
):
    """
    Get distinct process_type values for filtering
    """
    query = db.query(ProcessType.process_type, ProcessType.process_id)
    
    if status:
        query = query.filter(ProcessType.status == status)
    if buisness_entity:
        query = query.filter(ProcessType.entity == buisness_entity)
    if buisness_sub_entity:
        query = query.filter(ProcessType.sub_entity == buisness_sub_entity)
    if entity_id:
        query = query.filter(ProcessType.entity == entity_id)
    if sub_entity_id:
        query = query.filter(ProcessType.sub_entity == sub_entity_id)
    
    process_types = (
        query
        .order_by(ProcessType.process_type.asc())
        .all()
    )
    
    return {
        "process_types": [
            {
                "value": pt.process_id,
                "label": pt.process_type
            }
            for pt in process_types if pt.process_type
        ]
    }


@router.get("/service-interruptions/filters", dependencies=[Depends(security)])
async def get_service_interruption_filters(
    issue_status: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db),
):
    base_query = db.query(ServiceInterruption)
    
    if issue_status:
        base_query = base_query.filter(ServiceInterruption.issue_status.in_(issue_status))

    buisness_entities = (
        base_query
        .with_entities(
            ServiceInterruption.buisness_entity,
            Entity.entity_name
        )
        .join(Entity, ServiceInterruption.buisness_entity == Entity.entity_id)
        .distinct()
        .order_by(Entity.entity_name.asc())
        .all()
    )
    
    carriers = (
        base_query
        .with_entities(
            ServiceInterruption.carrier_id,
            ServiceInterruption.carrier_name
        )
        .distinct()
        .order_by(ServiceInterruption.carrier_name.asc())
        .all()
    )
    
    issue_dates = (
        base_query
        .with_entities(ServiceInterruption.report_date)
        .filter(ServiceInterruption.report_date.isnot(None))
        .distinct()
        .order_by(ServiceInterruption.report_date.desc())
        .all()
    )
    
    process_types = (
        base_query
        .join(
            ProcessType,
            ServiceInterruption.process_name.ilike(
                func.concat("%", ProcessType.process_type, "%")
            )
        )
        .with_entities(ProcessType.process_type)
        .distinct()
        .order_by(ProcessType.process_type.asc())
        .all()
    )
    
    return {
        "buisness_entities": [
            {
                "value": b.buisness_entity,
                "label": b.entity_name
            }
            for b in buisness_entities if b.buisness_entity
        ],
        "carriers": [
            {
                "value": c.carrier_id,
                "label": c.carrier_name
            }
            for c in carriers if c.carrier_id
        ],
        "issue_dates": [
            {
                "value": str(d[0]),
                "label": str(d[0])
            }
            for d in issue_dates if d[0]
        ],
        "process_types": [
            {
                "value": pt[0],
                "label": pt[0]
            }
            for pt in process_types if pt[0]
        ]
    }


@router.post("/proccess-type", dependencies=[Depends(security)])
async def create_process_type(
    process: ProcessTypeCreate,
    db: Session = Depends(get_db)
):
    new_process = ProcessType(
        process_type = process.process_type,
        process_description = process.process_description,
        owner = process.owner,
        status = process.status,
        entity = process.entity,
        sub_entity = process.sub_entity
    )
    db.add(new_process)
    db.commit()
    db.refresh(new_process)
    return new_process

@router.patch("/process-type/{process_id}", dependencies=[Depends(security)])
async def update_process_type(
    process_id: str,
    process: ProcessTypeUpdate,
    db: Session = Depends(get_db)
):
    existing_process = db.query(ProcessType).filter(ProcessType.process_id == process_id).first()

    update_data = process.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        
        setattr(existing_process, field, value)

    db.commit()
    db.refresh(existing_process)

    return existing_process

@router.delete("/process-type/{process_id}", dependencies=[Depends(security)])
async def delete_process_type(
    process_id: str,
    db: Session = Depends(get_db)
):
    existing_process = db.query(ProcessType).filter(ProcessType.process_id == process_id).first()
    if not existing_process:
        raise HTTPException(status_code=404, detail="Process type not found")

    db.delete(existing_process)
    db.commit()

    return {"detail": "Process type deleted successfully"}

@router.get("/teams/channels", dependencies=[Depends(security)])
async def get_all_teams_channels():

    allowed_channels = [
        "19:7a8c0de7cd584c03a6d13df1cae6ebf3@thread.tacv2",
        "19:c716c00bc3f9485484916b8f29f929f2@thread.tacv2",
        "19:6d46cf58af244d26a496e73e321fc48a@thread.tacv2",
        "19:8565b3c4dabb4662abf5069b35aff53a@thread.tacv2",
        "19:fcd7798466e54aefbed16784137e6356@thread.tacv2",
        "19:id3K3Mo7o6665QmUA9CqEfTYhPpc4KQWo_TNt3s7Q9U1@thread.tacv2"
    ]

    try:
        # team_id = "4bfef33c-dfc1-4c71-90fc-040b1a491eda"
        team_id = "1029fe9d-b438-4012-9473-5b9ca208c6d6"
        # team_id = "64d04e1d-6671-4f05-831b-11c27fa681ab"
        # channel_id = "19:6d46cf58af244d26a496e73e321fc48a@thread.tacv2"
        channels = await list_all_channels(team_id)
        # channel = await get_channel_details(team_id, channel_id)
        # messages = await get_channel_messages(team_id, channel_id)

        channels = [channel for channel in channels if channel['channel_id'] in allowed_channels]
        return channels     

    except Exception as e:
        print(f"Graph API error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Not able to fetch teams or channels")

@router.get("/service-interruptions/list", dependencies=[Depends(security)])
async def get_status_counts(
    db: Session = Depends(get_db),
    carrier_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    process_type: Optional[str] = Query(None),
    entity_id: Optional[uuid.UUID] = Query(None),
    sub_entity_id: Optional[uuid.UUID] = Query(None),
):
    query = db.query(
        ServiceInterruption.interruption_id,
        ServiceInterruption.issue_description,
    ).filter(
        ServiceInterruption.carrier_id == carrier_id, 
        ServiceInterruption.report_date.contains(date), 
        ServiceInterruption.process_name.contains(process_type)
    )

    if entity_id:
        query = query.filter(ServiceInterruption.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(ServiceInterruption.sub_entity_id == sub_entity_id)

    row = query.all()

    return [
        {
            "id": r[0],
            "value": f"{r[0]}-{r[1]}",
        }
        for r in row
    ]


@router.get("/interruption-activities/{interruption_id}", dependencies=[Depends(security)])
async def get_interruption_activities(
    interruption_id: str,
    page: int = 1,
    page_size: int = 50,
    view_type: Optional[str] = "dashboard",
    db: Session = Depends(get_db),
):
    query = db.query(
        InterruptionActivity.date,
        InterruptionActivity.type.label("resolution_type"),
        InterruptionActivity.description.label("resolution_description"),
        InterruptionActivity.pk_id.label("resolution_id"),
        InterruptionActivity.interruption_id
    ).filter(InterruptionActivity.interruption_id == interruption_id).order_by(InterruptionActivity.date.desc())

    if view_type == "dashboard":
        offset = (page - 1) * page_size
        rows = query.offset(offset).limit(page_size).all()

    result = [
            {
                "date": r.date,
                "resolution_type": r.resolution_type,
                "resolution_description": r.resolution_description,
                "resolution_id": r.resolution_id,
                "interruption_id": r.interruption_id
            }
            for r in rows
        ]
    
    return result

@router.get("/interruption-activities", dependencies=[Depends(security)])
async def get_interruption_activities(
    id: str,
    db: Session = Depends(get_db),
):
    interruption = db.query(ServiceInterruption).filter(
        ServiceInterruption.interruption_id == id
    ).first()
    
    if not interruption:
        raise HTTPException(status_code=404, detail="Interruption not found")

    activities = db.query(
        InterruptionActivity.date,
        InterruptionActivity.type.label("resolution_type"),
        InterruptionActivity.description.label("resolution_description"),
        InterruptionActivity.pk_id.label("resolution_id"),
        InterruptionActivity.interruption_id
    ).filter(
        InterruptionActivity.interruption_id == interruption.id
    ).order_by(InterruptionActivity.date.desc()).all()

    result = {
        "interruption_details": {
            "id": interruption.id,
            "process_name": interruption.process_name,
            "report_date": interruption.report_date,
            "carrier_id": interruption.carrier_id,
            "carrier_name": interruption.carrier_name,
            "raw_file_name": interruption.raw_file_name,
            "received": interruption.received,
            "processed": interruption.processed,
            "issue_description": interruption.issue_description,
            "resolution_description": interruption.resolution_description,
            "issue_status": interruption.issue_status,
            "issue_date": interruption.issue_date,
            "resolution_date": interruption.resolution_date,
            "cadence": interruption.cadence
        },
        "activities": [
            {
                "date": a.date,
                "resolution_type": a.resolution_type,
                "resolution_description": a.resolution_description,
                "resolution_id": a.resolution_id,
                "interruption_id": a.interruption_id
            }
            for a in activities
        ]
    }
    
    return result

@router.post("/interruption-activities", response_model=InterruptionActivitySchema)
async def create_interruption_activity(
    activity: InterruptionActivityCreateSchema,
    db: Session = Depends(get_db),
):
    new_activity = InterruptionActivity(
        interruption_id=activity.interruption_id,
        description=activity.description,
        date= datetime.now(timezone.utc),
        type=activity.type
    )
    db.add(new_activity)
    db.commit()
    db.refresh(new_activity)

    interruption_details = db.query(ServiceInterruption).filter(ServiceInterruption.id == activity.interruption_id).first()

    email_info = {
        "issue_description": interruption_details.issue_description,
        "resolution_description": new_activity.description,
        "Activity_date": new_activity.date,
        "Activity_type": new_activity.type
    }

    process_data = db.query(ProcessType.process_id, ProcessType.process_type).join(
        ServiceInterruption,
        ServiceInterruption.process_name.ilike(ProcessType.process_type)
        ).filter(ServiceInterruption.id == activity.interruption_id).first()
    
    process_id = process_data[0] if process_data else None
    process_type = process_data[1] if process_data else None
    print(f"Process ID for email: {process_id}, Process Type: {process_type}")

    users = await get_process_type_users(process_id, db) 
    email_recipient_list = [u.get("user_email") for u in users if u.get("email") == True]

    channels = await get_process_type(process_type=[process_type], db=db)
    teams_recipient_list = [
        c.get("email") 
        for c in channels.get("data", []) 
        for c in (c.get("teams_channel") or [])   # unwrap the nested list
        if c.get("email")
    ]

    print("Sending Service Interruption Email...")
    email_service = EmailService()
    email_status = await email_service.send_service_interruption_email(
        "interruption_activity",
        email_info,
        email_recipient_list
    )
    teams_status = await email_service.send_teams_notification(
        "interruption_activity",
        email_info,
        teams_recipient_list
    )
    if not email_status:
        logging.error("Failed to send service interruption email")
    elif not teams_status:
        logging.error("Failed to send service interruption Teams notification")
    else:
        logging.info("Service interruption notification sent successfully")

    return new_activity

@router.get("/process-type-users", dependencies=[Depends(security)])
async def get_process_type_users(
    process_id: UUID,
    db: Session = Depends(get_db)
):
    rows = db.query(
        ProcessTypeUsers,
        Entity.entity_name.label("entity_name")
    )\
    .join(Users, Users.login == ProcessTypeUsers.user_email)\
    .outerjoin(UserPermissions, Users.user_id == UserPermissions.user_id)\
    .outerjoin(Entity, Entity.id == UserPermissions.default_entity_id)\
    .filter(ProcessTypeUsers.process_id == process_id)\
    .all()

    result = []
    for rec, entity_name in rows:
        result.append({
            "pk_id": rec.pk_id,
            "process_id": rec.process_id,
            "email": rec.email,
            "user_email": rec.user_email,
            "entity_name": entity_name
        })

    return result

@router.post("/process-type-users", response_model=ProcessTypeUsersSchema, dependencies=[Depends(security)])
async def create_process_type_user(data: ProcessTypeUsersCreate, db: Session = Depends(get_db)):
    new_rec = ProcessTypeUsers(**data.model_dump())
    db.add(new_rec)
    db.commit()
    db.refresh(new_rec)
    return new_rec

@router.patch("/process-type-users/bulk", response_model=List[ProcessTypeUsersSchema], dependencies=[Depends(security)])
async def bulk_patch_process_type_users(
    updates: List[ProcessTypeUsersUpdate],
    db: Session = Depends(get_db)
):
    updated_records = []

    for upd in updates:
        rec = (
            db.query(ProcessTypeUsers)
            .filter(ProcessTypeUsers.pk_id == upd.pk_id)
            .first()
        )

        if not rec:
            continue

        update_data = upd.model_dump(exclude_unset=True)
        update_data.pop("pk_id", None)

        for key, value in update_data.items():
            setattr(rec, key, value)

        updated_records.append(rec)

    db.commit()

    # refresh updated records
    for rec in updated_records:
        db.refresh(rec)

    return updated_records

@router.delete("/process-type-users/{pk_id}", dependencies=[Depends(security)])
async def delete_process_type_user(pk_id: UUID, db: Session = Depends(get_db)):
    rec = db.query(ProcessTypeUsers).filter(ProcessTypeUsers.pk_id == pk_id).first()
    if not rec:
       raise HTTPException(status_code=404, detail="Record not found")

    db.delete(rec)
    db.commit()
    return {"detail": "Record deleted successfully"}

@router.get("/service-interruptions/status-counts", dependencies=[Depends(security)])
async def get_status_counts(
    db: Session = Depends(get_db),
    carrier_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    process_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    sub_entity_id: Optional[str] = Query(None, description="Filter by sub-entity ID"),
    buisness_entity: Optional[str] = Query(None, description="Filter by business entity"),
    buisness_sub_entity: Optional[str] = Query(None, description="Filter by business sub-entity")
):
    query = db.query(ServiceInterruption)
    
    if process_type:
        query = query.join(
            ProcessType,
            ServiceInterruption.process_name.ilike(
                func.concat("%", ProcessType.process_type, "%")
            )
        ).filter(ProcessType.process_type == process_type)
    
    if carrier_id:
        query = query.filter(ServiceInterruption.carrier_id == carrier_id)
    
    if date:
        query = query.filter(ServiceInterruption.report_date == date)
    if entity_id:
        query = query.filter(ServiceInterruption.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(ServiceInterruption.sub_entity_id == sub_entity_id)
    if buisness_entity:
        query = query.filter(ServiceInterruption.buisness_entity == buisness_entity)
    if buisness_sub_entity:
        query = query.filter(ServiceInterruption.buisness_sub_entity == buisness_sub_entity)
        
    counts = {
        "open": query.filter(ServiceInterruption.issue_status == "Open").count(),
        "resolved": query.filter(ServiceInterruption.issue_status == "Resolved").count(),
        "in_progress": query.filter(ServiceInterruption.issue_status == "InProgress").count(),
        "blank": query.filter(
            or_(
                ServiceInterruption.issue_status.is_(None),
                ServiceInterruption.issue_status == ""
            )
        ).count()
    }
    
    return counts
