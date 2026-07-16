from pydantic import BaseModel
from typing import Optional
import uuid


class PaymentTypeBase(BaseModel):
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    payment_type: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True

class ProductTypeBase(BaseModel):
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    product_type: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True

class AppointmentTypeBase(BaseModel):
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    appointment_type: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True

class LevelCategoryBase(BaseModel):
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    level_cat: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True

class CarrierBase(BaseModel):
    id: Optional[str] = None
    vendor_name: Optional[str] = None
    market: Optional[str] = None
    state_availability: Optional[str] = None
    modified_time: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True
