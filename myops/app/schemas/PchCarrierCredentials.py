from pydantic import BaseModel
from typing import Optional
import uuid

class PchCarrierCredentialsSchema(BaseModel):
    txn_id_provider: uuid.UUID
    status: Optional[str] = None
    credential_type: Optional[str] = None
    carrier_id: Optional[str] = None
    carrier_name: Optional[str] = None
    carrier_market: Optional[str] = None
    carrier_plan: Optional[str] = None
    date_time: Optional[str] = None
    login: Optional[str] = None
    module: Optional[str] = None

    class Config:
        from_attributes = True

class PchCarrierCredentialsCreateSchema(PchCarrierCredentialsSchema):
    pass

class PchCarrierCredentialsUpdateSchema(BaseModel):
    pk_id: uuid.UUID
    status: Optional[str] = None
    credential_type: Optional[str] = None
    carrier_id: Optional[str] = None
    carrier_name: Optional[str] = None
    carrier_market: Optional[str] = None
    carrier_plan: Optional[str] = None
    date_time: Optional[str] = None
    login: Optional[str] = None
    module: Optional[str] = None

    class Config:
        from_attributes = True