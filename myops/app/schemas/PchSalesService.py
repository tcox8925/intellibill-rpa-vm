from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from decimal import Decimal

class PchSalesServiceSchema(BaseModel):
    id: int
    txn_id: str
    service_status: str
    service_name: str
    service_type: str
    service_description: Optional[str]
    service_rate_type: Optional[str]
    service_rate: Optional[Decimal]
    service_price_desc: Optional[str]
    created_on: datetime
    updated_on: Optional[datetime]

    class Config:
        from_attributes = True

class PchSalesServiceCreateUpdateSchema(BaseModel):
    service_status: str
    service_name: str
    service_type: str
    service_description: Optional[str]
    service_rate_type: Optional[str]
    service_rate: Optional[Decimal]
    service_price_desc: Optional[str]