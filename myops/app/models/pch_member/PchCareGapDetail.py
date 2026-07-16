from sqlalchemy import Column, String, Date, Float
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
import uuid


class PchCareGapDetail(Base):
    __tablename__ = "pch_care_gap_detail"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4,
        nullable=False
    )
    carrier_id = Column(String)
    report_date = Column(Date)
    health_plan = Column(String)
    sub_line_bus = Column(String)
    mem_f_name = Column(String)
    mem_l_name = Column(String)
    dob = Column(Date)
    mem_add = Column(String)
    mem_id = Column(String)
    qra_index = Column(Float)
    qra_qual_index = Column(Float)
    qra_risk_index = Column(Float)
    qra_qual_score = Column(Float)
    mpi = Column(String)
    mem_phone_1 = Column(String)
    mem_phone_2 = Column(String)
    mem_phone_3 = Column(String)
    mem_add_1 = Column(String)
    gap_type = Column(String)
    measure = Column(String)
    measure_2 = Column(String)
    weight = Column(String)
    measure_status = Column(String)
    compl_cat = Column(String)
    eligibility_thru = Column(Date)
    compl_thru = Column(Date)
    service_strt = Column(Date)
    service_end = Column(Date)
    loy_eng_cat = Column(String)
    last_visit = Column(Date)
    prov_id = Column(String)
    prov_name = Column(String)
    npi = Column(String)
    npi_name = Column(String)
    tin_num = Column(String)
    tin_name = Column(String)
    service_prov_id = Column(String)
    service_prov_name = Column(String)
    service_prov_tin = Column(String)
    service_date = Column(Date)
    star_flg = Column(String)
    hpr_flg = Column(String)
    qrs_flg = Column(String)
    report_flg = Column(String)
    medicaid_state = Column(String)
    company = Column(String)
    mem_city = Column(String)
    mem_state = Column(String)
    mem_zip = Column(String)
    source = Column(String)
    source_type = Column(String)


    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchCareGapDetail {values}>"