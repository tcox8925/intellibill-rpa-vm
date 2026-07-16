from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchProviderLocationSchema(BaseModel):
    txn_id: Optional[str] = None
    source: Optional[str] = None
    type: Optional[str] = None
    location_name: Optional[str] = None
    contact: Optional[str] = None
    fax: Optional[str] = None
    address_1: Optional[str] = None
    address_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    txn_id_provider: Optional[str] = None
    updated_on: Optional[datetime] = None

    class config:
        from_attributes = True