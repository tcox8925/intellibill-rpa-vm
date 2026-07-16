from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchProviderIdentifiersSchema(BaseModel):
    txn_id: Optional[str] = None
    status: Optional[str] = None
    id_type: Optional[str] = None
    id_issuer: Optional[str] = None
    id_type_value: Optional[str] = None
    id_description: Optional[str] = None
    id_issue_date: Optional[str] = None
    id_state: Optional[str] = None
    txn_id_provider: Optional[str] = None
    updated_on: Optional[datetime] = None
    source: Optional[str] = None

    class config:
        from_attributes: True