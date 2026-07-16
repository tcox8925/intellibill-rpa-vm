from datetime import date, datetime, timezone
import uuid
import json
from fastapi import APIRouter, Depends, Query, HTTPException, Body, status, UploadFile, File, Form
from typing import List, Literal, Optional
from app.models import AgentStatus, LupUsers, Agents, Carrier, LupAgentLicenses , LicenseAddRequest, LicenseUpdateRequest, CertificationAddRequest, CertificationUpdateRequest, SBEAddRequest, SBEUpdateRequest, Social, AgentSocial, AgentCommunication, AgentAddress, CommunicationMedium, AddressType, AgentLanguages, Languages, LupCertifications, LupLicenses, LupSBELicenses, AgentCompliance, Users, AgentMasterContracts, CrmNotes, CrmAttachments
from sqlalchemy.orm import Session
from sqlalchemy import case, or_, func, and_, extract, asc, desc, String
from app.db.session import get_db
from app.models.agentModels.salutation import Salutation
from app.schemas.Agent import AgentComplianceCreateSchema, AgentComplianceUpdateSchema, PaginatedAgentsResponse, AgentLicensesResponse, AgentSchema, AgentStatusBase, AgentUpdateSchema, CarrierBase, AgentLicenseCreate, AgentSocialBase, AgentSocialUpdateSchema, AgentAddressUpdateSchema, AgentAddressCreateSchema, AgentCommunicationUpdateSchema, FinancialBulkUpdateRequest, LanguagesBase, SimpleFinancialUpsertRequest, AgentEmailUpdateRequest, AgentEmailCreateRequest, AgentPhoneTextUpdateRequest, AgentPhoneTextCreateRequest, AgentFinancialUpdateRequest, AgentAffiliationsResponse, AgentComplianceResponseSchema, ComplianceNoteOut, ComplianceAttachmentOut
import uuid as _uuid_module
from uuid import UUID, uuid4
from app.models.agentModels.agents import AgentFinancial
from app.middleware.validator import get_current_user
from app.utils.attachment_utility import upload_blob_to_path
from fastapi.security import HTTPBearer
    
router = APIRouter(tags=["AGENT ROUTES"])
security = HTTPBearer()

@router.get("/agents", response_model=List[AgentSchema])
def search_agents(
    entityId: Optional[str] = Query(None, description="Company ID filter"),
    subEntityId: Optional[str] = Query(None, description="Sub-entity ID filter"),
    search: Optional[str] = Query(None, description="Search string"),
    agent_type: Optional[str] = Query(None, description="filter by agent type"),
    limit: int = Query(50, ge=1, le=200, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db)
):
    if not search or not search.strip():
        return []

    search_text = search.strip().lower()
    parts = search_text.split()

    base_query = db.query(
        Agents.pk_id,
        Agents.npn,
        Agents.first_name,
        Agents.last_name,
        Agents.full_name
    )
    if agent_type:
        base_query = base_query.filter(Agents.type == agent_type)
    if entityId and subEntityId:
        base_query = base_query.filter(
            Agents.company_id == entityId,
            Agents.sub_entity_id == subEntityId
        )
    if search_text.isdigit():
        agents = (
            base_query
            .filter(Agents.npn.ilike(f"{search_text}%"))
            .order_by(Agents.full_name)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return agents
    agents = []
    if len(parts) == 2:
        agents = (
            base_query
            .filter(
                or_(
                    and_(
                        func.lower(Agents.first_name).like(f"%{parts[0]}%"),
                        func.lower(Agents.last_name).like(f"%{parts[1]}%"),
                    ),
                    and_(
                        func.lower(Agents.first_name).like(f"%{parts[1]}%"),
                        func.lower(Agents.last_name).like(f"%{parts[0]}%"),
                    ),
                )
            )
            .order_by(Agents.full_name)
            .offset(offset)
            .limit(limit)
            .all()
        )
        if agents:
            return agents

    agents = (
        base_query
        .filter(func.lower(Agents.full_name).like(f"%{search_text}%"))
        .order_by(Agents.full_name)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return agents

@router.get("/carriers/all", response_model=List[CarrierBase])
def get_all_carriers(
    db: Session = Depends(get_db)
):
    # SELECT CAST(Id AS VARCHAR) AS Id, vendor_name, Market, modified_time FROM [raw].[lup_carriers]
    carriers = db.query(
        Carrier.pk_id,
        Carrier.id,
        Carrier.vendor_name,
        Carrier.market,
        Carrier.modified_time
    ).all()
    return carriers

@router.get("/agents/filter-status", response_model=list[AgentStatusBase])
def get_agent_filter_status(
    agencyId: str,
    db: Session = Depends(get_db),
):
    query = db.query(AgentStatus).filter(AgentStatus.company_id == agencyId)
    return query.all()

@router.get("/agents/contract-requirements", dependencies=[Depends(security)])
def get_agents_carrier_requirements(
    npn: str ,
    db: Session = Depends(get_db),
):
    """
    Get agent carrier requirements based on check for the given NPN.
    Returns status of: Date of Birth, E&O, Voided Check, W-9, FFM
    
    Args:
        npn: The agent's NPN (required)
    
    Returns:
        Dictionary with boolean status for each compliance item
        
    Raises:
        HTTPException: 422 if NPN is not provided
    """
    
    if not npn:
        raise HTTPException(status_code=422, detail="NPN is required")
        
    agent = db.query(Agents).filter(Agents.npn == npn).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Check Date of Birth - exists in lup_agents table
    dob_exists = db.query(Agents).filter(
        Agents.npn == npn,
        Agents.date_of_birth.isnot(None)
    ).first() is not None
    
    # Check E&O - exists in crm_attachments AND expiration_date > current date
    eo_check = False
    eo_record = db.query(CrmAttachments).filter(
        CrmAttachments.npn == npn,
        CrmAttachments.file_type == 'E&O'
    ).first()
    
    if eo_record:
        agent = db.query(Agents).filter(Agents.npn == npn).first()
        if agent and agent.expiration_date:
            try:
                expiration_date = datetime.strptime(agent.expiration_date, '%Y-%m-%d').date() if isinstance(agent.expiration_date, str) else agent.expiration_date
                if expiration_date > date.today():
                    eo_check = True
            except (ValueError, TypeError):
                eo_check = False
    
    # Check Voided Check - exists in crm_attachments
    voided_check_exists = db.query(CrmAttachments).filter(
        CrmAttachments.npn == npn,
        CrmAttachments.file_type == 'Voided Check'
    ).first() is not None
    
    # Check W-9 - exists in crm_attachments
    w9_exists = db.query(CrmAttachments).filter(
        CrmAttachments.npn == npn,
        CrmAttachments.file_type == 'W-9'
    ).first() is not None
    
    # Check FFM - expiry_date from lup_agent_licenses > current date
    ffm_check = False
    ffm_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.agent_npn == npn,
        LupAgentLicenses.status == 'FFM'
    ).first()
    
    if ffm_record and ffm_record.expiry_date:
        try:
            expiry_date = ffm_record.expiry_date if isinstance(ffm_record.expiry_date, date) else datetime.strptime(str(ffm_record.expiry_date), '%Y-%m-%d').date()
            current_year = date.today().year
            next_year = current_year + 1
            if expiry_date.year in (current_year, next_year):
                ffm_check = True
            else:
                ffm_check = False
        except (ValueError, TypeError):
            ffm_check = False
    
    return {
        "Date of Birth": dob_exists,
        "E&O": eo_check,
        "FFM": ffm_check,
        "Voided Check": voided_check_exists,
        "W-9": w9_exists,
    }

@router.get("/agents/languages-list", response_model=List[LanguagesBase])
def get_agent_languages(db: Session = Depends(get_db)):
    languages_list = db.query(
        Languages.pk_id,
        Languages.language
    ).all()

    return languages_list

@router.get("/agents/address-types")
def get_address_types(db: Session = Depends(get_db)):
    address_types = db.query(
        AddressType.pk_id,
        AddressType.type
    ).all()
    
    return [
        {
            "label": addr_type.type,
            "value": str(addr_type.pk_id)
        }
        for addr_type in address_types
    ]

@router.get("/agents/communication-types/email")
def get_email_communication_types(db: Session = Depends(get_db)):
    email_types = db.query(
        CommunicationMedium.pk_id,
        CommunicationMedium.sub_type
    ).filter(
        func.lower(CommunicationMedium.type) == "email"
    ).all()
    
    return [
        {
            "label": email_type.sub_type,
            "value": str(email_type.pk_id)
        }
        for email_type in email_types
    ]

@router.get("/agents/communication-types/phone")
def get_phone_communication_types(db: Session = Depends(get_db)):
    phone_types = db.query(
        CommunicationMedium.pk_id,
        CommunicationMedium.sub_type
    ).filter(
        func.lower(CommunicationMedium.type) == "phone"
    ).all()
    
    return [
        {
            "label": phone_type.sub_type,
            "value": str(phone_type.pk_id)
        }
        for phone_type in phone_types
    ]

@router.get("/agents/{pk_id}")
def get_agent_full_info(pk_id: str, db: Session = Depends(get_db)):

    # If pk_id is a UUID, look up by Agents.pk_id; otherwise treat pk_id as an NPN and lookup by Agents.npn
    try:
        _uuid_module.UUID(pk_id)
        lookup_by_uuid = True
    except Exception:
        lookup_by_uuid = False

    agent_query = (
        db.query(
            Agents.pk_id,
            Agents.npn,
            Agents.salutation,
            Agents.first_name,
            Agents.middle_name,
            Agents.last_name,
            Agents.nickname,
            Agents.date_of_birth,
            Agents.gender,
            Agents.status,
            Agents.type,
            Agents.company_id,
            Agents.company_name,
            Agents.assigns_commissions,
            Agents.assignee_npn,
            Agents.overrides,
            Agents.overrides1,
            Agents.ssn,
            Agents.id.label("agent_id"),
            Agents.recruiter.label("recruiter_id"),
            LupUsers.full_name.label("recruiter_name"),
            Agents.sales_director.label("sales_director_id"),
            LupUsers.full_name.label("sales_director_name"),
            Agents.responsible_agency,
            func.concat(
                func.coalesce(Agents.first_name, ""), " ",
                func.coalesce(Agents.last_name, "")
            ).label("responsible_agency_name"),
            Agents.fein,
            Agents.cms_termed,
            case(
                (
                    or_(
                        Users.agent_npn.isnot(None),
                        Users.agent_principal_npn.isnot(None)
                    ),
                    True
                ),
                else_=False
            ).label("MyAdmin360")
        )
        .outerjoin(LupUsers, Agents.recruiter == LupUsers.id)
        .outerjoin(Users, or_(Users.agent_npn == Agents.npn, Users.agent_principal_npn == Agents.npn))
        .filter(Agents.pk_id == pk_id if lookup_by_uuid else Agents.npn == pk_id)
    )

    agent = agent_query.first()
    if not agent:
        return {"error": "Agent not found"}
    agent_data = dict(agent._mapping)

    # Use the agent's pk_id for related-table lookups (these tables store agent_id as pk_id)
    agent_pk = agent.pk_id

    # languages = (
    #     db.query(
    #         AgentLanguages.language_id,
    #         Languages.language,
    #         AgentLanguages.preferred_language
    #     ).join(
    #         Languages, Languages.pk_id == AgentLanguages.language_id
    #     ).filter(AgentLanguages.agent_id == agent_pk).all()
    # )

    languages = (
        db.query(
            AgentLanguages.language_id,
            Languages.language,
            AgentLanguages.preferred_language,
            case(
                (AgentLanguages.preferred_language == True, Languages.language),
                else_=None
            ).label("preferred_language_text")
        )
        .join(Languages, Languages.pk_id == AgentLanguages.language_id)
        .filter(AgentLanguages.agent_id == agent_pk)
        .all()
    )

    agent_data["languages"] = [
        {
            "language_id": lang.language_id,
            "language": lang.language
        } for lang in languages
    ]

    agent_data["preferred_language"] = [{
        "language_id": next((lang.language_id for lang in languages if lang.preferred_language), None),
        "language": next((lang.preferred_language_text for lang in languages if lang.preferred_language), None)
    }]

    addresses = (
        db.query(
            AgentAddress.pk_id,
            AddressType.pk_id.label("address_type_id"),
            AddressType.type.label("address_type"),
            AgentAddress.line1,
            AgentAddress.line2,
            AgentAddress.street,
            AgentAddress.city,
            AgentAddress.state,
            AgentAddress.county,
            AgentAddress.zip,
            AgentAddress.primary
        )
        .join(AddressType, AddressType.pk_id == AgentAddress.address_type_id)
        .filter(AgentAddress.agent_id == agent_pk)
        .all()
    )

    agent_data["addresses"] = [
        {
            "pk_id": addr.pk_id,
            "address_type_id": addr.address_type_id,
            "address_type": addr.address_type,
            "line1": addr.line1,
            "line2": addr.line2,
            "street": addr.street,
            "city": addr.city,
            "state": addr.state,
            "county": addr.county,
            "zip": addr.zip,
            "primary": addr.primary
        } for addr in addresses
    ]

    # Communications — LEFT JOIN with lookup table to always get all mediums
    communications = (
        db.query(
            AgentCommunication.pk_id,
            CommunicationMedium.pk_id.label("communication_id"),
            CommunicationMedium.type.label("comm_type"),
            CommunicationMedium.sub_type.label("comm_sub_type"),
            AgentCommunication.value,
            AgentCommunication.text_opt,
            AgentCommunication.dnd,
            AgentCommunication.ai_pre_recording,
            AgentCommunication.marketing_opt_in,
            AgentCommunication.extension,
            AgentCommunication.primary
        )
        .outerjoin(
            AgentCommunication,
            (AgentCommunication.communication_id == CommunicationMedium.pk_id) &
            ((AgentCommunication.agent_id == agent_pk) | (AgentCommunication.agent_id.is_(None)))
        )
        .all()
    )

   # Dynamic grouping
    comm_dict = {}
    for comm in communications:
        if comm.comm_type not in comm_dict:
            comm_dict[comm.comm_type] = []

        comm_dict[comm.comm_type].append({
            "pk_id": comm.pk_id,
            "communication_id": comm.communication_id,
            "comm_sub_type": comm.comm_sub_type,
            "value": comm.value,
            "text_opt": comm.text_opt,
            "dnd": comm.dnd,
            "ai_pre_recording": comm.ai_pre_recording,
            "marketing_opt_in": comm.marketing_opt_in,
            "extension": comm.extension,
            "primary": comm.primary
        })

    agent_data["communications"] = comm_dict  # each key is a comm_type
    return agent_data

@router.patch("/agents/{agent_id}/cms-termed")
def update_agent_cms_termed(
    agent_id: str,
    cms_termed: str = Body(..., embed=True),
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.cms_termed = cms_termed

    db.commit()
    db.refresh(agent)

    return {
        "message": "CMS termed status updated successfully",
        "agent_id": agent_id,
        "cms_termed": agent.cms_termed
    }

@router.get("/agents/{agent_id}/emails")
def get_agent_emails(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    email_communications = (
        db.query(
            AgentCommunication.pk_id,
            CommunicationMedium.pk_id.label("communication_id"),
            CommunicationMedium.type.label("comm_type"),
            CommunicationMedium.sub_type.label("comm_sub_type"),
            AgentCommunication.value,
            AgentCommunication.marketing_opt_in,
            AgentCommunication.primary
        )
        .join(CommunicationMedium, CommunicationMedium.pk_id == AgentCommunication.communication_id)
        .filter(
            AgentCommunication.agent_id == agent_id,
            func.lower(CommunicationMedium.type) == "email"
        )
        .all()
    )
    
    emails = [
        {
            "pk_id": str(email.pk_id),
            "communication_id": str(email.communication_id),
            "type": email.comm_sub_type,
            "email": email.value,
            "marketing_opt_in": email.marketing_opt_in,
            "primary": email.primary
        }
        for email in email_communications
    ]
    
    return {
        "agent_id": agent_id,
        "agent_name": agent.full_name,
        "emails": emails
    }

@router.post("/agents/{agent_id}/emails")
def create_agent_email(
    agent_id: str,
    email_data: AgentEmailCreateRequest,
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    comm_medium = db.query(CommunicationMedium).filter(
        func.lower(CommunicationMedium.sub_type) == func.lower(email_data.communication_type)
    ).first()
    if not comm_medium:
        raise HTTPException(status_code=400, detail=f"Invalid communication_type:{email_data.communication_type}")
    existing_email_count = db.query(AgentCommunication).join(
        CommunicationMedium,
        AgentCommunication.communication_id == CommunicationMedium.pk_id
    ).filter(
        AgentCommunication.agent_id == agent_id,
        func.lower(CommunicationMedium.type) == "email"
    ).count()
    if existing_email_count == 0:
        email_data.primary = True
    
    if email_data.primary:
        email_comm_ids = [
            row[0] for row in db.query(CommunicationMedium.pk_id).filter(
                func.lower(CommunicationMedium.type) == "email"
            ).all()
        ]
        db.query(AgentCommunication).filter(
            AgentCommunication.agent_id == agent_id,
            AgentCommunication.communication_id.in_(email_comm_ids)
        ).update({AgentCommunication.primary: False}, synchronize_session=False)
    
    new_email = AgentCommunication(
        agent_id=agent_id,
        communication_id=comm_medium.pk_id,
        value=email_data.value,
        marketing_opt_in=email_data.marketing_opt_in,
        primary=email_data.primary
    )
    
    db.add(new_email)
    db.commit()
    db.refresh(new_email)
    
    return {
        "message": "Email created successfully",
        "pk_id": str(new_email.pk_id)
    }

@router.patch("/agents/{agent_id}/emails")
def update_agent_emails(agent_id: str, email_data: AgentEmailUpdateRequest, db: Session = Depends(get_db)):
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    email_record = db.query(AgentCommunication).filter(
        AgentCommunication.pk_id == email_data.pk_id,
        AgentCommunication.agent_id == agent_id
    ).first()
    
    if not email_record:
        raise HTTPException(status_code=404, detail="Email record not found")
    
    if email_data.communication_type is not None:
        comm_medium = db.query(CommunicationMedium).filter(
            func.lower(CommunicationMedium.sub_type) == func.lower(email_data.communication_type)
        ).first()
        if not comm_medium:
            raise HTTPException(status_code=400, detail=f"Invalid communication_type: {email_data.communication_type}")
        email_record.communication_id = comm_medium.pk_id 
    if email_data.value is not None:
        email_record.value = email_data.value
    if email_data.marketing_opt_in is not None:
        email_record.marketing_opt_in = email_data.marketing_opt_in
    if email_data.primary is not None:
        if email_data.primary:
            other_email = db.query(AgentCommunication.pk_id).join(
                CommunicationMedium,
                AgentCommunication.communication_id == CommunicationMedium.pk_id
            ).filter(
                AgentCommunication.agent_id == agent_id,
                AgentCommunication.pk_id != email_data.pk_id,
                func.lower(CommunicationMedium.type) == "email"
            ).all()  
            if other_email:
                email = [email[0] for email in other_email]
                db.query(AgentCommunication).filter(
                    AgentCommunication.pk_id.in_(email)
                ).update({"primary": False}, synchronize_session=False)
        
        email_record.primary = email_data.primary
    
    db.commit()
    
    return {
        "message": "Email communication updated successfully",
        "pk_id": str(email_data.pk_id)
    }

@router.delete("/agents/emails/{pk_id}")
def delete_agent_email(
    pk_id: str,
    db: Session = Depends(get_db)
):
    email_record = db.query(AgentCommunication).filter(
        AgentCommunication.pk_id == pk_id
    ).first()
    
    if not email_record:
        raise HTTPException(status_code=404, detail="Email record not found")
    
    agent_id = email_record.agent_id  
    db.delete(email_record)
    db.commit()
    email_comm_ids = [
        row[0] for row in db.query(CommunicationMedium.pk_id).filter(
            func.lower(CommunicationMedium.type) == "email"
        ).all()
    ]
    
    remaining_emails = db.query(AgentCommunication).filter(
        AgentCommunication.agent_id == agent_id,
        AgentCommunication.communication_id.in_(email_comm_ids)
    ).all()
    if len(remaining_emails) == 1 and not remaining_emails[0].primary:
        remaining_emails[0].primary = True
        db.commit()
    
    return {
        "message": "Email communication deleted successfully",
        "pk_id": str(pk_id)
    }
   
@router.get("/agents/social/{agent_id}", response_model=List[AgentSocialBase])
def get_agent_social(agent_id: str, db: Session = Depends(get_db)):
    result = (
        db.query(
            Social.pk_id.label("platform_id"),
            Social.platform,
            AgentSocial.url,
            AgentSocial.pk_id.label("agent_social_id"),
        )
        .outerjoin(
            AgentSocial,
            (AgentSocial.platform_id == Social.pk_id) & (AgentSocial.agent_id == agent_id)
        )
        .distinct(Social.platform)
        .all()
    )

    return result

@router.patch("/agents/social")
def update_agent_social(payload: AgentSocialUpdateSchema, db: Session = Depends(get_db)):
    """
    Update social media links for a specific agent.
    """
    for social in payload.social_links:
        # manually map fields from the pydantic object to the SQLAlchemy model
        agent_social = AgentSocial(
            platform_id=social.platform_id,
            url=social.url,
            agent_id=payload.agent_id,
            pk_id=social.agent_social_id
        )
        db.merge(agent_social)

    db.commit()
    return {"message": "Social media links updated successfully."}

@router.patch("/agents/address")
def update_agent_address(
    payload: AgentAddressUpdateSchema,
    db: Session = Depends(get_db),
):
    """
    Update addresses for a specific agent.
    """
    address_record = db.query(AgentAddress).filter(
        AgentAddress.pk_id == payload.pk_id,
        AgentAddress.agent_id == payload.agent_id
    ).first()
    
    if not address_record:
        raise HTTPException(status_code=404, detail="Address not found")
    address_record.address_type_id = payload.address_type_id
    if payload.line1 is not None:
        address_record.line1 = payload.line1
    if payload.line2 is not None:
        address_record.line2 = payload.line2
    if payload.street is not None:
        address_record.street = payload.street
    if payload.city is not None:
        address_record.city = payload.city
    if payload.state is not None:
        address_record.state = payload.state
    if payload.county is not None:
        address_record.county = payload.county
    if payload.zip is not None:
        address_record.zip = payload.zip
    if payload.primary is not None:
        if payload.primary:
            db.query(AgentAddress).filter(
                AgentAddress.agent_id == payload.agent_id,
                AgentAddress.pk_id != payload.pk_id
            ).update({"primary": False})
        address_record.primary = payload.primary

    db.commit()
    db.refresh(address_record)
    
    return {"message": "Address updated successfully."}

@router.post("/agents/address")
def create_agent_address(payload: AgentAddressCreateSchema, db: Session = Depends(get_db)):
    agent = db.query(Agents).filter(Agents.pk_id == payload.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    existing_address = db.query(AgentAddress).filter(
        AgentAddress.agent_id == payload.agent_id
    ).count()
    if existing_address == 0:
        payload.primary = True
    
    if payload.primary:
        db.query(AgentAddress).filter(
            AgentAddress.agent_id == payload.agent_id
        ).update({"primary": False})
    
    new_address = AgentAddress(
        agent_id=payload.agent_id,
        address_type_id=payload.address_type_id,
        line1=payload.line1,
        line2=payload.line2,
        street=payload.street,
        city=payload.city,
        state=payload.state,
        county=payload.county,
        zip=payload.zip,
        primary=payload.primary
    )
    
    db.add(new_address)
    db.commit()
    db.refresh(new_address)
    
    return {
        "message": "Address created successfully.",
        "pk_id": str(new_address.pk_id)
    }

@router.get("/agents/address/{agent_id}")
def get_agent_addresses(agent_id: UUID, db: Session = Depends(get_db)):
    addresses = (
        db.query(
            AgentAddress.pk_id,
            AgentAddress.address_type_id,
            AddressType.type,
            AgentAddress.line1,
            AgentAddress.line2,
            AgentAddress.street,
            AgentAddress.city,
            AgentAddress.state,
            AgentAddress.county,
            AgentAddress.zip,
            AgentAddress.primary
        )
        .join(AddressType, AddressType.pk_id == AgentAddress.address_type_id)
        .filter(AgentAddress.agent_id == agent_id)
        .all()
    )
    
    return [
        {
            "pk_id": addr.pk_id,
            "address_type_id": addr.address_type_id,
            "address_type": addr.type,
            "line1": addr.line1,
            "line2": addr.line2,
            "street": addr.street,
            "city": addr.city,
            "state": addr.state,
            "county": addr.county,
            "zip": addr.zip,
            "primary": addr.primary
        } for addr in addresses
    ]

@router.delete("/agents/address/{pk_id}")
def delete_agent_address(
    pk_id: str,
    db: Session = Depends(get_db)
):
    address_record = db.query(AgentAddress).filter(
        AgentAddress.pk_id == pk_id
    ).first()
    
    if not address_record:
        raise HTTPException(status_code=404, detail="Address not found")
    
    agent_id = address_record.agent_id  
    db.delete(address_record)
    db.commit()
    remaining_addresses = db.query(AgentAddress).filter(
        AgentAddress.agent_id == agent_id
    ).all()
    if len(remaining_addresses) == 1 and not remaining_addresses[0].primary:
        remaining_addresses[0].primary = True
        db.commit()
    
    return {
        "success": True,
        "message": "Address deleted successfully",
        "deleted_id": str(pk_id)
    }
@router.patch("/agents/communication")
def update_agent_communication(
    payload: AgentCommunicationUpdateSchema,
    db: Session = Depends(get_db),
):
    """
    Update communications for a specific agent.
    """
    for communication in payload.communications:
        communication_obj = AgentCommunication(
            pk_id=communication.pk_id,
            agent_id=payload.agent_id,
            communication_id=communication.communication_id,
            value=communication.value,
            text_opt=communication.text_opt,
            dnd=communication.dnd,
            ai_pre_recording=communication.ai_pre_recording,
            marketing_opt_in=communication.marketing_opt_in,
            extension=communication.extension,
            primary=communication.primary
        )
        db.merge(communication_obj)

    db.commit()
    return {"message": "Communications updated successfully."}

@router.patch("/agents/{agent_id}", response_model=AgentSchema)
def update_agent(
    agent_id: str,
    payload: AgentUpdateSchema =  Body(...),
    db: Session = Depends(get_db),
):
    """
    Partially update an agent record by agent_id.
    Only provided fields will be updated.
    """   
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agents not found")

    if payload.languages:
        # Extract the list of language IDs from the payload
        new_lang_ids = {str(lang.language_id) for lang in payload.languages}

        existing_langs = db.query(AgentLanguages.language_id).filter(
            AgentLanguages.agent_id == agent_id
        ).all()
        existing_lang_ids = {str(lang_id[0]) for lang_id in existing_langs}

        db.query(AgentLanguages).filter(
            AgentLanguages.agent_id == agent_id,
            ~AgentLanguages.language_id.in_(new_lang_ids)
        ).delete(synchronize_session=False)

        langs_to_add = new_lang_ids - existing_lang_ids
        for lang_id in langs_to_add:
            db.add(AgentLanguages(agent_id=agent_id, language_id=lang_id))

    if payload.preferred_language:
        pref_lang_id = str(payload.preferred_language)
        db.query(AgentLanguages).filter(
            AgentLanguages.agent_id == agent_id
        ).update(
            {
                AgentLanguages.preferred_language: case(
                    (AgentLanguages.language_id == pref_lang_id, True),
                    else_=False
                )
            },
            synchronize_session=False
        )

    update_data = payload.model_dump(by_alias=True, exclude_unset=True, exclude={'languages'})

    for field, value in update_data.items():
        if field == 'recruiter_id':
            setattr(agent, 'recruiter', value)
        elif field == 'recruiter_name':
            pass
        else:
            setattr(agent, field, value)

    db.commit()
    db.refresh(agent)

    return agent

@router.get("/agent-licenses-year")
def get_licenses_by_license_year(
    agent_npn: str,
    db: Session = Depends(get_db),
):
    """
    Get distinct license years from issue_date for a given agent_npn.
    """
    query = (
        db.query(
            # func.distinct(LupAgentLicenses.certification_year).label("license_year', LupAgentLicenses.issue_date)).label("license_year")
            func.distinct(LupAgentLicenses.certification_year).label("license_year")
        )
        .filter(LupAgentLicenses.agent_npn == agent_npn)
        .order_by(desc("license_year"))
    )

    result = query.all()

    license_years = [int(row.license_year) for row in result if row.license_year is not None]

    return {"license_years": license_years}

@router.get("/agent-master-contracts/status")

def get_agent_status_list(db: Session = Depends(get_db)):

    statuses = (

        db.query(func.distinct(AgentMasterContracts.status))

        .filter(AgentMasterContracts.status.isnot(None))

        .all()

    )
 
    status_list = [row[0] for row in statuses]
 
    return {

        "statuses": sorted(status_list)

    }

 
@router.get("/agents/carrier/{carrier_id}", response_model=PaginatedAgentsResponse)
def get_agent_carrier_affiliations(
    carrier_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None, description="=Agent status"),
    export: bool = Query(False, description="Set to true to export all results without pagination"),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size

    filters = [AgentMasterContracts.carrier_id == carrier_id]
    if status:
        filters.append(AgentMasterContracts.status == status)

    # 🔹 Total distinct agents count
    total_count = (
        db.query(func.count(func.distinct(Agents.npn)))
        .select_from(AgentMasterContracts)
        .join(Agents, Agents.npn == AgentMasterContracts.npn)
        .filter(*filters)
        .scalar()
    )

    query = (
        db.query(
            AgentMasterContracts.carrier_id,
            AgentMasterContracts.carrier_name,
            Agents.full_name,
            Agents.npn,
            Agents.email,
            AgentMasterContracts.status,
        )
        .join(Agents, Agents.npn == AgentMasterContracts.npn)
        .filter(*filters)
        .distinct(Agents.npn)
        .order_by(Agents.npn)
    )

    if not export:
        query = query.offset(offset).limit(page_size)

    agents = query.all()

    return {
        "total_count": total_count,
        "page": page if not export else 1,
        "page_size": page_size if not export else total_count,
        "agents": agents,
    }


@router.get("/agent-licenses", response_model=AgentLicensesResponse)
def get_licenses_by_license_year(
    agent_npn: str,
    license_year: Optional[str] = Query(None, description="License year filter"),
    db: Session = Depends(get_db),
):
    """
    Get agent licenses filtered by license year from issue_date and agent_npn.
    Returns grouped lists for license, sbe_licenses, certifications.
    """
    # --------------------------------------------------
    # Reference Data
    # --------------------------------------------------
    state_dict = dict(
        db.query(
            LupLicenses.state_code,
            LupLicenses.state_name
        ).all()
    )

    sbe_license_rows = db.query(
        LupSBELicenses.state_code,
        LupSBELicenses.name,
        LupSBELicenses.qualification,
        LupSBELicenses.site_link,
        LupSBELicenses.state_name,
        LupSBELicenses.file_url,
    ).all()

    sbe_license_dict = {
        row.state_code.strip().upper(): {
            "site_name": row.name,
            "qualification": row.qualification,
            "site_link": row.site_link,
            "state_name": row.state_name,
            "file_url": row.file_url,
        }
        for row in sbe_license_rows
    }

    certifications_list = [
        row[0] for row in db.query(LupCertifications.cert_code).all()
    ]
    query = (
        db.query(
            LupAgentLicenses.state.label("state_code"),
            LupAgentLicenses.type,
            LupAgentLicenses.status,
            LupAgentLicenses.transaction_id,
            LupAgentLicenses.agent_npn,
            LupAgentLicenses.issue_date,
            LupAgentLicenses.expiry_date,
            LupAgentLicenses.license_market,
            LupAgentLicenses.license_number
        )
        .filter(
            LupAgentLicenses.agent_npn == agent_npn
        )
    )

    year_filter_applied = False

    if license_year and license_year.strip().lower() != "all":
        try:
            year_int = int(license_year)
            year_filter_applied = True

            query = query.filter(
                LupAgentLicenses.issue_date.isnot(None),
                extract("year", LupAgentLicenses.issue_date) == year_int,
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid License Year format. Must be YYYY or 'all'",
            )

    result = query.all()

    db_data = {}
    cert_data = {} 
    for row in result:
        d = dict(row._mapping)
        state_code = (d.get("state_code") or "").strip().upper()
        if not state_code:
            continue

        d["state_name"] = state_dict.get(state_code)
        db_data[(state_code, d["type"])] = d

        if d["type"] and d["type"].lower() == "cert":
            tx = str(d.get("transaction_id") or "")
            for cert in certifications_list:
                if cert in tx:
                    cert_data[cert] = d

    # --------------------------------------------------
    # LICENSES
    # --------------------------------------------------
    if year_filter_applied:
        licenses = [
            v for (code, t), v in db_data.items() if t == "lic"
        ]
    else:
        licenses = [
            db_data.get(
                (code, "lic"),
                {
                    "state_code": code,
                    "state_name": name,
                    "type": "lic",
                    "status": None,
                    "transaction_id": None,
                    "agent_npn": agent_npn,
                    "issue_date": None,
                    "expiry_date": None,
                    "license_market": None,
                    "license_number": None,
                },
            )
            for code, name in state_dict.items()
        ]

    # ---------------------------
    # SBE LICENSES
    # ---------------------------
    if year_filter_applied:
        sbe_licenses = [
            v for (code, t), v in db_data.items() if t == "sbe"
        ]
    else:
        sbe_licenses = []
        for code, meta in sbe_license_dict.items():
            if (code, "sbe") in db_data:
                record = db_data[(code, "sbe")].copy()
                record["status"] = "true"
            else:
                record = {
                    "state_code": code,
                    "type": "sbe",
                    "status": "false",
                    "transaction_id": None,
                    "agent_npn": agent_npn,
                    "issue_date": None,
                    "expiry_date": None,
                    "license_market": None,
                    "license_number": None,
                    **meta,
                }
            sbe_licenses.append(record)

    # ---------------------------
    # CERTIFICATIONS
    # ---------------------------
    if year_filter_applied:
        certifications = list(cert_data.values())
    else:
        certifications = []
        for cert in certifications_list:
            if cert in cert_data:
                record = cert_data[cert].copy()
            else:
                record = {
                    "certification_name": cert,
                    "type": "cert",
                    "status": None,
                    "transaction_id": None,
                    "agent_npn": agent_npn,
                    "issue_date": None,
                    "license_market": None,
                    "license_number": None,
                }
            certifications.append(record)

    return {
        "license": licenses,
        "sbe_licenses": sbe_licenses,
        "certifications": certifications,
    }

@router.post("/agent-licenses")
def create_agent_license(
    licenses_data: List[AgentLicenseCreate],
    license_type: Literal["lic", "sbe", "cert"],
    db: Session = Depends(get_db)
):
    inserted_count = 0
    updated_count = 0
    deleted_count = 0

    for license_data in licenses_data:
        if not license_data.issue_date:
            license_data.issue_date = date.today()

        # --- CERT LOGIC ---
        if license_type == "cert":
            state_val = (license_data.state or "").strip().upper()
            status_val = (license_data.status or "").strip().upper()
            if state_val and status_val and state_val == status_val:
                existing = (
                    db.query(LupAgentLicenses)
                    .filter_by(agent_npn=license_data.agent_npn, type="cert", status=status_val)
                    .first()
                )

                if not existing:
                    new_license = LupAgentLicenses(
                        transaction_id=license_data.transaction_id or uuid.uuid4(),
                        agent_npn=license_data.agent_npn,
                        type=license_data.type,
                        status=license_data.status, 
                        state="",
                        issue_date=license_data.issue_date,
                        license_market=license_data.license_market,
                        license_number=license_data.license_number,
                        expiry_date=license_data.expiry_date
                    )
                    db.add(new_license)
                    inserted_count += 1
            elif state_val and not status_val:
                record_to_delete = (
                    db.query(LupAgentLicenses)
                    .filter_by(agent_npn=license_data.agent_npn, type="cert", status=state_val)
                    .first()
                )
                if record_to_delete:
                    db.delete(record_to_delete)
                    deleted_count += 1

        # --- LOGIC FOR SBE ---
        elif license_type == "sbe":
            existing_license = None
            if license_data.transaction_id:
                existing_license = db.query(LupAgentLicenses).filter_by(
                    transaction_id=license_data.transaction_id
                ).first()

            if existing_license:
                if license_data.status == "true":
                    existing_license.status = "true"
                    updated_count += 1
                elif license_data.status == "false":
                    db.delete(existing_license)
                    deleted_count += 1
            else:
                if license_data.status == "true":
                    new_transaction_id = license_data.transaction_id or uuid.uuid4()
                    new_license = LupAgentLicenses(
                        transaction_id=new_transaction_id,
                        agent_npn=license_data.agent_npn,
                        type=license_data.type,
                        status="true",
                        issue_date=license_data.issue_date,
                        state=license_data.state,
                        expiry_date=license_data.expiry_date,
                        license_market=license_data.license_market,
                        license_number=license_data.license_number
                    )
                    db.add(new_license)
                    inserted_count += 1

        # --- LOGIC FOR LIC ---
        else:
            existing_license = None
            if license_data.transaction_id:
                existing_license = db.query(LupAgentLicenses).filter_by(
                    transaction_id=license_data.transaction_id
                ).first()

            if existing_license:
                update_fields = [
                    "agent_npn", "type", "status", "state",
                    "issue_date", "expiry_date", "lic_id",
                    "license_number", "license_owner", "license_market"
                ]
                for field in update_fields:
                    new_value = getattr(license_data, field)
                    if new_value is not None:
                        setattr(existing_license, field, new_value)
                updated_count += 1
            else:
                new_transaction_id = license_data.transaction_id or uuid.uuid4()
                new_license = LupAgentLicenses(
                    transaction_id=new_transaction_id,
                    agent_npn=license_data.agent_npn,
                    type=license_data.type,
                    status=license_data.status,
                    state=license_data.state,
                    issue_date=license_data.issue_date,
                    expiry_date=license_data.expiry_date,
                    lic_id=license_data.lic_id,
                    license_number=license_data.license_number,
                    license_owner=license_data.license_owner,
                    license_market=license_data.license_market
                )
                db.add(new_license)
                inserted_count += 1

    db.commit()

    return {
        "message": "Agent License Data processed successfully",
        "inserted": inserted_count,
        "updated": updated_count,
        "deleted": deleted_count
    }

@router.get(
    "/agent-licenses/licenses",
    summary="List agent state insurance licenses",
)
def get_agent_licenses(
    agent_npn: str,
    license_year: Optional[str] = Query(None, description="Filter by license issue year or 'all'"),
    status: Optional[str] = Query(None, description="Filter by license status: Active, Inactive, or All"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sortOrder: str = Query("asc", regex="^(asc|desc)$"),
    sortColumn: str = Query("current_status"),
    db: Session = Depends(get_db),
):
    """
    Get the agent’s state insurance licenses.

    - Returns only states where a license exists in LupAgentLicenses
    - Joins with LupLicenses for state metadata
    - Adds derived Status column:
        - Active   -> expiry_date is in the future
        - Inactive -> expiry_date is in the past or null
    """

    today = date.today()

    current_status_expr = case(
        (
            LupAgentLicenses.expiry_date >= today,
            "Active",
        ),
        else_="Inactive",
    ).label("current_status")

    query = db.query(
        LupAgentLicenses.state.label("state_code"),
        LupLicenses.state_name,
        LupAgentLicenses.type,
        LupAgentLicenses.transaction_id,
        LupAgentLicenses.agent_npn,
        LupAgentLicenses.issue_date,
        LupAgentLicenses.expiry_date,
        LupAgentLicenses.license_market,
        LupAgentLicenses.license_number,
        LupAgentLicenses.status,
        current_status_expr,
    ).join(
        LupLicenses,
        func.upper(func.trim(LupLicenses.state_code)) ==
        func.upper(func.trim(LupAgentLicenses.state))
    ).filter(
        LupAgentLicenses.agent_npn == agent_npn,
        LupAgentLicenses.type == "lic",
    )

    if status and status.lower() != "all":
        if status.lower() == "active":
            query = query.filter(
                LupAgentLicenses.expiry_date >= today
            )
        elif status.lower() == "inactive":
            query = query.filter(
                or_(
                    LupAgentLicenses.expiry_date < today,
                    LupAgentLicenses.expiry_date.is_(None),
                )
            )

    if license_year and license_year.lower() != "all":
        query = query.filter(
            LupAgentLicenses.issue_date == license_year
        )

    sort_column_map = {
        "state_code": LupAgentLicenses.state,
        "state_name": LupLicenses.state_name,
        "issue_date": LupAgentLicenses.issue_date,
        "expiry_date": LupAgentLicenses.expiry_date,
        "license_number": LupAgentLicenses.license_number,
        "status": LupAgentLicenses.status,
        "current_status": current_status_expr,
    }

    sort_col = sort_column_map.get(sortColumn, current_status_expr)

    if sortOrder == "desc":
        query = query.order_by(desc(sort_col))
    else:
        query = query.order_by(asc(sort_col))

    query = query.offset((page - 1) * page_size).limit(page_size)

    rows = query.all()

    results = []
    for r in rows:
        results.append({
            "state_code": (r.state_code).strip(),
            "state_name": r.state_name,

            "type": "lic",
            "transaction_id": r.transaction_id,
            "agent_npn": agent_npn,
            "issue_date": r.issue_date,
            "expiry_date": r.expiry_date,
            "license_number": r.license_number,
            "license_market": r.license_market,
            "status": r.status,
            "current_status": r.current_status,
        })

    return results

@router.post(
    "/agent-licenses/licenses",
    summary="Add an agent state insurance license",
)
def add_agent_license(
    payload: LicenseAddRequest,
    db: Session = Depends(get_db),
):
    """
    Add a state insurance license for an agent.
    """

    existing = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.agent_npn == payload.agent_npn,
        LupAgentLicenses.type == "lic",
        LupAgentLicenses.state == payload.state_code.upper(),
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"data": None, "success": False, "message": "License already exists"},
        )

    new_license = LupAgentLicenses(
        agent_npn=payload.agent_npn,
        type="lic",
        state=payload.state_code.upper(),
        lic_id=payload.lic_id,
        issue_date=payload.issue_date,
        expiry_date=payload.expiry_date,
        license_number=payload.license_number,
        license_market=payload.license_market,
        status=payload.status,
    )

    db.add(new_license)
    db.commit()
    db.refresh(new_license)

    return {"message": "License added", "license": new_license}

@router.put(
    "/agent-licenses/licenses",
    summary="Update an agent state insurance license",
)
def update_agent_license(
    payload: LicenseUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Update an existing state insurance license by transaction_id.
    """

    license_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.transaction_id == payload.transaction_id,
        LupAgentLicenses.type == "lic",
    ).first()

    if not license_record:
        raise HTTPException(status_code=404, detail="License not found")

    if payload.state_code:
        license_record.state = payload.state_code.upper()
    if payload.issue_date:
        license_record.issue_date = payload.issue_date
    if payload.expiry_date:
        license_record.expiry_date = payload.expiry_date
    # if payload.license_number:
    #     license_record.license_number = payload.license_number
    if payload.license_market:
        license_record.license_market = payload.license_market
    # if payload.status:
    #     license_record.status = payload.status

    db.commit()
    db.refresh(license_record)

    return {"message": "License updated", "license": license_record}

@router.get(
    "/agent-licenses/licenses/state-lookup",
    summary="List all agent licenses",
)
def list_agent_licenses(
    db: Session = Depends(get_db),
):
    """
    List all agent licenses for dropdowns or autocomplete.

    Returns:
        - id/value pairs where `id` is the license state code and
          `value` is a descriptive string combining state code and state name.
    
    Use Case:
    - Populate agent license dropdowns
    - Display license reference data
    - Support agent eligibility and onboarding flows
    """

    licenses = (
        db.query(
            LupLicenses.state_code,
            LupLicenses.state_name
        )
        .order_by(LupLicenses.state_code)
        .all()
    )

    return [
        {
            "id": l.state_code.strip(),
            "value": f"{l.state_code.strip()} - {l.state_name}".strip(),
        }
        for l in licenses
        if l.state_code
    ]

@router.delete(
    "/agent-licenses/licenses",
    summary="Delete an agent state insurance license",
)
def delete_agent_license(
    transaction_id: str = Query(..., description="Transaction ID of the license to delete"),
    db: Session = Depends(get_db),
):
    """
    Delete a state insurance license by transaction_id.
    """

    license_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.transaction_id == transaction_id,
        LupAgentLicenses.type == "lic",
    ).first()

    if not license_record:
        raise HTTPException(status_code=404, detail="License not found")

    db.delete(license_record)
    db.commit()

    return {"message": "License deleted", "transaction_id": transaction_id}

@router.get(
    "/agent-licenses/sbe-licenses",
    summary="List agent State-Based Exchange (SBE) access",
)
def get_agent_sbe_licenses(
    agent_npn: str,
    license_year: Optional[str] = Query(None, description="Filter by SBE certification year or 'all'"),
    db: Session = Depends(get_db),
):
    """
    Get the agent’s State-Based Exchange (SBE) access by state.

    - SBE access determines whether an agent can sell through a state's health marketplace
    - Includes SBE reference metadata:
        - Site name
        - Qualification requirements
        - Marketplace URL
        - Reference file links

    License Year Behavior:
    - If license_year is provided (YYYY):
        - Returns only SBE records issued in that year
    - If license_year is not provided or set to 'all':
        - Returns all SBE states
        - Indicates whether the agent has access ("true" / "false")

    Use Case:
    - Determine agent eligibility to sell on state marketplaces
    - Provide direct links to SBE portals
    - Track missing SBE approvals
    """

    query = db.query(
        LupAgentLicenses.state.label("state_code"),
        LupSBELicenses.state_name,
        LupSBELicenses.name.label("site_name"),
        LupSBELicenses.qualification,
        LupSBELicenses.site_link,
        LupSBELicenses.file_url,

        LupAgentLicenses.type,
        LupAgentLicenses.status,
        LupAgentLicenses.transaction_id,
        LupAgentLicenses.agent_npn,
        LupAgentLicenses.issue_date,
        LupAgentLicenses.expiry_date,
        LupAgentLicenses.license_market,
        LupAgentLicenses.license_number,
        LupAgentLicenses.certification_year,
    ).join(
        LupSBELicenses,
        func.upper(func.trim(LupSBELicenses.state_code)) ==
        func.upper(func.trim(LupAgentLicenses.state))
    ).filter(
        LupAgentLicenses.agent_npn == agent_npn,
        LupAgentLicenses.type == "sbe",
    )

    if license_year and license_year.lower() != "all":
        query = query.filter(
            LupAgentLicenses.certification_year == license_year
        )

    rows = query.all()

    results = []
    for r in rows:
        results.append({
            "state_code": (r.state_code or "").strip().upper(),
            "state_name": r.state_name,
            "site_name": r.site_name,
            "qualification": r.qualification,
            "site_link": r.site_link,
            "file_url": r.file_url,

            "type": "sbe",
            "status": r.status,
            "transaction_id": r.transaction_id,
            "agent_npn": agent_npn,
            "issue_date": r.issue_date,
            "expiry_date": r.expiry_date,
            "license_market": r.license_market,
            "license_number": r.license_number,
            "certification_year": r.certification_year,
        })

    def sort_key(item):
        year = item.get("certification_year")
        return (
            item["state_code"],
            -(int(year) if year else 0),
        )

    results.sort(key=sort_key)
    return results

@router.post(
    "/agent-licenses/sbe-licenses",
    summary="Add an agent SBE license",
)
def add_agent_sbe_license(
    payload: SBEAddRequest,
    db: Session = Depends(get_db),
):
    """
    Add a State-Based Exchange (SBE) license for an agent.

    - `agent_npn`: Agent NPN
    - `state_code`: Two-letter state code
    - `issue_date`: License issue date
    - `expiry_date`: License expiry date (optional)
    - `license_number`: License number (optional)
    - `license_market`: Marketplace info (optional)
    - `certification_year`: Year of certification (optional)
    """

    # Check if license already exists for this agent and state
    existing_license = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.agent_npn == payload.agent_npn,
        LupAgentLicenses.type == "sbe",
        LupAgentLicenses.certification_year == payload.certification_year,
        LupAgentLicenses.state == payload.state_code.upper(),
    ).first()

    if existing_license:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"data": None, "success": False, "message": "SBE license already exists"},
        )

    new_license = LupAgentLicenses(
        agent_npn=payload.agent_npn,
        type="sbe",
        state=payload.state_code.upper(),
        issue_date=payload.issue_date,
        expiry_date=payload.expiry_date,
        license_number=payload.license_number,
        license_market=payload.license_market,
        certification_year=payload.certification_year,
    )

    db.add(new_license)
    db.commit()
    db.refresh(new_license)

    return {"message": "SBE license added", "license": new_license}

@router.put(
    "/agent-licenses/sbe-licenses",
    summary="Update an agent SBE license",
)
def update_agent_sbe_license(
    payload: SBEUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Update an existing State-Based Exchange (SBE) license by `transaction_id`.

    - Fields are optional; only provided fields will be updated
    """
    license_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.transaction_id == payload.transaction_id
    ).first()

    if not license_record:
        raise HTTPException(status_code=404, detail="SBE license not found")

    if payload.state_code:
        license_record.state = payload.state_code.upper()
    if payload.issue_date:
        license_record.issue_date = payload.issue_date
    if payload.expiry_date:
        license_record.expiry_date = payload.expiry_date
    if payload.license_number:
        license_record.license_number = payload.license_number
    if payload.license_market:
        license_record.license_market = payload.license_market
    if payload.certification_year:
        license_record.certification_year = payload.certification_year

    db.commit()
    db.refresh(license_record)

    return {"message": "SBE license updated", "license": license_record}

@router.get(
    "/agent-licenses/sbe-states/lookup",
    summary="List all State-Based Exchange (SBE) states",
)
def list_sbe_states(
    db: Session = Depends(get_db),
):
    """
    List all State-Based Exchange (SBE) states and marketplace metadata.

    Returns:
    Returns id/value pairs for dropdowns or autocomplete

    Use Case:
    - Populate SBE state dropdowns
    - Display marketplace reference data
    - Support agent eligibility and onboarding flows
    """

    states = (
        db.query(
            LupSBELicenses.state_code,
            LupSBELicenses.state_name,
            LupSBELicenses.name.label("site_name"),
        )
        .order_by(LupSBELicenses.state_code)
        .all()
    )

    return [
        {
            "id": s.state_code.strip(),
            "value": f"{s.state_code.strip()} - {s.state_name}".strip(),
        }
        for s in states
        if s.state_code
    ]

@router.delete(
    "/agent-licenses/sbe-licenses",
    summary="Delete an agent SBE license",
)
def delete_agent_sbe_license(
    transaction_id: str = Query(..., description="Transaction ID of the SBE license to delete"),
    db: Session = Depends(get_db),
):
    """
    Delete a State-Based Exchange (SBE) license by `transaction_id`.

    - Returns a confirmation message upon successful deletion
    - Returns 404 if license is not found
    """
    license_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.transaction_id == transaction_id,
        LupAgentLicenses.type == "sbe"
    ).first()

    if not license_record:
        raise HTTPException(status_code=404, detail="SBE license not found")

    db.delete(license_record)
    db.commit()

    return {"message": "SBE license deleted", "transaction_id": transaction_id}

@router.get(
    "/agent-licenses/certifications",
    summary="List agent certifications and training status",
)
def get_agent_certifications(
    agent_npn: str,
    license_year: Optional[str] = Query(None, description="Filter by certification issue year or 'all'"),
    db: Session = Depends(get_db),
):
    """
    Get the agent’s professional certifications and training completions.

    - Certifications are not state-based
    - Each certification includes `cert_code` and `description`
    - Certification completion is identified from license transaction data

    License Year Behavior:
    - If license_year is provided (YYYY):
        - Returns certifications earned in that year only
    - If license_year is not provided or set to 'all':
        - Returns all known certifications
        - Indicates completed vs missing certifications

    Use Case:
    - Verify agent training and compliance
    - Display certification completion status
    - Identify required certifications not yet completed
    """
    query = db.query(
        LupCertifications.cert_code.label("certification_name"),
        LupCertifications.description.label("certification_description"),

        LupAgentLicenses.transaction_id,
        LupAgentLicenses.agent_npn,
        LupAgentLicenses.certification_year,
        LupAgentLicenses.npn_valid,
        LupAgentLicenses.issue_date,
        LupAgentLicenses.license_market,
        LupAgentLicenses.license_number,
        LupAgentLicenses.status,
    ).join(
        LupCertifications,
        func.upper(func.trim(LupCertifications.cert_code)) ==
        func.upper(func.trim(LupAgentLicenses.status))
    ).filter(
        LupAgentLicenses.agent_npn == agent_npn,
        LupAgentLicenses.type == "cert",
    )

    if license_year and license_year.lower() != "all":
        query = query.filter(
            # extract("year", LupAgentLicenses.certification_year) == int(license_year)
            LupAgentLicenses.certification_year == license_year
        )

    rows = query.all()

    results = []
    for r in rows:
        results.append({
            # "state_name": (r.state_name).strip(),
            "certification_name": r.certification_name,
            "certification_description": r.certification_description,

            "type": "cert",
            "transaction_id": r.transaction_id,
            "agent_npn": agent_npn,
            "certification_year": r.certification_year,
            "npn_valid": r.npn_valid,
            "issue_date": r.issue_date,
            "license_market": r.license_market,
            "license_number": r.license_number,
            "status": r.status,
        })

    def sort_key(item):
        year = item.get("certification_year")
        return (
            item["certification_name"],
            -(int(year) if year else 0),
        )

    results.sort(key=sort_key)
    return results

@router.post(
    "/agent-licenses/certifications",
    summary="Add an agent certification",
)
def edit_agent_certification(
    payload: CertificationAddRequest,
    db: Session = Depends(get_db),
):
    """
    Add an agent's certification.

    - `agent_npn`: The agent NPN
    - `certification_name`: The code/name of the certification
    - `issue_date`: Certification issue date
    - `certification_year`: Certification year
    """

    # Check if the certification exists for this agent
    cert_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.agent_npn == payload.agent_npn,
        LupAgentLicenses.type == "cert",
        LupAgentLicenses.certification_year == payload.certification_year,
        LupAgentLicenses.status.ilike(f"%{payload.certification_name}%")
    ).first()

    if cert_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"data": None, "success": False, "message": "Certification already exist"},
        )

    # If it doesn't exist, create a new record
    new_cert = LupAgentLicenses(
        agent_npn=payload.agent_npn,
        type="cert",
        status=payload.certification_name,
        issue_date=payload.issue_date,
        certification_year=payload.certification_year,
        npn_valid=payload.npn_valid,
    )
    db.add(new_cert)
    db.commit()
    db.refresh(new_cert)
    return {"message": "Certification added", "certification": new_cert}

@router.put(
    "/agent-licenses/certifications",
    summary="Update an agent certification",
)
def update_agent_certification(
    payload: CertificationUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Update an agent's certification by transaction_id.

    - `transaction_id`: The unique ID of the certification record
    - `certification_name`: New certification name/code (optional)
    - `issue_date`: New issue date (optional)
    - `certification_year`: New certification year (optional)
    """
    cert_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.transaction_id == payload.transaction_id
    ).first()

    if not cert_record:
        raise HTTPException(status_code=404, detail="Certification not found")

    if payload.certification_name:
        cert_record.status = payload.certification_name
    if payload.issue_date:
        cert_record.issue_date = payload.issue_date
    if payload.certification_year:
        cert_record.certification_year = payload.certification_year
    if payload.npn_valid:
        cert_record.npn_valid = payload.npn_valid

    db.commit()
    db.refresh(cert_record)

    return {"message": "Certification updated", "certification": cert_record}

@router.delete(
    "/agent-licenses/certifications",
    summary="Delete an agent certification",
)
def delete_agent_certification(
    transaction_id: str = Query(..., description="Transaction ID of the certification to delete"),
    db: Session = Depends(get_db),
):
    """
    Delete a certification record by `transaction_id`.

    - Returns a confirmation message upon successful deletion
    - Returns 404 if certification is not found
    """
    cert_record = db.query(LupAgentLicenses).filter(
        LupAgentLicenses.transaction_id == transaction_id,
        LupAgentLicenses.type == "cert"
    ).first()

    if not cert_record:
        raise HTTPException(status_code=404, detail="Certification not found")

    db.delete(cert_record)
    db.commit()

    return {"message": "Certification deleted", "transaction_id": transaction_id}

@router.get(
    "/agent-licenses/certifications/lookup",
    summary="Search certification codes list in id value pair",
)
def get_certification_lookup(
    db: Session = Depends(get_db),
):
    """
    List or search available certifications.

    - Returns id/value pairs for dropdowns or autocomplete
    """

    query = db.query(
        LupCertifications.cert_code,
        LupCertifications.description,
    )

    results = query.order_by(LupCertifications.cert_code).limit(100).all()

    return [
        {
            "id": row.cert_code.strip(),
            "value": row.cert_code.strip(),
            # "value": f"{row.cert_code} - {row.description}".strip(),
        }
        for row in results
        if row.cert_code
    ]

@router.get("/agent-compliance", dependencies=[Depends(security)])
def get_agent_compliance(
    agent_npn: str = Query(..., description="Agent NPN to filter compliance records"),
    page: int = Query(1, ge=1, description="Page number (starting from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    db: Session = Depends(get_db),
):
    base_query = db.query(AgentCompliance).filter(AgentCompliance.npn == agent_npn)

    total_count = base_query.with_entities(func.count()).scalar() or 0

    results = (
        base_query
        .order_by(AgentCompliance.created_time.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
        .all()
    )

    items = [
        {
            "pk_id": r.pk_id,
            "inquiry_id": r.inquiry_id,
            "npn": r.npn,
            "inquiry_type": r.inquiry_type,
            "inquiry_status": r.inquiry_status,
            "subject_of_inquiry": r.subject_of_inquiry,
            "agent_status": r.agent_status,
            "date_received": r.date_received,
            "due_date": r.due_date,
            "date_resolved": r.date_resolved,
            "subject_email": r.subject_email,
            "inquiry_description": r.inquiry_description,
            "related_carrier": r.related_carrier,
            "comm_sent_to_agent": r.comm_sent_to_agent,
            "comm_sent_to_agency": r.comm_sent_to_agency,
            "dir_upline": r.dir_upline,
            "carrier_case_no": r.carrier_case_no,
            "agent_name": r.agent_name,
            "agent_email": r.agent_email,
            "created_by": r.created_by,
            "created_time": r.created_time,
            "modified_by": r.modified_by,
            "modified_time": r.modified_time,
            "last_activity_time": r.last_activity_time
        }
        for r in results
    ]

    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "items": items,
    }


@router.post("/agent-compliance", response_model=AgentComplianceResponseSchema, dependencies=[Depends(security)])
async def create_agent_compliance(
    data: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    files_data: str = Form(default="[]"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Parse request body
    try:
        payload = AgentComplianceCreateSchema(**json.loads(data))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {str(e)}")

    # Parse files metadata
    try:
        files_metadata = json.loads(files_data)  # List of {attachment_type, is_private}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid files_data format: {str(e)}")

    # Validate agent
    agent = db.query(Agents).filter(Agents.npn == payload.npn).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with NPN {payload.npn} not found")

    now = datetime.now(timezone.utc)

    try:
        # Generate inquiry_id
        max_val = db.query(AgentCompliance.inquiry_id).order_by(
            AgentCompliance.inquiry_id.desc()
        ).first()

        if max_val and max_val[0]:
            last_num = int(max_val[0].replace("INQ-", "").replace("INQ_", ""))
            next_num = max(last_num + 1, 15000)  #if existing < 15000, jump to 15000
        else:
            next_num = 15000

        inquiry_id = f"INQ_{next_num:05d}"

        # Create compliance record
        new_compliance = AgentCompliance(
            inquiry_id=inquiry_id,
            npn=payload.npn,
            inquiry_type=payload.inquiry_type,
            inquiry_status=payload.inquiry_status,
            subject_of_inquiry=payload.subject_of_inquiry,
            agent_status=payload.agent_status,
            date_received=payload.date_received,
            due_date=payload.due_date,
            date_resolved=payload.date_resolved,
            subject_email=payload.subject_email,
            inquiry_description=payload.inquiry_description,
            related_carrier=payload.related_carrier,
            comm_sent_to_agent=payload.comm_sent_to_agent,
            comm_sent_to_agency=payload.comm_sent_to_agency,
            dir_upline=payload.dir_upline,
            carrier_case_no=payload.carrier_case_no,
            agent_name=payload.agent_name or agent.full_name,
            agent_email=payload.agent_email or agent.email,
            created_by=current_user.get("email", ""),
            created_time=now,
            modified_by=current_user.get("email", ""),
            modified_time=now,
            last_activity_time=now,
        )

        db.add(new_compliance)
        db.flush()

        # Create notes
        created_notes = []
        if payload.notes:
            for note in payload.notes:
                new_note = CrmNotes(
                    type=note.type,
                    sub_type="Inquiry",
                    description=note.description,
                    agent_id=agent.pk_id,
                    agent_npn=payload.npn,
                    source_id=new_compliance.pk_id,
                    user_id=current_user.get("user_id"),
                    time_stamp=now,
                    is_private=note.is_private,
                )
                db.add(new_note)
                created_notes.append(new_note)

        # Upload attachments
        created_attachments = []
        if files:
            for i, file in enumerate(files):
                if not file or not file.filename:
                    continue

                # Get metadata for this file, fallback to defaults if not provided
                meta = files_metadata[i] if i < len(files_metadata) else {}
                attachment_type = meta.get("attachment_type", "")
                is_private = meta.get("is_private", False)

                file_content = await file.read()

                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                file_ext = file.filename.rsplit(".", 1)
                filename_with_ts = f"{file_ext[0]}_{timestamp}.{file_ext[1]}" if len(file_ext) == 2 else f"{file.filename}_{timestamp}"
                blob_path = f"Documents/{payload.npn}/{filename_with_ts}"

                await upload_blob_to_path(blob_path=blob_path, file=file_content)

                new_attachment = CrmAttachments(
                    path=blob_path,
                    file_type=attachment_type,
                    agent_id=agent.pk_id,
                    user_id=current_user["user_id"],
                    time_stamp=now,
                    source_id=new_compliance.pk_id,
                    is_private=is_private,
                    npn=payload.npn,
                    
                )
                db.add(new_attachment)
                created_attachments.append(new_attachment)

        db.commit()
        db.refresh(new_compliance)

        # Prepare response
        response = AgentComplianceResponseSchema.model_validate(new_compliance)
        response.notes = [
            ComplianceNoteOut.model_validate(n).model_dump()
            for n in created_notes
        ] if created_notes else []

        response.attachments = [
            ComplianceAttachmentOut.model_validate(a).model_dump()
            for a in created_attachments
        ] if created_attachments else []

        return response

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create compliance record: {str(e)}"
        )

    
@router.patch("/agent-compliance/{pk_id}", response_model=AgentComplianceResponseSchema, dependencies=[Depends(security)])
async def update_agent_compliance(
    pk_id: UUID,
    data: AgentComplianceUpdateSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    compliance = db.query(AgentCompliance).filter(AgentCompliance.pk_id == pk_id).first()
    if not compliance:
        raise HTTPException(status_code=404, detail=f"Compliance record {pk_id} not found")

    now = datetime.now(timezone.utc)

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(compliance, field, value)

    compliance.modified_by = current_user.get("email", "")
    compliance.modified_time = now
    compliance.last_activity_time = now

    db.commit()
    db.refresh(compliance)

    return compliance


# @router.post("/agent-compliance", response_model=AgentComplianceResponseSchema)
# async def create_agent_compliance(
#     data: str = Form(...),  # JSON string
#     files: List[UploadFile] = File(None),
#     db: Session = Depends(get_db),
#     current_user: dict = Depends(get_current_user),
# ):
    
#     agent = db.query(Agents).filter(Agents.npn == npn).first()
#     if not agent:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=f"Agent with NPN {npn} not found"
#         )
#     if not agent_name:
#         agent_name = agent.full_name
#     if not agent_email:
#         agent_email = agent.email
    
#     date_received_dt = datetime.fromisoformat(date_received) if date_received else None
#     due_date_dt = datetime.fromisoformat(due_date) if due_date else None
#     date_resolved_dt = datetime.fromisoformat(date_resolved) if date_resolved else None
    
#     notes_list = [n.strip().strip('"').strip("'") for n in notes.split(',') if n.strip()] if notes else []
            
#     try:
#         max_val = db.query(AgentCompliance.inquiry_id).order_by(
#             AgentCompliance.inquiry_id.desc()
#         ).first()
        
#         if max_val and max_val[0]:
#             last_num = int(max_val[0].replace("INQ-", ""))
#             next_num = last_num + 1
#         else:
#             next_num = 1
        
#         inquiry_id = f"INQ-{next_num:05d}"
#         new_compliance = AgentCompliance(
#             inquiry_id=inquiry_id,
#             npn=npn,
#             inquiry_type=inquiry_type,
#             inquiry_status=inquiry_status,
#             subject_of_inquiry=subject_of_inquiry,
#             agent_status=agent_status,
#             date_received=date_received_dt,
#             due_date=due_date_dt,
#             date_resolved=date_resolved_dt,
#             subject_email=subject_email,
#             inquiry_description=inquiry_description,
#             related_carrier=related_carrier,
#             comm_sent_to_agent=comm_sent_to_agent,
#             comm_sent_to_agency=comm_sent_to_agency,
#             dir_upline=dir_upline,
#             carrier_case_no=carrier_case_no,
#             agent_name=agent_name,
#             agent_email=agent_email,
#             created_by=current_user.get("login", ""),
#             created_time=datetime.now(timezone.utc),
#             modified_by=current_user.get("login", ""),
#             modified_time=datetime.now(timezone.utc),
#             last_activity_time=datetime.now(timezone.utc),
#         )
        
#         db.add(new_compliance)
#         db.flush()
        
#         compliance_notes = []
#         if notes_list:
#             for note_description in notes_list:
#                 if note_description and note_description:
#                     new_note = CrmNotes(
#                         type=note_type,
#                         sub_type="Inquiry",
#                         description=note_description,
#                         agent_id=agent.pk_id,
#                         agent_npn=npn,
#                         source_id=new_compliance.pk_id,
#                         user_id=current_user.get("user_id"),
#                         time_stamp=datetime.now(timezone.utc),
#                         is_private=is_private,
#                     )
#                     db.add(new_note)
#                     compliance_notes.append(new_note)

#         compliance_attachments = []
#         if files:
#             for file in files:
#                 if not file or not hasattr(file, 'filename') or not file.filename:
#                     continue
                
#                 try:
#                     file_name = file.filename
#                     blob_path = f"Documents/compliance/{agent.pk_id}/{file_name}"
                    
#                     file_content = await file.read()
                    
#                     await upload_blob_to_path(
#                         blob_path=blob_path,
#                         file=file_content
#                     )
                    
#                     new_attachment = CrmAttachments(
#                         path=blob_path,
#                         file_type="Compliance",
#                         agent_id=agent.pk_id,
#                         user_id=current_user["user_id"],
#                         time_stamp=datetime.now(timezone.utc),
#                     )
#                     db.add(new_attachment)
#                     compliance_attachments.append(new_attachment)
#                 except Exception as file_error:
#                     raise HTTPException(status_code=500, detail=f"File upload failed: {str(file_error)}")
        
#         db.commit()
#         db.refresh(new_compliance)
    
#         notes_list_dict = [
#             ComplianceNoteOut.model_validate(note).model_dump()
#             for note in compliance_notes
#         ] if compliance_notes else None
        
#         attachments_list_dict = [
#             ComplianceAttachmentOut.model_validate(att).model_dump()
#             for att in compliance_attachments
#         ] if compliance_attachments else None
        
#         response = AgentComplianceResponseSchema.model_validate(new_compliance)
#         response.notes = notes_list_dict
#         response.attachments = attachments_list_dict
        
#         return response
    
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to create compliance record: {str(e)}"
#         )

@router.get("/agent-compliance/carrier-contracts", dependencies=[Depends(security)])
def get_carrier_contracts(
    npn: str,
    db: Session = Depends(get_db)
):

    contracts = db.query(
        AgentMasterContracts.carrier_id,
        AgentMasterContracts.carrier_name,
        AgentMasterContracts.upline_npn,
        AgentMasterContracts.status.label("contract_status"),
        Agents.full_name.label("agent_name"),
        Agents.status,
        Agents.type
    ).outerjoin(
        Agents,
        AgentMasterContracts.upline_npn == Agents.npn
    ).filter(
        AgentMasterContracts.npn == npn,
        # AgentMasterContracts.status == "Active"
    ).all()

    if not contracts:
        return []

    grouped_carriers = {}

    for row in contracts:
        carrier_id = row.carrier_id

        if carrier_id not in grouped_carriers:
            grouped_carriers[carrier_id] = {
                "carrier_id": row.carrier_id,
                "carrier_name": row.carrier_name,
                "status": row.contract_status,
                "uplines": []
            }

        # Avoid duplicate uplines per carrier
        if not any(u["upline_npn"] == row.upline_npn 
                   for u in grouped_carriers[carrier_id]["uplines"]):

            grouped_carriers[carrier_id]["uplines"].append({
                "upline_npn": row.upline_npn,
                "upline_name": row.agent_name,
                "upline_status": row.status,
                "upline_type": row.type,
            })

    return list(grouped_carriers.values())

@router.get("/agent-compliance/{pk_id}", dependencies=[Depends(security)])
def get_agent_compliance_detail(
    pk_id: UUID,
    db: Session = Depends(get_db)
):
    agent_compliance_data = db.query(
        AgentCompliance.pk_id,
        AgentCompliance.inquiry_id,
        AgentCompliance.npn,
        AgentCompliance.inquiry_type,
        AgentCompliance.inquiry_status,
        AgentCompliance.subject_of_inquiry,
        AgentCompliance.agent_status,
        AgentCompliance.date_received,
        AgentCompliance.due_date,
        AgentCompliance.date_resolved,
        AgentCompliance.subject_email,
        AgentCompliance.inquiry_description,
        AgentCompliance.related_carrier,
        AgentCompliance.comm_sent_to_agent,
        AgentCompliance.comm_sent_to_agency,
        AgentCompliance.dir_upline,
        AgentCompliance.carrier_case_no,
        AgentCompliance.agent_name,
        AgentCompliance.agent_email,
        AgentCompliance.created_by,
        AgentCompliance.created_time,
        AgentCompliance.modified_by,
        AgentCompliance.modified_time,
        AgentCompliance.last_activity_time,
        Carrier.vendor_name.label("carrier_name")
    ).join(
        Carrier, Carrier.id == AgentCompliance.related_carrier, isouter=True
    ).filter(AgentCompliance.pk_id == compliance_id).first()

    if not agent_compliance_data:
        raise HTTPException(status_code=404, detail="Compliance record not found")

    agents_data = db.query(Agents).filter(or_(Agents.npn == agent_compliance_data.npn, Agents.npn == agent_compliance_data.dir_upline)).all()

    agent_data = next(
        (a for a in agents_data if a.npn == agent_compliance_data.npn),
        None
    )
    upline_data = next(
        (a for a in agents_data if a.npn == agent_compliance_data.dir_upline),
        None
    )

    agent_emails = db.query(
        AgentCommunication.value,
        AgentCommunication.primary,
        CommunicationMedium.type,
        CommunicationMedium.sub_type
        ).join(
            CommunicationMedium, CommunicationMedium.pk_id == AgentCommunication.communication_id
        ).filter(
            AgentCommunication.agent_id == agent_data.pk_id, CommunicationMedium.type == "email"
        ).all()
    
    upline_emails = db.query(
        AgentCommunication.value,
        AgentCommunication.primary,
        CommunicationMedium.type,
        CommunicationMedium.sub_type
        ).join(
            CommunicationMedium, CommunicationMedium.pk_id == AgentCommunication.communication_id
        ).filter(
            AgentCommunication.agent_id == upline_data.pk_id, CommunicationMedium.type == "email"
        ).all()
    
    def extract_email(emails) -> str | None:
        """Get compliance email first, fallback to primary, else None."""
        compliance_email = next(
            (e.value for e in emails if e.sub_type and e.sub_type.lower() == "compliance"),
            None
        )
        if compliance_email:
            return compliance_email
        return next(
            (e.value for e in emails if e.primary),
            None
        )
    
    result = {
        "pk_id": agent_compliance_data.pk_id,
        "inquiry_id": agent_compliance_data.inquiry_id,
        "npn": agent_compliance_data.npn,
        "inquiry_type": agent_compliance_data.inquiry_type,
        "inquiry_status": agent_compliance_data.inquiry_status,
        "subject_of_inquiry": agent_compliance_data.subject_of_inquiry,
        "agent_status": agent_compliance_data.agent_status,
        "date_received": agent_compliance_data.date_received,
        "due_date": agent_compliance_data.due_date,
        "date_resolved": agent_compliance_data.date_resolved,
        "subject_email": agent_compliance_data.subject_email,
        "inquiry_description": agent_compliance_data.inquiry_description,
        "related_carrier": agent_compliance_data.carrier_name,
        "comm_sent_to_agent": agent_compliance_data.comm_sent_to_agent,
        "comm_sent_to_agency": agent_compliance_data.comm_sent_to_agency,
        "dir_upline": upline_data.full_name if upline_data else None,
        "carrier_case_no": agent_compliance_data.carrier_case_no,
        "agent_name": agent_compliance_data.agent_name,
        "agent_email": extract_email(agent_emails),
        "upline_email": extract_email(upline_emails),
        "created_by": agent_compliance_data.created_by,
        "created_time": agent_compliance_data.created_time,
        "modified_by": agent_compliance_data.modified_by,
        "modified_time": agent_compliance_data.modified_time,
        "last_activity_time": agent_compliance_data.last_activity_time
    }

    return result

@router.get("/agents_financial/{agent_id}", response_model=List[dict])
async def agility_get_financials(agent_id: str, db: Session = Depends(get_db)):

    records = (
        db.query(AgentFinancial)
        .filter(AgentFinancial.agent_id == str(agent_id))
        .order_by(
            AgentFinancial.is_primary.desc()
        )
        .all()
    )

    if not records:
        return []

    return [
        {
            "pkId": str(r.pk_id) if getattr(r, "pk_id", None) else None,
            "agentId": r.agent_id,
            "bankName": r.bank_name,
            "accountNumber": r.account_number,
            "routingNumber": r.routing_number,
            "isPrimary": r.is_primary,
        }
        for r in records
    ]

@router.post("/agents_financial/{agent_id}", response_model=dict)
async def create_financial_record(
    agent_id: str,
    payload: SimpleFinancialUpsertRequest,
    db: Session = Depends(get_db),
):
    if payload.isPrimary:
        db.query(AgentFinancial).filter(
            AgentFinancial.agent_id == str(agent_id)
        ).update({"is_primary": False})
    
    new_rec = AgentFinancial(
        pk_id=str(uuid4()),
        agent_id=str(agent_id),
        bank_name=payload.bankName,
        routing_number=payload.routingNumber,
        account_number=payload.accountNumber,
        is_primary=payload.isPrimary if payload.isPrimary is not None else False,
    )

    db.add(new_rec)
    db.commit()
    db.refresh(new_rec)

    return {
        "pkId": str(new_rec.pk_id),
        "agentId": new_rec.agent_id,
        "bankName": new_rec.bank_name,
        "routingNumber": new_rec.routing_number,
        "accountNumber": new_rec.account_number,
        "isPrimary": new_rec.is_primary,
    }
@router.put("/agents_financial/{agent_id}", response_model=dict)
async def bulk_update_financial(
    agent_id: str,
    payload: FinancialBulkUpdateRequest,
    db: Session = Depends(get_db),
):
    updated_records = []

    for item in payload.items:
        try:
            pk_uuid = UUID(item.pkId)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid pkId format: {item.pkId}")

        rec = (
            db.query(AgentFinancial)
            .filter(
                AgentFinancial.pk_id == str(pk_uuid),
                AgentFinancial.agent_id == str(agent_id)
            )
            .first()
        )

        if not rec:
            raise HTTPException(
                status_code=404,
                detail=f"Financial record not found for pkId {item.pkId}"
            )

        if item.bankName is not None:
            rec.bank_name = item.bankName
        if item.routingNumber is not None:
            rec.routing_number = item.routingNumber
        if item.accountNumber is not None:
            rec.account_number = item.accountNumber
        if item.isPrimary is not None:
            rec.is_primary = item.isPrimary

        updated_records.append(str(rec.pk_id))

    db.commit()

    return {
        "updatedCount": len(updated_records),
        "updatedRecords": updated_records,
    }

@router.get("/agents/{agent_id}/phone-text")
def get_agent_phone_text(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(Agents.pk_id).filter(Agents.pk_id == agent_id).first()
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    communications = (
        db.query(
            AgentCommunication.pk_id,
            CommunicationMedium.pk_id.label("communication_id"),
            CommunicationMedium.sub_type,
            AgentCommunication.value,
            AgentCommunication.extension,
            AgentCommunication.text_opt,
            AgentCommunication.dnd,
            AgentCommunication.ai_pre_recording,
            AgentCommunication.primary
        )
        .join(CommunicationMedium, AgentCommunication.communication_id == CommunicationMedium.pk_id)
        .filter(AgentCommunication.agent_id == str(agent.pk_id), CommunicationMedium.type == "phone")
        .all()
    )

    return [
        {
            "pk_id": str(c.pk_id),
            "communication_id": str(c.communication_id),
            "type": c.sub_type,
            "phone": c.value,
            "extension": c.extension,
            "text_opt_in": c.text_opt,
            "do_not_call": c.dnd,
            "ai_pre_recording_opt_in": c.ai_pre_recording,
            "primary": c.primary
        }
        for c in communications
    ]


@router.post("/agents/{agent_id}/phone-text")
def create_agent_phone_text(
    agent_id: str,
    phone_data: AgentPhoneTextCreateRequest,
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    comm_medium = db.query(CommunicationMedium).filter(
        func.lower(CommunicationMedium.sub_type) == func.lower(phone_data.communication_type)
    ).first()
    if not comm_medium:
        raise HTTPException(status_code=400, detail=f"Invalid communication_type:{phone_data.communication_type}")
    existing_phone_count = db.query(AgentCommunication).join(
        CommunicationMedium,
        AgentCommunication.communication_id == CommunicationMedium.pk_id
    ).filter(
        AgentCommunication.agent_id == agent_id,
        func.lower(CommunicationMedium.type) == "phone"
    ).count()
    if existing_phone_count == 0:
        phone_data.primary = True
    
    if phone_data.primary:
        phone_comm_ids = [
            row[0] for row in db.query(CommunicationMedium.pk_id).filter(
                func.lower(CommunicationMedium.type) == "phone"
            ).all()
        ]
        db.query(AgentCommunication).filter(
            AgentCommunication.agent_id == agent_id,
            AgentCommunication.communication_id.in_(phone_comm_ids)
        ).update({AgentCommunication.primary: False}, synchronize_session=False)
    
    new_phone = AgentCommunication(
        agent_id=agent_id,
        communication_id=comm_medium.pk_id,
        value=phone_data.phone,
        extension=phone_data.extension,
        text_opt=phone_data.text_opt_in,
        dnd=phone_data.do_not_call,
        ai_pre_recording=phone_data.ai_pre_recording_opt_in,
        primary=phone_data.primary
    )
    
    db.add(new_phone)
    db.commit()
    db.refresh(new_phone)
    
    return {
        "message": "Phone record created successfully",
        "pk_id": str(new_phone.pk_id)
    }

@router.patch("/agents/{agent_id}/phone-text")
def update_agent_phone_text(
    agent_id: str,
    phone_data: AgentPhoneTextUpdateRequest,
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    phone_record = (
        db.query(AgentCommunication)
        .filter(
            AgentCommunication.pk_id == str(phone_data.pk_id),
            AgentCommunication.agent_id == str(agent_id)
        )
        .first()
    )
    
    if not phone_record:
        raise HTTPException(
            status_code=404,
            detail="Phone record not found"
        )
 
    if phone_data.communication_type is not None:
        comm_medium = db.query(CommunicationMedium).filter(
            func.lower(CommunicationMedium.sub_type) == func.lower(phone_data.communication_type)
        ).first()
        if not comm_medium:
            raise HTTPException(status_code=400, detail=f"Invalid communication_type: {phone_data.communication_type}")
        phone_record.communication_id = comm_medium.pk_id 
    if phone_data.phone is not None:
        phone_record.value = phone_data.phone
    if phone_data.extension is not None:
        phone_record.extension = phone_data.extension
    if phone_data.text_opt_in is not None:
        phone_record.text_opt = phone_data.text_opt_in
    if phone_data.ai_pre_recording_opt_in is not None:
        phone_record.ai_pre_recording = phone_data.ai_pre_recording_opt_in
    if phone_data.do_not_call is not None:
        phone_record.dnd = phone_data.do_not_call
    if phone_data.primary is not None:
        if phone_data.primary:
            other_phone = db.query(AgentCommunication.pk_id).join(
                CommunicationMedium,
                AgentCommunication.communication_id == CommunicationMedium.pk_id
            ).filter(
                AgentCommunication.agent_id == agent_id,
                AgentCommunication.pk_id != phone_data.pk_id,
                func.lower(CommunicationMedium.type) == "phone"
            ).all()   
            if other_phone:
                phone_ids = [phone[0] for phone in other_phone]
                db.query(AgentCommunication).filter(
                    AgentCommunication.pk_id.in_(phone_ids)
                ).update({AgentCommunication.primary: False}, synchronize_session=False)
        phone_record.primary = phone_data.primary
    
    db.commit()
    
    return {
        "message": "Phone record updated successfully",
        "pk_id": str(phone_data.pk_id)
    }

@router.delete("/agents/phone-text/{phone_id}")
def delete_agent_phone_text(
    phone_id: str,
    db: Session = Depends(get_db)
):
    phone_record = (
        db.query(AgentCommunication)
        .join(CommunicationMedium, AgentCommunication.communication_id == CommunicationMedium.pk_id)
        .filter(
            AgentCommunication.pk_id == str(phone_id),
            CommunicationMedium.type == "phone"
        )
        .first()
    )
    
    if not phone_record:
        raise HTTPException(
            status_code=404,
            detail="Phone record not found"
        )
    
    agent_id = phone_record.agent_id
    
    db.delete(phone_record)
    db.commit()
    remaining_phones = (
        db.query(AgentCommunication)
        .join(CommunicationMedium, AgentCommunication.communication_id == CommunicationMedium.pk_id)
        .filter(
            AgentCommunication.agent_id == agent_id,
            func.lower(CommunicationMedium.type) == "phone"
        )
        .all()
    )
    if len(remaining_phones) == 1 and not remaining_phones[0].primary:
        remaining_phones[0].primary = True
        db.commit()
    
    return {
        "success": True,
        "message": "Phone record deleted successfully",
        "deleted_id": str(phone_id)
    }

@router.patch("/agents/{agent_id}/financials")
async def update_agent_financial(
    agent_id: str,
    financial_data: AgentFinancialUpdateRequest,
    db: Session = Depends(get_db)
):
    agent = db.query(Agents).filter(Agents.pk_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    financial_record = (
        db.query(AgentFinancial)
        .filter(
            AgentFinancial.pk_id == str(financial_data.pk_id),
            AgentFinancial.agent_id == str(agent_id)
        )
        .first()
    )
    
    if not financial_record:
        raise HTTPException(
            status_code=404,
            detail="Financial record not found"
        )
    if financial_data.bank_name is not None:
        financial_record.bank_name = financial_data.bank_name
    if financial_data.routing_number is not None:
        financial_record.routing_number = financial_data.routing_number
    if financial_data.account_number is not None:
        financial_record.account_number = financial_data.account_number
    if financial_data.is_primary is not None:
        if financial_data.is_primary:
            db.query(AgentFinancial).filter(
                AgentFinancial.agent_id == str(agent_id),
                AgentFinancial.pk_id != str(financial_data.pk_id)
            ).update({"is_primary": False})
        financial_record.is_primary = financial_data.is_primary
    
    db.commit()
    
    return {
        "message": "Financial record updated successfully",
        "pk_id": str(financial_data.pk_id)
    }

@router.delete("/agents/financials/{financial_id}")
def delete_agent_financial(
    financial_id: str,
    db: Session = Depends(get_db)
):
    financial_record = (
        db.query(AgentFinancial)
        .filter(AgentFinancial.pk_id == str(financial_id))
        .first()
    )
    
    if not financial_record:
        raise HTTPException(
            status_code=404,
            detail="Financial record not found"
        )
    
    agent_id = financial_record.agent_id
    db.delete(financial_record)
    db.commit()
    remaining_financials = db.query(AgentFinancial).filter(
        AgentFinancial.agent_id == agent_id
    ).all()
    if len(remaining_financials) == 1 and not remaining_financials[0].is_primary:
        remaining_financials[0].is_primary = True
        db.commit()
    
    return {
        "success": True,
        "message": "Financial record deleted successfully",
        "deleted_id": str(financial_id)
    }
        
@router.get("/agents-status")
def get_agents_status_list(db: Session = Depends(get_db)):
    salutation = (
        db.query(func.distinct(Salutation.salutation))
        .filter(Salutation.salutation.isnot(None))
        .all()
    )
    status = (
        db.query(func.distinct(AgentStatus.status))
        .filter(AgentStatus.status.isnot(None))
        .all()
    )
    types = (
        db.query(func.distinct(Agents.type))
        .filter(Agents.type.isnot(None))
        .all()
    )
    return {
        "salutation": sorted(row[0] for row in salutation),
        "status": sorted([row[0] for row in status]),
        "types": sorted([row[0] for row in types])
    }
