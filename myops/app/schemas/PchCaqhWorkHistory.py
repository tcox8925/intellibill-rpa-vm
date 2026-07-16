from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


class PchCaqhWorkHistorySchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    employer_name: Optional[str] = None
    position_title: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True