from pydantic import BaseModel, UUID4
from typing import Optional
from datetime import date, datetime


class PchCaqhProviderInfoSchema(BaseModel):
    txn_id: UUID4
    txn_id_provider: UUID4
    npi: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    birth_city: Optional[str] = None
    birth_state: Optional[str] = None
    birth_county: Optional[str] = None
    birth_country: Optional[str] = None
    citizenship_status: Optional[str] = None
    email: Optional[str] = None
    cell_phone: Optional[str] = None
    race_ethnicity_level_1: Optional[str] = None
    race_ethnicity_level_2: Optional[str] = None
    graduate_type: Optional[str] = None
    provider_type: Optional[str] = None
    other_interests: Optional[str] = None
    dea_flag: Optional[bool] = None
    cds_flag: Optional[bool] = None
    upin_flag: Optional[bool] = None
    npi_flag: Optional[bool] = None
    medicare_flag: Optional[bool] = None
    medicaid_flag: Optional[bool] = None
    fellowship_flag: Optional[bool] = None
    secondary_specialty_flag: Optional[bool] = None
    hospital_privilege_flag: Optional[bool] = None
    military_service_flag: Optional[bool] = None
    work_history_gap_flag: Optional[bool] = None
    hospital_based_flag: Optional[bool] = None
    affiliated_flag: Optional[bool] = None
    delegated_flag: Optional[bool] = None
    updated_on: Optional[datetime] = None

    class Config:
        from_attributes = True