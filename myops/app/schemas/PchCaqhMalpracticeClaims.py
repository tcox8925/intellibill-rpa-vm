from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


class PchCaqhMalpracticeClaimsSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    disclosure_id: Optional[str] = None
    question_summary: Optional[str] = None
    carrier_name: Optional[str] = None
    policy_number: Optional[str] = None
    occurrence_date: Optional[date] = None
    claim_date: Optional[date] = None
    allegation: Optional[str] = None
    primary_defendant_flag: Optional[bool] = None
    num_other_codefendant: Optional[int] = None
    case_involvement: Optional[str] = None
    patient_injury_description: Optional[str] = None
    npdb_case_flag: Optional[bool] = None
    patient_died_flag: Optional[bool] = None
    claim_status: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    phone: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True