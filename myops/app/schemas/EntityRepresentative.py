from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID as uuid

class EntityRepresentativeSchema(BaseModel):
    sub_entity_id: str = Field(..., alias="subEntityId")
    rep_id: str = Field(..., alias="repId")
    rep_lname: str = Field(..., alias="repLName")
    rep_fname: str = Field(..., alias="repFName")
    rep_address_1: Optional[str] = Field(None, alias="repAddress1")
    rep_address_2: Optional[str] = Field(None, alias="repAddress2")
    rep_city: Optional[str] = Field(None, alias="repCity")
    rep_state: Optional[str] = Field(None, alias="repState")
    rep_zip: Optional[str] = Field(None, alias="repZip")

    rep_entity_id_1_type: Optional[str] = Field(None, alias="repEntityRefIdType1")
    rep_entity_id_1: Optional[str] = Field(None, alias="repEntityRefId1")
    rep_entity_id_2_type: Optional[str] = Field(None, alias="repEntityRefIdType2")
    rep_entity_id_2: Optional[str] = Field(None, alias="repEntityRefId2")
    rep_entity_id_3_type: Optional[str] = Field(None, alias="repEntityRefIdType3")
    rep_entity_id_3: Optional[str] = Field(None, alias="repEntityRefId3")
    rep_entity_id_4_type: Optional[str] = Field(None, alias="repEntityRefIdType4")
    rep_entity_id_4: Optional[str] = Field(None, alias="repEntityRefId4")

    class Config:
        from_attributes = True   # for SQLAlchemy -> Pydantic conversion
        populate_by_name = True  # allow snake_case -> camelCase

class EntityRepresentativePatchSchema(BaseModel):
    sub_entity_id: Optional[str] = None
    rep_id: Optional[str] = None
    rep_lname: Optional[str] = None
    rep_fname: Optional[str] = None
    rep_address_1: Optional[str] = None
    rep_address_2: Optional[str] = None
    rep_city: Optional[str] = None
    rep_state: Optional[str] = None
    rep_zip: Optional[str] = None
    rep_entity_id_1_type: Optional[str] = None
    rep_entity_id_1: Optional[str] = None
    rep_entity_id_2_type: Optional[str] = None
    rep_entity_id_2: Optional[str] = None
    rep_entity_id_3_type: Optional[str] = None
    rep_entity_id_3: Optional[str] = None
    rep_entity_id_4_type: Optional[str] = None
    rep_entity_id_4: Optional[str] = None

    class Config:
        from_attributes = True