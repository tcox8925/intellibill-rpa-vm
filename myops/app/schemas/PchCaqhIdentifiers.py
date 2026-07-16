from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


class PchCaqhIdentifiersSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    id_type: Optional[str] = None
    id_value: Optional[str] = None
    state: Optional[str] = None
    issue_date: Optional[date] = None
    expiration_date: Optional[date] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True