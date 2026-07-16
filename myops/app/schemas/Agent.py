from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Union, Text
from pydantic import BaseModel, EmailStr, Field
import uuid

class AgentSchema(BaseModel):
    pk_id: uuid.UUID = Field(None, alias="pkId")
    # assignee_first_name: Optional[str] = Field(None, alias="assigneeFirstName")
    # assignee_last_name: Optional[str] = Field(None, alias="assigneeLastName")
    assignee_npn: Optional[str] = Field(None, alias="assigneeNPN")
    assigns_commissions: Optional[str] = Field(None, alias="assignsCommissions")
    company_id: Optional[str] = Field(None, alias="companyId")
    company_name: Optional[str] = Field(None, alias="companyName")
    email: Optional[str] = Field(None, alias="email")
    first_name: Optional[str] = Field(None, alias="firstName")
    id: Optional[str] = Field(None, alias="id")
    last_name: Optional[str] = Field(None, alias="lastName")
    mailing_city: Optional[str] = Field(None, alias="mailingCity")
    mailing_state: Optional[str] = Field(None, alias="mailingState")
    mailing_street: Optional[str] = Field(None, alias="mailingStreet")
    mailing_street_2: Optional[str] = Field(None, alias="mailingStreet2")
    mailing_zip: Optional[str] = Field(None, alias="mailingZip")
    npn: Optional[str] = Field(None, alias="npn")
    overrides: Optional[str] = Field(None, alias="overrides")
    overrides1: Optional[str] = Field(None, alias="overrides1")
    phone: Optional[str] = Field(None, alias="phone")
    recruiter: Optional[str] = Field(None, alias="recruiterId")
    # recruiter_name: Optional[str] = Field(None, alias="recruiterName")
    responsible_agency: Optional[str] = Field(None, alias="responsibleAgency")
    # responsible_agency_name: Optional[str] = Field(None, alias="responsibleAgencyName")
    sales_director_id: Optional[str] = Field(None, alias="salesDirectorId")
    # sales_director_name: Optional[str] = Field(None, alias="salesDirectorName")
    status: Optional[str] = Field(None, alias="status")
    type: Optional[str] = Field(None, alias="type")
    salutation: Optional[str] = Field(None, alias="salutation")
    full_name: Optional[str] = Field(None, alias="full_name")
    class Config:
        from_attributes = True 
        populate_by_name = True

class LanguagesBase(BaseModel):
    pk_id: uuid.UUID
    language: str

    class Config:
        from_attributes = True

class LanguagesUpdateSchema(BaseModel):
    language_id: str
        
class AgentUpdateSchema(BaseModel):
    # pk_id: uuid.UUID = Field(None, alias="pkId")
    npn: Optional[str] = Field(None, alias="npn")
    type: Optional[str] = Field(None, alias="type")
    salutation: Optional[str] = Field(None, alias="salutation")
    # email: Optional[str] = Field(None, alias="email")
    # phone: Optional[str] = Field(None, alias="phone")
    company_id: Optional[str] = Field(None, alias="company_id")
    company_name: Optional[str] = Field(None, alias="company_name")
    assigns_commissions: Optional[str] = Field(None, alias="assigns_commissions")
    assignee_npn: Optional[str] = Field(None, alias="assignee_npn")
    first_name: Optional[str] = Field(None, alias="first_name")
    middle_name: Optional[str] = Field(None, alias="middle_name")
    last_name: Optional[str] = Field(None, alias="last_name")
    nick_name: Optional[str] = Field(None, alias="nickname")
    date_of_birth: Optional[date] = Field(None, alias="date_of_birth")
    gender: Optional[str] = Field(None, alias="gender")
    status: Optional[str] = Field(None, alias="status")
    overrides: Optional[str] = Field(None, alias="overrides")
    overrides1: Optional[str] = Field(None, alias="overrides1")
    agent_id: Optional[str] = Field(None, alias="agent_id")
    recruiter_id: Optional[str] = Field(None, alias="recruiter_id")
    recruiter_name: Optional[str] = Field(None, alias="recruiter_name")
    sales_director_id: Optional[str] = Field(None, alias="sales_director_id")
    sales_director_name: Optional[str] = Field(None, alias="sales_director_name")
    # mailingStreet: Optional[str] = Field(None, alias="mailing_street")
    # mailingStreet2: Optional[str] = Field(None, alias="mailing_street_2")
    # mailingCity: Optional[str] = Field(None, alias="mailing_city")
    # mailingState: Optional[str] = Field(None, alias="mailing_state")
    # mailingZip: Optional[str] = Field(None, alias="mailing_zip")
    responsible_agency: Optional[str] = Field(None, alias="responsible_agency")
    responsible_agency_name: Optional[str] = Field(None, alias="responsible_agency_name")
    languages: Optional[List[LanguagesUpdateSchema]] | None = None
    preferred_language: Optional[str] = Field(None, alias="preferred_language")

    class Config:
        from_attributes = True
        populate_by_name = True

# Bank info schemas
class AgentBankInfo(BaseModel):
    bank_name: Optional[str] = Field(None, alias="bankName")
    bank_account_name: Optional[str] = Field(None, alias="bankAccountName")
    bank_account_no: Optional[str] = Field(None, alias="bankAccountNumber")
    bank_routing_no: Optional[str] = Field(None, alias="bankRoutingNumber")
    bank_account_type: Optional[str] = Field(None, alias="bankAccountType")
    bank_updated: Optional[str] = Field(None, alias="bankUpdated")

    class Config:
        from_attributes = True
        populate_by_name = True

class AgentBankUpdateRequest(BaseModel):
    bank_name: Optional[str] = Field(None, alias="bankName")
    bank_account_no: Optional[str] = Field(None, alias="bankAccountNumber")
    bank_routing_no: Optional[str] = Field(None, alias="bankRoutingNumber")

    class Config:
        populate_by_name = True

# Agent financial record schema (wpo.agent_financial)
class AgentFinancialRecord(BaseModel):
    pk_id: str = Field(..., alias="pkId")
    agent_id: str = Field(..., alias="agentId")
    bank_name: str = Field(..., alias="bankName")
    routing_number: str = Field(..., alias="routingNumber")
    account_number: str = Field(..., alias="accountNumber")
    is_primary: bool = Field(..., alias="isPrimary")
    created_at: Optional[datetime] = Field(None, alias="createdAt")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")

    class Config:
        from_attributes = True
        populate_by_name = True
class SimpleFinancialUpsertRequest(BaseModel):
    pkId: Optional[str] = None
    bankName: Optional[str] = None
    routingNumber: Optional[str] = None
    accountNumber: Optional[str] = None
    isPrimary: Optional[bool] = None
    
class FinancialUpdateItem(BaseModel):
    pkId: str
    bankName: Optional[str] = None
    routingNumber: Optional[str] = None
    accountNumber: Optional[str] = None
    isPrimary: Optional[bool] = None

class FinancialBulkUpdateRequest(BaseModel):
    items: List[FinancialUpdateItem]

class AgentFinancialUpdateRequest(BaseModel):
    pk_id: str
    bank_name: Optional[str] = None
    routing_number: Optional[str] = None
    account_number: Optional[str] = None
    is_primary: Optional[bool] = None

class AgentEmailUpdateRequest(BaseModel):
    pk_id: str
    communication_type: Optional[str] = None
    value: Optional[str] = None
    marketing_opt_in: Optional[bool] = None
    primary: Optional[bool] = None

class AgentEmailCreateRequest(BaseModel):
    communication_type: str
    value: str
    marketing_opt_in: bool = False
    primary: bool = False
    
    class Config:
        from_attributes = True

# AGENT STATUS SCHEMAS
class AgentStatusBase(BaseModel):
    pk_id: uuid.UUID
    company_id: str = Field(None, alias="companyId")
    company_name: Optional[str] = Field(None, alias="companyName")
    status: Optional[str] = None
    class Config:
        from_attributes = True  
        populate_by_name = True


# agent contract schema


class AgentContractsResponse(BaseModel):
    company_id: Optional[str] = Field(None, alias="companyId")
    company_name: Optional[str] = Field(None, alias="companyName")
    carrier_id: Optional[str] = Field(None, alias="carrierId")
    carrier_name: Optional[str] = Field(None, alias="carrierName")
    contract_id_crm: Optional[str] = Field(None, alias="contractIdCrm")
    name: Optional[str] = Field(None, alias="name")
    status: Optional[str] = Field(None, alias="status")
    writing_number: Optional[str] = Field(None, alias="writingNumber")
    product_type: Optional[str] = Field(None, alias="productType")
    plan_year: Optional[str] = Field(None, alias="planYear")
    com_schedule: Optional[str] = Field(None, alias="comSchedule")
    or_schedule: Optional[str] = Field(None, alias="orSchedule")
    npn: Optional[str] = Field(None, alias="npn")
    appointment_type: Optional[str] = Field(None, alias="appointmentType")
    parent_contract: Optional[str] = Field(None, alias="parentContract")
    upline: Optional[str] = Field(None, alias="upline")
    upline_npn: Optional[str] = Field(None, alias="uplineNPN")
    top_upline: Optional[str] = Field(None, alias="topUpline")
    top_upline_npn: Optional[str] = Field(None, alias="topUplineNPN")
    field_sales_director: Optional[str] = Field(None, alias="fieldSalesDirector")
    recruiter_fsd_confirm: Optional[str] = Field(None, alias="recruiterFsdConfirm")
    agent_id_crm: Optional[str] = Field(None, alias="agentIdCrm")
    first_name: Optional[str] = Field(None, alias="firstName")
    last_name: Optional[str] = Field(None, alias="lastName")
    agent_name: Optional[str] = Field(None, alias="agentName")
    recruiter: Optional[str] = Field(None, alias="recruiter")
    assigns_commissions: Optional[str] = Field(None, alias="assignsCommissions")
    assignee: Optional[str] = Field(None, alias="assignee")
    assignee_npn: Optional[str] = Field(None, alias="assigneeNPN")
    overrides1: Optional[str] = Field(None, alias="overrides1")
    type: Optional[str] = Field(None, alias="type")
    responsible_agent: Optional[str] = Field(None, alias="responsibleAgent")
    or_exclusion: Optional[str] = Field(None, alias="orExclusion")
    level_cat: Optional[str] = Field(None, alias="levelCat")
    start_datetime: Optional[str] = Field(None, alias="startDateTime")
    end_datetime: Optional[str] = Field(None, alias="endDateTime")
    source_system: Optional[str] = Field(None, alias="sourceSystem")
    pause_commission_payment: Optional[bool] = Field(None, alias="pauseCommissionPayment")
    created_date: Optional[date] = Field(None, alias="createdDate")

    class Config:
        populate_by_name = True  # allows using field names internally
        from_attributes = True

class AgentContractsBulkRequest(BaseModel):
    contracts: List[AgentContractsResponse]

class MasterContractOperation(BaseModel):
    create_contract: AgentContractsResponse
    update_contract: AgentContractsResponse

class OrScheduleSchema(BaseModel):
    id: Optional[str] = Field(None, alias="id")
    or_schedule_id: Optional[str] = Field(None, alias="orScheduleId")
    or_detail_id: Optional[str] = Field(None, alias="orDetailId")
    company_id: Optional[str] = Field(None, alias="companyId")
    company_name: Optional[str] = Field(None, alias="companyName")
    carrier_name: Optional[str] = Field(None, alias="carrierName")
    payment_type: Optional[str] = Field(None, alias="paymentType")
    plan_year: Optional[str] = Field(None, alias="planYear")
    status: Optional[str] = Field(None, alias="status")
    level_category: Optional[str] = Field(None, alias="levelCategory")
    level: Optional[str] = Field(None, alias="level")
    territory: Optional[str] = Field(None, alias="territory")
    rate_type: Optional[str] = Field(None, alias="rateType")
    rate_value: Optional[str] = Field(None, alias="rateValue")
    base_product: Optional[str] = Field(None, alias="baseProduct")
    carrier_base_rate: Optional[str] = Field(None, alias="carrierBaseRate")
    agent_base_rate: Optional[str] = Field(None, alias="agentBaseRate")
    agility_base_rate: Optional[str] = Field(None, alias="agilityBaseRate")
    rate_type0: Optional[str] = Field(None, alias="rateType0")
    rate_value0: Optional[str] = Field(None, alias="rateValue0")
    rate_type1: Optional[str] = Field(None, alias="rateType1")
    rate_value1: Optional[str] = Field(None, alias="rateValue1")
    rate_type2: Optional[str] = Field(None, alias="rateType2")
    rate_value2: Optional[str] = Field(None, alias="rateValue2")
    load_date: Optional[str] = Field(None, alias="loadDate")

    class Config:
        populate_by_name = True
        from_attributes = True

class AgentHierarchyModel(BaseModel):
    carrier_name: Optional[str] = Field(None, alias="carrierName")
    agent_npn: str = Field(alias="agentNpn")
    agent_name: Optional[str] = Field(None, alias="agentName")
    upline_agent_npn: Optional[str] = Field(None, alias="uplineAgentNPN")
    upline_agent_name: Optional[str] = Field(None, alias="uplineAgentName")
    carrier_id: str = Field(alias="carrierId")
    order_num: Optional[int] = Field(None, alias="orderNum")
    
    class Config:
        populate_by_name = True
        from_attributes = True


class AgentMasterContractsBase(BaseModel):
    pk_id: Optional[uuid.UUID] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    carrier_id: Optional[str] = None
    carrier_name: Optional[str] = None
    contract_id_crm: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    writing_number: Optional[str] = None
    product_type: Optional[str] = None
    plan_year: Optional[str] = None
    com_schedule: Optional[str] = None
    or_schedule: Optional[str] = None
    npn: Optional[str] = None
    appointment_type: Optional[str] = None
    parent_contract: Optional[str] = None
    upline: Optional[str] = None
    upline_npn: Optional[str] = None
    top_upline: Optional[str] = None
    top_upline_npn: Optional[str] = None
    field_sales_director: Optional[str] = None
    recruiter_fsd_confirm: Optional[str] = None
    agent_id_crm: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    agent_name: Optional[str] = None
    recruiter: Optional[str] = None
    assigns_commissions: Optional[str] = None
    assignee: Optional[str] = None
    assignee_npn: Optional[str] = None
    overrides1: Optional[str] = None
    type: Optional[str] = None
    responsible_agent: Optional[str] = None
    or_exclusion: Optional[str] = None
    level_cat: Optional[str] = None
    start_datetime: Optional[str] = None
    end_datetime: Optional[str] = None
    source_system: Optional[str] = None

    class Config:
        from_attributes = True


class ContractCreateRequest(BaseModel):
    agent_id: str
    carrier_id: str
    assigns_commissions: str
    status: Optional[str] = None
    assignee: Optional[str] = None
    assignee_npn: Optional[str] = None
    entity_id: Optional[str] = None  
    subentity_id: Optional[str] = None
    entity_name: Optional[str] = None  
    carrier_name: Optional[str] = None
    product_type: Optional[str] = None
    parent_contract_id: Optional[str] = None  
    upline_npn: Optional[str] = None
    topupline_npn: Optional[str] = None
    agent_name: Optional[str] = None
    requested_state: Optional[Dict] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True
        
class NameValueModel(BaseModel):
    value: str
    name: str | None

class LupUsersBase(BaseModel):
    id: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    modified_time: Optional[str] = None

    class Config:
        from_attributes = True

class CarrierBase(BaseModel):

    id: Optional[str] = None 
    vendor_name: Optional[str] =  None
    market: Optional[str] =  None
    state_availability: Optional[str] =  None
    modified_time: Optional[str] =  None
    pk_id: Optional[uuid.UUID] =  None

class LicenseItem(BaseModel):
    state_code: str = None
    state_name: str = None
    type: Optional[str] = None
    status: Optional[str] = None
    transaction_id: Optional[uuid.UUID] = None
    agent_npn: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    license_market: Optional[str] = None
    license_number: Optional[str] = None
    file_url: Optional[str] = None
    site_link: Optional[str] = None
    site_name: Optional[str] = None
    qulification: Optional[str] = None
    
class AgentLicensesResponse(BaseModel):
    license: List[LicenseItem]
    sbe_licenses: List[LicenseItem]
    certifications: List[dict]
    
class AgentLicenseCreate(BaseModel):
    transaction_id: Optional[uuid.UUID] = None
    agent_npn: str
    type: str
    status: str
    state: str
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    lic_id: Optional[str] = None
    license_number: Optional[str] = None
    license_owner: Optional[str] = None
    license_market: Optional[str] = None        

# Request body model
class SendAgentEmail(BaseModel):
    sender: EmailStr
    to: List[EmailStr]                
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    subject: Optional[str] = None
    body: str
    body_format: Optional[str] = 'plain'  
    email_type: Literal["mail", "draft","scheduled"] = None
    sent_datetime: Optional[datetime] = None
    schedule_datetime: Optional[datetime] = None
    attachments: Optional[List[dict]] = None
    source_id: Optional[uuid.UUID] = None

class AgentSocialBase(BaseModel):
    platform_id: uuid.UUID
    platform: str
    url: str | None = None
    agent_social_id: uuid.UUID | None = None

    class Config:
        from_attributes = True     
    
class TemplateSchema(BaseModel):
    template: str
    template_name: str
    template_category: str
    template_module: str
    entity_id: str
    sub_entity_id: str
    template_id : str | None =  None
    template_design: Optional[Union[str, Dict[str, Any]]] = None
    owner: Optional[str] = None


class AgentSocialUpdateSchema(BaseModel):
    agent_id: uuid.UUID
    social_links: List[AgentSocialBase]
    
    class Config:
        from_attributes = True

class AgentAddressBase(BaseModel):
    pk_id: uuid.UUID | None = None
    address_type_id: uuid.UUID
    address_type: str
    line1: str | None = None
    line2: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    county: str | None = None
    zip: str | None = None
    primary: bool | None = None
    
    class Config:
        from_attributes = True

class AgentAddressUpdateSchema(BaseModel):
    pk_id: uuid.UUID
    agent_id: uuid.UUID
    address_type_id: uuid.UUID
    line1: str | None = None
    line2: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    county: str | None = None
    zip: str | None = None
    primary: bool | None = None
    
    class Config:
        from_attributes = True

class AgentAddressCreateSchema(BaseModel):
    agent_id: uuid.UUID
    address_type_id: uuid.UUID
    line1: str
    line2: str | None = None
    street: str | None = None
    city: str
    state: str
    county: str | None = None
    zip: str | None = None
    primary: bool | None = False
    
    class Config:
        from_attributes = True

class AgentCommunicationBase(BaseModel):
    pk_id: uuid.UUID | None = None
    communication_id: uuid.UUID
    comm_sub_type: str
    value: str | None = None
    text_opt: bool | None = None
    dnd: str | None = None
    ai_pre_recording: bool | None = None
    marketing_opt_in: bool | None = None
    extension: str | None = None
    primary: bool | None = None
    class Config:
        from_attributes = True

class AgentCommunicationUpdateSchema(BaseModel):
    agent_id: uuid.UUID
    communications: List[AgentCommunicationBase]

class AgentLanguagesBase(BaseModel):
    agent_id: uuid.UUID
    languages: List[str]

    class Config:
        from_attributes = True


class TicketSchema(BaseModel):
    ticket_id: str = None
    subject: str
    description: str
    type: Optional[str] = None
    status: Optional[str] = None
    created_by: str
    owner: Optional[str] = Field(None, alias="owner_email")
    agent_email: Optional[str] = None
    entity_id: Optional[str] = Field(None, alias="entity")
    sub_entity_id: Optional[str] = Field(None, alias="sub_entity")
    resolution: Optional[str] = None
    class Config:
        from_attributes = True
        populate_by_name = True
        
class UpdateTicketSchema(BaseModel):
    ticket_id: str = None
    subject: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    created_by: Optional[str] = None
    owner: Optional[str] = Field(None, alias="owner_email")
    agent_email: Optional[str] = None
    entity_id: Optional[str] = Field(None, alias="entity")
    sub_entity_id: Optional[str] = Field(None, alias="sub_entity")
    resolution: Optional[str] = None
    class Config:
        from_attributes = True
        populate_by_name = True

class TicketAuditCreate(BaseModel):
    action: str
    action_message: str
    # user_id: str
    tab: str
    entity_id:str 
    sub_entity_id:str
    class Config:
        from_attributes = True
    
class CrmAuditHistoryCreate(BaseModel):
    agent_id: uuid.UUID
    entity_id: str
    npn: str
    action: str
    action_message: str
    login_user: str
    sub_entity_id: str
    source_id: Optional[uuid.UUID] = None
    module: Optional[str] = None
    sub_module: Optional[str] = None
    tab: Optional[str] = None

    
class ServiceInterruptionSchema(BaseModel):
    report_date: Optional[str] = None
    process_id: Optional[uuid.UUID] = None
    process_name: Optional[str] = None
    carrier_id: Optional[str] = None
    carrier_name: Optional[str] = None
    raw_file_name: Optional[str] = None
    received: Optional[str] = None
    processed: Optional[str] = None
    issue_description: Optional[str] = None
    issue_status: Optional[str] = None
    issue_date: Optional[str] = None
    resolution_date: Optional[str] = None
    cadence: Optional[str] = None
    id : Optional[uuid.UUID] = None
    interruption_id: Optional[int] = None
    entity_id: Optional[Text] = None
    sub_entity_id : Optional[Text] = None
    resolution_description: Optional[str] = None
    buisness_entity: Optional[str] = None
    buisness_sub_entity: Optional[str] = None
    business_lead: Optional[str] = None
    class Config:
        from_attributes = True

    

class ServiceInterruptionEditSchema(BaseModel):
    id: Optional[uuid.UUID] = None
    process_id: Optional[uuid.UUID] = None
    process_name: Optional[str] = None
    process_type: Optional[str] = None
    process_description: Optional[str] = None
    report_date: Optional[str] = None
    carrier_id: Optional[str] = None
    carrier_name: Optional[str] = None
    issue_description: Optional[str] = None
    issue_status: Optional[str] = None
    issue_date: Optional[str] = None
    resolution_date: Optional[str] = None
    resolution_description: Optional[str] = None
    entity_id: Optional[str] = None
    sub_entity_id: Optional[str] = None
    raw_file_name: Optional[str] = None
    received: Optional[str] = None
    processed: Optional[str] = None
    cadence: Optional[str] = None
    buisness_entity: Optional[str] = None
    buisness_sub_entity: Optional[str] = None
    business_lead: Optional[str] = None
 

class InterruptionActivitySchema(BaseModel):
    interruption_id: uuid.UUID
    description: Optional[str] = None
    date: Optional[datetime] = None
    type: Optional[str] = None
    pk_id : Optional[uuid.UUID] = None

class InterruptionActivityCreateSchema(BaseModel):
    interruption_id: uuid.UUID
    description: Optional[str] = None
    type: str
    date: Optional[str] = None
    
class AgentCarrierResponse(BaseModel):
    carrier_id: str
    carrier_name: Optional[str] = None 
    full_name: Optional[str] = None
    npn: str
    email: Optional[str] = None
    status: Optional[str] = None
    
class PaginatedAgentsResponse(BaseModel):
    total_count: int
    page: int
    page_size: int
    agents: List[AgentCarrierResponse]
    class Config:
        from_attributes = True

class OrganizationEmailCreateSchema(BaseModel):
    email: EmailStr
    department: Optional[str] = None
    type: Optional[str] = None

class OrganizationEmailUpdateSchema(BaseModel):
    email: Optional[EmailStr] = None
    department: Optional[str] = None
    type: Optional[str] = None


class OrganizationEmailResponseSchema(BaseModel):
    pk_id: uuid.UUID
    email: EmailStr
    department: Optional[str]
    type: Optional[str]

    class Config:
        from_attributes = True

class AgentPhoneTextUpdateRequest(BaseModel):
    pk_id: str
    communication_type: Optional[str] = None
    phone: Optional[str] = None
    extension: Optional[str] = None
    text_opt_in: Optional[bool] = None
    ai_pre_recording_opt_in: Optional[bool] = None
    do_not_call: Optional[bool] = None
    primary: Optional[bool] = None

class AgentPhoneTextCreateRequest(BaseModel):
    communication_type: str
    phone: str
    extension: Optional[str] = None
    text_opt_in: Optional[bool] = False
    ai_pre_recording_opt_in: Optional[bool] = False
    do_not_call: Optional[bool] = False
    primary: Optional[bool] = False

class ContractRequirementsResponse(BaseModel):
    requirement: str
    status: str

# Affiliations Schema
class AgentAffiliationsResponse(BaseModel):
    agent_npn: str
    agent_type: str
    principal_agents: List[dict] = []
    responsible_agents: List[dict] = []

    class Config:
        from_attributes = True
        populate_by_name = True

class ComplianceNoteOut(BaseModel):
    pk_id: uuid.UUID
    type: str
    description: Optional[str] = None
    time_stamp: Optional[datetime] = None
    user_id: uuid.UUID
    is_private: Optional[bool] = None
    source_id: Optional[uuid.UUID] = None
    sub_type: Optional[str] = None
    agent_id: Optional[uuid.UUID] = None
    agent_npn: Optional[str] = None
    owner_full_name: Optional[str] = None
    login: Optional[str] = None

    class Config:
        from_attributes = True


class ComplianceAttachmentOut(BaseModel):
    pk_id: uuid.UUID
    path: str
    file_type: Optional[str] = None
    time_stamp: Optional[datetime] = None
    user_id: uuid.UUID
    agent_id: uuid.UUID
    owner_full_name: Optional[str] = None
    login: Optional[str] = None

    class Config:
        from_attributes = True

# schemas/agent_compliance.py
class AgentComplianceResponseSchema(BaseModel):
    pk_id: uuid.UUID
    inquiry_id: str
    npn: str
    inquiry_type: Optional[str] = None
    inquiry_status: Optional[str] = None
    subject_of_inquiry: Optional[str] = None
    agent_status: Optional[str] = None
    date_received: Optional[datetime] = None
    due_date: Optional[datetime] = None
    date_resolved: Optional[datetime] = None
    subject_email: Optional[str] = None
    inquiry_description: Optional[str] = None
    related_carrier: Optional[str] = None
    comm_sent_to_agent: Optional[bool] = None
    comm_sent_to_agency: Optional[bool] = None
    dir_upline: Optional[str] = None
    carrier_case_no: Optional[str] = None
    agent_name: Optional[str] = None
    agent_email: Optional[str] = None
    created_by: Optional[str] = None
    created_time: Optional[datetime] = None
    modified_by: Optional[str] = None
    modified_time: Optional[datetime] = None
    last_activity_time: Optional[datetime] = None
    notes: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[Dict[str, Any]]] = None

    class Config:
        from_attributes = True

class ComplianceNoteCreateSchema(BaseModel):
    type: Optional[str] = None
    description: str
    is_private: Optional[bool] = False

class AgentComplianceCreateSchema(BaseModel):
    npn: str
    inquiry_type: Optional[str] = None
    inquiry_status: Optional[str] = None
    subject_of_inquiry: Optional[str] = None
    agent_status: Optional[str] = None
    date_received: Optional[datetime] = None
    due_date: Optional[datetime] = None
    date_resolved: Optional[datetime] = None
    subject_email: Optional[EmailStr] = None
    inquiry_description: Optional[str] = None
    related_carrier: Optional[str] = None
    comm_sent_to_agent: Optional[bool] = None
    comm_sent_to_agency: Optional[bool] = None
    dir_upline: Optional[str] = None
    carrier_case_no: Optional[str] = None
    agent_name: Optional[str] = None
    agent_email: Optional[EmailStr] = None
    notes: Optional[List[ComplianceNoteCreateSchema]] = None

class AgentComplianceUpdateSchema(AgentComplianceCreateSchema):
    pass