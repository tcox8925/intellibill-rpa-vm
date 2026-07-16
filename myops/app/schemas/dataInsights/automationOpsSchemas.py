from typing import Optional, Any, Dict
import uuid
from pydantic import BaseModel
from datetime import date, datetime

class OpsAutomationDashboardBase(BaseModel):
    record_date: date
    carrier_id: int
    carrier_name: Optional[str] = None
    carrier_status: Optional[str] = None
    cadence: Optional[str] = None

    acu_download: Optional[int] = 0
    acu_process: Optional[int] = 0
    acu_status: Optional[int] = 0

    bob_download: Optional[int] = 0
    bob_process: Optional[int] = 0
    bob_status: Optional[int] = 0

    com_download: Optional[int] = 0
    com_process: Optional[int] = 0
    com_status: Optional[int] = 0

    acc_status: Optional[int] = 0
    acr_status: Optional[int] = 0

    last_updated: Optional[datetime] = None

    acc_priority: Optional[str] = None
    acr_priority: Optional[str] = None
    acu_priority: Optional[str] = None
    bob_priority: Optional[str] = None
    com_priority: Optional[str] = None

    acu_automation_type: Optional[str] = None
    bob_automation_type: Optional[str] = None
    com_automation_type: Optional[str] = None
    acc_automation_type: Optional[str] = None
    acr_automation_type: Optional[str] = None

    interruptions: Optional[Dict[str, Any]] = None
    notes: Optional[Dict[str, Any]] = None

class OpsAutomationDashboardCreate(OpsAutomationDashboardBase):
    pass

class OpsAutomationUpdateSchema(BaseModel):
    carrier_name: str
    date: date
    process_type: str
    interruptions: Optional[Any] = None
    notes: Optional[Any] = None
    entity_id: Optional[uuid.UUID] = None
    sub_entity_id: Optional[uuid.UUID] = None

class OpsAutomationDashboardResponse(OpsAutomationDashboardBase):
    id: int

    class Config:
        from_attributes = True

