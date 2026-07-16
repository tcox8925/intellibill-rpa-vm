from pydantic import BaseModel
from typing import List


class LupPaymentLevelResponse(BaseModel):
    company_id: str
    company_name: str
    payment_type: str
    level: str

    class Config:
        from_attributes = True