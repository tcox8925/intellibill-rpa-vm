from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


class PchCaqhEducationSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    program_name: Optional[str] = None
    type: Optional[str] = None
    specialty: Optional[str] = None
    grad_year: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    location_city: Optional[str] = None
    location_state: Optional[str] = None
    country: Optional[str] = None
    degree_abbreviation: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True