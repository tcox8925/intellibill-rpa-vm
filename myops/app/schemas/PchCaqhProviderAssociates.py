from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchCaqhProviderAssociatesSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    relationship: Optional[str] = None
    associate_npi: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True