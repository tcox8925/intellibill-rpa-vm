from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

class CrmTicketsUsersCreate(BaseModel):
    user_id: UUID
    status: str

class CrmTicketsUsersOut(BaseModel):
    pk_id: UUID
    user_id: UUID
    user_email: str
    time_stamp: str | None
    first_name: str | None
    last_name: str | None

class CrmTicketsUsersUpdate(BaseModel):
    user_id: UUID
    status: Optional[str] = None