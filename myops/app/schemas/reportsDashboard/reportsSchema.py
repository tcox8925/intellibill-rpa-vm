from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class FilterItem(BaseModel):
    column: str
    operator: str
    value: Optional[str | List[str]] = None

class AgentReportRequest(BaseModel):
    filters: Optional[List[FilterItem]] = None
    entity_id: Optional[str] = None
    sub_entity_id: Optional[str] = None
    page: int = 1
    page_size: int = 50
    view_type: str = "dashboard"

class CreateReportRequest(BaseModel):
    report_id: Optional[str] = None
    report_name: str
    entity_id: str
    sub_entity_id: str
    description: Optional[str] = None
    filters: Optional[Dict[str, List[FilterItem]]] = None
    selected_columns_order: Optional[List[Dict[str, Any]]] = None
   