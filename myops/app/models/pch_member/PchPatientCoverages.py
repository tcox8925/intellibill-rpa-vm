import uuid
from sqlalchemy import (
    Column,
    String,
    Integer,
    Numeric,
    Text,
    TIMESTAMP,
    func
)
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base

class PchPatientCoverages(Base):
    __tablename__ = "pch_patient_coverages"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True,  default=uuid.uuid4, nullable=False)
    source = Column(String(100), nullable=False, server_default="Tebra")
    client_id = Column(Integer)
    group_id = Column(Integer)
    practice_id = Column(Integer)
    pat_id = Column(String(500))
    pat_source = Column(String(100), nullable=False)
    pat_sub_lnam = Column(String(500), nullable=False)
    pat_fnam = Column(String(500), nullable=False)
    pat_dob = Column(String(500), nullable=False)
    cov_status = Column(String(500))
    cov_type = Column(String(500))
    cov_car_id = Column(String(500))
    cov_car_nam = Column(String(500))
    cov_car_type = Column(String(500))
    cov_rel = Column(String(500))
    cov_sub_id = Column(String(500))
    cov_dep_id = Column(String(500))
    cov_start_date = Column(String(500))
    cov_end_date = Column(String(500))
    insurance_type = Column(String(50))
    company_id = Column(Text)
    company_name = Column(String(500))
    policy_number = Column(String(500))
    group_number = Column(String(500))
    copay = Column(Numeric(18, 4))
    deductible = Column(Numeric(18, 4))
    effective_start_date = Column(Text)
    effective_end_date = Column(Text)
    patient_relationship_to_insured = Column(String(10))
    insured_full_name = Column(String(500))
    insured_id_number = Column(String(500))
    insured_ssn = Column(String(50))
    insured_dob = Column(Text)
    insured_gender = Column(String(10))
    insured_address1 = Column(String(500))
    insured_address2 = Column(String(500))
    insured_city = Column(String(500))
    insured_state = Column(String(500))
    insured_zip = Column(String(50))
    insured_country = Column(String(500))
    insured_notes = Column(Text)
    plan_id = Column(Text)
    plan_name = Column(String(500))
    plan_address1 = Column(String(500))
    plan_address2 = Column(String(500))
    plan_city = Column(String(500))
    plan_state = Column(String(500))
    plan_zip = Column(String(50))
    plan_country = Column(String(500))
    plan_phone = Column(String(500))
    plan_phone_ext = Column(String(50))
    plan_fax = Column(String(500))
    plan_fax_ext = Column(String(50))
    plan_adjuster_name = Column(String(500))
    cov_dep_name = Column(String(500))
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP,nullable=False, server_default=func.now(), onupdate=func.now())
    member_id = Column(String(500))

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchPatientCoverages {values}>"