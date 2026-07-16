from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


class CrmNoteBase(BaseModel):
    pk_id: Optional[UUID] = None
    type: str
    description: str
    agent_id: UUID
    is_private: Optional[bool] = None
    source_id: Optional[UUID] = None
    sub_type: Optional[str] = None
    time_stamp: Optional[datetime] = None
    agent_npn: Optional[str] = None

class CrmNoteCreate(CrmNoteBase):
    # user_id: Optional[UUID] = None
    pass


class CrmNoteUpdate(BaseModel):
    pk_id: UUID
    type: Optional[str] = None
    is_private: Optional[bool] = None
    sub_type: Optional[str] = None
    description: Optional[str] = None


class CrmNoteOut(BaseModel):
    pk_id: UUID
    type: str
    description: Optional[str] = None
    time_stamp: Optional[datetime] = None
    user_id: UUID
    is_private: Optional[bool] = None
    source_id: Optional[UUID] = None
    sub_type: Optional[str] = None
    agent_id: Optional[UUID] = None
    owner_full_name: Optional[str] = None
    login: Optional[str] = None

    class Config:
        from_attributes = True
