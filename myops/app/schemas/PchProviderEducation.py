from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchProviderEducationSchema(BaseModel):
    txn_id: Optional[str] = None
    school_program_name: Optional[str] = None
    type: Optional[str] = None
    specialty: Optional[str] = None
    grad_year: Optional[str] = None
    location: Optional[str] = None
    txn_id_provider: Optional[str] = None
    updated_on: Optional[datetime] = None
    source: Optional[str] = None

    class config:
        from_attributes = True