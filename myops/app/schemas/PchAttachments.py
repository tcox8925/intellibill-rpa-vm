from pydantic import BaseModel
from typing import Optional


class PchAttachmentsSchema(BaseModel):
    txn_id: Optional[str] = None
    path: Optional[str] = None
    description: Optional[str] = None
    date_time: Optional[str] = None
    login: Optional[str] = None
    owner_name: Optional[str] = None
    txn_id_provider: Optional[str] = None

    class config:
        from_attributes = True

class PchAttachmentsGetSchema(PchAttachmentsSchema):
    full_name: Optional[str] = None  # New field for full name
    