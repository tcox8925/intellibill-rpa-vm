import datetime
import json
from pydantic import BaseModel
from typing import Optional

class WpoNavigationSchema(BaseModel):
    id: Optional[int] = None
    parent_id: Optional[int] 
    nav_abbr: Optional[str] 
    name: Optional[str]
    active: Optional[bool]
    order_num: Optional[int]
    icon: Optional[str]
    redirect_path: Optional[str]
    affiliation: Optional[str]

    class Config:
        from_attributes = True   # works with SQLAlchemy ORM

class WpoNavigationUpdateSchema(BaseModel):
    id: Optional[int] = None
    parent_id: Optional[int] = None
    nav_abbr: Optional[str] = None
    name: Optional[str] = None
    active: Optional[bool] = None
    order_num: Optional[int] = None
    icon: Optional[str] = None
    redirect_path: Optional[str] = None
    affiliation: Optional[str] = None

    class Config:
        from_attributes = True   # works with SQLAlchemy ORM

