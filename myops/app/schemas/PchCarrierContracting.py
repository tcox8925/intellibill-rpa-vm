from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class PchCarrierContractingSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    status: Optional[str]
    carrier_id: Optional[str]
    carrier_name: Optional[str]
    carrier_product: Optional[str]
    carrier_status: Optional[str]
    is_credentialing: Optional[bool]
    created_on: datetime
    updated_on: Optional[datetime]

    class Config:
        from_attributes = True

class PchCarrierContractingCreateUpdateSchema(BaseModel):
    txn_id_provider: str
    status: Optional[str]
    carrier_id: Optional[str]
    carrier_name: Optional[str]
    carrier_product: Optional[str]
    carrier_status: Optional[str]
    is_credentialing: Optional[bool]