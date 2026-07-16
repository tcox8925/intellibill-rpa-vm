import datetime
from typing import Optional
import uuid
from pydantic import BaseModel, Field


class MembercareAgentResponse(BaseModel):
    id: Optional[uuid.UUID] = Field(None)
    email: Optional[str] = Field(None)
    name: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    npn: Optional[str] = Field(None)
    phone: Optional[str] = Field(None)
    supervisor: Optional[str] = Field(None)
    active_since: Optional[datetime.datetime] = Field(None, alias="activeSince")
    languages: Optional[str] = Field(None)
    location: Optional[str] = Field(None)

    class Config:
        from_attributes = True
        populate_by_name = True


class MembercareAgentCreateSchema(BaseModel):
    id: str = Field(..., description="Must be an existing users.user_id")
    email: str = Field(...)
    name: str = Field(...)
    status: Optional[str] = Field(None)
    npn: Optional[str] = Field(None)
    phone: Optional[str] = Field(None)
    supervisor: Optional[str] = Field(None)
    languages: Optional[str] = Field(None)
    location: Optional[str] = Field(None)

    class Config:
        from_attributes = True
        populate_by_name = True


class MembercareAgentUpdateSchema(BaseModel):
    email: Optional[str] = Field(None)
    name: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    npn: Optional[str] = Field(None)
    phone: Optional[str] = Field(None)
    supervisor: Optional[str] = Field(None)
    languages: Optional[str] = Field(None)
    location: Optional[str] = Field(None)

    class Config:
        from_attributes = True
        populate_by_name = True
