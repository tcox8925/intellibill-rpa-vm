from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID
from decimal import Decimal


class PchAuditHistorySchema(BaseModel):
    id: Optional[int] = None
    audit_id: UUID
    txn_id: Optional[str] = None
    created_at: Optional[datetime] = None

    user_email: str
    user_id: int

    action_message: Optional[str] = None
    action: Optional[str] = None
    tab: Optional[str] = None
    module: Optional[str] = None
    sub_module: Optional[str] = None

    entity_id: Optional[Decimal] = None
    sub_entity_id: Optional[Decimal] = None

    class Config:
        from_attributes = True


class PchAuditHistoryCreateSchema(BaseModel):
    txn_id: Optional[str] = None

    action_message: Optional[str] = None
    action: Optional[str] = None
    tab: Optional[str] = None
    module: Optional[str] = None
    sub_module: Optional[str] = None

    entity_id: Optional[Decimal] = None
    sub_entity_id: Optional[Decimal] = None
