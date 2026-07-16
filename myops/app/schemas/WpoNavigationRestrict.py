import datetime
import json
from pydantic import BaseModel
from typing import Optional

class WpoNavigationRestrictSchema(BaseModel):
    id: Optional[int] 
    nav_id: Optional[int] 
    user_id: Optional[int] 
    
    class Config:
        from_attributes = True   # works with SQLAlchemy ORM
