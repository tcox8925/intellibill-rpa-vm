from typing import List, Optional
from fastapi.params import Query
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import null


class CommissionHistoryFilter(BaseModel):
    entitiy_id: Optional[List[str]] = None
    carrier_id: Optional[List[str]] = None
    job_status: Optional[List[str]] = None
    commission_status: Optional[List[str]] = None
    product: Optional[List[str]] = None
    report_date: Optional[str] = None
    comm_month: Optional[str] = None
    page: int = 1
    page_size: int = 50
    sort_column: str = "job_start_datetime"
    sort_order: str = "desc"
    
    class Config:
        from_attributes: True
        

class CommissionProcessHistoryJoinedDTO(BaseModel):
    job_id: Optional[str]
    company_id: Optional[str]
    company_name: Optional[str]
    process_type: Optional[str]
    carrier_id: Optional[str]
    carrier_name: Optional[str]
    product_id: Optional[str]
    product_name: Optional[str]
    report_month: Optional[str]
    com_month: Optional[str]
    file_name: Optional[str]
    job_status: Optional[str]
    commission_status: Optional[str]
    job_start_datetime: Optional[str]
    job_update_datetime: Optional[str]
    job_end_datetime: Optional[str]

class PagedResult(BaseModel):
    total_count: int
    page: int = 1
    page_size: int = 50
    items: List[CommissionProcessHistoryJoinedDTO]


class ComExceptionTotalContractsFilters(BaseModel):
    txnId: str
    selectedAgentNpns: Optional[List[str]] = Field(default_factory=list)
    selectedAgentNames: Optional[List[str]] = Field(default_factory=list)
    selectedExceptionCodes: Optional[List[str]] = Field(default_factory=list)
    selectedWritingNumbers: Optional[List[str]] = Field(default_factory=list)

    class Config:
        from_attributes = True

class ComCalsFiltersSchema(BaseModel):
    job_id: str
    selected_agent_npns: Optional[List[str]] = Field(default_factory=list)
    selected_agent_name: Optional[List[str]] = Field(default_factory=list)
    selected_transaction_statuses: Optional[List[str]] = Field(default_factory=list)
    page: int = 1
    page_size: int = 20
    sort_column: str = "agent_name"
    sort_order: str = "ASC"
    view_type: str = "dashboard"

    class Config:
        from_attributes = True

class ComExceptionsFiltersSchema(BaseModel):
    job_id: str
    selected_agent_npns: Optional[List[str]] = Field(default_factory=list)
    selected_agent_name: Optional[List[str]] = Field(default_factory=list)
    selected_exception_codes: Optional[List[str]] = Field(default_factory=list)
    selected_writing_numbers: Optional[List[str]] = Field(default_factory=list)
    page: int = 1
    page_size: int = 20
    sort_column: str = "total_contracts"
    sort_order: str = "ASC"
    view_type: str = "dashboard"

    class Config:
        from_attributes = True

class ComCalcsFromHeadersFiltersSchema(BaseModel):
    selected_agent_npns: Optional[List[str]] = Field(default_factory=list)
    selected_transaction_statuses: Optional[List[str]] = Field(default_factory=list)
    report_date: Optional[str] = None
    page: int = 1
    page_size: int = 20
    view_type: str = "dashboard"

    class Config:
        from_attributes = True

class OpsRpaScriptLogsFilterSchema(BaseModel):
    companyId: Optional[List[str]] = Field(default_factory=list)
    carrierId: Optional[List[str]] = Field(default_factory=list)
    product: Optional[List[str]] = Field(default_factory=list)
    reportDate: Optional[str] = None
    commMonth: Optional[str] = None

    class Config:
        from_attributes = True

class RunCommissionRequest(BaseModel):
    report_date: str
    statement_date: str
    commission_status: str
    carrier_name: str
    company_id: str
    company_name: str
    entity_affiliation: str
    file_name: str
    login_name: str
    login_email: str

    class Config:
        from_attributes = True

class AdjustmentComCalcsFiltersSchema(BaseModel):
    job_id: str
    page: int = Query(1, ge=1)
    page_size: int = Query(10, ge=1, le=100)
    sort_column: str = Query("agent_name")
    sort_order: str = Query("ASC", regex="^(?i)(asc|desc)$")
    agent_npn: Optional[List[str]] = Query(None)
    agent_name: Optional[List[str]] = Query(None)
    selected_transaction_statuses: Optional[List[str]] = Query(["A"])
    view_type: str = Query("dashboard", description="dashboard/export")

    class Config:
        from_attributes = True

class CommItemsFiltersSchema(BaseModel):
    job_id: str
    selected_agent_npns: Optional[List[str]] = Query(None)
    selected_agent_names: Optional[List[str]] = Query(None)
    selected_payment_types: Optional[List[str]] = Query(None)
    page: int = Query(1, ge=1)
    page_size: int = Query(10, ge=1, le=500)
    sort_column: str = Query("agent_name")
    sort_order: str = Query("ASC", regex="^(?i)(asc|desc)$")
    view_type: str = Query("dashboard", description="dashboard/export")

    class Config:
        from_attributes = True

class CommTotalsFiltersSchema(BaseModel):
    job_id: str
    selected_agent_npns: Optional[List[str]] = Query(None)
    selected_agent_names: Optional[List[str]] = Query(None)
    selected_payment_types: Optional[List[str]] = Query(None)
    selected_lobs: Optional[List[str]] = Query(None)
    page: int = Query(1, ge=1)
    page_size: int = Query(10, ge=1, le=500)
    sort_column: str = Query("job_id")
    sort_order: str = Query("ASC", regex="^(?i)(asc|desc)$")
    view_type: str = Query("dashboard", description="dashboard/export")

    class Config:
        from_attributes = True

class SummaryEmailSchema(BaseModel):
    email: EmailStr
    job_id: str
    carrier_name: str
    commission_month: str
    revenue: str
    commissions: str
    overrides: str
    bonus: str
    adjustments: str

    class Config:
        from_attributes = True

class ExportHistoryRequestSchema(BaseModel):
    job_id: str

    class Config:
        from_attributes = True

class OpsRpaScriptLogsAddSchema(BaseModel):
    script_name: str
    start_datetime: str
    error: Optional[str] = None
    success: str
    file_status: str
    file_path: str
    process_type: str
    file_report_month: str
    file_com_month: str
    company_id: int
    carrier_id: str
    company_name: Optional[str] = None
    vendor_name_full: Optional[str] = None
    vendor_name: Optional[str] = None
    product_name: str

    class Config:
        from_attributes = True