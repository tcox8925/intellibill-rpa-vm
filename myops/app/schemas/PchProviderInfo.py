from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from .PchProviderLocation import PchProviderLocationSchema
from .PchProviderIdentifiers import PchProviderIdentifiersSchema
from .PchProviderEducation import PchProviderEducationSchema
from .PchRegulatoryValidation import PchRegulatoryValidationSchema
from .PchAffiliations import PchAffiliationsSchema
from .PchCarriers import PchCarriersSchema

class PchProviderInfoSchema(BaseModel):
    txn_id: Optional[str] = None
    company_id: Optional[str] = None 
    company_name: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    group_pch_ipa: Optional[str] = None
    status: Optional[str] = None
    type: Optional[str] = None 
    npi: Optional[str] = None 
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    primary_address_1: Optional[str] = None
    primary_address_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    gender: Optional[str] = None
    race: Optional[str] = None
    email: Optional[str] = None
    primary_speciality: Optional[str] = None
    secondary_speciality: Optional[str] = None
    professional_degree: Optional[str] = None
    rx_waiver_expiration_date: Optional[str] = None
    awards: Optional[str] = None
    language: Optional[str] = None
    board_cert: Optional[str] = None
    board_cert_detail: Optional[str] = None
    updated_on: Optional[datetime] = None
    source: Optional[str] = None
    job_owner_name: Optional[str] = None
    job_owner_email: Optional[str] = None
    job_owner_id: Optional[str] = None
    caqh_number: Optional[str] = None
    caqh_status: Optional[bool] = None
    touch_user: Optional[str] = None
    last_touch: Optional[datetime] = None

    class Config:
        from_attributes = True

class PchProviderInfoSummarySchema(PchProviderInfoSchema):
    locations: Optional[list[PchProviderLocationSchema]] = None
    identifiers: Optional[list[PchProviderIdentifiersSchema]] = None
    education: Optional[list[PchProviderEducationSchema]] = None
    regulatory_validation: Optional[list[PchRegulatoryValidationSchema]] = None
    affiliations: Optional[list[PchAffiliationsSchema]] = None
    carriers: Optional[list[PchCarriersSchema]] = None

    class Config:
        from_attributes = True

class PchProviderInfoCreateSchema(PchProviderInfoSchema):
    
    class Config:
        from_attributes = True

