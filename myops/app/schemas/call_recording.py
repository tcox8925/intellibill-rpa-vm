from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
import uuid


class CallRecoringBase(BaseModel):
    id: int
    username: Optional[str] = None
    date: Optional[str] = None
    result_directory: Optional[str] = None
    file_cnt: Optional[int] = None
    status: Optional[str] = None
    created_on: Optional[datetime] = None

    class Config:
        from_attributes = True

class CallRecordingSearchSchema(BaseModel):
    username: str
    date: date   