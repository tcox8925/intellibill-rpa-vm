"""SQLAlchemy ORM model for "EDI_Tebra".patient_coverages, mirroring
migrations/patient_tables.sql.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class PatientCoverage(Base):
    __tablename__ = "patient_coverages"
    __table_args__ = {"schema": "EDI_Tebra"}

    # --- identity ---
    id: Mapped[int] = mapped_column(primary_key=True)  # serial4 PK - unset until the DB assigns it
    source: Mapped[str] = mapped_column(default="tebra", server_default=text("'Tebra'"))
    client_id: Mapped[Optional[int]] = mapped_column(default=None)
    group_id: Mapped[Optional[int]] = mapped_column(default=None)
    practice_id: Mapped[Optional[int]] = mapped_column(default=None)
    patient_header_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("EDI_Tebra.patient_header.patient_header_id"), default=None
    )

    # --- patient (denormalized onto the coverage row) ---
    pat_id: Mapped[Optional[str]] = mapped_column(default=None)
    pat_source: Mapped[str]
    pat_sub_lnam: Mapped[str]
    pat_fnam: Mapped[str]
    pat_dob: Mapped[str]

    # --- coverage / carrier ---
    cov_status: Mapped[Optional[str]] = mapped_column(default=None)
    cov_type: Mapped[Optional[str]] = mapped_column(default=None)  # "P" or "S" by convention
    cov_car_id: Mapped[Optional[str]] = mapped_column(default=None)
    cov_car_nam: Mapped[Optional[str]] = mapped_column(default=None)
    cov_car_type: Mapped[Optional[str]] = mapped_column(default=None)
    cov_rel: Mapped[Optional[str]] = mapped_column(default=None)
    cov_sub_id: Mapped[Optional[str]] = mapped_column(default=None)
    cov_dep_id: Mapped[Optional[str]] = mapped_column(default=None)
    cov_dep_name: Mapped[Optional[str]] = mapped_column(default=None)
    cov_start_date: Mapped[Optional[str]] = mapped_column(default=None)
    cov_end_date: Mapped[Optional[str]] = mapped_column(default=None)
    insurance_type: Mapped[Optional[str]] = mapped_column(default=None)

    # --- company / policy ---
    company_id: Mapped[Optional[str]] = mapped_column(default=None)
    company_name: Mapped[Optional[str]] = mapped_column(default=None)
    policy_number: Mapped[Optional[str]] = mapped_column(default=None)
    group_number: Mapped[Optional[str]] = mapped_column(default=None)

    # --- cost sharing ---
    copay: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), default=None)
    deductible: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), default=None)

    # --- effective dates (drive utils/coverage_rules.py's active/inactive
    # rules - stored as text) ---
    effective_start_date: Mapped[Optional[str]] = mapped_column(default=None)
    effective_end_date: Mapped[Optional[str]] = mapped_column(default=None)

    # --- insured (subscriber), when different from the patient ---
    patient_relationship_to_insured: Mapped[Optional[str]] = mapped_column(default=None)
    insured_full_name: Mapped[Optional[str]] = mapped_column(default=None)
    insured_id_number: Mapped[Optional[str]] = mapped_column(default=None)
    insured_ssn: Mapped[Optional[str]] = mapped_column(default=None)
    insured_dob: Mapped[Optional[str]] = mapped_column(default=None)
    insured_gender: Mapped[Optional[str]] = mapped_column(default=None)
    insured_address1: Mapped[Optional[str]] = mapped_column(default=None)
    insured_address2: Mapped[Optional[str]] = mapped_column(default=None)
    insured_city: Mapped[Optional[str]] = mapped_column(default=None)
    insured_state: Mapped[Optional[str]] = mapped_column(default=None)
    insured_zip: Mapped[Optional[str]] = mapped_column(default=None)
    insured_country: Mapped[Optional[str]] = mapped_column(default=None)
    insured_notes: Mapped[Optional[str]] = mapped_column(default=None)

    # --- plan ---
    plan_id: Mapped[Optional[str]] = mapped_column(default=None)
    plan_name: Mapped[Optional[str]] = mapped_column(default=None)
    plan_address1: Mapped[Optional[str]] = mapped_column(default=None)
    plan_address2: Mapped[Optional[str]] = mapped_column(default=None)
    plan_city: Mapped[Optional[str]] = mapped_column(default=None)
    plan_state: Mapped[Optional[str]] = mapped_column(default=None)
    plan_zip: Mapped[Optional[str]] = mapped_column(default=None)
    plan_country: Mapped[Optional[str]] = mapped_column(default=None)
    plan_phone: Mapped[Optional[str]] = mapped_column(default=None)
    plan_phone_ext: Mapped[Optional[str]] = mapped_column(default=None)
    plan_fax: Mapped[Optional[str]] = mapped_column(default=None)
    plan_fax_ext: Mapped[Optional[str]] = mapped_column(default=None)
    plan_adjuster_name: Mapped[Optional[str]] = mapped_column(default=None)

    # --- active/inactive rule flag (see utils/coverage_rules.py) ---
    active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    # --- audit ---
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=text("now()"))
