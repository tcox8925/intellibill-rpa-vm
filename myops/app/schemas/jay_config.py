"""Pydantic schemas for JAI Config admin API."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ─── Synonyms ────────────────────────────────────────────────────

class SynonymBase(BaseModel):
    term: str = Field(..., min_length=1, max_length=200)
    db_column: str = Field(..., min_length=1, max_length=200)
    notes: Optional[str] = None

class SynonymCreate(SynonymBase):
    pass

class SynonymUpdate(SynonymBase):
    pass

class SynonymPatch(BaseModel):
    is_active: Optional[bool] = None
    term: Optional[str] = None
    db_column: Optional[str] = None
    notes: Optional[str] = None

class SynonymResponse(SynonymBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Glossary ────────────────────────────────────────────────────

class GlossaryBase(BaseModel):
    term: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    sql_hint: Optional[str] = None
    module: Optional[str] = None

class GlossaryCreate(GlossaryBase):
    pass

class GlossaryUpdate(GlossaryBase):
    pass

class GlossaryPatch(BaseModel):
    is_active: Optional[bool] = None
    term: Optional[str] = None
    description: Optional[str] = None
    sql_hint: Optional[str] = None
    module: Optional[str] = None

class GlossaryResponse(GlossaryBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Filter Values ───────────────────────────────────────────────

class FilterValueBase(BaseModel):
    module: str = Field(..., min_length=1, max_length=100)
    filter_name: str = Field(..., min_length=1, max_length=100)
    column_name: str = Field(..., min_length=1, max_length=100)
    filter_type: Optional[str] = None
    valid_value: str = Field(..., min_length=1, max_length=200)
    sort_order: Optional[int] = 0

class FilterValueCreate(FilterValueBase):
    pass

class FilterValueUpdate(FilterValueBase):
    pass

class FilterValuePatch(BaseModel):
    is_active: Optional[bool] = None
    module: Optional[str] = None
    filter_name: Optional[str] = None
    column_name: Optional[str] = None
    filter_type: Optional[str] = None
    valid_value: Optional[str] = None
    sort_order: Optional[int] = None

class FilterValueResponse(FilterValueBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Query Examples ──────────────────────────────────────────────

class QueryExampleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    example_type: str = Field(default="sql_example", max_length=50)
    sql_text: Optional[str] = None
    natural_query: Optional[str] = None
    domain_tags: Optional[str] = None
    module_tag: Optional[str] = None

class QueryExampleCreate(QueryExampleBase):
    pass

class QueryExampleUpdate(QueryExampleBase):
    pass

class QueryExamplePatch(BaseModel):
    is_active: Optional[bool] = None
    name: Optional[str] = None
    example_type: Optional[str] = None
    sql_text: Optional[str] = None
    natural_query: Optional[str] = None
    domain_tags: Optional[str] = None
    module_tag: Optional[str] = None

class QueryExampleResponse(QueryExampleBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Bulk / Cache ────────────────────────────────────────────────

class BulkSynonymRequest(BaseModel):
    items: List[SynonymCreate]

class BulkGlossaryRequest(BaseModel):
    items: List[GlossaryCreate]

class BulkFilterValueRequest(BaseModel):
    items: List[FilterValueCreate]

class BulkQueryExampleRequest(BaseModel):
    items: List[QueryExampleCreate]

class CacheInvalidateRequest(BaseModel):
    key: Optional[str] = None
