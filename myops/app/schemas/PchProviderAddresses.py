from pydantic import BaseModel
import uuid

class ProviderAddressBase(BaseModel):
    pk_id: uuid.UUID | None = None
    address_type_id: uuid.UUID
    address_type: str | None = None
    line1: str | None = None
    line2: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    county: str | None = None
    zip: str | None = None
    primary: bool | None = None

    class Config:
        from_attributes = True


class ProviderAddressCreateSchema(BaseModel):
    provider_id: str
    provider_npi: str
    address_type_id: uuid.UUID
    line1: str | None = None
    line2: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    county: str | None = None
    zip: str | None = None
    primary: bool | None = False

    class Config:
        from_attributes = True


class ProviderAddressUpdateSchema(BaseModel):
    pk_id: uuid.UUID
    provider_id: uuid.UUID
    address_type_id: uuid.UUID
    line1: str | None = None
    line2: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    county: str | None = None
    zip: str | None = None
    primary: bool | None = None

    class Config:
        from_attributes = True
