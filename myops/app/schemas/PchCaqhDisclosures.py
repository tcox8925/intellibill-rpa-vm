from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchCaqhDisclosuresSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    disclosure_id: Optional[str] = None
    question_summary: Optional[str] = None
    answer_flag: Optional[bool] = None
    explanation: Optional[str] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True