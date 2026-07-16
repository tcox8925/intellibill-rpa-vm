from app.models.BaseClasses import Base
from sqlalchemy import Column, String, Integer, Date


class BobVarianceMV(Base):
    __tablename__ = "com_bob_variance_mv"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(String, primary_key=True)

    last_update = Column(Date)

    carrier_id = Column(String)
    mem_market = Column(String)

    agent_npn = Column(String)
    agent_full_name = Column(String)

    mem_name = Column(String)
    mem_dob = Column(String)
    mem_dob_date = Column(Date)

    mem_state = Column(String)
    mem_county = Column(String)
    mem_zip = Column(String)

    contract_count = Column(Integer)
    mem_count = Column(Integer)

    agent_recruiter = Column(String)

    mem_effective_date = Column(Date)
    effective_date_year = Column(Integer)
    effective_date_month = Column(String)
    effective_date_day = Column(Integer)

    mem_paid_thru_date = Column(Date)

    mem_cov_end_date = Column(Date)
    end_date_year = Column(Integer)
    end_date_month = Column(String)
    end_date_day = Column(Integer)

    report_date = Column(Date)
    report_mon_year = Column(String)

    mem_age = Column(Integer)
    agent_key = Column(String)
    agenow = Column(Integer)

    report_month_key = Column(String)

    agecategory = Column(String)
    carrier_status = Column(String)
    payment_status = Column(String)

    product_type = Column(String)

    com_report_date = Column(Date)

    carrier_short_name = Column(String)

    field_sales_director = Column(String)
    sales_director = Column(String)

    direct_upline_name = Column(String)
    top_upline_name = Column(String)

    product_type_mapped = Column(String)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<BobVarianceMV {values}>"