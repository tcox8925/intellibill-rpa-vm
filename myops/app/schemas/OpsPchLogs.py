from pydantic import BaseModel
from typing import Optional
from datetime import datetime

# Base schema (shared)
class OpsPchLogsSchema(BaseModel):
    log_id: Optional[int] = None
    txn_id: str
    script_name: str
    process_type: str
    status: str
    error: Optional[str] = None
    company_id: Optional[str] = None
    carrier_id: Optional[str] = None
    file_path: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
