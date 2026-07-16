from typing import List, Optional
from pydantic import BaseModel

class AgentAffilitationCreateRequest(BaseModel):
    npn: str
    affiliated_agent_npn: str
    associated_npn: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True
class AssociationItem(BaseModel):
    npn: str
    fullName: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None
    association_type: str

    class Config:
        from_attributes = True
        populate_by_name = True


class AgentAffiliationsResponse(BaseModel):
    agent_npn: str
    agent_type: str
    associations: List[AssociationItem] = []

    class Config:
        from_attributes = True
        populate_by_name = True
