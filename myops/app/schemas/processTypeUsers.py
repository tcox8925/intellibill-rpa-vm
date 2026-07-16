from pydantic import BaseModel
from typing import Dict, Optional, List
from uuid import UUID

class ProcessTypeUsersBase(BaseModel):
    process_id: UUID
    user_email: Optional[str] = None
    email: Optional[bool] = None

class ProcessTypeUsersCreate(ProcessTypeUsersBase):
    pass

class ProcessTypeUsersUpdate(BaseModel):
    pk_id: UUID
    user_email: Optional[str] = None
    email: Optional[bool] = None

class ProcessTypeUsersSchema(ProcessTypeUsersBase):
    pk_id: UUID
    user_email: Optional[str] = None
    entity_name: Optional[str] = None

    class Config:
        from_attributes = True