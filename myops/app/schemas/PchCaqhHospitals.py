from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchCaqhHospitalsSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    hospital_name: Optional[str] = None
    aha_id: Optional[str] = None
    privileges: Optional[str] = None
    staff_category: Optional[str] = None
    unrestricted_flag: Optional[bool] = None
    start_date: Optional[str] = None      # string per DDL
    end_date: Optional[str] = None        # string per DDL
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True