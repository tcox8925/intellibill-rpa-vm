from pydantic import BaseModel
from typing import List


class LupRateTypeResponse(BaseModel):
    company_id: str
    company_name: str
    rate_type: str

    class Config:
        from_attributes = True