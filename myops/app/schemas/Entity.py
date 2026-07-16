import json
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict, Any
from enum import Enum
from app.schemas.SubEntity import SubEntityLocSchema
from uuid import UUID as uuid

class EntityStatus(str, Enum):
    active = "active"
    inactive = "inactive"

class EntitySchema(BaseModel):
    entity_id: Optional[str] = Field(None, alias="entityId")
    id: Optional[uuid] = Field(None, alias="id")
    entity_name: Optional[str] = Field(None, alias="entityName")
    entity_affiliation: Optional[str] = Field(None, alias="entityAffiliation")
    entity_active_status: Optional[str] = Field(None, alias="entityStatus")
    entity_support_email: Optional[str] = Field(None, alias="entityEmail")
    # entity_logo: Optional[str] = Field(None, alias="entityLogo")
    entity_address_1: Optional[str] = Field(None, alias="entityAddress1")
    entity_address_2: Optional[str] = Field(None, alias="entityAddress2")
    entity_zip_code: Optional[str] = Field(None, alias="entityZip")
    entity_city: Optional[str] = Field(None, alias="entityCity")
    entity_state: Optional[str] = Field(None, alias="entityState")

    class Config:
        from_attributes = True   # works with SQLAlchemy ORM
        populate_by_name = True  # allows snake_case <-> camelCase aliasing

class EntityPatchSchema(BaseModel):  # good for PATCH (partial updates)
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    entity_affiliation: Optional[str] = None
    entity_active_status: Optional[str] = None
    entity_support_email: Optional[EmailStr] = None
    entity_logo: Optional[str] = None
    entity_address_1: Optional[str] = None
    entity_address_2: Optional[str] = None
    entity_zip_code: Optional[str] = None
    entity_city: Optional[str] = None
    entity_state: Optional[str] = None

    class Config:
        from_attributes = True



class EntitySubEntityLocSchema(EntitySchema):
    sub_entities: List[SubEntityLocSchema] = []

    class Config:
        from_attributes = True
        