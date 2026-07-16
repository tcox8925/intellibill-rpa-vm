from pydantic import BaseModel
from typing import Optional

class CarrierCombinedSchema(BaseModel):
    id: Optional[str]
    vendor_name: Optional[str]
    market: Optional[str]
    state_availability: Optional[str]
    modified_time: Optional[str]
    short_vendor_name: Optional[str]
    carrier_short_name: Optional[str]
    prefix: Optional[str]
    acu_file_name_prefix: Optional[str]
    bob_file_name_prefix: Optional[str]
    writing_num_flag: Optional[str]
    pch_agreement: Optional[str]
    delegated_cred: Optional[str]

    class Config:
        from_attributes = True