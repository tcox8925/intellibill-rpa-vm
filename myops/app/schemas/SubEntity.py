from pydantic import BaseModel, Field
from typing import Optional, List
from app.schemas.EntityLocation import EntityLocationSchema
from app.schemas.EntityRepresentative import EntityRepresentativeSchema
from uuid import UUID as uuid

class Sub_EntitySchema(BaseModel):
    entity_id: str 
    sub_entity_id: str
    sub_entity_lname: Optional[str]
    sub_entity_fname: str
    sub_entity_address_1: Optional[str] = None
    sub_entity_address_2: Optional[str] = None
    sub_entity_city: Optional[str] = None
    sub_entity_state: Optional[str] = None
    sub_entity_zip: Optional[str] = None
    sub_entity_id_1_type: Optional[str] = None
    sub_entity_id_1: Optional[str] = None
    sub_entity_id_2_type: Optional[str] = None
    sub_entity_id_2: Optional[str] = None
    sub_entity_id_3_type: Optional[str] = None
    sub_entity_id_3: Optional[str] = None
    sub_entity_id_4_type: Optional[str] = None
    sub_entity_id_4: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True
class SubEntitySchema(BaseModel):
    entity_id: str = Field(..., alias="entityId")
    sub_entity_id: str = Field(..., alias="subEntityId")
    sub_entity_lname: Optional[str] = Field(..., alias="subEntityLName")
    sub_entity_fname: str = Field(..., alias="subEntityFName")
    sub_entity_address_1: Optional[str] = Field(None, alias="subEntityAddress1")
    sub_entity_address_2: Optional[str] = Field(None, alias="subEntityAddress2")
    sub_entity_city: Optional[str] = Field(None, alias="subEntityCity")
    sub_entity_state: Optional[str] = Field(None, alias="subEntityState")
    sub_entity_zip: Optional[str] = Field(None, alias="subEntityZip")

    sub_entity_id_1_type: Optional[str] = Field(None, alias="subEntityRefIdType1")
    sub_entity_id_1: Optional[str] = Field(None, alias="subEntityRefId1")
    sub_entity_id_2_type: Optional[str] = Field(None, alias="subEntityRefIdType2")
    sub_entity_id_2: Optional[str] = Field(None, alias="subEntityRefId2")
    sub_entity_id_3_type: Optional[str] = Field(None, alias="subEntityRefIdType3")
    sub_entity_id_3: Optional[str] = Field(None, alias="subEntityRefId3")
    sub_entity_id_4_type: Optional[str] = Field(None, alias="subEntityRefIdType4")
    sub_entity_id_4: Optional[str] = Field(None, alias="subEntityRefId4")

    class Config:
        from_attributes = True
        populate_by_name = True

class SubEntityLocSchema(SubEntitySchema):
    locations: List[EntityLocationSchema] = []

    class Config:
        from_attributes = True

class SubEntityLocRepSchema(SubEntitySchema):
    locations: List[EntityLocationSchema] = []
    representatives: List[EntityRepresentativeSchema] = []

    class Config:
        from_attributes = True
class SubEntityLocationRepSchema(Sub_EntitySchema):
    locations: List[EntityLocationSchema] = Field(default_factory=list)
    representatives: List[EntityRepresentativeSchema] = Field(default_factory=list)

    class Config:
        from_attributes = True


class SubEntityPaginationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    data: List[SubEntityLocationRepSchema]

    class Config:
        from_attributes = True


class SubEntityPatchSchema(BaseModel):
    entity_id: Optional[str] = None
    sub_entity_id: Optional[str] = None
    sub_entity_lname: Optional[str] = None
    sub_entity_fname: Optional[str] = None
    sub_entity_address_1: Optional[str] = None
    sub_entity_address_2: Optional[str] = None
    sub_entity_city: Optional[str] = None
    sub_entity_state: Optional[str] = None
    sub_entity_zip: Optional[str] = None
    sub_entity_id_1_type: Optional[str] = None
    sub_entity_id_1: Optional[str] = None
    sub_entity_id_2_type: Optional[str] = None
    sub_entity_id_2: Optional[str] = None
    sub_entity_id_3_type: Optional[str] = None
    sub_entity_id_3: Optional[str] = None
    sub_entity_id_4_type: Optional[str] = None
    sub_entity_id_4: Optional[str] = None

    class Config:
        from_attributes = True
        
