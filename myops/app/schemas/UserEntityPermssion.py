from uuid import UUID as uuid
from pydantic import BaseModel
from typing import Optional, Dict

class UserEntityPermissionSchema(BaseModel):
    permission_id: Optional[uuid] = None
    user_id: Optional[uuid] 
    entity_id: Optional[uuid] 
    permission_json: Optional[str]
    
    class Config:
        from_attributes = True   # works with SQLAlchemy ORM

class UserEntityPermissionUpdateSchema(BaseModel):
    user_id: Optional[uuid] = None
    default_entity_id: Optional[uuid] = None
    default_sub_entity_id: Optional[str] = None
    permission_json: Optional[Dict] = None
    
    class Config:
        from_attributes = True   # works with SQLAlchemy ORM
