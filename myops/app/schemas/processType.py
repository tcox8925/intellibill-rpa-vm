from uuid import UUID
from pydantic import BaseModel
from typing import Dict, List, Optional

class teams_channel_base(BaseModel):
    channel_id: str
    channel_name:str
    email: str

class ProcessTypeBase(BaseModel):
    process_type: Optional[str] = None
    process_description: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    entity: Optional[str] = None
    sub_entity: Optional[str] = None
    teams_channel: Optional[List[teams_channel_base]] = None


class ProcessTypeCreate(ProcessTypeBase):
    pass


class ProcessTypeUpdate(ProcessTypeBase):
    pass


class ProcessTypeSchema(ProcessTypeBase):
    process_id: UUID

    class Config:
        from_attributes = True   # for ORM mode (Pydantic v2)
