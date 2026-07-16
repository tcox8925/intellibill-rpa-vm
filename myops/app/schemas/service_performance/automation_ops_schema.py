from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


class AutomationOpsBase(BaseModel):
    pk_id: Optional[UUID] = None
    type: str
    description: str
    agent_id: UUID
    is_private: Optional[bool] = None
    source_id: Optional[UUID] = None
    sub_type: Optional[str] = None
    time_stamp: Optional[datetime] = None
    agent_npn: Optional[str] = None
    
    class Config:
        from_attributes = True


class AutomationOpsUpdateSchema(BaseModel):
    id: int
    process_type: str
    carrier_id: str
    cadence: Optional[str] = None
    interruption: Optional[str] = None
    cadence_description: Optional[str] = None
    automated: Optional[str] = None
    automation_type: Optional[str] = None
    notes: Optional[str] = None
    
    class Config:
        from_attributes = True