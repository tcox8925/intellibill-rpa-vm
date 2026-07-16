from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchAffiliationsSchema(BaseModel):
    txn_id: Optional[str] = None
    affiliate_name: Optional[str] = None
    location: Optional[str] = None
    txn_id_provider: Optional[str] = None
    updated_on: Optional[datetime] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True