from datetime import date, datetime
from pydantic import BaseModel, field_validator
from typing import Optional
from uuid import UUID

class ProjectStatusBase(BaseModel):
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    phase: Optional[str] = None
    lead: Optional[str] = None
    entity_id: Optional[str] = None
    project_status: Optional[str] = None
    progress: Optional[float] = None
    priority: Optional[str] = None
    major_deliverable: Optional[str] = None
    project_date: Optional[str] = None
    requested: Optional[str] = None
    project_type: Optional[str] = None
    buisness_entity: Optional[str] = None
    attachments: Optional[list] = None

class ProjectStatusCreate(ProjectStatusBase):
    pass

class ProjectStatusUpdate(ProjectStatusBase):
    project_cost: Optional[float] = None
    class Config:
        from_attributes = True

class ProjectStatusResponse(ProjectStatusBase):
    pk_id: UUID

    class Config:
        from_attributes = True

class ProjectFeatureBase(BaseModel):
    project_pk_id: Optional[UUID] = None
    category: Optional[str] = None
    features: Optional[str] = None
    go_live_date: Optional[date] = None
    status: Optional[str] = None
    priority_number: Optional[float] = None
    project_name: Optional[str] = None
    phase: Optional[str] = None
    progress: Optional[str] = None
    
    @field_validator('go_live_date', mode='before')
    @classmethod
    def parse_go_live_date(cls, v):
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, str):
            if v.strip() == '':
                return None
            # Try MM/DD/YYYY format first
            try:
                return datetime.strptime(v, '%m/%d/%Y').date()
            except ValueError:
                pass
            # Try YYYY-MM-DD format
            try:
                return datetime.strptime(v, '%Y-%m-%d').date()
            except ValueError:
                pass
        return v

class ProjectFeatureCreate(BaseModel):
    project_pk_id: UUID
    status: str
    project_name: str
    category: Optional[str] = None
    features: Optional[str] = None
    go_live_date: Optional[date] = None
    phase: Optional[str] = None
    progress: Optional[str] = None
    
    @field_validator('go_live_date', mode='before')
    @classmethod
    def parse_go_live_date(cls, v):
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, str):
            if v.strip() == '':
                return None
            # Try MM/DD/YYYY format first
            try:
                return datetime.strptime(v, '%m/%d/%Y').date()
            except ValueError:
                pass
            # Try YYYY-MM-DD format
            try:
                return datetime.strptime(v, '%Y-%m-%d').date()
            except ValueError:
                pass
        return v
    
    class Config:
        from_attributes = True

class ProjectFeatureUpdate(ProjectFeatureBase):
    pass

class ProjectFeatureResponse(ProjectFeatureBase):
    pk_id: UUID

    class Config:
        from_attributes = True

class ProjectResourceBase(BaseModel):
    project_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    role: Optional[str] = None

class ProjectResourceCreate(ProjectResourceBase):
    pass

class ProjectResourceUpdate(ProjectResourceBase):
    pass
class ProjectResourceResponse(ProjectResourceBase):
    pk_id: UUID

    class Config:
        from_attributes = True
class ProjectStatusAuditHistoryCreate(BaseModel):
    # user_id: UUID
    project_id: UUID
    entity_id: str
    action_message: str
    action:str
    tab: str
    sub_entity_id:str

class ProjectStatusAuditHistoryResponse(BaseModel):
    pk_id: UUID
    created_at: datetime
    user_id: UUID
    project_id: UUID
    entity_id: str
    action_message: str
    action: str
    tab: str
    sub_entity_id:str

    class Config:
        from_attributes = True

class FeatureSubtaskBase(BaseModel):
    feature_pk_id: Optional[UUID] = None
    id: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    priority_number: Optional[float] = None
    assignee: Optional[str] = None
    description: Optional[str] = None

class FeatureSubtaskUpdate(FeatureSubtaskBase):
    pass

class FeatureSubtaskCreate(BaseModel):
    feature_pk_id: UUID
    status: str
    title: str
    priority_number: Optional[int] = None
    assignee: Optional[str] = None
    description: Optional[str] = None

class ProjectStatusNoteBase(BaseModel):
    pk_id: UUID
    module: str
    description: str
    time_stamp: Optional[datetime] = None
    user_id: Optional[UUID] = None
    source_id: Optional[UUID] = None

    model_config = {"from_attributes": True}
    
class ProjectStatusNoteCreate(BaseModel):
    module: str
    description: str
    user_id: Optional[UUID] = None
    source_id: Optional[UUID] = None

class ProjectStatusNoteUpdate(BaseModel):
    module: Optional[str] = None
    description: Optional[str] = None
    user_id: Optional[UUID] = None
    source_id: Optional[UUID] = None


