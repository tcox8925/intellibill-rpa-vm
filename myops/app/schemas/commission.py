from datetime import date
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field
import uuid

class CommissionTotalsCRMchema(BaseModel):
    statement_month: Optional[date] = None
    payment_type: Optional[str] = None
    statement_total: Optional[Decimal] = None
    status: Optional[str] = None
    status_date: Optional[date] = None
    carrier_name: Optional[str] = None
    carrier_id: Optional[str] = None
    class Config:
        from_attributes = True

class CommissionTotalsResponse(BaseModel):
    total_count: int
    records: List[CommissionTotalsCRMchema]

class MetricCardSchema(BaseModel):
    total_paid: Optional[float] = None
    total_average_ytd: Optional[float] = None
    total_commissions: Optional[float] = None
    total_overrides: Optional[float] = None
    total_assignment: Optional[float] = None
    total_bonus: Optional[float] = None

class CommissionItemSchema(BaseModel):
    carrier_name: Optional[str] = None
    payment: Optional[Decimal] = None
    payment_type: Optional[str] = None
    agent_name: Optional[str] = None
    npn: Optional[str] = None
    effective_date: Optional[str] = None
    coverage_month: Optional[str] = None
    market: Optional[str] = None
    insured_name: Optional[str] = None
    premium: Optional[str] = None
    split: Optional[str] = None
    first_year_renewal: Optional[str] = None
    statement_month: Optional[str] = None
    plan: Optional[str] = None
    lives: Optional[int] = None
    memo: Optional[str] = None
    agent_name: Optional[str] = None
    npn: Optional[str] = None