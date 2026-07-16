from pydantic import BaseModel
from typing import List


class LupTerritoryResponse(BaseModel):
    company_id: str
    company_name: str
    territory: str

    class Config:
        from_attributes = True