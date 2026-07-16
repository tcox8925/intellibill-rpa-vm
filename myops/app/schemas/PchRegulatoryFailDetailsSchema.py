from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
import uuid


class PchRegulatoryFailDetailsSchema(BaseModel):
    txn_id: Optional[uuid.UUID] = None
    txn_id_reg: Optional[uuid.UUID] = None
    txn_id_provider: Optional[uuid.UUID] = None
    source: Optional[str] = None
    check_type: Optional[str] = None
    action_date: Optional[date] = None
    description: Optional[str] = None
    created_on: Optional[datetime] = None

    class Config:
        from_attributes = True
