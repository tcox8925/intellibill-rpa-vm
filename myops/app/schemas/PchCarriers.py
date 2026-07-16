from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class PchCarriersSchema(BaseModel):
    txn_id: Optional[str]
    carrier_name: Optional[str]
    carrier_niac_number: Optional[str]
    network_status: Optional[str]
    txn_id_provider: Optional[str]
    updated_on: Optional[datetime]
    source: Optional[str]

    class config:
        from_attributes = True