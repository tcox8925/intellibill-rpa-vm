import datetime
from typing import Optional
import uuid
from pydantic import BaseModel, Field


class MembercareCustomerResponse(BaseModel):
    id: Optional[uuid.UUID] = Field(None)
    agent_pk_id: Optional[uuid.UUID] = Field(None, alias="agentPkId")
    caller_name: Optional[str] = Field(None, alias="callerName")
    caller_phone: Optional[str] = Field(None, alias="callerPhone")
    caller_gender: Optional[str] = Field(None, alias="callerGender")
    caller_dob: Optional[str] = Field(None, alias="callerDob")
    caller_address: Optional[str] = Field(None, alias="callerAddress")
    caller_email: Optional[str] = Field(None, alias="callerEmail")
    caller_zip: Optional[str] = Field(None, alias="callerZip")
    caller_county: Optional[str] = Field(None, alias="callerCounty")
    caller_ssn: Optional[str] = Field(None, alias="callerSsn")
    created_at: Optional[datetime.datetime] = Field(None, alias="createdAt")
    updated_at: Optional[datetime.datetime] = Field(None, alias="updatedAt")

    class Config:
        from_attributes = True
        populate_by_name = True


class MembercareCustomerCreateSchema(BaseModel):
    agent_pk_id: Optional[uuid.UUID] = Field(None)
    caller_name: str = Field(...)
    caller_phone: Optional[str] = Field(None)
    caller_gender: Optional[str] = Field(None)
    caller_dob: Optional[str] = Field(None)
    caller_address: Optional[str] = Field(None)
    caller_email: Optional[str] = Field(None)
    caller_zip: Optional[str] = Field(None)
    caller_county: Optional[str] = Field(None)
    caller_ssn: Optional[str] = Field(None)

    class Config:
        from_attributes = True
        populate_by_name = True


class MembercareCustomerUpdateSchema(BaseModel):
    agent_pk_id: Optional[uuid.UUID] = Field(None)
    caller_name: Optional[str] = Field(None)
    caller_phone: Optional[str] = Field(None)
    caller_gender: Optional[str] = Field(None)
    caller_dob: Optional[str] = Field(None)
    caller_address: Optional[str] = Field(None)
    caller_email: Optional[str] = Field(None)
    caller_zip: Optional[str] = Field(None)
    caller_county: Optional[str] = Field(None)
    caller_ssn: Optional[str] = Field(None)

    class Config:
        from_attributes = True
        populate_by_name = True
