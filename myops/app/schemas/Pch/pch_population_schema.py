from typing import List, Optional
from pydantic import BaseModel


# --- Cards ---
class PopulationCardSchema(BaseModel):
    card_key: str
    label: str
    value: Optional[float | int | str] = None


class PopulationCardsResponse(BaseModel):
    cards: List[PopulationCardSchema]


# --- Card Details ---
class PopulationCardDetailRow(BaseModel):
    amisys_number: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    member_dob: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    line_of_business: Optional[str] = None
    product: Optional[str] = None
    population_health_category: Optional[str] = None
    primary_risk_category: Optional[str] = None
    risk_score: Optional[str] = None

    class Config:
        from_attributes = True


class PopulationCardDetailsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[PopulationCardDetailRow]


# --- Filters ---
class FilterOption(BaseModel):
    label: str
    value: str


class PopulationFiltersResponse(BaseModel):
    report_dates: List[FilterOption]
    genders: List[FilterOption]
    age_groups: List[FilterOption]


# --- Dashboard composite pieces ---
class MembershipByMonthPoint(BaseModel):
    month: Optional[str] = None
    count: int = 0


class KpiPmpmPoint(BaseModel):
    category: str
    pmpm: Optional[float] = None


class PmpmByMonthPoint(BaseModel):
    month: Optional[str] = None
    inpatient: Optional[float] = None
    outpatient: Optional[float] = None
    primary_care: Optional[float] = None
    specialty: Optional[float] = None
    net_rx: Optional[float] = None
    net_other_medical: Optional[float] = None
    total: Optional[float] = None


class TopRxExpensePoint(BaseModel):
    drug_desc: Optional[str] = None
    total_paid: Optional[float] = None
    claim_count: Optional[int] = None


class TopDiagnosisPoint(BaseModel):
    diag_code: Optional[str] = None
    description: Optional[str] = None
    claim_count: Optional[int] = None
    total_paid: Optional[float] = None
    cms_hcc_v22: Optional[str] = None
    cms_hcc_v24: Optional[str] = None
    cms_hcc_v28: Optional[str] = None


class PopulationDashboardDataResponse(BaseModel):
    cards: List[PopulationCardSchema]
    membership_trend: List[MembershipByMonthPoint]
    kpi_pmpm: List[KpiPmpmPoint]
    pmpm_trend: List[PmpmByMonthPoint]
    top_rx: List[TopRxExpensePoint]
    top_diagnoses: List[TopDiagnosisPoint]
