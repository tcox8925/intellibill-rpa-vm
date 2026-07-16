from pydantic import BaseModel
from typing import Optional


class ProductionSummaryByCarrier(BaseModel):
    carrier: Optional[str]
    total_policies: int
    total_members: int

    class Config:
        from_attributes = True
