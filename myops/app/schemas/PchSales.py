from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from decimal import Decimal

class PchSalesSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    pch_header_txn: Optional[str]
    service_status: Optional[str]
    service_name: Optional[str]
    service_type: Optional[str]
    service_description: Optional[str]
    service_rate_type: Optional[str]
    service_rate: Optional[Decimal]
    service_price_desc: Optional[str]
    created_on: datetime
    updated_on: Optional[datetime]

    class Config:
        from_attributes = True

class PchSalesCreateUpdateSchema(BaseModel):
    txn_id_provider: str
    pch_header_txn: Optional[str]
    service_status: Optional[str]
    service_name: Optional[str]
    service_type: Optional[str]
    service_description: Optional[str]
    service_rate_type: Optional[str]
    service_rate: Optional[Decimal]
    service_price_desc: Optional[str]