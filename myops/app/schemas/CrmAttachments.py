from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


class CrmAttachmentBase(BaseModel):
    path: str
    file_type: Optional[str] = None
    agent_id: UUID
    npn: Optional[str] = None
    source_id: Optional[UUID] = None


class CrmAttachmentCreate(CrmAttachmentBase):
    user_id: Optional[UUID] = None


class CrmAttachmentUpdate(BaseModel):
    pk_id: UUID
    path: Optional[str] = None
    file_type: Optional[str] = None


class CrmAttachmentOut(BaseModel):
    pk_id: UUID
    path: str
    file_type: Optional[str] = None
    time_stamp: Optional[datetime] = None
    user_id: UUID
    agent_id: UUID
    owner_full_name: Optional[str] = None
    login: Optional[str] = None
    is_private: Optional[bool] = None
    source_id: Optional[UUID] = None
    class Config:
        from_attributes = True
