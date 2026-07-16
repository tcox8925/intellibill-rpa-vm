from pydantic import BaseModel
from typing import Optional


class PchRegulatoryValidationSchema(BaseModel):
    txn_id: Optional[str] = None
    audit_id: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    date_time: Optional[str] = None
    txn_id_provider: Optional[str] = None

    class config:
        from_attributes = True

class PchRegulatoryValidationWithFailuresSchema(BaseModel):
    txn_id: Optional[str] = None
    audit_id: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    date_time: Optional[str] = None
    txn_id_provider: Optional[str] = None
    fail_description: Optional[str] = None  # Concatenated descriptions

    class Config:
        from_attributes = True