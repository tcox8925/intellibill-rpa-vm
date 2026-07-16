import uuid
from sqlalchemy import Column, String
from app.models.BaseClasses import Base
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER


class PchMemberRoster(Base):
    __tablename__ = "pch_member_roster"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    amisys_number = Column(String, nullable=True)
    medicaid_number = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    middle_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    member_dob = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    member_phone_number = Column(String, nullable=True)
    member_address_line_1 = Column(String, nullable=True)
    member_address_line_2 = Column(String, nullable=True)
    member_city = Column(String, nullable=True)
    member_state = Column(String, nullable=True)
    member_zip = Column(String, nullable=True)
    mpk_name = Column(String, nullable=True)
    assigned_tin = Column(String, nullable=True)
    pcp_npi = Column(String, nullable=True)
    member_months = Column(String, nullable=True)
    line_of_business = Column(String, nullable=True)
    product = Column(String, nullable=True)
    population_health_category = Column(String, nullable=True)
    primary_risk_category = Column(String, nullable=True)
    risk_score = Column(String, nullable=True)
    total_pcp_claims = Column(String, nullable=True)
    all_admits = Column(String, nullable=True)
    acute_admits = Column(String, nullable=True)
    op_claims = Column(String, nullable=True)
    ed_visits = Column(String, nullable=True)
    ed_to_ip_visits = Column(String, nullable=True)
    snf_admits = Column(String, nullable=True)
    inntwrk_spec_claims = Column(String, nullable=True)
    outntwrk_spec_claims = Column(String, nullable=True)
    total_brand_rx_count = Column(String, nullable=True)
    generic_rx_count = Column(String, nullable=True)
    oth_claims = Column(String, nullable=True)
    preventable_admits = Column(String, nullable=True)
    preventable_ed_visits = Column(String, nullable=True)
    member_elig_start_month = Column(String, nullable=True)
    member_medicaid_start_month = Column(String, nullable=True)
    member_type = Column(String, nullable=True)
    email_address = Column(String, nullable=True)
    report_date = Column(String, nullable=True)
    company_id = Column(String, nullable=True)
    carrier_id = Column(String, nullable=True)
    load_date = Column(String, nullable=True)
    member_status = Column(String, nullable=True)
    source = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchMemberRoster {values}>"