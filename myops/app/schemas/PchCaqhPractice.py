# app/schemas/PchCaqhPractice.py
from pydantic import BaseModel, UUID4
from typing import Optional, List
from datetime import date, datetime


class PchCaqhPracticeDropdownItem(BaseModel):
    id: str
    value: str


class PracticeSchema(BaseModel):
    txn_id: UUID4
    txn_id_provider: UUID4
    npi: str
    practice_uid: Optional[str] = None
    practice_id: Optional[str] = None
    practice_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    after_hours_phone: Optional[str] = None
    currently_practicing_flag: Optional[bool] = None
    ada_flag: Optional[bool] = None
    interpreter_flag: Optional[bool] = None
    practice_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    list_in_directory_flag: Optional[bool] = None
    electronic_billing_flag: Optional[bool] = None
    primary_flag: Optional[bool] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PracticeAccessibilitySchema(BaseModel):
    txn_id: UUID4
    practice_uid: Optional[str] = None
    accessibility: Optional[str] = None
    accessibility_flag: Optional[bool] = None
    other_accessibility_description: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PracticeAssociatesSchema(BaseModel):
    txn_id: UUID4
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    middle_initial: Optional[str] = None
    relationship: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    license_number: Optional[str] = None
    license_state: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PracticeHoursSchema(BaseModel):
    txn_id: UUID4
    day: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    hours_type: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PracticeLanguagesSchema(BaseModel):
    txn_id: UUID4
    language: Optional[str] = None
    type: Optional[str] = None
    employee_type: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PracticeLimitationsSchema(BaseModel):
    txn_id: UUID4
    age_flag: Optional[bool] = None
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    gender_limitation: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PracticePatientAcceptanceSchema(BaseModel):
    txn_id: UUID4
    patient_type: Optional[str] = None
    accepts_flag: Optional[bool] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PracticeServicesSchema(BaseModel):
    txn_id: UUID4
    service_name: Optional[str] = None
    provided_flag: Optional[bool] = None
    lab_cert_program: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True


class PchCaqhPracticeDetailResponse(BaseModel):
    practice: PracticeSchema
    practice_accessibility: List[PracticeAccessibilitySchema]
    practice_associates: List[PracticeAssociatesSchema]
    practice_hours: List[PracticeHoursSchema]
    practice_languages: List[PracticeLanguagesSchema]
    practice_limitations: List[PracticeLimitationsSchema]
    practice_patient_acceptance: List[PracticePatientAcceptanceSchema]
    practice_services: List[PracticeServicesSchema]
