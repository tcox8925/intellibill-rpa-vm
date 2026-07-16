from pydantic import BaseModel
from typing import Optional
from enum import Enum
from datetime import datetime

class WpoCompaniesSchema(BaseModel):
    id: Optional[int]
    tax_id: Optional[str]
    name: Optional[str]
    address_1: Optional[str]
    address_2: Optional[str]
    city: Optional[str]
    zip: Optional[str]
    date: Optional[datetime]
    active: Optional[bool]
    logo_path: Optional[str]
    logo_back: Optional[str]


    class Config:
        from_attributes = True