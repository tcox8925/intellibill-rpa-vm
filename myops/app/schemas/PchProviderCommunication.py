from pydantic import BaseModel
from typing import Optional

# Base schema (shared)
class PchProviderCommunicationSchema(BaseModel):
    pk_id: Optional[str] = None
    provider_id: Optional[str] = None
    provider_npi: Optional[str] = None
    communication_id: Optional[str] = None
    value: Optional[str] = None
    extension: Optional[str] = None
    text_opt: Optional[bool] = None
    dnd: Optional[str] = None
    ai_pre_recording: Optional[bool] = None
    marketing_opt_in: Optional[bool] = None
    primary: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True

class PchProviderEmailCreateRequest(BaseModel):
    value: str
    communication_type: str
    marketing_opt_in: Optional[bool] = False
    primary: Optional[bool] = False

class PchProviderEmailUpdateRequest(BaseModel):
    pk_id: str
    value: Optional[str] = None
    communication_type: Optional[str] = None
    marketing_opt_in: Optional[bool] = None
    primary: Optional[bool] = None

class PchProviderPhoneTextCreateRequest(BaseModel):
    phone: str
    communication_type: str
    extension: Optional[str] = None
    text_opt_in: Optional[bool] = False
    do_not_call: Optional[bool] = None
    ai_pre_recording_opt_in: Optional[bool] = False
    primary: Optional[bool] = False

class PchProviderPhoneTextUpdateRequest(BaseModel):
    pk_id: str
    phone: Optional[str] = None
    communication_type: Optional[str] = None
    extension: Optional[str] = None
    text_opt_in: Optional[bool] = None
    do_not_call: Optional[str] = None
    ai_pre_recording_opt_in: Optional[bool] = None
    primary: Optional[bool] = None
