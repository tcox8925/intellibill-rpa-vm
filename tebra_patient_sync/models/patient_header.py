"""SQLAlchemy ORM model for "EDI_Tebra".patient_header, mirroring
migrations/patient_tables.sql.

practice_id has an FK to "EDI_Tebra".practice in the SQL, but there's no
Practice ORM model in this repo (out of scope - only client/group are
modeled), so it's left as a plain column here.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class PatientHeader(Base):
    __tablename__ = "patient_header"
    __table_args__ = {"schema": "EDI_Tebra"}

    # --- identity / source ---
    patient_header_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(default="tebra", server_default=text("'Tebra'"))
    pat_id: Mapped[Optional[str]] = mapped_column(default=None)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("EDI_Tebra.client.client_id"))
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("EDI_Tebra.group.id"))
    practice_id: Mapped[Optional[int]] = mapped_column(default=None)
    source_id: Mapped[Optional[str]] = mapped_column(default=None)

    # --- name ---
    sub_lnam: Mapped[str]
    pat_fnam: Mapped[str]
    middle_name: Mapped[Optional[str]] = mapped_column(default=None)
    prefix: Mapped[Optional[str]] = mapped_column(default=None)
    suffix: Mapped[Optional[str]] = mapped_column(default=None)
    patient_full_name: Mapped[Optional[str]] = mapped_column(default=None)

    # --- demographics ---
    pat_gender: Mapped[Optional[str]] = mapped_column(default=None)
    pat_dob: Mapped[str]
    age: Mapped[Optional[str]] = mapped_column(default=None)
    ssn: Mapped[Optional[str]] = mapped_column(default=None)
    marital_status: Mapped[Optional[str]] = mapped_column(default=None)
    medical_record_number: Mapped[Optional[str]] = mapped_column(default=None)
    active: Mapped[Optional[bool]] = mapped_column(default=True, server_default=text("true"))
    is_self_pay: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    # --- contact ---
    pat_email: Mapped[Optional[str]] = mapped_column(default=None)
    pat_contact: Mapped[Optional[str]] = mapped_column(default=None)
    work_phone: Mapped[Optional[str]] = mapped_column(default=None)
    work_phone_ext: Mapped[Optional[str]] = mapped_column(default=None)
    mobile_phone: Mapped[Optional[str]] = mapped_column(default=None)
    mobile_phone_ext: Mapped[Optional[str]] = mapped_column(default=None)
    home_phone: Mapped[Optional[str]] = mapped_column(default=None)
    home_phone_ext: Mapped[Optional[str]] = mapped_column(default=None)
    pat_contact_consent: Mapped[Optional[bool]] = mapped_column(default=None)
    pat_contact_method: Mapped[Optional[str]] = mapped_column(default=None)

    # --- emergency contact ---
    emergency_name: Mapped[Optional[str]] = mapped_column(default=None)
    emergency_phone: Mapped[Optional[str]] = mapped_column(default=None)
    emergency_phone_ext: Mapped[Optional[str]] = mapped_column(default=None)

    # --- address ---
    pat_addr1: Mapped[Optional[str]] = mapped_column(default=None)
    pat_addr2: Mapped[Optional[str]] = mapped_column(default=None)
    pat_city: Mapped[Optional[str]] = mapped_column(default=None)
    pat_st: Mapped[Optional[str]] = mapped_column(default=None)
    pat_zip: Mapped[Optional[str]] = mapped_column(default=None)
    country: Mapped[Optional[str]] = mapped_column(default=None)

    # --- employment / providers ---
    employer_name: Mapped[Optional[str]] = mapped_column(default=None)
    employment_status: Mapped[Optional[str]] = mapped_column(default=None)
    primary_care_physician_id: Mapped[Optional[str]] = mapped_column(default=None)
    primary_care_physician_full_name: Mapped[Optional[str]] = mapped_column(default=None)
    referring_provider_id: Mapped[Optional[str]] = mapped_column(default=None)
    referring_provider_full_name: Mapped[Optional[str]] = mapped_column(default=None)
    referral_source: Mapped[Optional[str]] = mapped_column(default=None)

    # --- billing ---
    collection_category_name: Mapped[Optional[str]] = mapped_column(default=None)
    total_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), default=None)
    patient_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), default=None)
    insurance_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), default=None)

    # --- alerts ---
    alert_message: Mapped[Optional[str]] = mapped_column(default=None)
    alert_show_patient_details: Mapped[Optional[bool]] = mapped_column(default=None)
    alert_show_encounters: Mapped[Optional[bool]] = mapped_column(default=None)
    alert_show_payments: Mapped[Optional[bool]] = mapped_column(default=None)
    alert_show_statements: Mapped[Optional[bool]] = mapped_column(default=None)
    alert_show_appointments: Mapped[Optional[bool]] = mapped_column(default=None)
    alert_show_claims: Mapped[Optional[bool]] = mapped_column(default=None)

    # --- last activity dates (stored as text in the source system) ---
    last_appointment_date: Mapped[Optional[str]] = mapped_column(default=None)
    last_encounter_date: Mapped[Optional[str]] = mapped_column(default=None)
    last_payment_date: Mapped[Optional[str]] = mapped_column(default=None)
    last_statement_date: Mapped[Optional[str]] = mapped_column(default=None)

    # --- defaults (case / location / provider) ---
    default_case_id: Mapped[Optional[str]] = mapped_column(default=None)
    default_case_name: Mapped[Optional[str]] = mapped_column(default=None)
    default_case_description: Mapped[Optional[str]] = mapped_column(default=None)
    default_service_location_id: Mapped[Optional[str]] = mapped_column(default=None)
    default_service_location_name: Mapped[Optional[str]] = mapped_column(default=None)
    default_rendering_provider_id: Mapped[Optional[str]] = mapped_column(default=None)
    default_rendering_provider_name: Mapped[Optional[str]] = mapped_column(default=None)

    # --- pcn (owned by DB triggers fn_generate_pcn_trigger /
    # fn_assign_pcn_original_after_insert - never written from application code) ---
    pcn: Mapped[Optional[str]] = mapped_column(default=None)
    pcn_original: Mapped[Optional[str]] = mapped_column(default=None)

    # --- audit ---
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=text("now()"))
    loaded_at: Mapped[Optional[datetime]] = mapped_column(default=None)
