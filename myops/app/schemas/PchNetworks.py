from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchNetworksSchema(BaseModel):
    id: Optional[int] = None
    pk_id: Optional[str] = None
    provider: Optional[str] = None
    affiliation: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class PchNetworksCreateUpdateSchema(BaseModel):
    provider: str
    affiliation: str
    status: Optional[str] = "Active"
