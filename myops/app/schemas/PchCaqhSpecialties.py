from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


class PchCaqhSpecialtiesSchema(BaseModel):
    txn_id: str
    txn_id_provider: str
    npi: str
    specialty_name: Optional[str] = None
    taxonomy_code: Optional[str] = None
    board_name: Optional[str] = None
    certification_date: Optional[date] = None
    expiration_date: Optional[date] = None
    board_certified_flag: Optional[bool] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True