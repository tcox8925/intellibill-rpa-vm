from pydantic import BaseModel
from typing import Optional

# Base schema (shared)
class PchNotesSchema(BaseModel):
    txn_id: Optional[str] = None
    note_type: Optional[str] = None
    notes_context: Optional[str] = None
    date_time: Optional[str] = None
    login: Optional[str] = None
    reference_id: Optional[str] = None

    class Config:
        from_attributes = True

class PchNotesCreateUpdateSchema(BaseModel):
    note_type: Optional[str] = None
    notes_context: Optional[str] = None
    reference_id: Optional[str] = None
    module: Optional[str] = None
