from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


class PchCaqhInsuranceSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    carrier_name: Optional[str] = None
    policy_number: Optional[str] = None
    insurance_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    occurrence: Optional[str] = None
    aggregate: Optional[str] = None
    self_insured: Optional[bool] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True