from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
import uuid

class PchInpatientCensus(Base):
    __tablename__ = "pch_inpatient_census"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4,
        nullable=False
    )

    pcp_irs_number = Column(Text, nullable=True)
    pcp_irs_name = Column(Text, nullable=True)
    pcp_npi = Column(Text, nullable=True)
    pcp_npi_name = Column(Text, nullable=True)
    amisys_nbr = Column(Text, nullable=True)
    medicare_nbr = Column(Text, nullable=True)
    medicaid_nbr = Column(Text, nullable=True)
    first_name = Column(Text, nullable=True)
    last_name = Column(Text, nullable=True)
    member_dob = Column(Text, nullable=True)
    sex = Column(Text, nullable=True)
    address_line_1 = Column(Text, nullable=True)
    address_line_2 = Column(Text, nullable=True)
    city = Column(Text, nullable=True)
    state = Column(Text, nullable=True)
    zip = Column(Text, nullable=True)
    home_phone_number = Column(Text, nullable=True)
    cell_phone_number = Column(Text, nullable=True)
    facility = Column(Text, nullable=True)
    service_type = Column(Text, nullable=True)
    admit_date = Column(Text, nullable=True)
    diagnosis_code = Column(Text, nullable=True)
    diagnosis_code_description = Column(Text, nullable=True)
    member_type = Column(Text, nullable=True)
    date_member_appended = Column(Text, nullable=True)
    product = Column(Text, nullable=True)
    report_date = Column(Text, nullable=True)
    carrier_id = Column(Text, nullable=True)
    company_id = Column(Text, nullable=True)
    load_date = Column(Text, nullable=True)
    status = Column(Text, nullable=True)
    source = Column(Text, nullable = True)
    source_type = Column(Text, nullable = True)
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchInpatientCensus {values}>"