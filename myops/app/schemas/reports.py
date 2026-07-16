from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class ReportFilter(BaseModel):
    column: str
    operator: str
    value: Optional[List[str]] = None


class Pagination(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=1000)


class ReportRequest(BaseModel):
    # source → list of filters
    filters: Dict[str, List[ReportFilter]] = Field(default_factory=dict)

    # source → list of column names
    columns: Dict[str, List[str]] = Field(default_factory=dict)

    # pagination: Pagination = Pagination()



class SubFilter(BaseModel):
    name: str
    values: Optional[List[str]] = None

class Filter(BaseModel):
    column: str
    sub_filters: List[SubFilter]

class ReportsResponse(BaseModel):
    columns: List[str]
    filters: List[Filter]

class ReportsBase(BaseModel):
    npn: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[str] = None
    email: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None

class ReportsCreate(ReportsBase):
    pass

class Reports(ReportsBase):
    pk_id: str

    class Config:
        from_attributes = True