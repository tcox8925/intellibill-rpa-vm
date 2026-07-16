from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID as uuid

class EntityLocationSchema(BaseModel):
    sub_entity_id: str = Field(..., alias="subEntityId")
    location_id: str = Field(None, alias="locId")
    location_name: str = Field(None, alias="locName")
    location_address_1: Optional[str] = Field(None, alias="locAddress1")
    location_address_2: Optional[str] = Field(None, alias="locAddress2")
    location_city: Optional[str] = Field(None, alias="locCity")
    location_state: Optional[str] = Field(None, alias="locState")
    location_zip: Optional[str] = Field(None, alias="locZip")

    location_entity_id_1_type: Optional[str] = Field(None, alias="locEntityRefIdType1")
    location_entity_id_1: Optional[str] = Field(None, alias="locEntityRefId1")
    location_entity_id_2_type: Optional[str] = Field(None, alias="locEntityRefIdType2")
    location_entity_id_2: Optional[str] = Field(None, alias="locEntityRefId2")
    location_entity_id_3_type: Optional[str] = Field(None, alias="locEntityRefIdType3")
    location_entity_id_3: Optional[str] = Field(None, alias="locEntityRefId3")
    location_entity_id_4_type: Optional[str] = Field(None, alias="locEntityRefIdType4")
    location_entity_id_4: Optional[str] = Field(None, alias="locEntityRefId4")

    class Config:
        from_attributes = True   # SQLAlchemy ORM objects -> Pydantic
        populate_by_name = True  # allow snake_case -> camelCase

class EntityLocationPatchSchema(BaseModel):
    sub_entity_id: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    location_address_1: Optional[str] = None
    location_address_2: Optional[str] = None
    location_city: Optional[str] = None
    location_state: Optional[str] = None
    location_zip: Optional[str] = None
    location_entity_id_1_type: Optional[str] = None
    location_entity_id_1: Optional[str] = None
    location_entity_id_2_type: Optional[str] = None
    location_entity_id_2: Optional[str] = None
    location_entity_id_3_type: Optional[str] = None
    location_entity_id_3: Optional[str] = None
    location_entity_id_4_type: Optional[str] = None
    location_entity_id_4: Optional[str] = None

    class Config:
        from_attributes = True